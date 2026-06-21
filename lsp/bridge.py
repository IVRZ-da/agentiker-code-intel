#!/usr/bin/env python3
"""
LSP Bridge — Native Language Server Protocol integration for Hermes code_intel.

Provides ``code_definition`` and ``code_references`` tools by spawning real
LSP servers (pyright, pylsp, etc.) and communicating via JSON-RPC over
stdin/stdout.  Includes automatic lifecycle management, timeout handling,
and a graceful fallback to AST-based search when the server is unavailable.

Architecture
------------
- ``LSPBridge``: manages a single LSP server process.  Thread-safe request/
  response matching via a background reader thread.
- ``LSPManager``: lazy singleton per language-server type.  Discovers the
  workspace root and keeps a warm server alive across calls.
"""

from __future__ import annotations

import atexit
import json
import logging

logging.raiseExceptions = False  # Suppress logging errors during shutdown (closed stderr)
import os
import resource
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from selectors import DefaultSelector, EVENT_READ
from typing import Any, Dict, List, Optional, Tuple

# fmt_imports are unused after subpackage split
from .._logging import setup_logger as _setup_lsp_bridge_logger

logger = _setup_lsp_bridge_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum time (seconds) to wait for a single LSP response.
# Reduced from 30 → 15: LSP servers routinely respond in <1s. If they
# don't, it's usually a deadlock (e.g. tsserver parsing a giant file
# that isn't actually relevant to the query).
_LSP_REQUEST_TIMEOUT = 15

# Maximum time (seconds) to wait for the server to start and respond to
# the ``initialize`` handshake.
# Reduced from 60 → 15: if a server can't init in 15s (warm or cold),
# it's likely blocked on something (stderr pipe, plugin init, etc.).
# A 60s timeout just makes Hermes stall for a full minute.
_LSP_INIT_TIMEOUT = 15

# How long to keep an idle server alive before shutting it down.
_LSP_IDLE_TIMEOUT = 300  # 5 minutes

# Delay constants for _wait_for_document_ready
_LSP_FIRST_REQUEST_DELAY = 0.5    # TS/JS first request delay
_LSP_SUBSEQUENT_DELAY = 0.05      # TS/JS subsequent request delay
_LSP_PYTHON_FIRST_DELAY = 0.05    # Python first request delay
_LSP_GENERIC_DELAY = 0.01         # Other languages / Python subsequent delay

# Supported language servers (checked in order of preference).
_LANGUAGE_SERVERS: Dict[str, List[Dict[str, Any]]] = {
    "python": [
        # pyright-langserver — excellent type resolution (via pyright npm/pip)
        {"command": "pyright-langserver", "args": ["--stdio"], "language_id": "python"},
        # pylsp — pure Python fallback, widely available
        {"command": "pylsp", "args": [], "language_id": "python"},
    ],
    "typescript": [
        # typescript-language-server — the standard TS LSP (install via npm)
        {"command": "typescript-language-server", "args": ["--stdio"], "language_id": "typescript"},
    ],
    "tsx": [
        # typescript-language-server handles TSX via languageId: typescriptreact
        {"command": "typescript-language-server", "args": ["--stdio"], "language_id": "typescriptreact"},
    ],
    "javascript": [
        {"command": "typescript-language-server", "args": ["--stdio"], "language_id": "javascript"},
    ],
    "jsx": [
        {"command": "typescript-language-server", "args": ["--stdio"], "language_id": "javascriptreact"},
    ],
    "rust": [
        # rust-analyzer — official Rust LSP (via rustup component)
        {"command": "rust-analyzer", "args": [], "language_id": "rust"},
    ],
    "go": [
        # gopls — official Go LSP (go install golang.org/x/tools/gopls@latest)
        {"command": "gopls", "args": [], "language_id": "go"},
    ],
    "c": [
        # clangd — LLVM-based C/C++ LSP (apt install clangd-18 or clangd)
        {"command": "clangd", "args": [], "language_id": "c"},
        # ccls — alternative C/C++ LSP
        {"command": "ccls", "args": [], "language_id": "c"},
    ],
    "cpp": [
        # clangd handles .cpp, .h, .hpp via language_id: cpp
        {"command": "clangd", "args": [], "language_id": "cpp"},
    ],
}

# ---------------------------------------------------------------------------
# AST File-Read Cache
# ---------------------------------------------------------------------------

_ast_file_cache: dict = {}  # abs_path -> (lines, timestamp)
_AST_CACHE_TTL = 30    # seconds (was 5 — increased for better hit rate)
_AST_CACHE_MAX = 100   # files (was 10 — increased for directory operations)


def _group_by_file(items: List[dict], file_key: str = "file") -> Dict[str, List[dict]]:
    """Group a list of items (each having a *file_key* field) by file.

    Eliminates the duplicated setdefault-pattern seen in
    ``code_callers_tool`` and ``code_references_tool``.
    """
    result: Dict[str, List[dict]] = {}
    for item in items:
        result.setdefault(item.get(file_key, ""), []).append(item)
    return result


def _cached_read_lines(path: str) -> list[str]:
    """Read file lines with a short-lived LRU cache (TTL 5s, max 10 files).

    Multiple AST fallback functions (definition, references, diagnostics)
    may read the same file in quick succession. This avoids redundant I/O.
    """
    abs_path = os.path.abspath(path)
    now = time.monotonic()
    cached = _ast_file_cache.get(abs_path)
    if cached and now - cached[1] < _AST_CACHE_TTL:
        return cached[0]
    lines = Path(path).read_text("utf-8", errors="replace").split("\n")
    _ast_file_cache[abs_path] = (lines, now)
    if len(_ast_file_cache) > _AST_CACHE_MAX:
        oldest = min(_ast_file_cache, key=lambda k: _ast_file_cache[k][1])
        del _ast_file_cache[oldest]
    return lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SUB_PROJECT_MARKERS: Dict[str, str] = {
    # JavaScript / TypeScript Frameworks
    "next.config.ts": "nextjs",
    "next.config.mjs": "nextjs",
    "next.config.js": "nextjs",
    "medusa-config.ts": "medusa",
    "medusa-config.js": "medusa",
    # Language-agnostic project markers (generisch)
    "pyproject.toml": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "Gemfile": "ruby",
    "composer.json": "php",
    "mix.exs": "elixir",
}

# Load user-defined project markers from ~/.hermes/code_intel_markers.json
# Users can extend the built-in markers without editing the plugin source.
# Format: {"filename": "language_tag", ...}
_USER_MARKERS_PATH = os.path.expanduser("~/.hermes/code_intel_markers.json")
if os.path.exists(_USER_MARKERS_PATH):
    try:
        with open(_USER_MARKERS_PATH) as _f:
            _user_markers = json.load(_f)
        if isinstance(_user_markers, dict):
            _SUB_PROJECT_MARKERS.update(
                {k: v for k, v in _user_markers.items()
                 if isinstance(k, str) and isinstance(v, str)}
            )
    except (json.JSONDecodeError, OSError) as _e:
        _logger = logging.getLogger("code_intel.lsp_bridge")
        _logger.warning("Failed to load %s: %s", _USER_MARKERS_PATH, _e)

# Workspace root cache (TTL 300s, max 100 entries)
_WORKSPACE_ROOT_CACHE: Dict[str, tuple[str, float]] = {}
_WORKSPACE_ROOT_CACHE_TTL = 300.0
_WORKSPACE_ROOT_CACHE_MAX = 100


