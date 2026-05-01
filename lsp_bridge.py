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

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ensure the plugin logger is always visible at DEBUG level.
# Hermes core may set its own level — this adds a dedicated handler
# so our DEBUG logs are always visible regardless of parent config.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
))
logger.handlers.clear()  # avoid duplicates on module reload
logger.addHandler(_handler)
logger.setLevel(logging.DEBUG)
logger.propagate = False  # don't double-log to Hermes root logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum time (seconds) to wait for a single LSP response.
_LSP_REQUEST_TIMEOUT = 30

# Maximum time (seconds) to wait for the server to start and respond to
# the ``initialize`` handshake.
_LSP_INIT_TIMEOUT = 60

# How long to keep an idle server alive before shutting it down.
_LSP_IDLE_TIMEOUT = 300  # 5 minutes

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
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_workspace_root(file_path: str) -> str:
    """Best-effort workspace root discovery for *file_path*.

    Walks up from the file's directory looking for common project markers.
    For monorepos, prefers the directory containing ``pnpm-workspace.yaml``,
    ``nx.json``, or ``lerna.json`` over a bare ``.git`` or ``package.json``.
    """
    p = Path(file_path).resolve().parent
    # Monorepo markers take priority — they define the true workspace root
    mono_markers = ("pnpm-workspace.yaml", "nx.json", "lerna.json")
    # Generic project markers
    generic_markers = (
        ".git",
        ".hg",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "tsconfig.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Makefile",
    )
    mono_root = None
    generic_root = None
    for _ in range(40):  # max depth guard
        for m in mono_markers:
            if (p / m).exists():
                if mono_root is None:
                    mono_root = str(p)
        if generic_root is None:
            for m in generic_markers:
                if (p / m).exists():
                    generic_root = str(p)
                    break
        # Stop early only if we already found both mono and generic markers
        if mono_root and generic_root:
            break
        parent = p.parent
        if parent == p:
            break
        p = parent
    # Prefer monorepo root over generic root
    return mono_root or generic_root or str(Path(file_path).resolve().parent)


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
        # nx / lerna: treat apps/ and packages/ conventionally
        for nx_marker in ("nx.json", "lerna.json"):
            if (root_path / nx_marker).exists():
                folders = []
                for d in ("apps", "packages", "modules", "libs"):
                    if (root_path / d).is_dir():
                        folders.append(str(root_path / d))
                return folders
        return []

    # Parse pnpm-workspace.yaml
    try:
        import yaml
        with open(workspace_cfg, "r") as f:
            cfg = yaml.safe_load(f)
    except Exception:
        # Minimal parser: find lines like "  - 'apps/*'"
        try:
            text = workspace_cfg.read_text("utf-8", errors="replace")
            patterns = []
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    val = stripped[2:].strip().strip("'\"")
                    if not val.startswith("!"):  # skip exclusions
                        patterns.append(val)
            cfg = {"packages": patterns} if patterns else None
        except Exception:
            return []

    if not cfg or "packages" not in cfg:
        return []

    folders: List[str] = []
    for pattern in cfg["packages"]:
        if pattern.startswith("!"):
            continue
        # Glob-expand the pattern (e.g. "apps/*" → all subdirs of apps/)
        matches = sorted(root_path.glob(pattern))
        for m in matches:
            if m.is_dir():
                folders.append(str(m))
        # Also include the parent as a workspace root hint
        parent_match = root_path / pattern.replace("/*", "")
        if parent_match.is_dir() and str(parent_match) not in folders:
            # Only if the pattern is a glob (contains *)
            if "*" not in pattern and str(parent_match) not in folders:
                folders.append(str(parent_match))
    return folders


def _resolve_command(cmd: str) -> Optional[str]:
    """Return the full path for *cmd* if it exists on ``$PATH``, else ``None``."""
    return shutil.which(cmd)


