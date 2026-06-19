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

from ._fmt import fmt_ok, fmt_err, fmt_info, fmt_warn, fmt_tree
from ._logging import setup_logger as _setup_lsp_bridge_logger

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
}

# ---------------------------------------------------------------------------
# AST File-Read Cache
# ---------------------------------------------------------------------------

_ast_file_cache: dict = {}  # abs_path -> (lines, timestamp)
_AST_CACHE_TTL = 5
_AST_CACHE_MAX = 10


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
    "next.config.ts": "nextjs",
    "next.config.mjs": "nextjs",
    "next.config.js": "nextjs",
    "medusa-config.ts": "medusa",
    "medusa-config.js": "medusa",
}

# Workspace root cache (TTL 300s, max 100 entries)
_WORKSPACE_ROOT_CACHE: Dict[str, tuple[str, float]] = {}
_WORKSPACE_ROOT_CACHE_TTL = 300.0
_WORKSPACE_ROOT_CACHE_MAX = 100


def _find_workspace_root(file_path: str) -> str:
    """Best-effort workspace root discovery for *file_path*.

    Three-pass strategy:
    1. Look for sub-project markers (next.config.ts, medusa-config.ts,
       or tsconfig.json + package.json indicating a TypeScript sub-project)
    2. Look for generic project markers, but SKIP monorepo roots
       (package.json with ``workspaces`` field) so we keep walking up
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
    _initialized: bool = field(default=False, init=False, repr=False)
    _init_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _diagnostics_cache: OrderedDict = field(default_factory=lambda: OrderedDict(), init=False, repr=False)
    _open_documents: set = field(default_factory=set, init=False, repr=False)  # Track open docs to avoid duplicate didOpen
    _reconcile_close_uris: OrderedDict[str, float] = field(default_factory=OrderedDict, init=False, repr=False)
    # Circuit breaker — prevents repeated attempts after N failures
    _failure_count: int = field(default=0, init=False, repr=False)
    _circuit_open_until: float = field(default=0.0, init=False, repr=False)
    _CIRCUIT_THRESHOLD: int = field(default=3, init=False, repr=False)
    _CIRCUIT_BACKOFF_BASE: int = field(default=30, init=False, repr=False)

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

    def ensure_initialized(self) -> bool:
        """Start the server (if needed) and complete the LSP handshake."""
        if self._lsp_circuit_open():
            logger.debug("LSP circuit breaker open for %s, skipping init", self.command)
            return False
        with self._init_lock:
            if self._alive and self._initialized:
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
            logger.info("LSP server stopped: %s", self.command)

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
                # Log response summary — truncate large payloads
                resp_str = json.dumps(resp) if resp else "None"
                if len(resp_str) > 300:
                    resp_str = resp_str[:300] + "..."
                logger.debug("LSP << %s (id=%d) %s", method, req_id, resp_str)
                return resp
            else:
                logger.warning("LSP request timed out: %s (id=%d, timeout=%.1fs)", method, req_id, timeout)
                with self._lock:
                    self._pending.pop(req_id, None)
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
                    sel.register(self._process.stdout, EVENT_READ)
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

        Uses a second-check pattern: the lock guards the check-and-discard
        atomically, while the notification is sent outside the lock to avoid
        deadlock with _write_message (which also takes self._lock).
        """
        uri = f"file://{file_path}"
        with self._lock:
            if uri not in self._open_documents:
                return
            self._open_documents.discard(uri)
        self._send_notification("textDocument/didClose", {
            "textDocument": {"uri": uri},
        })

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


def code_definition_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Go to definition: find where a symbol is defined.

    Uses LSP (pyright/pylsp) for Python files with automatic fallback
    to AST-based search if the server is unavailable.

    Args:
        path: Absolute file path.
        line: 1-based line number (where the symbol reference is).
        character: 1-based column (optional, will auto-detect the identifier).
        language: Language override (default: auto-detect from extension).

    Returns:
        JSON with definition locations.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_definition_tool: %s:%d:%s lang=%s", path, line, character or "auto", lang)

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            logger.warning("code_definition: no LSP bridge for lang=%s file=%s", lang, path)
        elif not bridge.ensure_initialized():
            logger.warning("code_definition: LSP bridge failed to initialize (server=%s)", bridge.command)
        else:
            logger.debug("code_definition: using LSP bridge: %s (rootUri=%s)", bridge.command, bridge.root_uri)
            locations = bridge.goto_definition(str(target), lsp_line, lsp_char)
            if locations:
                logger.info("code_definition: LSP returned %d locations", len(locations))
                defs = [_location_to_dict(loc) for loc in locations]
                return fmt_ok({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "definition_count": len(defs),
                    "definitions": defs,
                    "formatted": _format_definitions(defs),
                })
            else:
                logger.info("code_definition: LSP returned 0 locations, falling back to AST")

    # Fallback: AST-based definition search
    logger.debug("code_definition: using AST fallback")
    return _ast_fallback_definition(str(target), line, character, lang)


def code_highlight_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Find ALL occurrences of a symbol in the current file (file-local).

    Faster than code_references when you only need file-local matches.
    Returns ranges with kind (1=text, 2=read, 3=write) and surrounding context.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (optional, will auto-detect the identifier).
        language: Language override (default: auto-detect from extension).

    Returns:
        JSON with highlight locations.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_highlight_tool: %s:%d:%s lang=%s", path, line, character, lang)

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            logger.warning("code_highlight: no LSP bridge for lang=%s file=%s", lang, path)
        elif not bridge.ensure_initialized():
            logger.warning("code_highlight: LSP bridge failed to initialize (server=%s)", bridge.command)
        else:
            logger.debug("code_highlight: using LSP bridge: %s (rootUri=%s)", bridge.command, bridge.root_uri)
            highlights = bridge.document_highlight(str(target), lsp_line, lsp_char)
            if highlights:
                logger.info("code_highlight: LSP returned %d highlights", len(highlights))
                # Format highlights with context
                formatted = []
                for h in highlights:
                    rng = h.get("range", {})
                    start = rng.get("start", {})
                    end = rng.get("end", {})
                    hl_line = start.get("line", 0)
                    context_lines = _read_context_lines(str(target), hl_line, context=2)
                    fmt = {
                        "line": hl_line + 1,
                        "start_column": start.get("character", 0) + 1,
                        "end_line": end.get("line", 0) + 1,
                        "end_column": end.get("character", 0) + 1,
                        "kind": h.get("kind", 0),
                        "kind_label": {1: "text", 2: "read", 3: "write"}.get(h.get("kind", 0), "unknown"),
                        "text": context_lines[1].strip()[:200] if len(context_lines) > 1 else "",
                        "context": context_lines,
                    }
                    formatted.append(fmt)

                return fmt_ok({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "highlight_count": len(formatted),
                    "highlights": formatted,
                })

    # No LSP — documentHighlight has no AST fallback (it's LSP-only)
    return fmt_ok({
        "path": str(target),
        "query": {"line": line, "character": character},
        "method": "none",
        "highlight_count": 0,
        "highlights": [],
        "note": "documentHighlight requires LSP — no AST fallback available",
    })


def code_inlay_hints_tool(
    path: str,
    start_line: int = 1,
    end_line: int = 0,
) -> str:
    """Get inferred type hints (inlay hints) for a code range.

    Shows types for variables, parameters, and return values inline.
    Like VSCode's type hints but accessible from the terminal.

    Args:
        path: Absolute file path.
        start_line: 1-based start line (default: 1).
        end_line: 1-based end line (default: 0 = full file).

    Returns:
        JSON with inlay hints.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    logger.info("code_inlay_hints_tool: %s lines=%d-%d lang=%s", path, start_line, end_line or "EOF", lang)

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    hints = bridge.inlay_hints(str(target), start_line=start_line, end_line=end_line)
    if not hints:
        return fmt_ok({
            "path": str(target),
            "range": {"start_line": start_line, "end_line": end_line},
            "hint_count": 0,
            "hints": [],
            "note": "No inlay hints returned (LSP server may not support textDocument/inlayHint)",
        })

    # Format hints
    formatted = []
    for h in hints:
        pos = h.get("position", {})
        label_parts = h.get("label", [])
        # label can be a string or an array of InlayHintLabelPart
        if isinstance(label_parts, list):
            label = "".join(p.get("value", str(p)) for p in label_parts)
        else:
            label = str(label_parts)
        formatted.append({
            "line": pos.get("line", 0) + 1,
            "column": pos.get("character", 0) + 1,
            "label": label[:200],
            "kind": h.get("kind", 0),
            "kind_label": {1: "type", 2: "parameter"}.get(h.get("kind", 0), "unknown"),
        })

    return fmt_ok({
        "path": str(target),
        "range": {"start_line": start_line, "end_line": end_line},
        "method": "lsp",
        "lsp_server": bridge.command,
        "hint_count": len(formatted),
        "hints": formatted,
    })


def code_document_symbols_tool(
    path: str,
    language: Optional[str] = None,
) -> str:
    """Get all symbols in a file via LSP textDocument/documentSymbol.

    Returns functions, classes, variables, constants, type aliases, and
    other symbols with their hierarchy (children nesting). Supplements the
    AST-based code_symbols with LSP-level information including constants,
    type aliases, and proper nesting that pure AST parsing may miss.

    Args:
        path: Absolute file path.
        language: Language override (default: auto-detect from extension).

    Returns:
        JSON with document symbols tree.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    logger.info("code_document_symbols_tool: %s lang=%s", path, lang)

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err("Could not auto-detect language")

    symbols = bridge.document_symbols(str(target))
    if not symbols:
        return fmt_ok({
            "path": str(target),
            "method": "lsp",
            "lsp_server": bridge.command,
            "symbol_count": 0,
            "symbols": [],
            "note": "No document symbols returned (LSP server may not support textDocument/documentSymbol)",
        })

    # Format with kind names for readability
    _SYMBOL_KIND_NAMES = {
        1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
        6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
        11: "interface", 12: "function", 13: "variable", 14: "constant",
        15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
        20: "key", 21: "null", 22: "enumMember", 23: "struct", 24: "event",
        25: "operator", 26: "typeParameter",
    }

    def _format_symbol(sym: dict, depth: int = 0) -> dict:
        """Recursively format a DocumentSymbol with kind name."""
        kind_val = sym.get("kind", 0)
        rng = sym.get("selectionRange", {})
        start = rng.get("start", {}) if rng else {}
        formatted_sym = {
            "name": sym.get("name", ""),
            "kind": kind_val,
            "kind_name": _SYMBOL_KIND_NAMES.get(kind_val, "unknown"),
            "detail": sym.get("detail", ""),
            "line": start.get("line", 0) + 1,
        }
        children = sym.get("children")
        if children:
            formatted_sym["children"] = [
                _format_symbol(c, depth + 1) for c in children
            ]
            formatted_sym["child_count"] = len(children)
        return formatted_sym

    formatted = [_format_symbol(s) for s in symbols]

    return fmt_ok({
        "path": str(target),
        "method": "lsp",
        "lsp_server": bridge.command,
        "symbol_count": len(formatted),
        "symbols": formatted,
    })