def _find_workspace_root(file_path: str) -> str:
    """Best-effort workspace root discovery for *file_path*.

    Three-pass strategy:
    1. Look for sub-project markers (next.config.ts, medusa-config.ts,
       pyproject.toml, go.mod, Cargo.toml, etc.)
    2. Look for generic project markers, but SKIP monorepo roots
       (package.json with workspaces field) so we keep walking up
    3. Fallback: parent directory of the file

    Results are cached for ``_WORKSPACE_ROOT_CACHE_TTL`` seconds.
    """
    # Cache check
    now = time.monotonic()
    cached = _WORKSPACE_ROOT_CACHE.get(file_path)
    if cached and now - cached[1] < _WORKSPACE_ROOT_CACHE_TTL:
        return cached[0]

    p = Path(file_path).resolve().parent
    candidates = [p] + list(p.parents)
    max_depth = 40

    # Pass 1: Sub-project markers
    for parent in candidates[:max_depth]:
        # Fast sub-project markers (next.config, medusa-config)
        for marker in _SUB_PROJECT_MARKERS:
            if (parent / marker).exists():
                result = str(parent)
                _set_workspace_cache(file_path, result)
                return result

        # tsconfig.json + package.json = TypeScript sub-project
        if (parent / "tsconfig.json").exists() and (parent / "package.json").exists():
            result = str(parent)
            _set_workspace_cache(file_path, result)
            return result

    # Pass 2: Generic markers, skip monorepo roots
    mono_markers = ("pnpm-workspace.yaml", "nx.json", "lerna.json")
    generic_markers = (
        ".git", ".hg",
        "pyproject.toml", "setup.py", "setup.cfg",
        "package.json",
        "Cargo.toml", "go.mod",
        "pom.xml", "build.gradle", "Makefile",
    )
    mono_root = None
    generic_root = None
    for parent in candidates[:max_depth]:
        for m in mono_markers:
            if (parent / m).exists():
                if mono_root is None:
                    mono_root = str(parent)
        if generic_root is None:
            for m in generic_markers:
                if (parent / m).exists():
                    if m == "package.json":
                        # Skip monorepo roots — they're not specific enough
                        if _is_monorepo_root(parent):
                            continue
                    generic_root = str(parent)
                    break
        if mono_root and generic_root:
            break
    result = mono_root or generic_root or str(Path(file_path).resolve().parent)
    _set_workspace_cache(file_path, result)
    return result


def _is_monorepo_root(p: Path) -> bool:
    """Check if *p* is a monorepo root (package.json with workspaces)."""
    pkg_file = p / "package.json"
    if not pkg_file.exists():
        return False
    try:
        import json as _json
        data = _json.loads(pkg_file.read_text("utf-8", errors="replace"))
        return bool(data.get("workspaces"))
    except Exception:
        return False


def _set_workspace_cache(file_path: str, root: str) -> None:
    """Set a cache entry, evicting oldest if over max size."""
    now = time.monotonic()
    _WORKSPACE_ROOT_CACHE[file_path] = (root, now)
    if len(_WORKSPACE_ROOT_CACHE) > _WORKSPACE_ROOT_CACHE_MAX:
        oldest = min(
            _WORKSPACE_ROOT_CACHE,
            key=lambda k: _WORKSPACE_ROOT_CACHE[k][1]
        )
        del _WORKSPACE_ROOT_CACHE[oldest]


def _find_tsconfig_root(file_path: str) -> Optional[str]:
    """For TypeScript files, find the best ``tsconfig.json`` directory.

    In a monorepo pnpm workspace, we want the *project-level* tsconfig (e.g.
    ``apps/immodossier/tsconfig.json``), NOT the monorepo root.  But if a
    root tsconfig exists that uses project references, we prefer that root
    so TSServer can resolve cross-project imports.

    Strategy:
    1. Walk up from the file directory looking for tsconfig.json
    2. Pick the NEAREST one (project-level) if it exists
    3. Only prefer the monorepo root if it has a tsconfig with project references
    """
    p = Path(file_path).resolve().parent

    # Collect ALL tsconfig.json directories going up
    tsconfig_dirs = []
    mono_root = None
    for _ in range(30):
        if (p / "tsconfig.json").exists():
            tsconfig_dirs.append(str(p))
        # Check for monorepo markers
        for marker in ("pnpm-workspace.yaml", "nx.json", "lerna.json"):
            if (p / marker).exists():
                mono_root = str(p)
                break
        parent = p.parent
        if parent == p:
            break
        p = parent

    if not tsconfig_dirs:
        logger.debug("_find_tsconfig_root: no tsconfig.json found for %s", file_path)
        return None

    # If we have a monorepo root with tsconfig, prefer it (enables cross-project resolution)
    if mono_root and mono_root in tsconfig_dirs:
        # Check if root tsconfig has project references — if so, it's the right root
        root_tsconfig = Path(mono_root) / "tsconfig.json"
        try:
            import json as _json
            data = _json.loads(root_tsconfig.read_text("utf-8", errors="replace"))
            # If it has "references" or "composite", it's a proper root tsconfig
            if "references" in data or data.get("compilerOptions", {}).get("composite"):
                logger.debug("_find_tsconfig_root: %s -> mono_root %s (has references)", file_path, mono_root)
                return mono_root
        except Exception:
            pass

    # Otherwise, prefer the closest (project-level) tsconfig
    logger.debug("_find_tsconfig_root: %s -> %s (project-level)", file_path, tsconfig_dirs[0])
    return tsconfig_dirs[0]


def _find_workspace_folders(root: str) -> List[str]:
    """Discover workspace subfolders in a monorepo.

    Scans for ``pnpm-workspace.yaml``, ``nx.json``, or ``lerna.json`` and
    returns the resolved list of workspace folder paths.  Returns an empty
    list for non-monorepo projects.
    """
    root_path = Path(root)
    workspace_cfg = root_path / "pnpm-workspace.yaml"
    if not workspace_cfg.exists():
        return _find_nx_or_lerna_folders(root_path)

    patterns = _parse_pnpm_workspace(workspace_cfg)
    if not patterns:
        return []
    return _expand_workspace_patterns(patterns, root_path)


def _find_nx_or_lerna_folders(root_path: Path) -> List[str]:
    """Check for nx/lerna monorepo markers and return workspace folders."""
    for nx_marker in ("nx.json", "lerna.json"):
        if (root_path / nx_marker).exists():
            folders = []
            for d in ("apps", "packages", "modules", "libs"):
                if (root_path / d).is_dir():
                    folders.append(str(root_path / d))
            return folders
    return []


def _parse_pnpm_workspace(cfg_path: Path) -> List[str]:
    """Parse pnpm-workspace.yaml and return package patterns."""
    try:
        import yaml
        with open(cfg_path, "r") as f:
            cfg = yaml.safe_load(f)
    except Exception:
        # Minimal parser: find lines like "  - 'apps/*'"
        try:
            text = cfg_path.read_text("utf-8", errors="replace")
            patterns = []
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    val = stripped[2:].strip().strip("'\"")
                    if not val.startswith("!"):
                        patterns.append(val)
            return patterns
        except Exception:
            return []

    if not cfg or "packages" not in cfg:
        return []
    return cfg["packages"]