# ---------------------------------------------------------------------------
# LSP Bridge — manages a single server process
# ---------------------------------------------------------------------------


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
    _diagnostics_cache: Dict[str, List[dict]] = field(default_factory=dict, init=False, repr=False)
    _open_documents: set = field(default_factory=set, init=False, repr=False)  # Track open docs to avoid duplicate didOpen

    # -- lifecycle -----------------------------------------------------------

    def _build_env(self) -> Dict[str, str]:
        """Build environment variables for the LSP server process."""
        env = {**os.environ}
        if self.language_id == "python":
            env["PYRIGHT_PYTHON_FORCE_VERSION"] = ""
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

    def ensure_initialized(self) -> bool:
        """Start the server (if needed) and complete the LSP handshake."""
        with self._init_lock:
            if self._alive and self._initialized:
                self._last_activity = time.monotonic()
                return True
            if self._alive:
                self.shutdown()
            return self._start_and_init()

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
            self._process = subprocess.Popen(
                [cmd_path] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self.root_uri,
                env=self._build_env(),
            )
            self._alive = True
            self._last_activity = time.monotonic()

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
                if self._initialized:
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
        with self._lock:
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
                resp = self._responses.pop(req_id, None)
                # Log response summary — truncate large payloads
                resp_str = json.dumps(resp) if resp else "None"
                if len(resp_str) > 300:
                    resp_str = resp_str[:300] + "..."
                logger.debug("LSP << %s (id=%d) %s", method, req_id, resp_str)
                return resp
            else:
                logger.warning("LSP request timed out: %s (id=%d, timeout=%.1fs)", method, req_id, timeout)
                self._pending.pop(req_id, None)
                return None
        except Exception as exc:
            logger.error("LSP request failed: %s (id=%d): %s", method, req_id, exc)
            return None
        finally:
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
            fd = self._process.stdout.fileno() if self._process and self._process.stdout else None
            while self._alive and self._process and self._process.poll() is None:
                try:
                    # Use os.read() to read available bytes without blocking
                    # (unlike .read(4096) which blocks until 4096 bytes or EOF)
                    import selectors
                    sel = selectors.DefaultSelector()
                    sel.register(self._process.stdout, selectors.EVENT_READ)
                    ready = sel.select(timeout=1.0)
                    sel.close()
                    if not ready:
                        continue  # No data yet, check if still alive
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    buf += chunk
                except Exception:
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
        except Exception:
            pass
        finally:
            self._alive = False
            # Wake up any pending waiters
            for event in list(self._pending.values()):
                event.set()

    def _dispatch(self, msg: dict) -> None:
        """Dispatch a received JSON-RPC message."""
        if "id" in msg and msg["id"] in self._pending:
            self._responses[msg["id"]] = msg.get("result")
            self._pending[msg["id"]].set()
        elif "method" in msg:
            method = msg["method"]
            if method == "window/logMessage":
                # Log server messages — useful for debugging TS server issues
                # LSP MessageType: 1=Error, 2=Warning, 3=Info, 4=Log
                # NOTE: tsserver and other servers routinely send informational
                # messages with type=1 (Error). We downgrade 1→INFO to avoid
                # false-positive ERROR log spam.
                params = msg.get("params", {})
                level = params.get("type", 3)
                text = params.get("message", "")
                level_map = {1: logging.INFO, 2: logging.WARNING, 3: logging.INFO, 4: logging.DEBUG}
                logger.log(level_map.get(level, logging.DEBUG), "LSP server: %s", text)
            elif method == "textDocument/publishDiagnostics":
                # Log diagnostics for the opened file (errors/warnings) and cache them
                params = msg.get("params", {})
                uri = params.get("uri", "")
                diagnostics = params.get("diagnostics", [])
                path = LSPBridge._uri_to_path(uri)
                self._diagnostics_cache[path] = diagnostics
                errors = [d for d in diagnostics if d.get("severity") == 1]  # Error=1, Warning=2, Info=3, Hint=4
                warnings = [d for d in diagnostics if d.get("severity") == 2]
                if errors:
                    for e in errors[:5]:  # Cap at 5 to avoid spam
                        logger.warning("LSP diagnostic: %s:%d: %s",
                            path, e.get("range", {}).get("start", {}).get("line", 0) + 1,
                            e.get("message", ""))
                if warnings:
                    for w in warnings[:3]:  # Cap at 3
                        logger.debug("LSP diagnostic: %s:%d: %s",
                            path, w.get("range", {}).get("start", {}).get("line", 0) + 1,
                            w.get("message", ""))
            elif method in ("$/progress", "textDocument/didOpen", "textDocument/didChange",
                          "textDocument/didClose", "textDocument/didSave"):
                pass
            else:
                logger.debug("LSP notification: %s", method)

    # -- LSP operations ------------------------------------------------------

    def open_document(self, file_path: str, content: Optional[str] = None) -> None:
        """Tell the LSP server to open a document. No-op if already open.

        Some LSP servers (notably ``typescript-language-server``) may keep a
        document open even if our Python-side bookkeeping was lost across a
        gateway/plugin reload. In that stale-state case a duplicate didOpen
        triggers a server-side notification error ("Can't open already open
        document") and can block workspace/symbol results. We handle this by
        sending a best-effort didClose before the first didOpen for a URI in
        this bridge instance.
        """
        uri = f"file://{file_path}"
        needs_reconcile_close = False
        with self._lock:
            if uri in self._open_documents:
                return  # Already open — skip duplicate didOpen
            # Pre-register BEFORE reading file & sending to prevent concurrent
            # threads from racing through and sending duplicate didOpen.
            self._open_documents.add(uri)
            needs_reconcile_close = True
        if content is None:
            try:
                content = Path(file_path).read_text("utf-8", errors="replace")
            except OSError:
                logger.warning("open_document: failed to read %s", file_path)
                with self._lock:
                    self._open_documents.discard(uri)
                return
        if needs_reconcile_close:
            logger.debug("LSP didClose before didOpen (reconcile): %s", file_path)
            self._send_notification("textDocument/didClose", {
                "textDocument": {"uri": uri}
            })
            if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
                time.sleep(0.05)
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
        """Tell the LSP server to close a document."""
        uri = f"file://{file_path}"
        self._send_notification("textDocument/didClose", {
            "textDocument": {
                "uri": uri,
            }
        })
        with self._lock:
            self._open_documents.discard(uri)

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

        # Small delay to let the server process the didOpen
        # TS server needs a bit longer for project indexing on first request
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)

        t0 = time.monotonic()
        logger.debug("goto_definition: %s:%d:%d", file_path, line, character)
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
            logger.debug("  definition empty, retrying after 500ms...")
            time.sleep(0.5)
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
            List of location dicts, or None on failure.
        """
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)

        t0 = time.monotonic()
        logger.debug("find_references: %s:%d:%d (includeDeclaration=%s)",
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
            logger.debug("  references empty, retrying after 500ms...")
            time.sleep(0.5)
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
            if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
                time.sleep(3.0)  # TS server needs time to index large monorepos
            else:
                time.sleep(0.05)
        try:
            result = self._send_request("workspace/symbol", {"query": query})
        except Exception as exc:
            logger.debug("workspace_symbol error: %s", exc)
            return None
        if not result:
            # Retry — TS server might still be indexing on first call
            if anchor_file and self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
                logger.debug("workspace_symbol: empty result, retrying after 5s...")
                time.sleep(5.0)
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
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)
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
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)

        logger.debug("hover: %s:%d:%d", file_path, line, character)
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

    # -- helpers -------------------------------------------------------------

    def type_definition(
        self, file_path: str, line: int, character: int
    ) -> Optional[List[dict]]:
        """Request 'textDocument/typeDefinition' from the LSP server."""
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.3)
        else:
            time.sleep(0.05)

        result = self._send_request("textDocument/typeDefinition", {
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
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.3)
        else:
            time.sleep(0.05)
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
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)
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
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)
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
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)
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
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)
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
                    oldest_bridge.shutdown()
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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_definition_tool: %s:%d:%d lang=%s", path, line, character, lang)

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
                return _json.dumps({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "definition_count": len(defs),
                    "definitions": defs,
                    "formatted": _format_definitions(defs),
                }, indent=2)
            else:
                logger.info("code_definition: LSP returned 0 locations, falling back to AST")

    # Fallback: AST-based definition search
    logger.debug("code_definition: using AST fallback")
    return _ast_fallback_definition(str(target), line, character, lang)


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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_references_tool: %s:%d:%d lang=%s includeDecl=%s",
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
                    return _json.dumps({
                        "path": str(target),
                        "query": {"line": line, "character": character},
                        "method": "lsp",
                        "lsp_server": bridge.command,
                        "reference_count": len(refs),
                        "files_affected": len(by_file),
                        "references": refs,
                        "by_file": by_file,
                        "formatted": _format_references(refs, by_file),
                    }, indent=2)
                # Compact group-by-file mode (token-saving)
                compact_by_file = {
                    f: [{"line": r["line"], "column": r.get("column"), "text": r.get("text", "")[:80]}
                         for r in file_refs]
                    for f, file_refs in sorted(by_file.items())
                }
                return _json.dumps({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "reference_count": len(refs),
                    "files_affected": len(by_file),
                    "by_file": compact_by_file,
                    "formatted": _format_references(refs, by_file),
                }, indent=2)
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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    diagnostics: list[dict] = []
    bridge: Optional[LspBridge] = None

    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge and bridge.ensure_initialized():
            # Open the document first so the LSP server sends publishDiagnostics
            bridge.open_document(str(target))
            # Wait for publishDiagnostics notification to arrive in cache
            if bridge.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
                time.sleep(1.0)  # TSServer needs time to analyze
            else:
                time.sleep(0.1)

            # Try cached LSP diagnostics (populated by textDocument/publishDiagnostics)
            cached = bridge.get_cached_diagnostics(str(target))
            if cached:
                diagnostics = cached
                logger.info("code_diagnostics: got %d cached diagnostics for %s", len(cached), str(target))

    # If no cached diagnostics, try pull diagnostics (LSP 3.17+)
    if not diagnostics and bridge and bridge.ensure_initialized():
        try:
            resp = bridge._send_request("textDocument/diagnostic", {
                "textDocument": {"uri": f"file://{str(target)}"},
                "identifier": "code_intel",
                "previousResultId": None,
            }, timeout=10)
            if resp and "items" in resp:
                diagnostics = resp["items"]
                logger.info("code_diagnostics: LSP pull returned %d items", len(diagnostics))
        except Exception as exc:
            logger.debug("textDocument/diagnostic not supported by %s: %s", bridge.command, exc)

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
        return _json.dumps(summary, indent=2)

    # Fallback: AST heuristic
    logger.debug("code_diagnostics: using AST fallback")
    return _ast_fallback_diagnostics(str(target), lang)


def code_callers_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
    group_by_file: bool = False,
) -> str:
    """Find call sites of a symbol (where it is invoked)."""
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))

    # First get all references, then heuristically filter to call sites
    refs_json = code_references_tool(
        path=str(target),
        line=line,
        character=character,
        language=lang,
        include_declaration=False,
        group_by_file=True,
    )
    try:
        refs_data = _json.loads(refs_json)
    except Exception:
        return _json.dumps({"error": "Failed to resolve references for caller analysis"})

    if "error" in refs_data:
        return refs_json

    by_file = refs_data.get("by_file", {})
    callers: list[dict] = []
    for file_path, locations in by_file.items():
        try:
            text = Path(file_path).read_text("utf-8", errors="replace")
            lines = text.split("\n")
            for loc in locations:
                l = loc.get("line", 0)
                if 1 <= l <= len(lines):
                    line_text = lines[l - 1]
                    stripped = line_text.strip()
                    # Simple heuristic: call sites contain '(' after the symbol
                    # or are in RHS expressions
                    if '(' in stripped or '=' in stripped or 'return' in stripped:
                        callers.append({
                            "file": file_path,
                            "line": l,
                            "column": loc.get("column"),
                            "text": line_text[:120],
                        })
        except Exception:
            continue

    if not callers:
        return _json.dumps({
            "path": str(target),
            "query": {"line": line},
            "callers": [],
            "note": "Could not identify call sites via LSP/AST. Use code_references for raw usages.",
        })

    result = {
        "path": str(target),
        "query": {"line": line, "character": character},
        "caller_count": len(callers),
        "files_affected": len({c["file"] for c in callers}),
        "callers": callers,
    }
    if group_by_file:
        by_file_callers: dict[str, list[dict]] = {}
        for c in callers:
            by_file_callers.setdefault(c["file"], []).append(c)
        result["by_file"] = by_file_callers

    return _json.dumps(result, indent=2)


def code_callees_tool(
    path: str,
    line: int,
    language: Optional[str] = None,
) -> str:
    """Find symbols CALLED BY a specific function/method.

    Uses AST extraction (call expressions inside the function body) for Python/TS/JS.
    """
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    return _ast_fallback_callees(str(target), line, lang)


# ---------------------------------------------------------------------------
# AST-based fallback
# ---------------------------------------------------------------------------


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
        lines = Path(file_path).read_text("utf-8", errors="replace").split("\n")
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

    # Try importing from plugin code_intel first, then from hermes tools
    _detect = None
    try:
        from .code_intel import detect_language as _detect_func
        _detect = _detect_func
    except ImportError:
        pass
    if _detect is None:
        try:
            from tools.code_intel import detect_language as _detect_func
            _detect = _detect_func
        except ImportError:
            pass
    if _detect is None:
        try:
            # Plugin loaded via hermes_plugins namespace
            from hermes_plugins.code_intel.code_intel import detect_language as _detect_func
            _detect = _detect_func
        except ImportError:
            pass
    if _detect is None:
        try:
            # Direct file import for when running as standalone plugin
            import importlib.util as _ilu
            _mod_path = str(Path(__file__).parent / "code_intel.py")
            _spec = _ilu.spec_from_file_location("code_intel_standalone", _mod_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _detect = _mod.detect_language
        except Exception:
            pass
    if _detect is None:
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    # Read the identifier at the cursor position
    try:
        lines = Path(file_path).read_text("utf-8", errors="replace").split("\n")
        text_line = lines[line - 1] if 0 < line <= len(lines) else ""
    except (OSError, IndexError):
        text_line = ""

    # Extract identifier
    identifier = ""
    if character and text_line and character <= len(text_line):
        idx = character - 1
        start = idx
        while start > 0 and (text_line[start - 1].isalnum() or text_line[start - 1] == '_'):
            start -= 1
        end = idx
        while end < len(text_line) and (text_line[end].isalnum() or text_line[end] == '_'):
            end += 1
        identifier = text_line[start:end]

    if not identifier:
        return _json.dumps({
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
        return _json.dumps({
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

    return _json.dumps({
        "path": file_path,
        "query": {"line": line, "character": character, "identifier": identifier},
        "method": "fallback_ast",
        "warning": "LSP server unavailable, using AST-based search. Results may be incomplete.",
        "definition_count": len(defs),
        "definitions": defs,
    }, indent=2)


def _ast_fallback_references(
    file_path: str, line: int, character: Optional[int], lang: Optional[str]
) -> str:
    """Fallback: use grep-style search for references."""
    import json as _json

    try:
        from tools.code_intel import detect_language as _detect
    except ImportError:
        _detect = None
    if _detect is None:
        try:
            from hermes_plugins.code_intel.code_intel import detect_language as _detect
        except ImportError:
            pass
    if _detect is None:
        try:
            import importlib.util as _ilu
            _mod_path = str(Path(__file__).parent / "code_intel.py")
            _spec = _ilu.spec_from_file_location("code_intel_standalone", _mod_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _detect = _mod.detect_language
        except Exception:
            pass
    if _detect is None:
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    # Extract identifier
    try:
        lines = Path(file_path).read_text("utf-8", errors="replace").split("\n")
        text_line = lines[line - 1] if 0 < line <= len(lines) else ""
    except (OSError, IndexError):
        text_line = ""

    identifier = ""
    if character and text_line and character <= len(text_line):
        idx = character - 1
        start = idx
        while start > 0 and (text_line[start - 1].isalnum() or text_line[start - 1] == '_'):
            start -= 1
        end = idx
        while end < len(text_line) and (text_line[end].isalnum() or text_line[end] == '_'):
            end += 1
        identifier = text_line[start:end]

    if not identifier:
        return _json.dumps({
            "path": file_path,
            "query": {"line": line, "character": character},
            "method": "fallback",
            "warning": "Could not extract an identifier at the given position.",
        })

    # Use text-based search as fallback (reliable for exact identifier match)
    import subprocess as _sp

    root = _find_workspace_root(file_path)
    try:
        result = _sp.run(
            ["rg", "--no-heading", "--line-number", "-n", "-w", identifier, root],
            capture_output=True, text=True, timeout=15,
        )
        refs = []
        for match_line in result.stdout.strip().split("\n"):
            if not match_line:
                continue
            # Parse rg output: filepath:linenum:content
            parts = match_line.split(":", 2)
            if len(parts) >= 3:
                refs.append({
                    "file": parts[0],
                    "line": int(parts[1]),
                    "text": parts[2].strip()[:200],
                })

        by_file: Dict[str, List[dict]] = {}
        for r in refs:
            by_file.setdefault(r["file"], []).append(r)

        return _json.dumps({
            "path": file_path,
            "query": {"line": line, "character": character, "identifier": identifier},
            "method": "fallback_text",
            "warning": "LSP server unavailable, using text-based search. May include false positives.",
            "reference_count": len(refs),
            "files_affected": len(by_file),
            "references": refs,
            "by_file": by_file,
        }, indent=2)

    except FileNotFoundError:
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": "LSP server unavailable and rg (ripgrep) not found.",
            "suggestion": "Install a language server (pyright/pylsp) for accurate results.",
        })
    except _sp.TimeoutExpired:
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": "Text-based search timed out.",
        })


def _ast_fallback_diagnostics(file_path: str, lang: Optional[str]) -> str:
    """Lightweight AST-based heuristic for common issues: unused imports, undefined names."""
    import json as _json
    content = ""
    try:
        content = Path(file_path).read_text("utf-8", errors="replace")
    except Exception as exc:
        return _json.dumps({"path": file_path, "method": "fallback", "warning": str(exc)})

    diagnostics: list[dict] = []

    if lang == "python":
        try:
            import ast
            tree = ast.parse(content)
            imported_names: set[str] = set()
            used_names: set[str] = set()
            defined_names: set[str] = set()

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_names.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imported_names.add(alias.asname or alias.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined_names.add(node.name)
                elif isinstance(node, ast.Name):
                    if isinstance(node.ctx, ast.Store):
                        defined_names.add(node.id)
                    elif isinstance(node.ctx, ast.Load):
                        used_names.add(node.id)

            # Suggest unused imports
            for name in sorted(imported_names - used_names - defined_names):
                # crude line detection
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
        # Token-based heuristic for TS/JS
        lines = content.split("\n")
        for i, line_text in enumerate(lines, 1):
            stripped = line_text.strip()
            if stripped.startswith("import ") and "from " in stripped:
                imp = stripped.split("from")[0].split("{")[-1].split("}")[0]
                imp = imp.replace("import ", "").replace("* as ", "").strip()
                # check if used later in file
                if imp and not any(imp in ln for ln in lines[i:]):
                    diagnostics.append({
                        "severity": 2,
                        "message": f"Possibly unused import: {imp}",
                        "range": {"start": {"line": i - 1, "character": 0},
                                  "end":   {"line": i - 1, "character": len(line_text)}},
                        "source": "ast_heuristic",
                    })

    return _json.dumps({
        "path": file_path,
        "method": "ast_heuristic",
        "warning": "LSP server unavailable. Using lightweight AST heuristic.",
        "diagnostic_count": len(diagnostics),
        "errors": len([d for d in diagnostics if d.get("severity", 1) == 1]),
        "warnings": len([d for d in diagnostics if d.get("severity", 2) == 2]),
        "diagnostics": diagnostics,
    }, indent=2)


def _ast_fallback_callees(file_path: str, line: int, lang: Optional[str]) -> str:
    """AST fallback: extract call expressions from the function/method at *line*."""
    import json as _json
    content = ""
    try:
        content = Path(file_path).read_text("utf-8", errors="replace")
    except Exception as exc:
        return _json.dumps({"path": file_path, "method": "fallback", "warning": str(exc)})

    callees: list[dict] = []

    if lang == "python":
        try:
            import ast
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_start = getattr(node, "lineno", 1)
                    func_end = getattr(node, "end_lineno", func_start)
                    if func_start <= line <= func_end:
                        for child in ast.walk(node):
                            if isinstance(child, ast.Call):
                                name = ""
                                if isinstance(child.func, ast.Name):
                                    name = child.func.id
                                elif isinstance(child.func, ast.Attribute):
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

    elif lang in ("typescript", "javascript"):
        # Token-based call extraction
        lines = content.split("\n")
        if 0 < line <= len(lines):
            # Naive: scan the function region (until empty line or dedent equivalent)
            for i in range(line - 1, min(len(lines), line + 200)):
                ln = lines[i]
                # match simple calls: identifier()
                stripped = ln.strip()
                import re
                for match in re.finditer(r'([A-Za-z_]\w*)\s*\(', ln):
                    name = match.group(1)
                    if name not in {"if", "while", "for", "switch", "catch", "function", "return", "new"}:
                        callees.append({
                            "name": name,
                            "line": i + 1,
                            "type": "call",
                        })

    if not callees:
        return _json.dumps({
            "path": file_path,
            "query": {"line": line},
            "method": "ast_heuristic",
            "warning": "Could not extract callees via AST. Ensure line points to a function/method.",
            "callees": [],
        })

    return _json.dumps({
        "path": file_path,
        "query": {"line": line},
        "method": "ast_heuristic",
        "callee_count": len(callees),
        "callees": callees,
    }, indent=2)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_definitions(defs: List[dict]) -> str:
    """Format definition results for display."""
    if not defs:
        return "No definition found."

    lines = []
    for i, d in enumerate(defs, 1):
        lines.append(f"{i}. {d['file']}:{d['line']}")
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
            text = r.get("text", "")
            if len(text) > 120:
                text = text[:117] + "..."
            lines.append(f"    L{r['line']:>4d}  {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool schemas & registration
# ---------------------------------------------------------------------------

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
            "path": {"type": "string", "description": "Absolute file path containing the symbol"},
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
            "path": {"type": "string", "description": "Absolute file path to analyze"},
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
            "path": {"type": "string", "description": "Absolute file path containing the callee"},
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
            "path": {"type": "string", "description": "Absolute file path containing the function"},
            "line": {"type": "integer", "description": "1-based line number where the function is defined"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
        },
        "required": ["path", "line"],
    },
}


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


def code_workspace_symbols_tool(
    query: str,
    path: Optional[str] = None,
    language: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
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
        return _json.dumps({"error": f"Path not found: {anchor}"})

    # If user passed a dir, pick a meaningful source file to seed the LSP root.
    # Prefer files inside well-known project dirs (packages/, apps/, src/) over
    # random legacy files — the TS server indexes faster from a proper project root.
    probe_file = anchor
    if anchor.is_dir():
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
        if hit:
            probe_file = hit

    lang = language or _detect_language_for_lsp(str(probe_file))
    if not lang:
        return _json.dumps({
            "error": "Could not auto-detect language. Pass language= explicitly.",
            "query": query,
        })

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(probe_file))
    if bridge is None or not bridge.ensure_initialized():
        return _json.dumps({
            "error": f"No LSP bridge available for language={lang}",
            "query": query,
            "hint": "Use search_files (target='content') as fallback",
        })

    logger.info("code_workspace_symbols: query=%r lang=%s root=%s",
                query, lang, bridge.root_uri)
    raw = bridge.workspace_symbol(query, anchor_file=str(probe_file))
    if raw is None:
        return _json.dumps({
            "error": "LSP workspace/symbol request failed or not supported",
            "query": query,
            "lsp_server": bridge.command,
        })

    # LSP SymbolKind enum (1-based)
    _KIND_NAMES = {
        1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
        6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
        11: "interface", 12: "function", 13: "variable", 14: "constant",
        15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
        20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
        25: "operator", 26: "type_parameter",
    }

    symbols: List[dict] = []
    for sym in raw:
        loc = sym.get("location") or {}
        # WorkspaceSymbol (LSP 3.17+) may have location as {uri: ...} without range;
        # SymbolInformation has {uri, range}.
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

    truncated = len(symbols) > limit
    symbols = symbols[:limit]

    return _json.dumps({
        "query": query,
        "language": lang,
        "lsp_server": bridge.command,
        "total_returned": len(symbols),
        "truncated": truncated,
        "symbols": symbols,
    }, indent=2)


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
            "path": {"type": "string", "description": "Optional anchor file/dir for LSP root detection. Defaults to cwd."},
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
        limit=args.get("limit", 50),
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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return _json.dumps({"error": "Could not auto-detect language"})

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return _json.dumps({
            "error": f"No LSP bridge available for language={lang}",
            "hint": "LSP server is required for semantic rename. Falls-back refactor available via code_refactor (text-AST).",
        })

    logger.info("code_rename: %s:%d:%d -> %r (dry_run=%s)",
                path, line, character, new_name, dry_run)
    workspace_edit = bridge.rename(str(target), lsp_line, lsp_char, new_name)
    if not workspace_edit:
        return _json.dumps({
            "error": "LSP rename returned no edits (symbol not renameable or not found)",
            "query": {"path": str(target), "line": line, "character": character, "new_name": new_name},
        })

    # WorkspaceEdit: {changes: {uri: [TextEdit]}} OR {documentChanges: [...]}
    edits_by_file: dict = {}
    changes = workspace_edit.get("changes") or {}
    for uri, text_edits in changes.items():
        file_path = uri[7:] if uri.startswith("file://") else uri
        edits_by_file.setdefault(file_path, []).extend(text_edits)

    for doc_change in workspace_edit.get("documentChanges") or []:
        if "textDocument" in doc_change:
            uri = doc_change["textDocument"].get("uri", "")
            file_path = uri[7:] if uri.startswith("file://") else uri
            edits_by_file.setdefault(file_path, []).extend(doc_change.get("edits", []))

    # Preview: render per-file edit summary
    preview = []
    total_edits = 0
    for fp, tedits in sorted(edits_by_file.items()):
        total_edits += len(tedits)
        preview.append({
            "file": fp,
            "edit_count": len(tedits),
            "lines": sorted({e.get("range", {}).get("start", {}).get("line", 0) + 1 for e in tedits}),
        })

    result = {
        "dry_run": dry_run,
        "new_name": new_name,
        "files_affected": len(edits_by_file),
        "total_edits": total_edits,
        "preview": preview,
        "lsp_server": bridge.command,
    }

    if dry_run:
        result["hint"] = "Re-run with dry_run=False to apply. Changes are NOT written."
        return _json.dumps(result, indent=2)

    # Apply edits: sort per-file by (line, char) DESC to avoid offset drift
    applied = []
    for fp, tedits in edits_by_file.items():
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            # Split to lines for offset math
            lines_arr = content.splitlines(keepends=True)
            # Build absolute offset per (line, char)
            def _offset(ln: int, ch: int) -> int:
                return sum(len(l) for l in lines_arr[:ln]) + ch

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
                # rebuild lines_arr after each edit (reverse order keeps earlier offsets valid,
                # but safer to recompute)
                lines_arr = new_content.splitlines(keepends=True)

            with open(fp, "w", encoding="utf-8") as f:
                f.write(new_content)
            applied.append({"file": fp, "edits": len(tedits), "status": "ok"})
        except Exception as exc:
            applied.append({"file": fp, "edits": len(tedits), "status": f"error: {exc}"})
            logger.exception("code_rename apply failed for %s", fp)

    result["applied"] = applied
    return _json.dumps(result, indent=2)


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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return _json.dumps({"error": "Could not auto-detect language"})

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return _json.dumps({"error": f"No LSP bridge available for language={lang}"})

    result = bridge.hover(str(target), lsp_line, lsp_char)
    if not result:
        return _json.dumps({"error": "No hover info at position", "path": str(target), "line": line})

    # Normalize MarkupContent / MarkedString[] / string
    contents = result.get("contents")
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

    return _json.dumps({
        "path": str(target),
        "line": line,
        "character": character,
        "hover": "\n".join(t for t in text_parts if t).strip(),
        "lsp_server": bridge.command,
    }, indent=2)


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


def _handle_code_hover(args, **kw):
    return code_hover_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return _json.dumps({"error": "Could not auto-detect language"})

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return _json.dumps({"error": f"No LSP bridge available for language={lang}"})

    try:
        locs = bridge.type_definition(str(target), lsp_line, lsp_char)
    except Exception as exc:
        logger.debug("type_definition error for %s:%d: %s", str(target), line, exc)
        return _json.dumps({"error": f"type_definition failed: {exc}"})

    if not locs:
        return _json.dumps({"error": "No type definition found at position"})

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
        return _json.dumps({"error": "No type definition found at position"})
    return _json.dumps({"type_definitions": out, "lsp_server": bridge.command}, indent=2)


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


def _check_lsp_reqs() -> bool:
    """Return True if at least one LSP server is available."""
    for lang_configs in _LANGUAGE_SERVERS.values():
        for cfg in lang_configs:
            if _resolve_command(cfg["command"]):
                return True
    return True  # Always visible — fallback works without LSP


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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return _json.dumps({"error": "Could not auto-detect language"})

    lsp_line = line - 1
    if character is None:
        # Try to land cursor inside the first '(' on the line
        try:
            with open(target, "r", encoding="utf-8") as f:
                src_line = f.readlines()[lsp_line] if lsp_line < sum(1 for _ in open(target)) else ""
        except Exception:
            src_line = ""
        idx = src_line.find("(")
        character = (idx + 2) if idx >= 0 else 1
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return _json.dumps({"error": f"No LSP bridge available for language={lang}"})

    sig = bridge.signature_help(str(target), lsp_line, lsp_char)
    if not sig or not sig.get("signatures"):
        return _json.dumps({
            "found": False,
            "query": {"path": str(target), "line": line, "character": character},
            "hint": "No signature help — cursor must be INSIDE function call parens.",
        })

    active_sig_idx = sig.get("activeSignature", 0) or 0
    active_param_idx = sig.get("activeParameter", 0) or 0
    out_sigs = []
    for i, s in enumerate(sig.get("signatures", [])):
        params = []
        for p in s.get("parameters", []):
            label = p.get("label")
            # label may be a string or [start, end] offsets into the signature label
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

    return _json.dumps({
        "found": True,
        "lsp_server": bridge.command,
        "signatures": out_sigs,
    }, indent=2)


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
            "path": {"type": "string", "description": "Absolute file path of the call site."},
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
                return sum(len(l) for l in lines_arr[:ln]) + ch

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
        return _json.dumps({"error": f"Path not found: {path}"})

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return _json.dumps({"error": "Could not auto-detect language"})

    lsp_line = line - 1
    lsp_end_line = (end_line - 1) if end_line else lsp_line

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return _json.dumps({"error": f"No LSP bridge available for language={lang}"})

    # Pull current diagnostics so quickfix actions know what to fix
    diags = bridge.publish_diagnostics(str(target)) or []
    # Filter to diagnostics overlapping the target range
    relevant_diags = [
        d for d in diags
        if d.get("range", {}).get("start", {}).get("line", -1) <= lsp_end_line
        and d.get("range", {}).get("end", {}).get("line", -1) >= lsp_line
    ]

    actions = bridge.code_action(
        str(target), lsp_line, 0, lsp_end_line, 999,
        only_kinds=only_kinds, diagnostics=relevant_diags,
    ) or []

    if not actions:
        return _json.dumps({
            "found": False,
            "query": {"path": str(target), "line": line, "end_line": end_line, "only_kinds": only_kinds},
            "diagnostics_in_range": len(relevant_diags),
            "hint": "No actions available. Try widening range, removing only_kinds filter, or check diagnostics first.",
        }, indent=2)

    # Summarize actions
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

    if apply_index is None:
        return _json.dumps({
            "found": True,
            "lsp_server": bridge.command,
            "diagnostics_in_range": len(relevant_diags),
            "actions": summary,
            "hint": "Re-run with apply_index=N to apply. Prefer is_preferred=true actions for safe quick-fixes.",
        }, indent=2)

    if apply_index < 0 or apply_index >= len(actions):
        return _json.dumps({"error": f"apply_index {apply_index} out of range (0..{len(actions)-1})"})

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

    return _json.dumps({
        "applied": True,
        "action": {"title": action.get("title", ""), "kind": action.get("kind", "")},
        "edits_applied": applied_edits,
        "command_result": cmd_result,
    }, indent=2)


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


def register_lsp_tools() -> None:
    """Register code_definition and code_references with the tool registry.

    Called from ``code_intel.py`` to keep registration in one place.
    """
    from tools.registry import registry

    registry.register(
        name="code_definition",
        toolset="code_intel",
        schema=CODE_DEFINITION_SCHEMA,
        handler=_handle_code_definition,
        check_fn=_check_lsp_reqs,
        emoji="📍",
    )

    registry.register(
        name="code_references",
        toolset="code_intel",
        schema=CODE_REFERENCES_SCHEMA,
        handler=_handle_code_references,
        check_fn=_check_lsp_reqs,
        emoji="🔗",
    )

    registry.register(
        name="code_diagnostics",
        toolset="code_intel",
        schema=CODE_DIAGNOSTICS_SCHEMA,
        handler=_handle_code_diagnostics,
        check_fn=_check_lsp_reqs,
        emoji="🩺",
    )

    registry.register(
        name="code_callers",
        toolset="code_intel",
        schema=CODE_CALLERS_SCHEMA,
        handler=_handle_code_callers,
        check_fn=_check_lsp_reqs,
        emoji="📤",
    )

    registry.register(
        name="code_callees",
        toolset="code_intel",
        schema=CODE_CALLEES_SCHEMA,
        handler=_handle_code_callees,
        check_fn=_check_lsp_reqs,
        emoji="📥",
    )

    registry.register(
        name="code_workspace_symbols",
        toolset="code_intel",
        schema=CODE_WORKSPACE_SYMBOLS_SCHEMA,
        handler=_handle_code_workspace_symbols,
        check_fn=_check_lsp_reqs,
        emoji="🔎",
    )

    registry.register(
        name="code_rename",
        toolset="code_intel",
        schema=CODE_RENAME_SCHEMA,
        handler=_handle_code_rename,
        check_fn=_check_lsp_reqs,
        emoji="✏️",
    )

    registry.register(
        name="code_hover",
        toolset="code_intel",
        schema=CODE_HOVER_SCHEMA,
        handler=_handle_code_hover,
        check_fn=_check_lsp_reqs,
        emoji="💡",
    )

    registry.register(
        name="code_type_definition",
        toolset="code_intel",
        schema=CODE_TYPE_DEFINITION_SCHEMA,
        handler=_handle_code_type_definition,
        check_fn=_check_lsp_reqs,
        emoji="🧬",
    )

    registry.register(
        name="code_signatures",
        toolset="code_intel",
        schema=CODE_SIGNATURES_SCHEMA,
        handler=_handle_code_signatures,
        check_fn=_check_lsp_reqs,
        emoji="📝",
    )

    registry.register(
        name="code_action",
        toolset="code_intel",
        schema=CODE_ACTION_SCHEMA,
        handler=_handle_code_action,
        check_fn=_check_lsp_reqs,
        emoji="🔧",
    )

    logger.info("LSP tools registered: code_definition, code_references, code_diagnostics, code_callers, code_callees, code_workspace_symbols, code_rename, code_hover, code_type_definition, code_signatures, code_action")