def code_references_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
    include_declaration: bool = True,
    group_by_file: bool = False,
) -> str:
    """Find all references to a symbol across the project.

    Uses LSP (pyright/pylsp) for Python files with automatic fallback
    to AST-based search if the server is unavailable.

    Args:
        path: Absolute file path.
        line: 1-based line number (where the symbol is).
        character: 1-based column (optional, will auto-detect the identifier).
        language: Language override (default: auto-detect from extension).
        include_declaration: Include the symbol's own declaration (default: True).
        group_by_file: Return references grouped by file instead of a flat list (default: False).
            Reduces token usage for large codebases.

    Returns:
        JSON with reference locations.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_references_tool: %s:%d:%s lang=%s includeDecl=%s",
        path, line, character, lang, include_declaration)

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            logger.warning("code_references: no LSP bridge for lang=%s file=%s", lang, path)
        elif not bridge.ensure_initialized():
            logger.warning("code_references: LSP bridge failed to initialize (server=%s)", bridge.command)
        else:
            logger.debug("code_references: using LSP bridge: %s (rootUri=%s)", bridge.command, bridge.root_uri)
            locations = bridge.find_references(
                str(target), lsp_line, lsp_char, include_declaration
            )
            if locations:
                logger.info("code_references: LSP returned %d locations", len(locations))
                refs = [_location_to_dict(loc) for loc in locations]
                # Group by file
                by_file: Dict[str, List[dict]] = {}
                for r in refs:
                    by_file.setdefault(r["file"], []).append(r)

                if not group_by_file:
                    return fmt_ok({
                        "path": str(target),
                        "query": {"line": line, "character": character},
                        "method": "lsp",
                        "lsp_server": bridge.command,
                        "reference_count": len(refs),
                        "files_affected": len(by_file),
                        "references": refs,
                        "by_file": by_file,
                        "formatted": _format_references(refs, by_file),
                    })
                # Compact group-by-file mode (token-saving)
                compact_by_file = {
                    f: [{"line": r["line"], "column": r.get("column"), "text": r.get("text", "")[:80]}
                         for r in file_refs]
                    for f, file_refs in sorted(by_file.items())
                }
                return fmt_ok({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "reference_count": len(refs),
                    "files_affected": len(by_file),
                    "by_file": compact_by_file,
                    "formatted": _format_references(refs, by_file),
                })
            else:
                logger.info("code_references: LSP returned 0 locations, falling back to AST")

    # Fallback: AST-based references search
    logger.debug("code_references: using AST fallback")
    return _ast_fallback_references(str(target), line, character, lang)


def code_diagnostics_tool(
    path: str,
    language: Optional[str] = None,
) -> str:
    """Fetch LSP diagnostics (errors, warnings, info) for a file.

    Falls back to lightweight AST heuristic if no LSP server is available.
    """
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("No implementations found at position")

    lang = language or _detect_language_for_lsp(str(target))
    diagnostics: list[dict] = []
    bridge: Optional[Any] = None

    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge and bridge.ensure_initialized():
            # Open the document first so the LSP server sends publishDiagnostics
            bridge.open_document(str(target))
            bridge._wait_for_document_ready(is_first_request=True)

            # Try cached LSP diagnostics (populated by textDocument/publishDiagnostics)
            cached = bridge.get_cached_diagnostics(str(target))
            if cached:
                diagnostics = cached
                logger.info("code_diagnostics: got %d cached diagnostics for %s", len(cached), str(target))

    diagnostics = _pull_lsp_diagnostics(diagnostics, bridge, str(target))

    if diagnostics:
        summary = {
            "path": str(target),
            "method": "lsp",
            "lsp_server": bridge.command if bridge else None,
            "diagnostic_count": len(diagnostics),
            "errors": len([d for d in diagnostics if d.get("severity", 1) == 1]),
            "warnings": len([d for d in diagnostics if d.get("severity", 2) == 2]),
            "diagnostics": diagnostics[:20],  # Cap to avoid token bloat
        }
        return fmt_ok(summary)

    # Fallback: AST heuristic
    logger.debug("code_diagnostics: using AST fallback")
    return _ast_fallback_diagnostics(str(target), lang)



def _pull_lsp_diagnostics(diagnostics: list, bridge, target: str) -> list:
    """Try LSP 3.17+ diagnostic pull, return updated diagnostics list."""
    if diagnostics or not bridge or not bridge.ensure_initialized():
        return diagnostics
    try:
        resp = bridge._send_request("textDocument/diagnostic", {
            "textDocument": {"uri": f"file://{target}"},
            "identifier": "code_intel",
            "previousResultId": None,
        }, timeout=10)
        if resp and "items" in resp:
            diagnostics = resp["items"]
            logger.info("code_diagnostics: LSP pull returned %d items", len(diagnostics))
    except Exception as exc:
        logger.debug("textDocument/diagnostic not supported by %s: %s", bridge.command, exc)
    return diagnostics


def _resolve_target_and_lang(
    path: str, line: int, character: Optional[int] = None, language: Optional[str] = None,
):
    """Resolve path, detect language, auto-detect identifier column.

    Returns ``(target: Path | None, lang: str | None, col_or_error: int | str)``.
    On failure ``target`` is ``None`` and ``col_or_error`` holds the error JSON string.
    """
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return None, None, fmt_err(f"Path not found: {path}")
    lang = language or _detect_language_for_lsp(str(target))
    character_resolved = character
    if character_resolved is None:
        character_resolved = _auto_detect_identifier_column(str(target), line)
    col = character_resolved if character_resolved is not None else 1
    return target, lang, col


def _try_lsp_callers(target, lang, line, col):
    """Try LSP callHierarchy/incomingCalls, return ``(callers, None)`` or ``(None, error_json)``."""
    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None
    if not bridge or not bridge.ensure_initialized():
        return None, None
    try:
        lsp_results = bridge.incoming_calls(str(target), line - 1, col - 1)
        if not lsp_results:
            return None, None
        callers = []
        for item in lsp_results:
            file_path = LSPBridge._uri_to_path(item.get("uri", ""))
            rng = item.get("range", {})
            start = rng.get("start", {}) if isinstance(rng, dict) else {}
            sl = start.get("line", 0) + 1
            callers.append({
                "file": file_path, "line": sl,
                "name": item.get("name", ""), "kind": item.get("kind", 0),
            })
        return callers, None
    except Exception as exc:
        logger.debug("code_callers: LSP callHierarchy failed: %s", exc)
        return None, None


def _fallback_reference_callers(target, line, character, lang):
    """Fallback: use ``code_references_tool`` + heuristic filter to find callers."""
    import json as _json
    refs_json = code_references_tool(
        path=str(target), line=line, character=character,
        language=lang, include_declaration=False, group_by_file=True,
    )
    try:
        refs_data = _json.loads(refs_json)
    except Exception:
        return fmt_err("No implementations found at position")
    if "error" in refs_data:
        return refs_json

    by_file = refs_data.get("by_file", {})
    callers = []
    for file_path, locations in by_file.items():
        try:
            lines_list = _cached_read_lines(file_path)
            for loc in locations:
                ln = loc.get("line", 0)
                if 1 <= ln <= len(lines_list):
                    line_text = lines_list[ln - 1]
                    stripped = line_text.strip()
                    if '(' in stripped or '=' in stripped or 'return' in stripped:
                        callers.append({
                            "file": file_path, "line": ln,
                            "column": loc.get("column"), "text": line_text[:120],
                        })
        except Exception:
            continue
    return callers


def code_callers_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
    group_by_file: bool = False,
) -> str:
    """Find call sites of a symbol (where it is invoked).

    Uses LSP ``callHierarchy/incomingCalls`` when a language server is
    available, falls back to reference-based heuristic filtering.
    """
    import json as _json

    target, lang, col_or_error = _resolve_target_and_lang(path, line, character, language)
    if target is None:
        return str(col_or_error)  # error JSON

    col = int(col_or_error)  # type: ignore[arg-type]

    # ── Try LSP callHierarchy first ──
    callers, _ = _try_lsp_callers(target, lang, line, col)
    if callers is not None:
        result = {
            "path": str(target), "method": "lsp_call_hierarchy",
            "query": {"line": line, "character": col},
            "caller_count": len(callers),
            "files_affected": len({c["file"] for c in callers}),
            "callers": callers,
        }
        if group_by_file:
            result["by_file"] = _group_by_file(callers)
        return fmt_ok(result)

    # ── Fallback: reference-based heuristic ──
    fallback = _fallback_reference_callers(str(target), line, character, lang)
    if isinstance(fallback, str):
        return fallback  # error JSON
    if not fallback:
        return fmt_ok({
            "path": str(target), "query": {"line": line},
            "callers": [],
            "note": "Could not identify call sites via LSP/AST. Use code_references for raw usages.",
        })
    result = {
        "path": str(target), "method": "fallback_heuristic",
        "query": {"line": line, "character": character},
        "caller_count": len(fallback),
        "files_affected": len({c["file"] for c in fallback}),
        "callers": fallback,
    }
    if group_by_file:
        result["by_file"] = _group_by_file(fallback)
    return fmt_ok(result)


def code_callees_tool(
    path: str,
    line: int,
    language: Optional[str] = None,
) -> str:
    """Find symbols CALLED BY a specific function/method.

    Uses LSP ``callHierarchy/outgoingCalls`` when available, falls back
    to AST extraction (call expressions inside the function body).
    """
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))

    # ── Try LSP callHierarchy first ──
    col = _auto_detect_identifier_column(str(target), line) or 1
    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None
    if bridge and bridge.ensure_initialized():
        try:
            lsp_results = bridge.outgoing_calls(str(target), line - 1, col - 1)
            if lsp_results:
                callees = []
                for item in lsp_results:
                    file_path = LSPBridge._uri_to_path(item.get("uri", ""))
                    rng = item.get("range", {})
                    start = rng.get("start", {}) if isinstance(rng, dict) else {}
                    sl = start.get("line", 0) + 1
                    callees.append({
                        "file": file_path,
                        "line": sl,
                        "name": item.get("name", ""),
                        "kind": item.get("kind", 0),
                    })
                return fmt_ok({
                    "path": str(target),
                    "method": "lsp_call_hierarchy",
                    "query": {"line": line, "character": col},
                    "callee_count": len(callees),
                    "callees": callees,
                })
        except Exception as exc:
            logger.debug("code_callees: LSP callHierarchy failed: %s", exc)

    # ── Fallback: AST extraction ──
    return _ast_fallback_callees(str(target), line, lang)


def code_call_hierarchy_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    direction: str = "both",
    max_depth: int = 3,
    max_callers_per_level: int = 20,
    language: Optional[str] = None,
) -> str:
    """Find call hierarchy — incoming calls (who calls this) and outgoing calls (what this calls).

    Uses LSP callHierarchy with configurable transitive depth.
    Returns a formatted tree. Faster than code_callers + code_callees for
    understanding the full call graph.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (auto-detected if omitted).
        direction: "incoming", "outgoing", or "both" (default).
        max_depth: Maximum transitive depth (default: 3, max: 5).
        max_callers_per_level: Max callers shown per level (default: 20).
        language: Language override.

    Returns:
        Formatted tree string.
    """
    import json as _json

    target, lang, col_or_error = _resolve_target_and_lang(path, line, character, language)
    if target is None:
        return str(col_or_error)

    col = int(col_or_error)
    max_depth = min(max_depth, 5)  # hard cap at 5

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None
    if not bridge or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    from pathlib import Path
    seen: set = set()
    warnings: list[str] = []

    def _walk_incoming(file_path: str, ln: int, ch: int, depth: int) -> list[str]:
        """Rekursiv incoming callers mit Tiefensteuerung."""
        if depth <= 0:
            return []
        key = f"{file_path}:{ln}"
        if key in seen:
            return [f"    {'  ' * (max_depth - depth)}↺ {Path(file_path).name}:{ln} (cycle)"]
        seen.add(key)

        lsp_items = bridge.incoming_calls(file_path, ln - 1, ch - 1)
        if not lsp_items:
            return []

        if len(lsp_items) > max_callers_per_level:
            warnings.append(f"Level {max_depth - depth}: >{max_callers_per_level} callers at {Path(file_path).name}:{ln}, truncated")
            lsp_items = lsp_items[:max_callers_per_level]

        lines = []
        for i, item in enumerate(lsp_items):
            caller_file = LSPBridge._uri_to_path(item.get("uri", ""))
            caller_name = item.get("name", "?")
            rng = item.get("range", {})
            start = rng.get("start", {}) if isinstance(rng, dict) else {}
            caller_line = start.get("line", 0)
            connector = "├── " if i < len(lsp_items) - 1 else "└── "
            indent = "    " if i < len(lsp_items) - 1 else "    "
            lines.append(f"{'  ' * depth}{connector}{Path(caller_file).name}:{caller_line + 1} — {caller_name}")
            children = _walk_incoming(caller_file, caller_line + 1, 1, depth - 1)
            for child in children:
                lines.append(f"{'  ' * depth}{indent}{child}")
        return lines

    def _walk_outgoing(file_path: str, ln: int, ch: int, depth: int) -> list[str]:
        """Rekursiv outgoing calls mit Tiefensteuerung."""
        if depth <= 0:
            return []
        key = f"out:{file_path}:{ln}"
        if key in seen:
            return []
        seen.add(key)

        lsp_items = bridge.outgoing_calls(file_path, ln - 1, ch - 1)
        if not lsp_items:
            return []

        if len(lsp_items) > max_callers_per_level:
            warnings.append(f"Level {max_depth - depth}: >{max_callers_per_level} outgoing at {Path(file_path).name}:{ln}, truncated")
            lsp_items = lsp_items[:max_callers_per_level]

        lines = []
        for i, item in enumerate(lsp_items):
            callee_file = LSPBridge._uri_to_path(item.get("uri", ""))
            callee_name = item.get("name", "?")
            rng = item.get("range", {})
            start = rng.get("start", {}) if isinstance(rng, dict) else {}
            callee_line = start.get("line", 0)
            connector = "├── " if i < len(lsp_items) - 1 else "└── "
            indent = "    " if i < len(lsp_items) - 1 else "    "
            lines.append(f"{'  ' * depth}{connector}{Path(callee_file).name}:{callee_line + 1} — {callee_name}")
            children = _walk_outgoing(callee_file, callee_line + 1, 1, depth - 1)
            for child in children:
                lines.append(f"{'  ' * depth}{indent}{child}")
        return lines

    result_lines = []
    sym_name = Path(str(target)).name

    if direction in ("incoming", "both"):
        result_lines.append(f"Incoming Calls ({sym_name}:{line}):")
        incoming = _walk_incoming(str(target), line - 1, col - 1, max_depth)
        if incoming:
            result_lines.extend(incoming)
        else:
            result_lines.append("  (none)")

    if direction == "both":
        result_lines.append("")

    if direction in ("outgoing", "both"):
        result_lines.append(f"Outgoing Calls ({sym_name}:{line}):")
        outgoing = _walk_outgoing(str(target), line - 1, col - 1, max_depth)
        if outgoing:
            result_lines.extend(outgoing)
        else:
            result_lines.append("  (none)")

    if warnings:
        result_lines.append("")
        for w in warnings:
            result_lines.append(f"⚠️ {w}")

    return "\n".join(result_lines)


def code_type_hierarchy_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    direction: str = "both",
    language: Optional[str] = None,
) -> str:
    """Find type hierarchy — supertypes (parent types) and subtypes (child types).

    Uses LSP typeHierarchy when the server supports it (Java, C#, Swift).
    Falls back to AST-based analysis for Python/TypeScript.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (auto-detected if omitted).
        direction: "supertypes", "subtypes", or "both" (default).
        language: Language override.

    Returns:
        Formatted tree string.
    """
    import json as _json
    from pathlib import Path

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Path not found: {path}")

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None

    # LSP Server die TypeHierarchy unterstützen
    _LANG_SUPPORTS_LSP_TYPE_HIERARCHY = {"java", "csharp", "swift"}

    col = character
    if col is None:
        col = _auto_detect_identifier_column(str(target), line - 1) or 1

    result_lines = []
    warnings = []

    supers = None
    subs = None

    # LSP-Versuch (nur für Sprachen die TypeHierarchy unterstützen)
    if bridge and bridge.ensure_initialized() and lang in _LANG_SUPPORTS_LSP_TYPE_HIERARCHY:
        try:
            supers_lsp = bridge.type_supertypes(str(target), line - 1, col - 1)
            subs_lsp = bridge.type_subtypes(str(target), line - 1, col - 1)
            supers = supers_lsp
            subs = subs_lsp
            if supers or subs:
                warnings.append("via LSP TypeHierarchy")
        except Exception:
            pass

    # AST-Fallback (Python/TypeScript)
    if supers is None and subs is None:
        try:
            from .code_intel import _ast_type_hierarchy_supertypes, _ast_type_hierarchy_subtypes
            supers = _ast_type_hierarchy_supertypes(str(target), line)
            subs = _ast_type_hierarchy_subtypes(str(target), line)
            if supers or subs:
                warnings.append("via AST analysis (LSP typeHierarchy not available for this language)")
        except Exception:
            pass

    # Output
    if direction in ("supertypes", "both"):
        result_lines.append(f"Supertypes ({Path(target).name}:{line}):")
        if supers:
            for s in supers:
                result_lines.append(f"  ├── {s.get('name', '?')} ({s.get('kind', '?')}) — line {s.get('line', '?')}")
        else:
            result_lines.append("  (none)")

    if direction == "both":
        result_lines.append("")

    if direction in ("subtypes", "both"):
        result_lines.append(f"Subtypes ({Path(target).name}:{line}):")
        if subs:
            for s in subs:
                result_lines.append(f"  ├── {s.get('name', '?')} ({s.get('kind', '?')}) — line {s.get('line', '?')}")
        else:
            result_lines.append("  (none)")

    if warnings:
        result_lines.append("")
        for w in warnings:
            result_lines.append(f"ℹ️ {w}")

    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# AST-based fallback
# ---------------------------------------------------------------------------


def _auto_detect_paren_column(file_path: str, lsp_line: int) -> int:
    """Auto-detect column to land cursor inside the first '(' on the given line."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        src_line = lines[lsp_line] if 0 <= lsp_line < len(lines) else ""
    except Exception:
        src_line = ""
    idx = src_line.find("(")
    return (idx + 2) if idx >= 0 else 1


def _auto_detect_identifier_column(file_path: str, line: int) -> Optional[int]:
    """Find the column of the first meaningful identifier on *line* (0-based).

    Skips common language keywords (import, export, from, const, etc.) to land
    on actual symbol names like ``createLogger`` or ``PropertyService``.
    """
    _KEYWORDS = frozenset({
        "import", "export", "from", "const", "let", "var", "class", "function",
        "return", "async", "await", "type", "interface", "if", "else", "for",
        "while", "new", "throw", "try", "catch", "finally", "switch", "case",
        "break", "continue", "default", "extends", "implements", "super",
        "this", "static", "public", "private", "protected", "readonly",
        "declare", "enum", "namespace", "module", "require", "as",
        "void", "null", "undefined", "true", "false", "of", "in",
    })

    try:
        lines = _cached_read_lines(file_path)
        if line < 0 or line >= len(lines):
            return None
        text = lines[line]
        # Extract word-like tokens and skip keywords
        i = 0
        while i < len(text):
            ch = text[i]
            if ch.isalpha() or ch == '_':
                # Found start of a word
                start = i
                while i < len(text) and (text[i].isalnum() or text[i] == '_'):
                    i += 1
                word = text[start:i]
                if word not in _KEYWORDS:
                    return start + 1  # 1-based
                # else: skip this keyword, continue scanning
            elif ch in ('"', "'", '`'):
                # Skip string literals
                quote = ch
                i += 1
                while i < len(text) and text[i] != quote:
                    if text[i] == '\\':
                        i += 1
                    i += 1
                i += 1  # skip closing quote
            else:
                i += 1
    except OSError:
        pass
    return None


def _ast_fallback_definition(
    file_path: str, line: int, character: Optional[int], lang: Optional[str]
) -> str:
    """Fallback: use tree-sitter AST to find a definition."""
    import json as _json

    _detect = _import_detect_language()
    if _detect is None:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    # Read the identifier at the cursor position
    identifier = _extract_identifier(file_path, line, character)
    if not identifier:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line, "character": character},
            "method": "fallback",
            "warning": "Could not extract an identifier at the given position.",
            "suggestion": "Ensure line and character point to a valid identifier.",
        })

    # Search for the definition in the file tree
    root = _find_workspace_root(file_path)
    from .code_intel import code_search_tool  # late import: avoids circular import at module load
    result_str = code_search_tool(
        path=root,
        query="(function_definition name: (identifier) @name) @def\n(class_definition name: (identifier) @name) @def",
        pattern=identifier,
        language=detected,
        max_results=20,
    )

    try:
        result = _json.loads(result_str)
    except _json.JSONDecodeError:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "raw_search_result": result_str,
        })

    defs = []
    for r in result.get("results", []):
        defs.append({
            "file": r.get("file", file_path),
            "line": r.get("line"),
            "kind": r.get("kind", "unknown"),
            "text": r.get("text", ""),
        })

    return fmt_ok({
        "path": file_path,
        "query": {"line": line, "character": character, "identifier": identifier},
        "method": "fallback_ast",
        "warning": "LSP server unavailable, using AST-based search. Results may be incomplete.",
        "definition_count": len(defs),
        "definitions": defs,
    })


