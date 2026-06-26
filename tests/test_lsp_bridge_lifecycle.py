"""Tests for LSP Bridge lifecycle: _start_and_init, shutdown, is_alive."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from code_intel.lsp_bridge import (
    _LSP_INIT_TIMEOUT,
    LSPBridge,
)

# =============================================================================
# Helpers: create a bridge with mocked internals
# =============================================================================


def _make_bridge(language_id="python", root="/tmp", command="", args=None) -> LSPBridge:
    """Create an LSPBridge with no real server command."""
    return LSPBridge(
        command=command,
        args=args or [],
        root_uri=root,
        language_id=language_id,
    )


def _make_bridge_with_mocks(language_id="python"):
    """Create a bridge where _send_request and _send_notification are mocked."""
    bridge = _make_bridge(language_id=language_id)
    bridge._alive = True
    bridge._initialized = True
    bridge._process = MagicMock()
    bridge._process.stdin = MagicMock()
    bridge._process.stdout = MagicMock()
    bridge._process.poll.return_value = None
    bridge._send_request = MagicMock()
    bridge._send_notification = MagicMock()
    bridge._last_activity = time.monotonic()
    return bridge


# =============================================================================
# Lifecycle: _start_and_init
# =============================================================================


class TestStartAndInit:
    def test_server_not_found_returns_false(self):
        """If _resolve_command returns None, log and return False."""
        bridge = _make_bridge(command="nonexistent-lsp-server-xyz")
        with patch("code_intel.lsp_bridge._resolve_command", return_value=None):
            result = bridge._start_and_init()
        assert result is False

    def test_init_timeout_returns_false(self, caplog):
        """If initialize request times out, shutdown and return False."""
        bridge = _make_bridge(command="echo", root="/tmp")
        with patch("code_intel.lsp_bridge._resolve_command", return_value="/bin/echo"):
            with patch.object(bridge, "_send_request", return_value=None):
                with patch.object(bridge, "shutdown") as mock_shutdown:
                    result = bridge._start_and_init()
        assert result is False
        mock_shutdown.assert_called_once()

    def test_exception_during_start_returns_false(self, caplog):
        """If startup fails (server exits during startup), return False."""
        bridge = _make_bridge(command="echo")
        with patch("code_intel.lsp_bridge._resolve_command", side_effect=Exception("boom")):
            result = bridge._start_and_init()
        assert result is False

    def test_successful_init(self):
        """Successful init flow: Popen, reader thread, initialize, initialized."""
        bridge = _make_bridge(command="echo", root="/tmp", language_id="python")
        with patch("code_intel.lsp_bridge._resolve_command", return_value="/bin/echo"):
            with patch.object(bridge, "_send_request") as mock_send:
                mock_send.return_value = {
                    "capabilities": {},
                    "serverInfo": {"name": "test-server", "version": "1.0"},
                }
                with patch.object(bridge, "_send_notification") as mock_notify:
                    with patch("subprocess.Popen") as mock_popen:
                        mock_process = MagicMock()
                        mock_process.stdin = MagicMock()
                        mock_process.stdout = MagicMock()
                        mock_process.poll.return_value = None
                        mock_popen.return_value = mock_process
                        with patch("threading.Thread") as mock_thread:
                            mock_thread_instance = MagicMock()
                            mock_thread.return_value = mock_thread_instance
                            result = bridge._start_and_init()

        assert result is True
        assert bridge._initialized is True
        assert bridge._alive is True
        mock_send.assert_called_once()
        call_args, call_kwargs = mock_send.call_args
        assert call_args[0] == "initialize"
        assert call_kwargs.get("timeout") == _LSP_INIT_TIMEOUT
        mock_notify.assert_called_once_with("initialized", {})


# =============================================================================
# Lifecycle: shutdown
# =============================================================================


class TestShutdown:
    def test_shutdown_when_not_alive_does_nothing(self):
        bridge = _make_bridge()
        bridge._alive = False
        bridge.shutdown()

    def test_shutdown_initialized_server(self):
        """Shutdown an initialized server: shutdown request, exit notification, terminate."""
        bridge = _make_bridge(command="test-lsp")
        bridge._alive = True
        bridge._initialized = True
        bridge._pending = {1: threading.Event()}
        bridge._responses = {1: {}}
        bridge._open_documents.add("file:///tmp/test.py")
        bridge._diagnostics_cache["/tmp/test.py"] = []

        mock_process = MagicMock()
        bridge._process = mock_process

        send_req_calls = []
        send_notif_calls = []
        bridge._send_request = lambda m, p, timeout=5: send_req_calls.append((m, p)) or {}
        bridge._send_notification = lambda m, p: send_notif_calls.append((m, p))

        bridge.shutdown()

        assert len(send_req_calls) == 1
        assert send_req_calls[0][0] == "shutdown"
        assert len(send_notif_calls) == 1
        assert send_notif_calls[0][0] == "exit"
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called_once_with(timeout=5)
        assert bridge._process is None
        assert bridge._initialized is False

    def test_shutdown_not_initialized_no_lsp_requests(self):
        """Shutdown without initialization: skip LSP shutdown/exit, just kill."""
        bridge = _make_bridge(command="test-lsp")
        bridge._alive = True
        bridge._initialized = False
        mock_process = MagicMock()
        bridge._process = mock_process

        send_req_calls = []
        send_notif_calls = []
        bridge._send_request = lambda m, p, timeout=5: send_req_calls.append((m, p)) or {}
        bridge._send_notification = lambda m, p: send_notif_calls.append((m, p))

        bridge.shutdown()

        assert len(send_req_calls) == 0
        assert len(send_notif_calls) == 0

    def test_shutdown_terminate_fails_kills(self):
        """If terminate fails, fall back to kill."""
        bridge = _make_bridge(command="test-lsp")
        bridge._alive = True
        bridge._initialized = True
        mock_process = MagicMock()
        mock_process.terminate.side_effect = Exception("terminate failed")
        bridge._process = mock_process

        bridge._send_request = lambda m, p, timeout=5: {}
        bridge._send_notification = lambda m, p: None

        bridge.shutdown()

        mock_process.kill.assert_called_once()

    def test_shutdown_clears_state(self):
        """Shutdown clears pending, responses, open_documents, diagnostics_cache."""
        bridge = _make_bridge()
        bridge._alive = True
        bridge._initialized = True
        bridge._pending = {1: threading.Event()}
        bridge._responses = {1: {}}
        bridge._open_documents.add("file:///tmp/test.py")
        bridge._diagnostics_cache["/tmp/test.py"] = []
        mock_process = MagicMock()
        bridge._process = mock_process
        bridge._send_request = lambda m, p, timeout=5: {}
        bridge._send_notification = lambda m, p: None

        bridge.shutdown()

        assert len(bridge._pending) == 0
        assert len(bridge._responses) == 0
        assert len(bridge._open_documents) == 0
        assert len(bridge._diagnostics_cache) == 0


# =============================================================================
# Lifecycle: is_alive
# =============================================================================


class TestIsAlive:
    def test_not_alive_returns_false(self):
        bridge = _make_bridge()
        bridge._alive = False
        assert bridge.is_alive is False

    def test_no_process_returns_false(self):
        bridge = _make_bridge()
        bridge._alive = True
        bridge._process = None
        assert bridge.is_alive is False

    def test_process_terminated_returns_false(self):
        bridge = _make_bridge()
        bridge._alive = True
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # process terminated
        bridge._process = mock_process
        assert bridge.is_alive is False

    def test_idle_timeout_shuts_down_and_returns_false(self):
        bridge = _make_bridge(command="test-lsp")
        bridge._alive = True
        bridge._last_activity = time.monotonic() - 99999  # Very old
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        bridge._process = mock_process
        with patch.object(bridge, "shutdown") as mock_shutdown:
            result = bridge.is_alive
        assert result is False
        mock_shutdown.assert_called_once()

    def test_alive_returns_true(self):
        bridge = _make_bridge()
        bridge._alive = True
        bridge._last_activity = time.monotonic()
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        bridge._process = mock_process
        assert bridge.is_alive is True
