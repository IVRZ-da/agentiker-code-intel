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
    """For TypeScript files, find the nearest directory containing ``tsconfig.json``.

    TSServer needs ``rootUri`` to point at the tsconfig directory (not the
    monorepo root) for correct cross-file resolution within a single project.
    """
    p = Path(file_path).resolve().parent
    for _ in range(30):
        if (p / "tsconfig.json").exists():
            return str(p)
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


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
            self._process = subprocess.Popen(
                [cmd_path] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
                    }
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
                logger.error("LSP initialize timed out")
                self.shutdown()
                return False

            # Send initialized notification
            self._send_notification("initialized", {})
            self._initialized = True
            logger.info("LSP server initialized: %s", self.command)
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
        try:
            self._write_message({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            })
            if event.wait(timeout=timeout):
                resp = self._responses.pop(req_id, None)
                return resp
            else:
                logger.warning("LSP request timed out: %s (id=%d)", method, req_id)
                self._pending.pop(req_id, None)
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
        """Write a JSON-RPC message in LSP wire format (Content-Length header)."""
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
            if method in ("window/logMessage", "textDocument/publishDiagnostics",
                          "$/progress", "textDocument/didOpen", "textDocument/didChange",
                          "textDocument/didClose", "textDocument/didSave"):
                # Ignore most server notifications for now
                pass
            else:
                logger.debug("LSP notification: %s", method)

    # -- LSP operations ------------------------------------------------------

    def open_document(self, file_path: str, content: Optional[str] = None) -> None:
        """Tell the LSP server to open a document."""
        if content is None:
            try:
                content = Path(file_path).read_text("utf-8", errors="replace")
            except OSError:
                return
        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": f"file://{file_path}",
                "languageId": self.language_id,
                "version": 1,
                "text": content,
            }
        })

    def close_document(self, file_path: str) -> None:
        """Tell the LSP server to close a document."""
        self._send_notification("textDocument/didClose", {
            "textDocument": {
                "uri": f"file://{file_path}",
            }
        })

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

        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })

        normalized = self._normalize_locations(result)

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
            if def_path == file_path and def_line == line:
                td_result = self._send_request("textDocument/typeDefinition", {
                    "textDocument": {"uri": f"file://{file_path}"},
                    "position": {"line": line, "character": character},
                })
                td_normalized = self._normalize_locations(td_result)
                if td_normalized:
                    # Prefer typeDefinition result (points to actual class/interface)
                    normalized = td_normalized

        # TS server sometimes returns stale/empty results on first request
        # Retry once after a short delay if no locations found
        if not normalized and self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
            result2 = self._send_request("textDocument/definition", {
                "textDocument": {"uri": f"file://{file_path}"},
                "position": {"line": line, "character": character},
            })
            normalized = self._normalize_locations(result2)

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

        result = self._send_request("textDocument/references", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        })

        normalized = self._normalize_locations(result)

        # TS server sometimes returns empty results on first request
        if not normalized and self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
            result2 = self._send_request("textDocument/references", {
                "textDocument": {"uri": f"file://{file_path}"},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            })
            normalized = self._normalize_locations(result2)

        return normalized

    def hover(self, file_path: str, line: int, character: int) -> Optional[dict]:
        """Request 'textDocument/hover' from the LSP server."""
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
        if self.language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            time.sleep(0.5)
        else:
            time.sleep(0.05)

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

    # -- helpers -------------------------------------------------------------

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
            return None

        root = _find_workspace_root(file_path)

        # For TS/JS, use tsconfig directory as rootUri for better resolution
        ts_root = None
        if language_id in ("typescript", "typescriptreact", "javascript", "javascriptreact"):
            ts_root = _find_tsconfig_root(file_path)
            if ts_root:
                root = ts_root

        key = (language_id, root)
        ws_folders = self._get_workspace_folders(_find_workspace_root(file_path)) if ts_root else self._get_workspace_folders(root)

        with self._lock:
            # Check for existing bridge
            if key in self._bridges:
                bridge = self._bridges[key]
                if bridge.is_alive:
                    # Move to end (LRU)
                    self._bridges.move_to_end(key)
                    return bridge
                else:
                    del self._bridges[key]

            # Try each server config
            for cfg in server_configs:
                cmd = cfg["command"]
                if _resolve_command(cmd) is None:
                    logger.debug("LSP server not found: %s", cmd)
                    continue

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
    return lang_map.get(ext)


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
        "file": path,
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

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge and bridge.ensure_initialized():
            locations = bridge.goto_definition(str(target), lsp_line, lsp_char)
            if locations:
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

    # Fallback: AST-based definition search
    return _ast_fallback_definition(str(target), line, character, lang)


def code_references_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
    include_declaration: bool = True,
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

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge and bridge.ensure_initialized():
            locations = bridge.find_references(
                str(target), lsp_line, lsp_char, include_declaration
            )
            if locations:
                refs = [_location_to_dict(loc) for loc in locations]
                # Group by file
                by_file: Dict[str, List[dict]] = {}
                for r in refs:
                    by_file.setdefault(r["file"], []).append(r)

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

    # Fallback: AST-based references search
    return _ast_fallback_references(str(target), line, character, lang)


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

    try:
        from tools.code_intel import detect_language as _detect, code_search_tool
    except ImportError:
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": "LSP server unavailable and code_intel not importable.",
            "suggestion": "Install a language server: pip install pyright or pylsp",
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
        return _json.dumps({
            "path": file_path,
            "method": "fallback",
            "warning": "LSP server unavailable and code_intel not importable.",
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

    logger.info("LSP tools registered: code_definition, code_references")