def _import_detect_language():
    """4-stufiger Import-Fallback für detect_language aus code_intel.py."""
    try:
        from .code_intel import detect_language as _detect
        return _detect
    except ImportError:
        pass
    try:
        from tools.code_intel import detect_language as _detect
        return _detect
    except ImportError:
        pass
    try:
        from hermes_plugins.code_intel.code_intel import detect_language as _detect
        return _detect
    except ImportError:
        pass
    try:
        import importlib.util as _ilu
        _mod_path = str(Path(__file__).parent / "code_intel.py")
        _spec = _ilu.spec_from_file_location("code_intel_standalone", _mod_path)
        if _spec is None or _spec.loader is None:
            return None
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.detect_language
    except Exception:
        pass
    return None


def _extract_identifier(file_path: str, line: int, character: Optional[int]) -> str:
    """Extrahiere Identifier aus einer bestimmten Zeile/Spalte."""
    try:
        lines = _cached_read_lines(file_path)
        text_line = lines[line - 1] if 0 < line <= len(lines) else ""
    except (OSError, IndexError):
        text_line = ""
    if not character or not text_line or character > len(text_line):
        return ""
    idx = character - 1
    start = idx
    while start > 0 and (text_line[start - 1].isalnum() or text_line[start - 1] == '_'):
        start -= 1
    end = idx
    while end < len(text_line) and (text_line[end].isalnum() or text_line[end] == '_'):
        end += 1
    return text_line[start:end]


