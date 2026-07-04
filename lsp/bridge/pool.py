"""LSP Bridge — Pool Manager: bridge lifecycle and cleanup."""
from __future__ import annotations

import atexit
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..._logging import setup_logger as _setup_lsp_bridge_logger
from ..auto_install import _auto_install_lsp, _get_install_hint
from ..discovery import _find_tsconfig_root, _find_workspace_folders, _find_workspace_root
from .server import _LANGUAGE_SERVERS, LSPBridge, _resolve_command

logger = _setup_lsp_bridge_logger(__name__)
logger.propagate = True

class LSPManager:
    """Manages LSP bridges keyed by ``(language_id, workspace_root)``.

    Bridges are created lazily on first use and kept alive until they exceed
    the idle timeout.  Thread-safe.

    Monorepo support: workspace folders are discovered from
    ``pnpm-workspace.yaml`` / ``nx.json`` / ``lerna.json`` and sent to the
    LSP server during initialization so that cross-workspace resolution
    works correctly.
    """

    def __init__(self) -> None:
        self._bridges: OrderedDict[Tuple[str, str], LSPBridge] = OrderedDict()
        self._lock = threading.Lock()
        # Cache workspace folder discovery per root
        self._workspace_folders_cache: Dict[str, List[str]] = {}
        # Bridge TTL: close bridges idle longer than this (seconds, default 5 min)
        self._bridge_ttl = 300.0

    def _get_workspace_folders(self, root: str) -> List[str]:
        """Get (cached) workspace folders for a project root."""
        if root not in self._workspace_folders_cache:
            self._workspace_folders_cache[root] = _find_workspace_folders(root)
        return self._workspace_folders_cache[root]

    def _should_use_monorepo_ts_root(self, ts_root: str, mono_root: str, file_path: str) -> bool:
        """Return True when a TS bridge should be rooted at the monorepo root.

        Package-level tsconfigs are best for definitions/diagnostics inside the
        package, but ``workspace/symbol`` in a pnpm monorepo needs the root so
        symbols from sibling workspaces are visible. Detect this by checking that
        the nearest tsconfig sits below a real workspace root.
        """
        if ts_root == mono_root:
            return False
        mono = Path(mono_root)
        ts = Path(ts_root)
        if not (mono / "pnpm-workspace.yaml").exists():
            return False
        try:
            ts.relative_to(mono)
        except ValueError:
            return False
        return True

    def get_bridge(
        self, language_id: str, file_path: str
    ) -> Optional[LSPBridge]:
        """Get or create an LSP bridge for the given language and file.

        Returns ``None`` if no suitable language server is available.
        For TypeScript/JavaScript, uses the nearest ``tsconfig.json`` directory
        as the bridge ``root_uri`` for correct cross-file resolution.
        """
        server_configs = _LANGUAGE_SERVERS.get(language_id)
        if not server_configs:
            logger.debug("get_bridge: no server config for language_id=%s", language_id)
            return None

        root = _find_workspace_root(file_path)

        # For TS/JS, use tsconfig directory as rootUri for better resolution
        ts_root = None
        if language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            ts_root = _find_tsconfig_root(file_path)
            if ts_root:
                logger.debug("get_bridge: TS detected, tsconfig_root=%s (mono_root=%s)", ts_root, root)
                if self._should_use_monorepo_ts_root(ts_root, root, file_path):
                    root = _find_workspace_root(file_path)
                    logger.debug("get_bridge: using monorepo TS root=%s for workspace-wide symbol search", root)
                else:
                    root = ts_root
            else:
                logger.debug("get_bridge: TS detected but no tsconfig.json found, using mono_root=%s", root)

        key = (language_id, root)
        ws_folders = self._get_workspace_folders(_find_workspace_root(file_path)) if ts_root else self._get_workspace_folders(root)

        with self._lock:
            # Check for existing bridge
            if key in self._bridges:
                bridge = self._bridges[key]
                if bridge.is_alive:
                    # Move to end (LRU)
                    self._bridges.move_to_end(key)
                    logger.debug("get_bridge: reusing existing bridge (key=%s)", key)
                    return bridge
                else:
                    logger.debug("get_bridge: existing bridge dead, removing (key=%s)", key)
                    del self._bridges[key]

            # Try each server config
            for cfg in server_configs:
                cmd = cfg["command"]
                if _resolve_command(cmd) is None:
                    logger.debug("LSP server not found: %s", cmd)
                    # Auto-Install versuchen — bei Erfolg Server starten
                    if _auto_install_lsp(cmd, key[0]):
                        if _resolve_command(cmd) is not None:
                            logger.info("LSP server %s installed, creating bridge", cmd)
                        else:
                            logger.warning("auto_install reported success but %s still not on PATH", cmd)
                            continue
                    else:
                        # Fehlschlag (kein sudo, prereqs fehlen) → naechste Config
                        hint = _get_install_hint(cmd)
                        if hint:
                            logger.info("Install hint for %s: %s", cmd, hint)
                        continue

                logger.info("get_bridge: creating new bridge (key=%s, ws_folders=%d)",
                    key, len(ws_folders))
                bridge = LSPBridge(
                    command=cmd,
                    args=cfg.get("args", []),
                    root_uri=root,
                    language_id=cfg.get("language_id", language_id),
                    workspace_folders=ws_folders,
                )
                self._bridges[key] = bridge
                # Evict oldest if we have too many (allow more for multi-lang monorepos)
                max_bridges = 8
                while len(self._bridges) > max_bridges:
                    oldest_key, oldest_bridge = next(iter(self._bridges.items()))
                    if oldest_bridge._alive:
                        oldest_bridge.shutdown()
                    # Kill-Fallback: wenn shutdown den Prozess nicht beendet hat
                    if oldest_bridge._process and oldest_bridge._process.poll() is None:
                        logger.warning("LSP bridge %s still alive after shutdown, killing", oldest_key)
                        oldest_bridge._process.kill()
                        try:
                            oldest_bridge._process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            logger.error("LSP zombie %s could not be killed", oldest_key)
                    del self._bridges[oldest_key]
                    logger.info("Evicted idle LSP bridge: %s (pool full)", oldest_key)
                return bridge

        return None

    def shutdown_all(self) -> None:
        """Shut down all active bridges and clear caches."""
        with self._lock:
            for bridge in self._bridges.values():
                bridge.shutdown()
            self._bridges.clear()
            self._workspace_folders_cache.clear()

    def cleanup_stale_bridges(self, max_age: float = 0) -> int:
        """Shut down bridges older than max_age seconds.

        Uses the LRU ordering of _bridges (oldest = front).
        Only affects bridges idle beyond their TTL.
        """
        import time as _time
        max_age = max_age or self._bridge_ttl
        now = _time.time()
        stale_count = 0
        with self._lock:
            keys_to_remove = []
            for key, bridge in self._bridges.items():
                # Check if bridge has a last_used attribute; default to process age
                age = getattr(bridge, "_last_used", 0) or (bridge._start_time if hasattr(bridge, "_start_time") else 0)
                if age and (now - age) > max_age:
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                bridge = self._bridges.pop(key, None)
                if bridge:
                    try:
                        if bridge._alive:
                            bridge.shutdown()
                        if bridge._process and bridge._process.poll() is None:
                            bridge._process.kill()
                    except Exception as e:
                        logger.debug("cleanup_stale_bridges: error shutting down %s: %s", key, e)
                    stale_count += 1
        if stale_count:
            logger.info("LSPManager: cleaned %d stale bridge(s)", stale_count)
        return stale_count