def _expand_workspace_patterns(patterns: List[str], root_path: Path) -> List[str]:
    """Expand workspace glob patterns to concrete folder paths."""
    folders: List[str] = []
    for pattern in patterns:
        if pattern.startswith("!"):
            continue
        matches = sorted(root_path.glob(pattern))
        for m in matches:
            if m.is_dir():
                folders.append(str(m))
        parent_match = root_path / pattern.replace("/*", "")
        if parent_match.is_dir() and str(parent_match) not in folders:
            if "*" not in pattern and str(parent_match) not in folders:
                folders.append(str(parent_match))
    return folders



def _parse_workspace_edit(workspace_edit: dict) -> Dict[str, list]:
    """Parse LSP WorkspaceEdit into {file_path: [TextEdit]} dict.
    Handles both {changes: ...} and {documentChanges: [...]} formats."""
    edits_by_file: dict = {}
    for uri, text_edits in (workspace_edit.get("changes") or {}).items():
        fp = uri[7:] if uri.startswith("file://") else uri
        edits_by_file.setdefault(fp, []).extend(text_edits)
    for doc_change in workspace_edit.get("documentChanges") or []:
        if "textDocument" in doc_change:
            uri = doc_change["textDocument"].get("uri", "")
            fp = uri[7:] if uri.startswith("file://") else uri
            edits_by_file.setdefault(fp, []).extend(doc_change.get("edits", []))
    return edits_by_file


def _build_rename_preview(edits_by_file: Dict[str, list]) -> List[dict]:
    """Build a human-readable preview of rename edits."""
    preview = []
    for fp, tedits in sorted(edits_by_file.items()):
        lines = sorted({e.get("range", {}).get("start", {}).get("line", 0) + 1 for e in tedits})
        preview.append({"file": fp, "edit_count": len(tedits), "lines": lines})
    return preview


def _apply_edits_by_file(edits_by_file: Dict[str, list]) -> List[Dict[str, object]]:
    """Apply TextEdits per file, sorted in reverse order to avoid offset drift."""
    applied = []
    for fp, tedits in edits_by_file.items():
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            lines_arr = content.splitlines(keepends=True)

            def _offset(ln: int, ch: int) -> int:
                return sum(len(line) for line in lines_arr[:ln]) + ch

            edits_sorted = sorted(
                tedits,
                key=lambda e: (
                    e["range"]["start"]["line"],
                    e["range"]["start"]["character"],
                ),
                reverse=True,
            )
            new_content = content
            for e in edits_sorted:
                s = e["range"]["start"]
                en = e["range"]["end"]
                start_off = _offset(s["line"], s["character"])
                end_off = _offset(en["line"], en["character"])
                new_content = new_content[:start_off] + e["newText"] + new_content[end_off:]
                lines_arr = new_content.splitlines(keepends=True)

            with open(fp, "w", encoding="utf-8") as f:
                f.write(new_content)
            applied.append({"file": fp, "edits": len(tedits), "status": "ok"})
        except Exception as exc:
            applied.append({"file": fp, "edits": len(tedits), "status": f"error: {exc}"})
            logger.exception("code_rename apply failed for %s", fp)
    return applied


def _resolve_command(cmd: str) -> Optional[str]:
    """Return the full path for *cmd* if it exists on ``$PATH``, else ``None``."""
    return shutil.which(cmd)


# ---------------------------------------------------------------------------
# LSP Bridge — manages a single server process
# ---------------------------------------------------------------------------


def _log_diagnostics(diagnostics: list, path: str) -> None:
    """Log LSP diagnostics: errors as warning, warnings as debug."""
    errors = [d for d in diagnostics if isinstance(d, dict) and d.get("severity") == 1]
    warnings = [d for d in diagnostics if isinstance(d, dict) and d.get("severity") == 2]
    for e in errors[:5]:
        logger.warning("LSP diagnostic: %s:%d: %s",
            path, e.get("range", {}).get("start", {}).get("line", 0) + 1,
            e.get("message", ""))
    for w in warnings[:3]:
        logger.debug("LSP diagnostic: %s:%d: %s",
            path, w.get("range", {}).get("start", {}).get("line", 0) + 1,
            w.get("message", ""))