def _rg_search(identifier: str, root: str) -> list:
    """Führe ripgrep-Suche aus und parse Ergebnisse."""
    import subprocess as _sp
    try:
        result = _sp.run(
            ["rg", "--no-heading", "--line-number", "-n", "-w", identifier, root],
            capture_output=True, text=True, timeout=15,
        )
        refs = []
        for match_line in result.stdout.strip().split("\n"):
            if not match_line:
                continue
            parts = match_line.split(":", 2)
            if len(parts) >= 3:
                refs.append({
                    "file": parts[0],
                    "line": int(parts[1]),
                    "text": parts[2].strip()[:200],
                })
        return refs
    except Exception:
        return []


def _ast_fallback_references(
    file_path: str, line: int, character: Optional[int], lang: Optional[str]
) -> str:
    """Fallback: use grep-style search for references."""
    import json as _json

    _detect = _import_detect_language()
    if _detect is None:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    identifier = _extract_identifier(file_path, line, character)
    if not identifier:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line, "character": character},
            "method": "fallback",
            "warning": "Could not extract an identifier at the given position.",
        })

    root = _find_workspace_root(file_path)
    refs = _rg_search(identifier, root)

    by_file: Dict[str, List[dict]] = {}
    for r in refs:
        by_file.setdefault(r["file"], []).append(r)

    return fmt_ok({
        "path": file_path,
        "query": {"line": line, "character": character, "identifier": identifier},
        "method": "fallback_text",
        "warning": "LSP server unavailable, using text-based search. May include false positives.",
        "reference_count": len(refs),
        "files_affected": len(by_file),
        "references": refs,
        "by_file": by_file,
    })


def _read_file_safe(file_path: str):
    """Read file content, returning ``(content, None)`` or ``(None, error_json)``."""
    import json as _json
    try:
        content = Path(file_path).read_text("utf-8", errors="replace")
        return content, None
    except Exception as exc:
        return None, _json.dumps({
            "path": file_path, "method": "fallback", "warning": str(exc),
        })


def _python_ast_analyze(content: str):
    """Walk Python AST, collect imported/used/defined names.

    Returns ``(imported, used, defined)`` sets, or ``None`` on syntax error.
    """
    import ast
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    except Exception:
        return None
    imported: set[str] = set()
    used: set[str] = set()
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                used.add(node.id)
    return imported, used, defined


def _build_unused_import_diags(
    imported: set, used: set, defined: set, content: str,
) -> list[dict]:
    """Build diagnostics for imports that are neither used nor re-defined."""
    diagnostics: list[dict] = []
    for name in sorted(imported - used - defined):
        for i, line_text in enumerate(content.split("\n"), 1):
            if name in line_text and ("import" in line_text or "from " in line_text):
                diagnostics.append({
                    "severity": 2,
                    "message": f"Possibly unused import: {name}",
                    "range": {"start": {"line": i - 1, "character": 0},
                              "end":   {"line": i - 1, "character": len(line_text)}},
                    "source": "ast_heuristic",
                })
                break
    return diagnostics


def _tsjs_import_heuristic(content: str) -> list[dict]:
    """Token-based import-unused heuristic for TypeScript / JavaScript."""
    diagnostics: list[dict] = []
    lines = content.split("\n")
    for i, line_text in enumerate(lines, 1):
        stripped = line_text.strip()
        if stripped.startswith("import ") and "from " in stripped:
            imp = stripped.split("from")[0].split("{")[-1].split("}")[0]
            imp = imp.replace("import ", "").replace("* as ", "").strip()
            if imp and not any(imp in ln for ln in lines[i:]):
                diagnostics.append({
                    "severity": 2,
                    "message": f"Possibly unused import: {imp}",
                    "range": {"start": {"line": i - 1, "character": 0},
                              "end":   {"line": i - 1, "character": len(line_text)}},
                    "source": "ast_heuristic",
                })
    return diagnostics


def _format_diagnostics_result(file_path: str, diagnostics: list[dict]) -> str:
    """Build the final JSON string for a diagnostics response."""
    import json as _json
    return fmt_ok({
        "path": file_path,
        "method": "ast_heuristic",
        "warning": "LSP server unavailable. Using lightweight AST heuristic.",
        "diagnostic_count": len(diagnostics),
        "errors": len([d for d in diagnostics if d.get("severity", 1) == 1]),
        "warnings": len([d for d in diagnostics if d.get("severity", 2) == 2]),
        "diagnostics": diagnostics,
    })


def _ast_fallback_diagnostics(file_path: str, lang: Optional[str]) -> str:
    """Lightweight AST-based heuristic for common issues: unused imports, undefined names."""
    content, error = _read_file_safe(file_path)
    if error:
        return error
    assert content is not None  # help pyright narrow the type
    diagnostics: list[dict] = []
    if lang == "python":
        result = _python_ast_analyze(content)
        if result is not None:
            imported, used, defined = result
            diagnostics = _build_unused_import_diags(imported, used, defined, content)
        else:
            try:
                import ast as _ast_mod
                _ast_mod.parse(content)  # raises SyntaxError
            except SyntaxError as exc:
                diagnostics.append({
                    "severity": 1,
                    "message": f"Syntax error: {exc.msg}",
                    "range": {"start": {"line": (exc.lineno or 1) - 1, "character": 0},
                              "end":   {"line": (exc.lineno or 1) - 1, "character": 0}},
                    "source": "ast_heuristic",
                })
            except Exception:
                pass
    elif lang in ("typescript", "javascript"):
        diagnostics = _tsjs_import_heuristic(content)
    return _format_diagnostics_result(file_path, diagnostics)


def _ast_fallback_callees(file_path: str, line: int, lang: Optional[str]) -> str:
    """AST fallback: extract call expressions from the function/method at *line*."""
    import json as _json
    content, error = _read_file_safe(file_path)
    if error:
        return error
    assert content is not None

    callees: list[dict] = []

    if lang == "python":
        callees = _extract_python_callees(content, line)
    elif lang in ("typescript", "javascript"):
        callees = _extract_ts_callees(content, line)

    if not callees:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line},
            "method": "ast_heuristic",
            "warning": "Could not extract callees via AST. Ensure line points to a function/method.",
            "callees": [],
        })

    return fmt_ok({
        "path": file_path,
        "query": {"line": line},
        "method": "ast_heuristic",
        "callee_count": len(callees),
        "callees": callees,
    })



