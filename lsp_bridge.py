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
        # pylsp — pure Python, widely available
        {"command": "pylsp", "args": [], "language_id": "python"},
        # pyright-langserver — excellent type resolution (install via npm)
        {"command": "pyright-langserver", "args": ["--stdio"], "language_id": "python"},
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_workspace_root(file_path: str) -> str:
    """Best-effort workspace root discovery for *file_path*.

    Walks up from the file's directory looking for common project markers.
    """
    p = Path(file_path).resolve().parent
    markers = (
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
    for _ in range(40):  # max depth guard
        for m in markers:
            if (p / m).exists():
                return str(p)
        parent = p.parent
        if parent == p:
            break
        p = parent
    # Fallback: the file's parent directory
    return str(Path(file_path).resolve().parent)


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
                env={**os.environ, "PYRIGHT_PYTHON_FORCE_VERSION": ""},
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
            init_result = self._send_request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": root_uri,
                    "rootPath": self.root_uri,
                    "capabilities": {
                        "textDocument": {
                            "definition": {"dynamicRegistration": False},
                            "references": {"dynamicRegistration": False},
                            "hover": {"dynamicRegistration": False, "contentFormat": ["plaintext", "markdown"]},
                            "typeDefinition": {"dynamicRegistration": False},
                        }
                    },
                },
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
        time.sleep(0.05)

        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        })

        return self._normalize_locations(result)

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
        time.sleep(0.05)

        result = self._send_request("textDocument/references", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        })

        return self._normalize_locations(result)

    def hover(self, file_path: str, line: int, character: int) -> Optional[dict]:
        """Request 'textDocument/hover' from the LSP server."""
        if not self.ensure_initialized():
            return None

        self.open_document(file_path)
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
    """

    def __init__(self) -> None:
        self._bridges: OrderedDict[Tuple[str, str], LSPBridge] = OrderedDict()
        self._lock = threading.Lock()

    def get_bridge(
        self, language_id: str, file_path: str
    ) -> Optional[LSPBridge]:
        """Get or create an LSP bridge for the given language and file.

        Returns ``None`` if no suitable language server is available.
        """
        server_configs = _LANGUAGE_SERVERS.get(language_id)
        if not server_configs:
            return None

        root = _find_workspace_root(file_path)
        key = (language_id, root)

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
                )
                self._bridges[key] = bridge
                # Evict oldest if we have too many
                while len(self._bridges) > 5:
                    oldest_key, oldest_bridge = next(iter(self._bridges.items()))
                    oldest_bridge.shutdown()
                    del self._bridges[oldest_key]
                return bridge

        return None

    def shutdown_all(self) -> None:
        """Shut down all active bridges."""
        with self._lock:
            for bridge in self._bridges.values():
                bridge.shutdown()
            self._bridges.clear()


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

    # Read context
    context_lines = _read_context_lines(path, line, context=3)
    # Find the symbol text (best-effort: first non-empty line of context)
    symbol_text = ""
    for cl in context_lines:
        stripped = cl.strip()
        if stripped:
            symbol_text = stripped[:200]
            break

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
    """Find the column of the first identifier on *line* (0-based)."""
    try:
        lines = Path(file_path).read_text("utf-8", errors="replace").split("\n")
        if line < 0 or line >= len(lines):
            return None
        text = lines[line]
        # Find first word-like token
        for i, ch in enumerate(text):
            if ch.isalpha() or ch == '_':
                return i + 1  # 1-based
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
        "Uses pyright/pylsp for Python (cross-file resolution). "
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
        "Uses pyright/pylsp for Python (cross-file resolution). "
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