@dataclass
class LSPBridge:
    """Manages one LSP server process over JSON-RPC stdin/stdout."""

    command: str
    args: List[str]
    root_uri: str
    language_id: str
    workspace_folders: List[str] = field(default_factory=list)
    _process: Optional[subprocess.Popen] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _req_id: int = field(default=0, init=False, repr=False)
    _pending: Dict[int, threading.Event] = field(default_factory=dict, init=False, repr=False)
    _responses: Dict[int, Any] = field(default_factory=dict, init=False, repr=False)
    _reader_thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _alive: bool = field(default=False, init=False, repr=False)
    _last_activity: float = field(default=0.0, init=False, repr=False)
    _last_heartbeat: float = field(default=0.0, init=False, repr=False)
    _initialized: bool = field(default=False, init=False, repr=False)
    _init_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _diagnostics_cache: OrderedDict = field(default_factory=lambda: OrderedDict(), init=False, repr=False)
    _open_documents: set = field(default_factory=set, init=False, repr=False)  # Track open docs to avoid duplicate didOpen
    _closing_uris: Dict[str, float] = field(default_factory=dict, init=False, repr=False)  # URI→timestamp — TTL-based guard vs open_document race
    _reconcile_close_uris: OrderedDict[str, float] = field(default_factory=OrderedDict, init=False, repr=False)
    # Circuit breaker — prevents repeated attempts after N failures
    _failure_count: int = field(default=0, init=False, repr=False)
    _circuit_open_until: float = field(default=0.0, init=False, repr=False)
    _CIRCUIT_THRESHOLD: int = field(default=3, init=False, repr=False)
    _CIRCUIT_BACKOFF_BASE: int = field(default=30, init=False, repr=False)

    # Heartbeat — periodic health checks
    _LSP_HEARTBEAT_INTERVAL: int = field(default=60, init=False, repr=False)   # seconds between keepalive pings
    _LSP_HEARTBEAT_TIMEOUT: int = field(default=10, init=False, repr=False)   # seconds to wait for a response

    # -- lifecycle -----------------------------------------------------------

    def _build_env(self) -> Dict[str, str]:
        """Build environment variables for the LSP server process."""
        env = {**os.environ}
        if self.language_id == "python":
            env["PYRIGHT_PYTHON_FORCE_VERSION"] = ""
            # Suppress plugin deprecation/indexing warnings that can fill stderr
            # and cause backpressure even with stderr=DEVNULL (some servers write
            # to stderr before the pipe is connected to /dev/null).
            env["PYTHONWARNINGS"] = "ignore"
        # TypeScript: ensure TSServer can resolve types from workspace
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            env["TSS_LOG"] = "-"  # Log to stderr (captured, not lost)
        return env

    def _get_initialization_options(self) -> Dict[str, Any]:
        """Return language-specific initialization options."""
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            return {
                "preferences": {
                    "includeCompletionsForModuleExports": True,
                    "includeCompletionsWithInsertText": True,
                },
                "completionDisableFilterText": True,
                "maxTsServerMemory": 8192,
            }
        if self.language_id == "python":
            # Pyright-specific: enable type resolution for workspace libraries
            return {
                "python": {
                    "analysis": {
                        "autoSearchPaths": True,
                        "useLibraryCodeForTypes": True,
                        "diagnosticMode": "openFilesOnly",
                    }
                }
            }
        return {}

    def _record_lsp_failure(self) -> None:
        """Record a failure and open circuit breaker if threshold exceeded.

        Uses exponential backoff: 30s, 60s, 120s, 240s, ... capped at 600s.
        """
        self._failure_count += 1
        if self._failure_count >= self._CIRCUIT_THRESHOLD:
            backoff = self._CIRCUIT_BACKOFF_BASE * (2 ** (self._failure_count - self._CIRCUIT_THRESHOLD))
            self._circuit_open_until = time.monotonic() + min(backoff, 600)
            logger.warning(
                "LSP circuit breaker opened for %s (%d failures, backoff %.0fs)",
                self.command, self._failure_count, min(backoff, 600),
            )

    def _lsp_circuit_open(self) -> bool:
        """Check if circuit breaker is open (too many recent failures)."""
        if time.monotonic() < self._circuit_open_until:
            return True
        if self._circuit_open_until > 0:
            self._failure_count = 0
            self._circuit_open_until = 0.0
        return False

    def _send_heartbeat(self) -> bool:
        """Send a health-check ping to see if the LSP server is still alive.

        Uses textDocument/documentLink as a lightweight query. If the server
        doesn't respond within _LSP_HEARTBEAT_TIMEOUT seconds, marks it as
        dead so the next request triggers reconnection.
        Returns True if the server responded, False otherwise.
        """
        if not self._alive:
            return False
        try:
            self._send_request(
                "textDocument/documentLink",
                {"textDocument": {"uri": ""}},
                timeout=self._LSP_HEARTBEAT_TIMEOUT,
            )
            self._last_activity = time.monotonic()
            return True
        except Exception:
            self._alive = False
            self._initialized = False
            logger.warning("LSP server %s heartbeat failed — marking as dead", self.command)
            return False

    def ensure_initialized(self) -> bool:
        """Start the server (if needed) and complete the LSP handshake."""
        if self._lsp_circuit_open():
            logger.debug("LSP circuit breaker open for %s, skipping init", self.command)
            return False
        with self._init_lock:
            if self._alive and self._initialized:
                # Periodic heartbeat check
                if time.monotonic() - self._last_heartbeat > self._LSP_HEARTBEAT_INTERVAL:
                    self._last_heartbeat = time.monotonic()
                    if not self._send_heartbeat():
                        self._alive = False
                        self._initialized = False
                        return False
                self._last_activity = time.monotonic()
                return True
            if self._alive:
                self.shutdown()
            success = self._start_and_init()
            if not success:
                self._record_lsp_failure()
            return success

    def _start_and_init(self) -> bool:
        try:
            cmd_path = _resolve_command(self.command)
            if cmd_path is None:
                logger.warning("LSP server not found on PATH: %s", self.command)
                return False

            logger.info("Starting LSP server: %s %s", cmd_path, " ".join(self.args))
            logger.debug("  rootUri: %s", self.root_uri)
            logger.debug("  language_id: %s", self.language_id)
            if self.workspace_folders:
                logger.debug("  workspace_folders (%d): %s",
                    len(self.workspace_folders),
                    self.workspace_folders[:5])
                if len(self.workspace_folders) > 5:
                    logger.debug("    ... and %d more", len(self.workspace_folders) - 5)

            # Set resource limits for the LSP child process
            def _set_limits():
                """Apply memory/cpu limits before exec(). Runs in child process."""
                try:
                    # 4GB virtual memory limit (increased for larger projects like TS)
                    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
                    # RLIMIT_RSS removed — not supported on modern Linux kernels
                    # 60s CPU time before SIGXCPU
                    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
                except (ValueError, resource.error):
                    import sys
                    sys.stderr.buffer.write(b"LSP child: resource limit failed, continuing\n")
                    sys.stderr.buffer.flush()

            self._process = subprocess.Popen(
                [cmd_path] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self.root_uri,
                env=self._build_env(),
                preexec_fn=_set_limits,
            )
            self._alive = True
            self._last_activity = time.monotonic()

            # Poll briefly (0.5s) to detect immediate crashes (bad binary, permission issues)
            for _ in range(10):  # 10 × 0.05s = 0.5s total
                if self._process.poll() is not None:
                    logger.error("LSP server exited during startup (rc=%s)", self._process.returncode)
                    self.shutdown()
                    return False
                time.sleep(0.1)

            # Start background reader
            self._reader_thread = threading.Thread(
                target=self._read_loop, daemon=True, name="lsp-reader"
            )
            self._reader_thread.start()

            # Initialize handshake
            root_uri = f"file://{self.root_uri}"
            init_params: Dict[str, Any] = {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "rootPath": self.root_uri,
                "workspaceFolders": [
                    {"uri": f"file://{f}", "name": Path(f).name}
                    for f in self.workspace_folders
                ] if self.workspace_folders else None,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "hover": {"dynamicRegistration": False, "contentFormat": ["plaintext", "markdown"]},
                        "typeDefinition": {"dynamicRegistration": False},
                        "rename": {"dynamicRegistration": False, "prepareSupport": True},
                        "callHierarchy": {"dynamicRegistration": False},
                        "typeHierarchy": {"dynamicRegistration": False},
                    },
                    "workspace": {
                        "symbol": {"dynamicRegistration": False},
                        "workspaceEdit": {"documentChanges": True},
                    },
                },
            }
            # Add language-specific initialization options (e.g. TS preferences)
            ts_opts = self._get_initialization_options()
            if ts_opts:
                init_params["initializationOptions"] = ts_opts

            init_result = self._send_request(
                "initialize",
                init_params,
                timeout=_LSP_INIT_TIMEOUT,
            )
            if init_result is None:
                logger.error("LSP initialize timed out (server: %s)", self.command)
                self.shutdown()
                return False

            # Send initialized notification
            self._send_notification("initialized", {})
            self._initialized = True
            server_info = init_result.get("serverInfo", {})
            logger.info("LSP server initialized: %s (%s %s) in %.1fs",
                self.command,
                server_info.get("name", "?"),
                server_info.get("version", "?"),
                time.monotonic() - self._last_activity)
            return True

        except Exception as exc:
            logger.error("Failed to start LSP server %s: %s", self.command, exc)
            self.shutdown()
            return False

    def shutdown(self) -> None:
        """Gracefully shut down the server."""
        with self._init_lock:
            if not self._alive:
                return
            self._alive = False
            try:
                if self._initialized and self._process and self._process.stdin:
                    self._send_request("shutdown", None, timeout=5)
                    self._send_notification("exit", None)
            except Exception:
                pass
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
                self._process = None
            self._initialized = False
            # Lock shared state to prevent race with reader thread
            with self._lock:
                self._pending.clear()
                self._responses.clear()
                self._open_documents.clear()
            self._diagnostics_cache.clear()
            try:
                logger.info("LSP server stopped: %s", self.command)
            except ValueError:
                pass  # stderr already closed during Python shutdown

    @property
    def is_alive(self) -> bool:
        if not self._alive or self._process is None:
            return False
        if self._process.poll() is not None:
            self._alive = False
            return False
        # Check idle timeout
        if time.monotonic() - self._last_activity > _LSP_IDLE_TIMEOUT:
            logger.info("LSP server idle timeout, shutting down: %s", self.command)
            self.shutdown()
            return False
        return True

    # -- JSON-RPC -----------------------------------------------------------

    def _send_request(self, method: str, params: Any, timeout: float = _LSP_REQUEST_TIMEOUT) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        with self._lock:
            self._req_id += 1
            req_id = self._req_id
            event = threading.Event()
            self._pending[req_id] = event
        logger.debug("LSP >> %s (id=%d)", method, req_id)
        try:
            self._write_message({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            })
            if event.wait(timeout=timeout):
                with self._lock:
                    resp = self._responses.pop(req_id, None)
                    self._pending.pop(req_id, None)
                # Log response summary — truncate large payloads
                resp_str = json.dumps(resp) if resp else "None"
                if len(resp_str) > 300:
                    resp_str = resp_str[:300] + "..."
                try:
                    logger.debug("LSP << %s (id=%d) %s", method, req_id, resp_str)
                except ValueError:
                    pass  # stderr already closed during Python shutdown
                return resp
            else:
                with self._lock:
                    self._pending.pop(req_id, None)
                    self._responses.pop(req_id, None)
                logger.warning("LSP request timed out: %s (id=%d, timeout=%.1fs)", method, req_id, timeout)
                return None
        except Exception as exc:
            logger.error("LSP request failed: %s (id=%d): %s", method, req_id, exc)
            return None
        finally:
            with self._lock:
                self._pending.pop(req_id, None)
            self._last_activity = time.monotonic()

    def _send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        self._write_message({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        })

    def _write_message(self, msg: dict) -> None:
        """Write a JSON-RPC message in LSP wire format (Content-Length header).

        Serializes and writes atomically under self._lock to prevent
        concurrent threads from interleaving writes to stdin.
        """
        with self._lock:
            if self._process is None or self._process.stdin is None:
                raise RuntimeError("LSP process not running")
            body = json.dumps(msg).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            self._process.stdin.write(header + body)
            self._process.stdin.flush()

    def _read_loop(self) -> None:
        """Background thread: read LSP messages and dispatch to waiters."""
        try:
            buf = b""
            proc = self._process
            if proc is None or proc.stdout is None:
                return  # Cannot read without stdout pipe
            fd = proc.stdout.fileno()
            while self._alive and proc and proc.poll() is None:
                try:
                    # Use os.read() to read available bytes without blocking
                    # (unlike .read(4096) which blocks until 4096 bytes or EOF)
                    sel = DefaultSelector()
                    sel.register(proc.stdout, EVENT_READ)
                    ready = sel.select(timeout=1.0)
                    sel.close()
                    if not ready:
                        continue  # No data yet, check if still alive
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    buf += chunk
                except Exception as exc:
                    logger.debug("read_loop: parse err: %s", exc)
                    break

                # Parse complete messages from buffer
                while True:
                    # Look for header separator
                    sep_idx = buf.find(b"\r\n\r\n")
                    if sep_idx == -1:
                        break

                    header = buf[:sep_idx].decode("ascii", errors="replace")
                    # Extract Content-Length
                    content_length = 0
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":", 1)[1].strip())
                            break

                    body_start = sep_idx + 4
                    body_end = body_start + content_length
                    if len(buf) < body_end:
                        break  # Incomplete message, wait for more data

                    body = buf[body_start:body_end].decode("utf-8", errors="replace")
                    buf = buf[body_end:]

                    try:
                        msg = json.loads(body)
                    except json.JSONDecodeError:
                        continue

                    self._dispatch(msg)
        except Exception as exc:
            logger.debug("read_loop: outer err: %s", exc)
        finally:
            self._alive = False
            # Wake up any pending waiters
            for event in list(self._pending.values()):
                event.set()

    def _dispatch(self, msg: dict) -> None:
        """Dispatch a received JSON-RPC message."""
        with self._lock:
            if "id" in msg and msg["id"] in self._pending:
                self._responses[msg["id"]] = msg.get("result")
                self._pending[msg["id"]].set()
                return
        if "method" in msg:
            method = msg["method"]
            if method == "window/logMessage":
                self._handle_log_message(msg)
            elif method == "textDocument/publishDiagnostics":
                self._handle_publish_diagnostics(msg)
            elif method in ("$/progress", "textDocument/didOpen", "textDocument/didChange",
                          "textDocument/didClose", "textDocument/didSave"):
                pass
            else:
                logger.debug("LSP notification: %s", method)

    def _handle_log_message(self, msg: dict) -> None:
        """Handle a window/logMessage notification from the LSP server."""
        # LSP MessageType: 1=Error, 2=Warning, 3=Info, 4=Log
        # NOTE: tsserver and other servers routinely send informational
        # messages with type=1 (Error). We downgrade 1→INFO to avoid
        # false-positive ERROR log spam.
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        level = params.get("type", 3)
        text = params.get("message", "")
        if self._is_expected_reconcile_close_message(text):
            logger.debug("LSP server reconcile-close noise suppressed: %s", text)
            return
        level_map = {1: logging.INFO, 2: logging.WARNING, 3: logging.INFO, 4: logging.DEBUG}
        logger.log(level_map.get(level, logging.DEBUG), "LSP server: %s", text)

    def _handle_publish_diagnostics(self, msg: dict) -> None:
        """Handle a textDocument/publishDiagnostics notification."""
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        uri = params.get("uri", "")
        if not isinstance(uri, str):
            uri = str(uri) if uri is not None else ""
        diagnostics = params.get("diagnostics", [])
        if not isinstance(diagnostics, list):
            diagnostics = []
        path = LSPBridge._uri_to_path(uri)
        # LRU-evict: cap at 500 entries to prevent unbounded growth
        cache = self._diagnostics_cache
        cache[path] = diagnostics
        cache.move_to_end(path)
        while len(cache) > 500:
            cache.popitem(last=False)
        _log_diagnostics(diagnostics, path)

    def _is_expected_reconcile_close_message(self, text: str) -> bool:
        """Return True for expected server noise from best-effort reconcile closes."""
        if not text:
            return False
        lower_text = text.lower()
        if "close" not in lower_text and "unexpected resource" not in lower_text:
            return False
        now = time.monotonic()
        # Keep the suppression window short and prune old entries so unrelated
        # close/open errors are still logged once reconciliation is over.
        stale_cutoff = now - 5.0
        with self._lock:
            for uri, ts in list(self._reconcile_close_uris.items()):
                if ts < stale_cutoff:
                    self._reconcile_close_uris.pop(uri, None)
        if not self._reconcile_close_uris:
            return False
        if "unexpected resource" in lower_text:
            return any(uri in text for uri in self._reconcile_close_uris)
        if "not open" not in lower_text and "not opened" not in lower_text:
            return False
        # URI-precise matching for "not opened" — if the server reports a
        # corrupted URI (e.g. s4ore instead of store), it won't match any
        # known reconcile URI and will be surfaced as a warning.
        any_match = any(uri in text for uri in self._reconcile_close_uris)
        if not any_match:
            logger.warning(
                "LSP URI mismatch (possible tsserver corruption): %s",
                text,
            )
        return any_match

    # -- LSP operations ------------------------------------------------------

    def open_document(self, file_path: str, content: Optional[str] = None) -> None:
        """Tell the LSP server to open a document. No-op if already open.

        Some LSP servers (notably ``typescript-language-server``) can keep a
        document open internally after a bridge restart while our Python-side
        bookkeeping is empty. In that stale-state case a duplicate ``didOpen``
        fails with "Can't open already open document". Closing first usually
        fixes that, but TypeScript also logs "Unexpected resource" when we
        close a document that was *not* actually open. We suppress the known
        reconciliation noise in the log handler.
        """
        uri = f"file://{file_path}"
        # If the URI is currently being closed (didClose in flight), wait briefly
        # for it to complete before opening. Uses TTL (0.5s) on the timestamp to
        # prevent didClose from killing a freshly opened document.
        for _wait in range(50):  # max ~0.5s spin-wait
            with self._lock:
                ts = self._closing_uris.get(uri)
                if ts is None or (time.monotonic() - ts) > 0.5:
                    self._closing_uris.pop(uri, None)
                    break
            time.sleep(0.01)
        with self._lock:
            if uri in self._open_documents:
                return  # Already open — skip duplicate didOpen
            # Pre-register BEFORE reading file & sending to prevent concurrent
            # threads from racing through and sending duplicate didOpen.
            self._open_documents.add(uri)
            # Track URI for suppression of expected server-side errors.
            # The actual didClose before didOpen is disabled because it
            # triggers tsserver heap corruption under load (not opened errors
            # accumulate in the server's internal document registry and
            # cause URI corruption like s4ore instead of store).
            if uri not in self._reconcile_close_uris:
                self._reconcile_close_uris[uri] = time.monotonic()
                # LRU bound: cap at 1000 to prevent unbounded growth
                while len(self._reconcile_close_uris) > 1000:
                    self._reconcile_close_uris.popitem(last=False)
        if content is None:
            try:
                content = Path(file_path).read_text("utf-8", errors="replace")
            except OSError:
                logger.warning("open_document: failed to read %s", file_path)
                with self._lock:
                    self._open_documents.discard(uri)
                return
        logger.debug("LSP didOpen: %s (%d chars)", file_path, len(content))
        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": self.language_id,
                "version": 1,
                "text": content,
            }
        })

    def close_document(self, file_path: str) -> None:
        """Tell the LSP server to close a document.

        Registers the URI with a timestamp so that ``open_document`` can detect
        an in-flight ``didClose`` via TTL guard (see spin-wait above).
        The notification is sent outside the lock to avoid deadlock with
        ``_write_message`` (which also takes ``self._lock``).
        """
        uri = f"file://{file_path}"
        with self._lock:
            if uri not in self._open_documents:
                return
            self._open_documents.discard(uri)
            self._closing_uris[uri] = time.monotonic()
        self._send_notification("textDocument/didClose", {
            "textDocument": {"uri": uri},
        })
        # No second lock — _closing_uris entry is cleaned by open_document's
        # TTL guard or the next close_document for the same URI.

    def _wait_for_document_ready(self, is_first_request: bool = False) -> None:
        """Wait briefly for the LSP server to process a didOpen notification.

        TS/JS servers need more time for project indexing on first request.
        This is a best-effort delay — the request itself has a timeout.
        Use ``is_first_request=True`` for the very first request to a cold
        bridge; subsequent calls to the same bridge are faster.
        """
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            delay = _LSP_FIRST_REQUEST_DELAY if is_first_request else _LSP_SUBSEQUENT_DELAY
        elif self.language_id in ("python",):
            delay = _LSP_PYTHON_FIRST_DELAY if is_first_request else _LSP_GENERIC_DELAY
        else:
            delay = _LSP_GENERIC_DELAY
        time.sleep(delay)

    def goto_definition(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'textDocument/definition' from the LSP server.

        Args:
            file_path: Absolute path to the file.
            line: 0-based line number.
            character: 0-based character offset.

        Returns:
            List of location dicts, or None on failure.
        """
        if not self.ensure_initialized():
            return None

        # Open the document first (ensure the server has its content)
        self.open_document(file_path)

        self._wait_for_document_ready(is_first_request=True)

        t0 = time.monotonic()
        logger.debug("goto_definition: %s:%d:%s", file_path, line, character)
        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })
        logger.debug("  definition response in %.2fs, raw keys: %s",
            time.monotonic() - t0, list(result.keys()) if isinstance(result, dict) else type(result).__name__)

        normalized = self._normalize_locations(result)
        logger.debug("  normalized: %d locations", len(normalized) if normalized else 0)

        # TS server sometimes returns the import binding itself as definition.
        # Try typeDefinition for a more useful result (jumps to the actual class).
        if (
            normalized
            and self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact")
            and len(normalized) == 1
        ):
            loc = normalized[0]
            # If the definition points to the same file and same line, it might
            # be an import binding — try typeDefinition for the actual type.
            def_path = LSPBridge._uri_to_path(loc.get("uri", ""))
            def_line = loc.get("range", {}).get("start", {}).get("line", -1)
            logger.debug("  import binding check: def_path=%s, def_line=%d, orig_path=%s, orig_line=%d",
                def_path, def_line, file_path, line)
            if def_path == file_path and def_line == line:
                logger.debug("  -> import binding detected, trying typeDefinition...")
                td_result = self._send_request("textDocument/typeDefinition", {
                    "textDocument": {"uri": f"file://{file_path}"},
                    "position": {"line": line, "character": character},
                })
                td_normalized = self._normalize_locations(td_result)
                logger.debug("  typeDefinition: %d locations", len(td_normalized) if td_normalized else 0)
                if td_normalized:
                    # Prefer typeDefinition result (points to actual class/interface)
                    normalized = td_normalized

        # TS server sometimes returns stale/empty results on first request
        # Retry once after a short delay if no locations found
        if not normalized and self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            logger.debug("  definition empty, retrying after 50ms...")
            self._wait_for_document_ready()
            result2 = self._send_request("textDocument/definition", {
                "textDocument": {"uri": f"file://{file_path}"},
                "position": {"line": line, "character": character},
            })
            normalized = self._normalize_locations(result2)
            logger.debug("  retry: %d locations", len(normalized) if normalized else 0)

        logger.debug("goto_definition done: %d locations in %.2fs",
            len(normalized) if normalized else 0, time.monotonic() - t0)
        return normalized

    def find_references(
        self, file_path: str, line: int, character: int, include_declaration: bool = True
    ) -> Optional[List[dict]]:
        """Request 'textDocument/references' from the LSP server.

        Args:
            file_path: Absolute path to the file.
            line: 0-based line number.
            character: 0-based character offset.
            include_declaration: Whether to include the declaration itself.

        Returns:
            List of location dicts (normalized), or None on failure.
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready(is_first_request=True)

        t0 = time.monotonic()
        logger.debug("find_references: %s:%d:%s (includeDeclaration=%s)",
            file_path, line, character, include_declaration)
        result = self._send_request("textDocument/references", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        })
        logger.debug("  references response in %.2fs, raw type: %s",
            time.monotonic() - t0, type(result).__name__)

        normalized = self._normalize_locations(result)
        logger.debug("  normalized: %d locations", len(normalized) if normalized else 0)

        # TS server sometimes returns empty results on first request
        if not normalized and self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            logger.debug("  references empty, retrying after 50ms...")
            self._wait_for_document_ready()
            result2 = self._send_request("textDocument/references", {
                "textDocument": {"uri": f"file://{file_path}"},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            })
            normalized = self._normalize_locations(result2)
            logger.debug("  retry: %d locations", len(normalized) if normalized else 0)

        logger.debug("find_references done: %d locations in %.2fs",
            len(normalized) if normalized else 0, time.monotonic() - t0)
        return normalized

    def document_highlight(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'textDocument/documentHighlight' from the LSP server.

        Returns all occurrences of the symbol at (line, character) in the
        current file, with kind (1=text, 2=read, 3=write).

        Args:
            file_path: Absolute path to the file.
            line: 0-based line number.
            character: 0-based character offset.

        Returns:
            List of highlight dicts with range and kind, or None on failure.
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/documentHighlight", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })

    def inlay_hints(
        self, file_path: str, start_line: int = 1, end_line: int = 0
    ) -> Optional[List[dict]]:
        """Request 'textDocument/inlayHint' from the LSP server.

        Returns inferred type hints for a code range: types for variables,
        parameters, and return values.

        Args:
            file_path: Absolute path to the file.
            start_line: 1-based start line (default: 1).
            end_line: 1-based end line (default: 0 = full file).

        Returns:
            List of inlay hint dicts with position, label, kind, or None on failure.
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        lsp_start = max(0, start_line - 1)
        lsp_end = (end_line - 1) if end_line >= 1 else 9999
        return self._send_request("textDocument/inlayHint", {
            "textDocument": {"uri": f"file://{file_path}"},
            "range": {
                "start": {"line": lsp_start, "character": 0},
                "end": {"line": lsp_end, "character": 0},
            },
        })

    def document_symbols(
        self, file_path: str
    ) -> Optional[List[dict]]:
        """Request 'textDocument/documentSymbol' from the LSP server.

        Returns all symbols (functions, classes, variables, constants, type
        aliases, etc.) in a file as a hierarchical tree. Supplements the
        AST-based code_symbols with LSP-level information including constants,
        type aliases, and proper nesting.

        Args:
            file_path: Absolute path to the file.

        Returns:
            List of DocumentSymbol dicts with name, kind, range,
            selectionRange, children, or None on failure.
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": f"file://{file_path}"},
        })

    def workspace_symbol(self, query: str, anchor_file: Optional[str] = None) -> Optional[List[dict]]:
        """Query workspace/symbol. Returns list of SymbolInformation-like dicts.

        Some LSP servers (notably typescript-language-server) require at least one
        file to be opened before the workspace index is available. If ``anchor_file``
        is provided, it will be opened first to seed the index.
        """
        if not self.ensure_initialized():
            return None
        # TypeScript server needs a didOpen before workspace/symbol returns results
        if anchor_file:
            self.open_document(anchor_file)
            self._wait_for_document_ready(is_first_request=True)
        try:
            result = self._send_request("workspace/symbol", {"query": query})
        except Exception as exc:
            logger.debug("workspace_symbol error: %s", exc)
            return None
        if not result:
            # Retry — TS server might still be indexing on first call
            if anchor_file and self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
                logger.debug("workspace_symbol: empty result, retrying after 1s...")
                time.sleep(1.0)
                try:
                    result = self._send_request("workspace/symbol", {"query": query})
                except Exception as exc:
                    logger.debug("workspace_symbol retry error: %s", exc)
                    return None
            if not result:
                return []
        if isinstance(result, list):
            return result
        return None

    def rename(self, file_path: str, line: int, character: int, new_name: str) -> Optional[dict]:
        """Request textDocument/rename. Returns WorkspaceEdit dict or None."""
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        try:
            result = self._send_request(
                "textDocument/rename",
                {
                    "textDocument": {"uri": f"file://{file_path}"},
                    "position": {"line": line, "character": character},
                    "newName": new_name,
                },
            )
        except Exception as exc:
            logger.debug("rename error: %s", exc)
            return None
        return result if isinstance(result, dict) else None

    def hover(self, file_path: str, line: int, character: int) -> Optional[dict]:
        """Request 'textDocument/hover' from the LSP server."""
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
        self._wait_for_document_ready()

        logger.debug("hover: %s:%d:%s", file_path, line, character)
        result = self._send_request("textDocument/hover", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })

        if result is None:
            return None
        return {
            "contents": result.get("contents", ""),
            "range": result.get("range"),
        }

    def format_document(self, file_path: str) -> Optional[list]:
        """Request 'textDocument/formatting' from the LSP server.

        Returns a list of TextEdit items or None if formatting failed
        or is not supported by the server.
        """
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
        self._wait_for_document_ready()

        logger.debug("format: %s", file_path)
        result = self._send_request("textDocument/formatting", {
            "textDocument": {"uri": f"file://{file_path}"},
            "options": {"tabSize": 4, "insertSpaces": True},
        })

        if not result:
            return None
        return result

    # -- helpers -------------------------------------------------------------

    def type_definition(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'textDocument/typeDefinition' from the LSP server."""
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
        self._wait_for_document_ready()

        result = self._send_request("textDocument/typeDefinition", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })

        return self._normalize_locations(result)

    def implementations(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'textDocument/implementation' from the LSP server.

        Returns locations where the symbol at the given position is implemented.
        Useful for finding concrete implementations of interfaces, abstract classes,
        or method overrides.
        """
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
        self._wait_for_document_ready()

        result = self._send_request("textDocument/implementation", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })

        return self._normalize_locations(result)

    def signature_help(
        self, file_path: str, line: int, character: int
    ) -> Optional[dict]:
        """Request 'textDocument/signatureHelp' — returns signatures + active param."""
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        try:
            result = self._send_request("textDocument/signatureHelp", {
                "textDocument": {"uri": f"file://{file_path}"},
                "position": {"line": line, "character": character},
            })
        except Exception as exc:
            logger.debug("signatureHelp error: %s", exc)
            return None
        return result if isinstance(result, dict) else None

    def code_action(
        self,
        file_path: str,
        line: int,
        character: int,
        end_line: Optional[int] = None,
        end_character: Optional[int] = None,
        only_kinds: Optional[List[str]] = None,
        diagnostics: Optional[List[dict]] = None,
    ) -> Optional[List[dict]]:
        """Request 'textDocument/codeAction' — returns list of actions/commands."""
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        end_l = end_line if end_line is not None else line
        end_c = end_character if end_character is not None else character
        context: dict = {"diagnostics": diagnostics or []}
        if only_kinds:
            context["only"] = only_kinds
        try:
            result = self._send_request("textDocument/codeAction", {
                "textDocument": {"uri": f"file://{file_path}"},
                "range": {
                    "start": {"line": line, "character": character},
                    "end": {"line": end_l, "character": end_c},
                },
                "context": context,
            })
        except Exception as exc:
            logger.debug("codeAction error: %s", exc)
            return None
        if result is None:
            return []
        return result if isinstance(result, list) else []

    def execute_command(self, command: str, arguments: Optional[List] = None) -> Optional[dict]:
        """Send 'workspace/executeCommand' (used to apply a code action's Command)."""
        if not self.ensure_initialized():
            return None
        try:
            return self._send_request("workspace/executeCommand", {
                "command": command,
                "arguments": arguments or [],
            })
        except Exception as exc:
            logger.debug("executeCommand error: %s", exc)
            return None

    def publish_diagnostics(self, file_path: str) -> Optional[List[dict]]:
        """Request 'textDocument/diagnostic' (pull diagnostics) from the LSP server.

        Many LSP servers also push diagnostics via 'textDocument/publishDiagnostics'
        — this method requests them explicitly.

        Returns:
            List of diagnostics dicts with keys: range, severity, code, message, source.
            None on failure.
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        result = self._send_request("textDocument/diagnostic", {
            "textDocument": {"uri": f"file://{file_path}"},
        }, timeout=10)
        if result and isinstance(result, dict) and "items" in result:
            return result["items"]
        # Pull diagnostics not supported — fall back to cached publishDiagnostics
        return None

    def outgoing_calls(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'callHierarchy/outgoingCalls' — functions this symbol calls."""
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        # Prepare call hierarchy item first
        prep = self._send_request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        }, timeout=10)
        if not prep:
            return None
        items = prep if isinstance(prep, list) else [prep]
        if not items:
            return []
        results = []
        for item in items:
            outgoing = self._send_request("callHierarchy/outgoingCalls", {
                "item": item,
            }, timeout=10)
            if isinstance(outgoing, list):
                for o in outgoing:
                    to_call = o.get("to") or o.get("target") or o
                    results.append({
                        "name": to_call.get("name", ""),
                        "kind": to_call.get("kind", 0),
                        "uri": to_call.get("uri", ""),
                        "range": to_call.get("range"),
                        "selectionRange": to_call.get("selectionRange"),
                    })
        return results if results else None

    def incoming_calls(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'callHierarchy/incomingCalls' — functions that call this symbol."""
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        prep = self._send_request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        }, timeout=10)
        if not prep:
            return None
        items = prep if isinstance(prep, list) else [prep]
        if not items:
            return []
        results = []
        for item in items:
            incoming = self._send_request("callHierarchy/incomingCalls", {
                "item": item,
            }, timeout=10)
            if isinstance(incoming, list):
                for inc in incoming:
                    from_call = inc.get("from") or inc.get("origin") or inc
                    results.append({
                        "name": from_call.get("name", ""),
                        "kind": from_call.get("kind", 0),
                        "uri": from_call.get("uri", ""),
                        "range": from_call.get("range"),
                        "selectionRange": from_call.get("selectionRange"),
                    })
        return results if results else None

    def type_supertypes(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'typeHierarchy/supertypes' — parent types of a symbol."""
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        prep = self._send_request("textDocument/prepareTypeHierarchy", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        }, timeout=10)
        if not prep:
            return None
        items = prep if isinstance(prep, list) else [prep]
        if not items:
            return []
        results = []
        for item in items:
            parents = self._send_request("typeHierarchy/supertypes", {
                "item": item,
            }, timeout=10)
            if isinstance(parents, list):
                for p in parents:
                    results.append({
                        "name": p.get("name", ""),
                        "kind": p.get("kind", 0),
                        "uri": p.get("uri", ""),
                        "detail": p.get("detail", ""),
                    })
        return results if results else None

    def type_subtypes(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'typeHierarchy/subtypes' — child types of a symbol."""
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        prep = self._send_request("textDocument/prepareTypeHierarchy", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        }, timeout=10)
        if not prep:
            return None
        items = prep if isinstance(prep, list) else [prep]
        if not items:
            return []
        results = []
        for item in items:
            children = self._send_request("typeHierarchy/subtypes", {
                "item": item,
            }, timeout=10)
            if isinstance(children, list):
                for c in children:
                    results.append({
                        "name": c.get("name", ""),
                        "kind": c.get("kind", 0),
                        "uri": c.get("uri", ""),
                        "detail": c.get("detail", ""),
                    })
        return results if results else None

    def get_cached_diagnostics(self, file_path: str) -> Optional[List[dict]]:
        """Return cached diagnostics that were pushed by textDocument/publishDiagnostics."""
        return self._diagnostics_cache.get(file_path)

    def get_server_info(self) -> dict:
        """Return basic health info about this bridge."""
        return {
            "command": self.command,
            "language_id": self.language_id,
            "root_uri": self.root_uri,
            "alive": self.is_alive if self._alive else False,
            "initialized": self._initialized,
            "workspace_folders": len(self.workspace_folders),
            "last_activity": time.monotonic() - self._last_activity if self._last_activity else None,
            "diagnostic_files": len(self._diagnostics_cache),
        }

    @staticmethod
    def _normalize_locations(result: Any) -> Optional[List[dict]]:
        """Normalize LSP Location/LocationLink results to a uniform list."""
        if result is None:
            return None

        locations: List[dict] = []

        if isinstance(result, dict):
            # Single Location
            if "uri" in result and "range" in result:
                locations.append(result)
            # LocationLink (range + targetUri + targetRange)
            elif "targetUri" in result:
                locations.append({
                    "uri": result["targetUri"],
                    "range": result.get("targetRange", result.get("targetSelectionRange", {})),
                })
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    if "uri" in item and "range" in item:
                        locations.append(item)
                    elif "targetUri" in item:
                        locations.append({
                            "uri": item["targetUri"],
                            "range": item.get("targetRange", item.get("targetSelectionRange", {})),
                        })

        return locations if locations else None

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        """Convert a ``file://`` URI to a local path."""
        if not isinstance(uri, str):
            return ""
        if uri.startswith("file://"):
            return uri[7:]
        return uri

    # ---- New LSP 3.18 methods for code_intel tools ----

    def completion(
        self, file_path: str, line: int, character: int
    ) -> Optional[dict]:
        """Request 'textDocument/completion' from the LSP server.

        Returns completion items at the given position.
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/completion", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
            "context": {"triggerKind": 1},
        })

    def code_lens(self, file_path: str) -> Optional[List[dict]]:
        """Request 'textDocument/codeLens' from the LSP server.

        Returns code lens items (reference counts, test status, etc.).
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/codeLens", {
            "textDocument": {"uri": f"file://{file_path}"},
        })

    def folding_range(self, file_path: str) -> Optional[List[dict]]:
        """Request 'textDocument/foldingRange' from the LSP server.

        Returns foldable regions in the file (imports, comments, regions).
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/foldingRange", {
            "textDocument": {"uri": f"file://{file_path}"},
        })

    def selection_range(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'textDocument/selectionRange' from the LSP server.

        Returns nested selection ranges (smallest → parent → top-level).
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/selectionRange", {
            "textDocument": {"uri": f"file://{file_path}"},
            "positions": [{"line": line, "character": character}],
        })

    def linked_editing(
        self, file_path: str, line: int, character: int
    ) -> Optional[dict]:
        """Request 'textDocument/linkedEditingRange' from the LSP server.

        Returns word range + list of paired editing ranges (e.g. HTML tags).
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/linkedEditingRange", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })

    def prepare_rename(
        self, file_path: str, line: int, character: int
    ) -> Optional[dict]:
        """Request 'textDocument/prepareRename' from the LSP server.

        Returns the range and placeholder text if the symbol is renameable,
        or an error/default range if not.
        """
        if not self.ensure_initialized():
            return None
        self.open_document(file_path)
        self._wait_for_document_ready()
        return self._send_request("textDocument/prepareRename", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })


# ---------------------------------------------------------------------------
# LSP Manager — lazy singleton per workspace
# ---------------------------------------------------------------------------


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
        """Shut down all active bridges."""
        with self._lock:
            for bridge in self._bridges.values():
                bridge.shutdown()
            self._bridges.clear()
            self._workspace_folders_cache.clear()


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