def _extract_python_callees(content: str, line: int) -> list:
    """Extract function calls from a Python function/method at given line."""
    import ast as _ast
    callees = []
    try:
        tree = _ast.parse(content)
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                func_start = getattr(node, "lineno", 1)
                func_end = getattr(node, "end_lineno", func_start)
                if func_start <= line <= func_end:
                    for child in _ast.walk(node):
                        if isinstance(child, _ast.Call):
                            name = ""
                            if isinstance(child.func, _ast.Name):
                                name = child.func.id
                            elif isinstance(child.func, _ast.Attribute):
                                name = child.func.attr
                            if name:
                                callees.append({
                                    "name": name,
                                    "line": getattr(child, "lineno", func_start),
                                    "type": "call",
                                })
                    break
    except SyntaxError:
        pass
    except Exception:
        pass
    return callees


def _extract_ts_callees(content: str, line: int) -> list:
    """Extract function calls from a TypeScript/JS function region."""
    import re as _re
    callees = []
    lines = content.split("\n")
    if 0 < line <= len(lines):
        for i in range(line - 1, min(len(lines), line + 200)):
            ln = lines[i]
            for mtch in _re.finditer(r'([A-Za-z_]\w*)\s*\(', ln):
                cname = mtch.group(1)
                if cname not in {"if", "while", "for", "switch", "catch", "function", "return", "new"}:
                    callees.append({
                        "name": cname,
                        "line": i + 1,
                        "type": "call",
                    })
    return callees


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_definitions(defs: List[dict]) -> str:
    """Format definition results for display."""
    if not defs:
        return "No definition found."

    lines = []
    for i, d in enumerate(defs, 1):
        if not isinstance(d, dict):
            lines.append(f"{i}. <malformed entry>")
            continue
        file_path = d.get("file", d.get("path", "<unknown>"))
        line_no = d.get("line", d.get("row", 0))
        lines.append(f"{i}. {file_path}:{line_no}")
        if d.get("text"):
            lines.append(f"   {d['text']}")
        if d.get("context"):
            for ctx_line in d["context"]:
                if ctx_line.strip():
                    lines.append(f"   {ctx_line}")
    return "\n".join(lines)


def _format_references(refs: List[dict], by_file: Dict[str, List[dict]]) -> str:
    """Format references results for display."""
    if not refs:
        return "No references found."

    lines = [f"Found {len(refs)} references across {len(by_file)} file(s):"]

    for file_path, file_refs in sorted(by_file.items()):
        # Shorten path if it's within the workspace
        short = file_path
        lines.append(f"\n  {short} ({len(file_refs)} ref(s))")
        for r in file_refs:
            text = r.get("text", "") if isinstance(r, dict) else str(r)[:120]
            if not isinstance(r, dict):
                lines.append("    <malformed ref>")
                continue
            line_no = r.get("line", r.get("row", 0))
            if len(text) > 120:
                text = text[:117] + "..."
            lines.append(f"    L{line_no:>4d}  {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool schemas & registration
# ---------------------------------------------------------------------------

CODE_HIGHLIGHT_SCHEMA = {
    "name": "code_highlight",
    "description": "Find ALL occurrences of a symbol in the current file (file-local). "
                   "Faster than code_references when you only need file-local matches. "
                   "Returns ranges with kind (1=text, 2=read, 3=write) and surrounding context.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
        },
        "required": ["path", "line"],
    },
}

CODE_INLAY_HINTS_SCHEMA = {
    "name": "code_inlay_hints",
    "description": "Get inferred type hints (inlay hints) for a code range. "
                   "Shows types for variables, parameters, and return values inline. "
                   "Like VSCode's type hints but accessible from the terminal.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "start_line": {"type": "integer", "description": "1-based start line (default: 1)"},
            "end_line": {"type": "integer", "description": "1-based end line (default: 0 = full file)"},
        },
        "required": ["path"],
    },
}

CODE_TYPE_HIERARCHY_SCHEMA = {
    "name": "code_type_hierarchy",
    "description": "Find type hierarchy for a symbol — supertypes (parent types) and subtypes "
                   "(child types). Uses LSP typeHierarchy when available (Java, C#, Swift), "
                   "falls back to AST-based analysis for Python/TypeScript.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
            "direction": {"type": "string", "enum": ["supertypes", "subtypes", "both"], "description": "Direction of hierarchy (default: both)"},
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path", "line"],
    },
}

CODE_CALL_HIERARCHY_SCHEMA = {
    "name": "code_call_hierarchy",
    "description": "Find call hierarchy for a symbol — incoming calls (who calls this) and outgoing calls "
                   "(what does this call). Uses LSP callHierarchy with configurable transitive depth. "
                   "Returns a formatted tree.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
            "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "description": "Direction of hierarchy (default: both)"},
            "max_depth": {"type": "integer", "description": "Maximum transitive depth (default: 3, max: 5)"},
            "max_callers_per_level": {"type": "integer", "description": "Max callers shown per level (default: 20)"},
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path", "line"],
    },
}

CODE_DOCUMENT_SYMBOLS_SCHEMA = {
    "name": "code_document_symbols",
    "description": "Get ALL symbols in a file via LSP textDocument/documentSymbol — functions, classes, variables, "
                   "constants, type aliases, and nested hierarchy. Supplements the AST-based code_symbols with "
                   "LSP-level information including constants, type aliases, and proper nesting.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Language override (auto-detected from extension)"},
        },
        "required": ["path"],
    },
}

CODE_DEFINITION_SCHEMA = {
    "name": "code_definition",
    "description": (
        "Navigate to the original declaration/definition of a symbol using LSP. "
        "Tells you WHERE a function, class, variable, or type is defined. "
        "Requires a file path and the line where the symbol reference appears. "
        "Uses pyright/pylsp for Python, typescript-language-server for TS/JS (cross-file resolution). "
        "Falls back to AST-based search if LSP is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path containing the symbol reference"},
            "line": {"type": "integer", "description": "1-based line number where the symbol appears"},
            "character": {"type": "integer", "description": "1-based column position of the symbol (optional, auto-detected)"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
        },
        "required": ["path", "line"],
    },
}

CODE_REFERENCES_SCHEMA = {
    "name": "code_references",
    "description": (
        "Find ALL project-wide usages/references of a symbol using LSP. "
        "Shows every file and line where a function, class, variable, or type is used. "
        "Requires a file path and the line where the symbol is defined or referenced. "
        "Uses pyright/pylsp for Python, typescript-language-server for TS/JS (cross-file resolution). "
        "Falls back to text-based search if LSP is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number where the symbol appears"},
            "character": {"type": "integer", "description": "1-based column position of the symbol (optional, auto-detected)"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
            "include_declaration": {"type": "boolean", "description": "Include the symbol's own declaration in results (default: True)"},
            "group_by_file": {"type": "boolean", "description": "Group references by file and truncate line text to save tokens (default: False)"},
        },
        "required": ["path", "line"],
    },
}

CODE_DIAGNOSTICS_SCHEMA = {
    "name": "code_diagnostics",
    "description": (
        "Fetch LSP diagnostics (errors, warnings, info) for a source file. "
        "Falls back to a lightweight AST lint heuristic if no LSP server is active."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
        },
        "required": ["path"],
    },
}

CODE_CALLERS_SCHEMA = {
    "name": "code_callers",
    "description": (
        "Find call sites of a symbol — files and lines WHERE it is invoked. "
        "Requires a file path and line where the callee is defined. Uses LSP references with heuristics."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number where the callee is defined"},
            "character": {"type": "integer", "description": "1-based column position (optional, auto-detected)"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
            "group_by_file": {"type": "boolean", "description": "Group results by file to save tokens (default: False)"},
        },
        "required": ["path", "line"],
    },
}

CODE_CALLEES_SCHEMA = {
    "name": "code_callees",
    "description": (
        "Find symbols CALLED BY a specific function or method. "
        "Requires a file path and the line where the function is defined. "
        "Uses AST-based extraction for Python/TS/JS; LSP fallback if available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number where the function is defined"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_highlight(args, **kw):
    return code_highlight_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_inlay_hints(args, **kw):
    return code_inlay_hints_tool(
        path=args.get("path", ""),
        start_line=args.get("start_line", 1),
        end_line=args.get("end_line", 0),
    )


def _handle_code_type_hierarchy(args, **kw):
    return code_type_hierarchy_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        direction=args.get("direction", "both"),
        language=args.get("language"),
    )


def _handle_code_call_hierarchy(args, **kw):
    return code_call_hierarchy_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        direction=args.get("direction", "both"),
        max_depth=args.get("max_depth", 3),
        max_callers_per_level=args.get("max_callers_per_level", 20),
        language=args.get("language"),
    )


def _handle_code_document_symbols(args, **kw):
    return code_document_symbols_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_definition(args, **kw):
    return code_definition_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_references(args, **kw):
    return code_references_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
        include_declaration=args.get("include_declaration", True),
        group_by_file=args.get("group_by_file", False),
    )


def _handle_code_diagnostics(args, **kw):
    return code_diagnostics_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_callers(args, **kw):
    return code_callers_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
        group_by_file=args.get("group_by_file", False),
    )


def _handle_code_callees(args, **kw):
    return code_callees_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# code_workspace_symbols — LSP workspace/symbol (monorepo-wide symbol search)
# ---------------------------------------------------------------------------


def _wss_find_anchor_file(anchor: Path) -> Path:
    """Wenn anchor ein Dir ist, finde eine passende Source-Datei für LSP-Seeding.

    Bevorzugt bekannte Projektverzeichnisse (packages, apps, src, lib, app)
    mit gängigen Source-Extensions.
    """
    if not anchor.is_dir():
        return anchor
    _PREFERRED_ANCHOR_DIRS = ("packages", "apps", "src", "lib", "app")
    _SMART_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs")
    hit = None
    for pref_dir in _PREFERRED_ANCHOR_DIRS:
        candidate = anchor / pref_dir
        if candidate.is_dir():
            for ext in _SMART_EXTENSIONS:
                hit = next(candidate.rglob(f"*{ext}"), None)
                if hit:
                    break
        if hit:
            break
    if not hit:
        for ext in _SMART_EXTENSIONS:
            hit = next(anchor.rglob(f"*{ext}"), None)
            if hit:
                break
    return hit if hit else anchor