# Global singleton
_lsp_manager = LSPManager()
atexit.register(_lsp_manager.shutdown_all)


def get_lsp_manager() -> LSPManager:
    """Return the global ``LSPManager`` singleton."""
    return _lsp_manager


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def _detect_language_for_lsp(file_path: str) -> Optional[str]:
    """Detect language suitable for LSP resolution."""
    ext = Path(file_path).suffix.lower()
    lang_map = {
        ".py": "python",
        ".pyi": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".mts": "typescript",
        ".cts": "typescript",
        ".js": "javascript",
        ".jsx": "jsx",
        ".mjs": "javascript",
        ".cjs": "javascript",
        # Rust, Go, Java, C/C++ (waren fälschlich nicht gemappt — LSP wurde nie probiert)
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".h": "c",
        ".hpp": "cpp",
    }
    lang = lang_map.get(ext)
    logger.debug("detect_language: %s -> %s (ext=%s)", file_path, lang, ext)
    return lang


def _read_context_lines(file_path: str, line: int, context: int = 2) -> List[str]:
    """Read *context* lines around *line* (0-based) from *file_path*."""
    try:
        lines = Path(file_path).read_text("utf-8", errors="replace").split("\n")
        start = max(0, line - context)
        end = min(len(lines), line + context + 1)
        return lines[start:end]
    except OSError:
        return []


def _location_to_dict(loc: dict) -> dict:
    """Convert an LSP Location to a Hermes-friendly dict."""
    uri = loc.get("uri", "")
    path = LSPBridge._uri_to_path(uri)
    rng = loc.get("range", {})
    start = rng.get("start", {})
    end = rng.get("end", {})
    line = start.get("line", 0)  # 0-based from LSP
    char = start.get("character", 0)

    # Read context — the target line itself plus surrounding lines
    context_lines = _read_context_lines(path, line, context=3)
    # context_lines starts at max(0, line - 3), so target offset = line - start
    start = max(0, line - 3)
    target_line_idx = line - start
    symbol_text = context_lines[target_line_idx].strip()[:200] if (context_lines and 0 <= target_line_idx < len(context_lines)) else ""

    return {
        "path": path,
        "file": path,  # kept for backward compat (e.g. code_references group_by_file)
        "line": line + 1,  # convert to 1-based
        "end_line": end.get("line", line) + 1,
        "column": char + 1,  # convert to 1-based
        "uri": uri,
        "text": symbol_text,
        "context": context_lines,
    }


__all__ = [
    "LSPBridge", "LSPManager", "get_lsp_manager",
    "_LSP_REQUEST_TIMEOUT", "_LSP_INIT_TIMEOUT", "_LSP_IDLE_TIMEOUT",
    "_LSP_FIRST_REQUEST_DELAY", "_LSP_SUBSEQUENT_DELAY",
    "_LSP_PYTHON_FIRST_DELAY", "_LSP_GENERIC_DELAY",
    "_ast_file_cache", "_cached_read_lines",
    "_AST_CACHE_TTL", "_AST_CACHE_MAX",
    "_LANGUAGE_SERVERS",
    "_SUB_PROJECT_MARKERS", "_USER_MARKERS_PATH",
    "_find_workspace_root", "_find_tsconfig_root",
    "_is_monorepo_root", "_set_workspace_cache",
    "_find_workspace_folders", "_find_nx_or_lerna_folders",
    "_parse_pnpm_workspace", "_expand_workspace_patterns",
    "_parse_workspace_edit", "_build_rename_preview",
    "_apply_edits_by_file", "_resolve_command",
    "_log_diagnostics",
    "_group_by_file",
    "_detect_language_for_lsp", "_read_context_lines",
    "_location_to_dict",
    "_WORKSPACE_ROOT_CACHE",
    "_WORKSPACE_ROOT_CACHE_TTL",
    "_WORKSPACE_ROOT_CACHE_MAX",
    "logger",
]