_LSP_KIND_NAMES = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
    25: "operator", 26: "type_parameter",
}


def _wss_format_symbol_results(raw: list, kind: Optional[str], max_results: int) -> tuple:
    """Formatiere raw LSP workspace/symbol Response in Hermes-Dicts.

    Returns (symbols, truncated).
    """
    _KIND_NAMES = _LSP_KIND_NAMES
    symbols: List[dict] = []
    for sym in raw:
        loc = sym.get("location") or {}
        uri = loc.get("uri", "")
        file_path = uri[7:] if uri.startswith("file://") else uri
        rng = loc.get("range") or {}
        start = rng.get("start") or {}
        kind_num = sym.get("kind", 0)
        kind_name = _KIND_NAMES.get(kind_num, f"kind_{kind_num}")

        if kind and kind.lower() != kind_name:
            continue

        symbols.append({
            "name": sym.get("name", ""),
            "kind": kind_name,
            "container": sym.get("containerName") or "",
            "file": file_path,
            "line": start.get("line", 0) + 1 if start else None,
            "character": start.get("character", 0) + 1 if start else None,
        })

    truncated = len(symbols) > max_results
    symbols = symbols[:max_results]
    return symbols, truncated


def code_workspace_symbols_tool(
    query: str,
    path: Optional[str] = None,
    language: Optional[str] = None,
    kind: Optional[str] = None,
    max_results: int = 50,
) -> str:
    """Search symbols across the workspace using LSP workspace/symbol.

    Much faster than search_files for finding classes/functions/interfaces by name
    in large projects — returns only real symbols (not comments/strings) with
    their kind (class, function, interface, etc.) pre-indexed by the LSP server.

    Note for monorepos: The LSP server indexes symbols based on open documents.
    For best results, pass a specific source file as ``path`` (not a directory).
    When a directory is given, the tool picks an anchor file from packages/ or apps/.
    If results are empty, the LSP server may not have indexed that part of the monorepo
    — use code_search (AST-based) as an alternative that works without LSP indexing.

    Args:
        query: Fuzzy symbol name (e.g. 'UserService', 'createLogger').
        path: Optional file in the workspace to anchor the LSP root detection.
            For monorepos, prefer passing a specific source file for best results.
            Defaults to cwd.
        language: Language override ('typescript', 'python', etc.). Auto-detected
            from ``path`` if provided.
        kind: Optional filter: class, function, method, interface, enum, variable,
            constant, module, struct.
        limit: Max results to return (default 50).

    Returns:
        JSON string with matched symbols (name, kind, file, line, container).
    """
    import json as _json

    anchor = Path(path).expanduser().resolve() if path else Path.cwd().resolve()
    if not anchor.exists():
        return fmt_err("No type definition found at position")

    probe_file = _wss_find_anchor_file(anchor)

    lang = language or _detect_language_for_lsp(str(probe_file))
    if not lang:
        return fmt_ok({
            "error": "Could not auto-detect language. Pass language= explicitly.",
            "query": query,
        })

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(probe_file))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "query": query,
            "hint": "Use search_files (target='content') as fallback",
        })

    logger.info("code_workspace_symbols: query=%r lang=%s root=%s",
                query, lang, bridge.root_uri)
    raw = bridge.workspace_symbol(query, anchor_file=str(probe_file))
    if raw is None:
        return fmt_ok({
            "error": "LSP workspace/symbol request failed or not supported",
            "query": query,
            "lsp_server": bridge.command,
        })

    symbols, truncated = _wss_format_symbol_results(raw, kind, max_results)

    return fmt_ok({
        "query": query,
        "language": lang,
        "lsp_server": bridge.command,
        "total_returned": len(symbols),
        "truncated": truncated,
        "symbols": symbols,
    })


CODE_WORKSPACE_SYMBOLS_SCHEMA = {
    "name": "code_workspace_symbols",
    "description": (
        "Fuzzy search symbols (classes, functions, interfaces, etc.) across the entire "
        "workspace via LSP workspace/symbol. Sub-second monorepo-wide lookup that returns "
        "ONLY real symbols (not comments or string matches) with their kind and location. "
        "Use this INSTEAD of search_files when looking for a named entity like 'UserService' "
        "or 'createLogger' across many apps — it is faster, semantic, and avoids false positives."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Fuzzy symbol name to search for."},
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "language": {"type": "string", "description": "Language override: typescript, python, go, rust, etc."},
            "kind": {"type": "string", "description": "Filter by symbol kind: class, function, method, interface, enum, variable, constant, module, struct."},
            "limit": {"type": "integer", "description": "Max results (default 50)."},
        },
        "required": ["query"],
    },
}


def _handle_code_workspace_symbols(args, **kw):
    return code_workspace_symbols_tool(
        query=args.get("query", ""),
        path=args.get("path"),
        language=args.get("language"),
        kind=args.get("kind"),
        max_results=args.get("max_results", 50),
    )


# ---------------------------------------------------------------------------
# code_rename — LSP textDocument/rename (semantic, cross-file)
# ---------------------------------------------------------------------------


def code_rename_tool(
    path: str,
    line: int,
    new_name: str,
    character: Optional[int] = None,
    language: Optional[str] = None,
    dry_run: bool = True,
) -> str:
    """Semantically rename a symbol across all files using LSP textDocument/rename.

    Unlike code_refactor (pure AST text match), this understands types, scopes, and
    imports — it only renames references to THIS specific symbol (not unrelated ones
    that happen to have the same name).

    Args:
        path: Absolute file path where the symbol appears.
        line: 1-based line number.
        new_name: New symbol name.
        character: 1-based column (auto-detected if omitted).
        language: Language override.
        dry_run: Preview changes without writing. Default TRUE — always preview first.

    Returns:
        JSON with per-file edit list and (if dry_run=False) applied diff.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("No type definition found at position")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "hint": "LSP server is required for semantic rename. Falls-back refactor available via code_refactor (text-AST).",
        })

    logger.info("code_rename: %s:%d:%s -> %r (dry_run=%s)",
                path, line, character, new_name, dry_run)
    workspace_edit = bridge.rename(str(target), lsp_line, lsp_char, new_name)
    if not workspace_edit:
        return fmt_ok({
            "error": "LSP rename returned no edits (symbol not renameable or not found)",
            "query": {"path": str(target), "line": line, "character": character, "new_name": new_name},
        })

    edits_by_file = _parse_workspace_edit(workspace_edit)
    preview = _build_rename_preview(edits_by_file)

    result = {
        "dry_run": dry_run,
        "new_name": new_name,
        "files_affected": len(edits_by_file),
        "total_edits": sum(p["edit_count"] for p in preview),
        "preview": preview,
        "lsp_server": bridge.command,
    }

    if dry_run:
        result["hint"] = "Re-run with dry_run=False to apply. Changes are NOT written."
        return fmt_ok(result)

    # Apply edits: sort per-file by (line, char) DESC to avoid offset drift
    applied = _apply_edits_by_file(edits_by_file)
    result["applied"] = applied
    return fmt_ok(result)


CODE_RENAME_SCHEMA = {
    "name": "code_rename",
    "description": (
        "Semantically rename a symbol across all files using LSP (understands types, scopes, imports). "
        "Only renames references to THIS symbol — not unrelated identifiers with the same name. "
        "Use this INSTEAD of code_refactor when renaming a class/function/variable across a monorepo. "
        "DRY-RUN by default — always preview before applying. Requires an LSP server (pyright, tsserver, gopls, etc.)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path where the symbol appears."},
            "line": {"type": "integer", "description": "1-based line number."},
            "new_name": {"type": "string", "description": "New symbol name."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
            "dry_run": {"type": "boolean", "description": "Preview without writing. Default: true."},
        },
        "required": ["path", "line", "new_name"],
    },
}


def _handle_code_rename(args, **kw):
    return code_rename_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        new_name=args.get("new_name", ""),
        character=args.get("character"),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
    )


# ---------------------------------------------------------------------------
# code_hover — LSP textDocument/hover (signatures, docstrings, types)
# ---------------------------------------------------------------------------




def _normalize_hover_contents(contents: Any) -> List[str]:
    """Normalize LSP hover response to text list."""
    text_parts: List[str] = []
    if isinstance(contents, str):
        text_parts.append(contents)
    elif isinstance(contents, dict):
        text_parts.append(contents.get("value", ""))
    elif isinstance(contents, list):
        for c in contents:
            if isinstance(c, str):
                text_parts.append(c)
            elif isinstance(c, dict):
                text_parts.append(c.get("value", ""))
    return text_parts


def code_hover_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get type signature + docstring for symbol at position (LSP hover).

    Faster than code_capsule when you only need the signature/type info
    (no references, no definition jump). Use BEFORE editing call sites to
    confirm parameter names/types match what you're passing.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    result = bridge.hover(str(target), lsp_line, lsp_char)
    if not result:
        return fmt_err("No hover info at position")

    text_parts = _normalize_hover_contents(result.get("contents"))

    return fmt_ok({
        "path": str(target),
        "line": line,
        "character": character,
        "hover": "\n".join(t for t in text_parts if t).strip(),
        "lsp_server": bridge.command,
    })


CODE_HOVER_SCHEMA = {
    "name": "code_hover",
    "description": (
        "Get type signature, parameter info, and docstring for a symbol via LSP hover. "
        "Use this BEFORE calling/editing a function to confirm its exact signature without "
        "reading the full definition. Faster + cheaper than code_capsule when you only need "
        "the type info. Requires LSP server (pyright/tsserver/gopls/etc)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}

CODE_FORMAT_SCHEMA = {
    "name": "code_format",
    "description": (
        "Format a file using the LSP server's textDocument/formatting. "
        "Automatically formats indentation, spacing, and style according to the "
        "language's formatter (pyright/pylsp for Python, tsserver for TypeScript, "
        "gopls for Go, rust-analyzer for Rust). "
        "Writes formatted content back to the file. "
        "Falls back to a safety check if LSP formatting is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path to format."},
            "language": {"type": "string", "description": "Language override (auto-detected from extension)."},
            "dry_run": {"type": "boolean", "description": "Preview changes without writing (default: true)."},
        },
        "required": ["path"],
    },
}


def _handle_code_hover(args, **kw):
    return code_hover_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def code_format_tool(
    path: str,
    dry_run: bool = True,
    language: Optional[str] = None,
) -> str:
    """Format a file using the LSP server's textDocument/formatting.

    Returns a diff-like preview of the changes or applies them.
    Falls back gracefully if no LSP formatter is available for the language.
    """
    import json as _json
    import difflib as _difflib

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Path not found: {path}")

    # Read original content
    original = target.read_text(encoding="utf-8")

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "hint": "LSP server is required for formatting. Install the appropriate server.",
        })

    edits = bridge.format_document(str(target))
    if not edits:
        return fmt_ok({
            "info": f"LSP formatter returned no changes for {lang}",
            "path": str(target),
        })

    # Apply TextEdits in reverse order (highest line first) to avoid offset drift
    sorted_edits = sorted(edits, key=lambda e: (
        -e.get("range", {}).get("start", {}).get("line", 0),
        -e.get("range", {}).get("start", {}).get("character", 0)
    ))

    content = list(original)  # character-level list
    edit_info = []
    for edit in sorted_edits:
        range_s = edit.get("range", {})
        start = range_s.get("start", {})
        end = range_s.get("end", {})
        s_line, s_char = start.get("line", 0), start.get("character", 0)
        e_line, e_char = end.get("line", 0), end.get("character", 0)
        new_text = edit.get("newText", "")

        # Convert to absolute offsets (simplified: line-based)
        lines_arr = original.splitlines(keepends=True)
        def _offset(ln: int, ch: int) -> int:
            return sum(len(x) for x in lines_arr[:ln]) + ch

        start_off = _offset(s_line, s_char)
        end_off = _offset(e_line, e_char)

        edit_info.append({
            "range": f"L{s_line+1}:{s_char}–L{e_line+1}:{e_char}",
            "old_len": end_off - start_off,
            "new_len": len(new_text),
        })

        content[start_off:end_off] = list(new_text)

    formatted = "".join(content)

    # Generate a unified diff for preview
    original_lines = original.splitlines(keepends=True)
    formatted_lines = formatted.splitlines(keepends=True)
    diff_lines = list(_difflib.unified_diff(
        original_lines, formatted_lines,
        fromfile=f"a/{target.name}", tofile=f"b/{target.name}",
        lineterm="",
    ))

    result = {
        "path": str(target),
        "language": lang,
        "lsp_server": bridge.command,
        "edit_count": len(edits),
        "edit_details": edit_info,
        "diff": diff_lines,
        "dry_run": dry_run,
        "formatted_length": len(formatted),
        "original_length": len(original),
    }

    if dry_run:
        result["hint"] = "Re-run with dry_run=False to apply formatting."
        return fmt_ok(result)

    # Write formatted content back
    target.write_text(formatted, encoding="utf-8")
    result["applied"] = True
    return fmt_ok(result)


def _handle_code_format(args: dict, **kw: Any) -> str:
    return code_format_tool(
        path=args.get("path", ""),
        dry_run=args.get("dry_run", True),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# code_type_definition — LSP textDocument/typeDefinition
# ---------------------------------------------------------------------------


def code_type_definition_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Jump to the TYPE of a symbol (not its declaration).

    For `const user = getUser()` at `user`, code_definition lands on
    `getUser()`'s implementation, but code_type_definition lands on the
    `User` interface/class. Crucial for understanding shape before refactor.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    try:
        locs = bridge.type_definition(str(target), lsp_line, lsp_char)
    except Exception as exc:
        logger.debug("type_definition error for %s:%d: %s", str(target), line, exc)
        return fmt_err("Could not auto-detect language")

    if not locs:
        return fmt_err(f"Path not found: {path}")

    out = []
    for loc in locs:
        try:
            d = _location_to_dict(loc)
            # _location_to_dict now returns both "path" and "file" keys
            out.append(d)
        except Exception as exc:
            logger.debug("Skipping malformed type_definition location: %s", exc)
            continue
    if not out:
        return fmt_err("No type definition found at position")
    return fmt_ok({"type_definitions": out, "lsp_server": bridge.command})


CODE_TYPE_DEFINITION_SCHEMA = {
    "name": "code_type_definition",
    "description": (
        "Jump to the TYPE definition of a symbol (interface/class/type alias), "
        "not its value declaration. Use this when you need to understand the SHAPE "
        "of a value before refactoring — e.g. for `const u = getUser()`, this lands on "
        "the `User` interface, while code_definition lands on `getUser()`'s body. "
        "Requires LSP (most useful for TypeScript/Go/Rust)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_type_definition(args, **kw):
    return code_type_definition_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def code_implementations_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Find implementations of a symbol (interface, abstract class, method override).

    Uses LSP textDocument/implementation. Helps find where interfaces are
    implemented, abstract methods are overridden, or virtual methods are defined
    in concrete classes.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Path not found: {path}")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"No LSP bridge for {lang or 'auto-detected'}")

    try:
        locs = bridge.implementations(str(target), lsp_line, lsp_char)
    except Exception as exc:
        logger.debug("implementations error for %s:%d: %s", str(target), line, exc)
        return fmt_err(f"Path not found: {path}")

    if not locs:
        return fmt_err("Failed to resolve references for caller analysis")

    out = []
    for loc in locs:
        try:
            d = _location_to_dict(loc)
            out.append(d)
        except Exception as exc:
            logger.debug("Skipping malformed implementation location: %s", exc)
            continue
    if not out:
        return fmt_err(f"Path not found: {path}")
    return fmt_ok({"implementations": out, "lsp_server": bridge.command})


CODE_IMPLEMENTATIONS_SCHEMA = {
    "name": "code_implementations",
    "description": (
        "Find implementations of a symbol via LSP textDocument/implementation. "
        "Useful for finding where interfaces are implemented, abstract methods "
        "are overridden, or concrete classes extend a base type."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_implementations(args, **kw):
    return code_implementations_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _check_lsp_reqs() -> bool:
    """Return True if at least one LSP server is available on PATH."""
    for lang_configs in _LANGUAGE_SERVERS.values():
        for cfg in lang_configs:
            if _resolve_command(cfg["command"]):
                return True
    return False  # No LSP servers found — tools will use AST fallback


# ---------------------------------------------------------------------------
# Registration — deferred to avoid circular imports
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# code_signatures — LSP textDocument/signatureHelp
# ---------------------------------------------------------------------------


def code_signatures_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get parameter / signature hints for a function call site via LSP signatureHelp.

    Use when generating or editing a call to an unfamiliar function — returns
    the parameter list, types, active parameter index, and inline docs without
    needing to read the source. Massively reduces wrong-args bugs in generated code.

    Args:
        path: Absolute file path of the call site.
        line: 1-based line number of the call (cursor inside the parens).
        character: 1-based column (auto-detected to inside parens if omitted).
        language: Language override.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"No LSP bridge for {lang}")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_paren_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    sig = bridge.signature_help(str(target), lsp_line, lsp_char)
    if not sig or not sig.get("signatures"):
        return fmt_ok({
            "found": False,
            "query": {"path": str(target), "line": line, "character": character},
            "hint": "No signature help — cursor must be INSIDE function call parens.",
        })

    active_sig_idx = sig.get("activeSignature", 0) or 0
    active_param_idx = sig.get("activeParameter", 0) or 0
    out_sigs = _format_signatures(sig, active_sig_idx, active_param_idx)

    return fmt_ok({
        "found": True,
        "lsp_server": bridge.command,
        "signatures": out_sigs,
    })




def _format_signatures(sig: dict, active_sig_idx: int, active_param_idx: int) -> List[dict]:
    """Format LSP signatureHelp response into structured output."""
    out_sigs = []
    for i, s in enumerate(sig.get("signatures", [])):
        params = []
        for p in s.get("parameters", []):
            label = p.get("label")
            if isinstance(label, list) and len(label) == 2:
                sig_label = s.get("label", "")
                label = sig_label[label[0]:label[1]]
            params.append({
                "label": label,
                "doc": _extract_md(p.get("documentation")),
            })
        out_sigs.append({
            "active": i == active_sig_idx,
            "label": s.get("label", ""),
            "doc": _extract_md(s.get("documentation")),
            "active_parameter": active_param_idx,
            "parameters": params,
        })
    return out_sigs


def _extract_md(doc) -> str:
    """Normalize LSP MarkupContent | str to plain text."""
    if not doc:
        return ""
    if isinstance(doc, str):
        return doc
    if isinstance(doc, dict):
        return doc.get("value", "")
    return str(doc)


CODE_SIGNATURES_SCHEMA = {
    "name": "code_signatures",
    "description": (
        "Get parameter / signature hints for a function call site via LSP signatureHelp. "
        "Use BEFORE writing or editing a call to an unfamiliar function — returns the "
        "parameter list, types, active parameter index, and inline docs without reading "
        "source files. Reduces wrong-args bugs in generated code. Cursor MUST be inside "
        "the call's parentheses."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line of the call."},
            "character": {"type": "integer", "description": "1-based column inside parens (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_signatures(args, **kw):
    return code_signatures_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        character=args.get("character"),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# code_action — LSP textDocument/codeAction (quick-fix, organize imports, etc.)
# ---------------------------------------------------------------------------


def _filter_diagnostics_in_range(bridge, file_path: str, lsp_line: int, lsp_end_line: int) -> list:
    """Pull diagnostics from bridge and filter to those overlapping the given range."""
    diags = bridge.publish_diagnostics(file_path) or []
    return [
        d for d in diags
        if d.get("range", {}).get("start", {}).get("line", -1) <= lsp_end_line
        and d.get("range", {}).get("end", {}).get("line", -1) >= lsp_line
    ]


def _summarize_actions(actions: list) -> list:
    """Summarize LSP code actions for display."""
    summary = []
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            continue
        summary.append({
            "index": i,
            "title": a.get("title", ""),
            "kind": a.get("kind", ""),
            "is_preferred": a.get("isPreferred", False),
            "has_edit": bool(a.get("edit")),
            "has_command": bool(a.get("command")),
        })
    return summary


def _apply_workspace_edit(workspace_edit: dict) -> List[dict]:
    """Apply an LSP WorkspaceEdit to the filesystem. Returns per-file status list.

    Shared between code_action and (in future) any tool that produces edits.
    """
    edits_by_file: dict = {}
    for uri, text_edits in (workspace_edit.get("changes") or {}).items():
        fp = uri[7:] if uri.startswith("file://") else uri
        edits_by_file.setdefault(fp, []).extend(text_edits)
    for doc_change in workspace_edit.get("documentChanges") or []:
        if "textDocument" in doc_change:
            uri = doc_change["textDocument"].get("uri", "")
            fp = uri[7:] if uri.startswith("file://") else uri
            edits_by_file.setdefault(fp, []).extend(doc_change.get("edits", []))

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
                key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]),
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
            logger.exception("apply_workspace_edit failed for %s", fp)
    return applied


def code_action_tool(
    path: str,
    line: int,
    end_line: Optional[int] = None,
    only_kinds: Optional[List[str]] = None,
    apply_index: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Request available LSP code actions (quick-fixes, organize imports, source actions).

    Two modes:
      1. apply_index=None (default): list all available actions. Inspect titles + kinds.
      2. apply_index=N: apply the Nth action (0-based) — writes files / runs commands.

    Common kinds:
      - quickfix: fix a diagnostic (e.g. add missing import)
      - source.organizeImports: organize all imports in the file
      - source.fixAll: apply all auto-fixable issues
      - refactor.extract: extract function/variable
      - refactor.inline: inline function/variable

    Args:
        path: Absolute file path.
        line: 1-based line number.
        end_line: 1-based end line for range-based actions (defaults to line).
        only_kinds: Optional filter list (e.g. ["source.organizeImports"]).
        apply_index: If set, apply the Nth action returned (0-based). Otherwise list-only.
        language: Language override.
    """
    import json as _json

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"No LSP bridge for {lang}")

    lsp_line = line - 1
    lsp_end_line = (end_line - 1) if end_line else lsp_line

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    relevant_diags = _filter_diagnostics_in_range(bridge, str(target), lsp_line, lsp_end_line)

    actions = bridge.code_action(
        str(target), lsp_line, 0, lsp_end_line, 999,
        only_kinds=only_kinds, diagnostics=relevant_diags,
    ) or []

    if not actions:
        return fmt_ok({
            "found": False,
            "query": {"path": str(target), "line": line, "end_line": end_line, "only_kinds": only_kinds},
            "diagnostics_in_range": len(relevant_diags),
            "hint": "No actions available. Try widening range, removing only_kinds filter, or check diagnostics first.",
        })

    summary = _summarize_actions(actions)

    if apply_index is None:
        return fmt_ok({
            "found": True,
            "lsp_server": bridge.command,
            "diagnostics_in_range": len(relevant_diags),
            "actions": summary,
            "hint": "Re-run with apply_index=N to apply. Prefer is_preferred=true actions for safe quick-fixes.",
        })

    if apply_index < 0 or apply_index >= len(actions):
        return fmt_err(f"Path not found: {path}")

    action = actions[apply_index]
    applied_edits = []
    cmd_result = None

    if action.get("edit"):
        applied_edits = _apply_workspace_edit(action["edit"])

    if action.get("command"):
        cmd = action["command"]
        if isinstance(cmd, dict):
            cmd_result = bridge.execute_command(cmd.get("command", ""), cmd.get("arguments"))
            # Some servers send back a WorkspaceEdit via applyEdit instead — already
            # handled by the bridge's incoming dispatch. For now we just record the result.

    return fmt_ok({
        "applied": True,
        "action": {"title": action.get("title", ""), "kind": action.get("kind", "")},
        "edits_applied": applied_edits,
        "command_result": cmd_result,
    })


CODE_ACTION_SCHEMA = {
    "name": "code_action",
    "description": (
        "Request LSP code actions: quick-fixes, organize imports, source.fixAll, refactor.extract/inline. "
        "Two modes — list (default) or apply_index=N. Use this AFTER code_diagnostics to auto-fix errors "
        "(e.g. add missing imports, remove unused vars). Use kind='source.organizeImports' for cleanup. "
        "MUCH safer than manual edits — preserves semantics via the language server."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "end_line": {"type": "integer", "description": "1-based end line (defaults to line)."},
            "only_kinds": {
                "type": "array", "items": {"type": "string"},
                "description": "Filter to specific kinds: quickfix, source.organizeImports, source.fixAll, refactor.extract, etc.",
            },
            "apply_index": {"type": "integer", "description": "0-based index of action to apply. Omit to list-only."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_action(args, **kw):
    return code_action_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        end_line=args.get("end_line"),
        only_kinds=args.get("only_kinds"),
        apply_index=args.get("apply_index"),
        language=args.get("language"),
    )


def _safe_register(name, toolset, schema, handler, check_fn=None, emoji=""):
    """Register a tool with error handling — one failure won't kill all registrations."""
    from tools.registry import registry

    try:
        registry.register(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            emoji=emoji,
        )
    except Exception as e:
        logger.warning("Failed to register tool '%s': %s", name, e)


def register_lsp_tools() -> None:
    """Register code_definition and code_references with the tool registry.

    Called from ``code_intel.py`` to keep registration in one place.
    """
    from tools.registry import registry

    _safe_register(
        name="code_definition",
        toolset="agentiker_code_intel",
        schema=CODE_DEFINITION_SCHEMA,
        handler=_handle_code_definition,
        check_fn=_check_lsp_reqs,
        emoji="📍",
    )

    _safe_register(
        name="code_type_hierarchy",
        toolset="agentiker_code_intel",
        schema=CODE_TYPE_HIERARCHY_SCHEMA,
        handler=_handle_code_type_hierarchy,
        check_fn=_check_lsp_reqs,
        emoji="🏛️",
    )

    _safe_register(
        name="code_call_hierarchy",
        toolset="agentiker_code_intel",
        schema=CODE_CALL_HIERARCHY_SCHEMA,
        handler=_handle_code_call_hierarchy,
        check_fn=_check_lsp_reqs,
        emoji="🌳",
    )

    _safe_register(
        name="code_highlight",
        toolset="agentiker_code_intel",
        schema=CODE_HIGHLIGHT_SCHEMA,
        handler=_handle_code_highlight,
        check_fn=_check_lsp_reqs,
        emoji="🟡",
    )

    _safe_register(
        name="code_inlay_hints",
        toolset="agentiker_code_intel",
        schema=CODE_INLAY_HINTS_SCHEMA,
        handler=_handle_code_inlay_hints,
        check_fn=_check_lsp_reqs,
        emoji="🔍",
    )

    _safe_register(
        name="code_document_symbols",
        toolset="agentiker_code_intel",
        schema=CODE_DOCUMENT_SYMBOLS_SCHEMA,
        handler=_handle_code_document_symbols,
        check_fn=_check_lsp_reqs,
        emoji="📋",
    )

    _safe_register(
        name="code_references",
        toolset="agentiker_code_intel",
        schema=CODE_REFERENCES_SCHEMA,
        handler=_handle_code_references,
        check_fn=_check_lsp_reqs,
        emoji="🔗",
    )

    _safe_register(
        name="code_diagnostics",
        toolset="agentiker_code_intel",
        schema=CODE_DIAGNOSTICS_SCHEMA,
        handler=_handle_code_diagnostics,
        check_fn=_check_lsp_reqs,
        emoji="🩺",
    )

    _safe_register(
        name="code_callers",
        toolset="agentiker_code_intel",
        schema=CODE_CALLERS_SCHEMA,
        handler=_handle_code_callers,
        check_fn=_check_lsp_reqs,
        emoji="📤",
    )

    _safe_register(
        name="code_callees",
        toolset="agentiker_code_intel",
        schema=CODE_CALLEES_SCHEMA,
        handler=_handle_code_callees,
        check_fn=_check_lsp_reqs,
        emoji="📥",
    )

    _safe_register(
        name="code_workspace_symbols",
        toolset="agentiker_code_intel",
        schema=CODE_WORKSPACE_SYMBOLS_SCHEMA,
        handler=_handle_code_workspace_symbols,
        check_fn=_check_lsp_reqs,
        emoji="🔎",
    )

    _safe_register(
        name="code_rename",
        toolset="agentiker_code_intel",
        schema=CODE_RENAME_SCHEMA,
        handler=_handle_code_rename,
        check_fn=_check_lsp_reqs,
        emoji="✏️",
    )

    _safe_register(
        name="code_hover",
        toolset="agentiker_code_intel",
        schema=CODE_HOVER_SCHEMA,
        handler=_handle_code_hover,
        check_fn=_check_lsp_reqs,
        emoji="💡",
    )

    _safe_register(
        name="code_format",
        toolset="agentiker_code_intel",
        schema=CODE_FORMAT_SCHEMA,
        handler=_handle_code_format,
        check_fn=_check_lsp_reqs,
        emoji="🎨",
    )

    _safe_register(
        name="code_type_definition",
        toolset="agentiker_code_intel",
        schema=CODE_TYPE_DEFINITION_SCHEMA,
        handler=_handle_code_type_definition,
        check_fn=_check_lsp_reqs,
        emoji="🧬",
    )

    _safe_register(
        name="code_implementations",
        toolset="agentiker_code_intel",
        schema=CODE_IMPLEMENTATIONS_SCHEMA,
        handler=_handle_code_implementations,
        check_fn=_check_lsp_reqs,
        emoji="🔨",
    )

    _safe_register(
        name="code_signatures",
        toolset="agentiker_code_intel",
        schema=CODE_SIGNATURES_SCHEMA,
        handler=_handle_code_signatures,
        check_fn=_check_lsp_reqs,
        emoji="📝",
    )

    _safe_register(
        name="code_action",
        toolset="agentiker_code_intel",
        schema=CODE_ACTION_SCHEMA,
        handler=_handle_code_action,
        check_fn=_check_lsp_reqs,
        emoji="🔧",
    )

    logger.info("LSP tools registered: code_definition, code_references, code_diagnostics, code_callers, code_callees, code_workspace_symbols, code_rename, code_hover, code_type_definition, code_signatures, code_action, code_format, code_implementations, code_type_hierarchy, code_call_hierarchy, code_highlight, code_inlay_hints, code_document_symbols")
