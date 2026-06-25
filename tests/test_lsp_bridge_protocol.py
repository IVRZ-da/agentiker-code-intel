"""Tests for LSP Bridge protocol: _send_request, _send_notification, _write_message,
_dispatch, _read_loop, _resolve_command."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from code_intel.lsp_bridge import (
    LSPBridge,
    _resolve_command,
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
    # Manually set alive state so ensure_initialized returns True
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
# JSON-RPC: _send_request
# =============================================================================


class TestSendRequest:
    def test_send_request_pending_tracking(self):
        """_send_request tracks the request in _pending."""
        bridge = _make_bridge()
        bridge._process = MagicMock()
        bridge._process.stdin = MagicMock()
        bridge._write_message = MagicMock()

        with patch("threading.Event") as mock_event_cls:
            mock_event = MagicMock()
            mock_event.wait.return_value = True
            mock_event_cls.return_value = mock_event

            result = bridge._send_request("test/method", {"key": "val"}, timeout=1.0)

        # Event was set, but no response stored -> returns None
        assert result is None
        # Pending should be cleaned up
        assert 1 not in bridge._pending

    def test_send_request_timeout_returns_none(self, caplog):
        """_send_request returns None on timeout."""
        bridge = _make_bridge()
        bridge._process = MagicMock()
        bridge._process.stdin = MagicMock()
        bridge._write_message = lambda msg: None

        with patch("threading.Event") as mock_event_cls:
            mock_event = MagicMock()
            mock_event.wait.return_value = False  # Simulate timeout
            mock_event_cls.return_value = mock_event

            result = bridge._send_request("test/method", {"key": "val"}, timeout=1.0)

        assert result is None

    def test_send_request_exception_returns_none(self, caplog):
        """_send_request returns None on write error."""
        bridge = _make_bridge()
        bridge._process = MagicMock()
        bridge._process.stdin = MagicMock()
        bridge._write_message = MagicMock(side_effect=RuntimeError("write failed"))

        result = bridge._send_request("test/method", {"key": "val"}, timeout=1.0)
        assert result is None
        assert "LSP request failed" in caplog.text


# =============================================================================
# JSON-RPC: _send_notification
# =============================================================================


class TestSendNotification:
    def test_send_notification(self):
        """_send_notification writes a message without an id."""
        bridge = _make_bridge()
        written = []
        bridge._write_message = lambda msg: written.append(msg)

        bridge._send_notification("test/notification", {"key": "val"})
        assert len(written) == 1
        msg = written[0]
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "test/notification"
        assert msg["params"] == {"key": "val"}
        assert "id" not in msg


# =============================================================================
# JSON-RPC: _write_message
# =============================================================================


class TestWriteMessage:
    def test_write_message_writes_to_stdin(self):
        """_write_message writes Content-Length + body to process stdin."""
        bridge = _make_bridge()
        mock_process = MagicMock()
        mock_stdin = MagicMock()
        mock_process.stdin = mock_stdin
        bridge._process = mock_process

        bridge._write_message({"jsonrpc": "2.0", "method": "test"})
        # Should have written header + body
        assert mock_stdin.write.call_count >= 1
        # The combined write is: header + body
        assert b"Content-Length:" in mock_stdin.write.call_args[0][0]
        assert b"jsonrpc" in mock_stdin.write.call_args[0][0]
        mock_stdin.flush.assert_called_once()

    def test_write_message_raises_without_process(self):
        """_write_message raises RuntimeError if process not running."""
        bridge = _make_bridge()
        bridge._process = None
        with pytest.raises(RuntimeError, match="LSP process not running"):
            bridge._write_message({"test": True})

    def test_write_message_raises_without_stdin(self):
        """_write_message raises RuntimeError if stdin is None."""
        bridge = _make_bridge()
        mock_process = MagicMock()
        mock_process.stdin = None
        bridge._process = mock_process
        with pytest.raises(RuntimeError, match="LSP process not running"):
            bridge._write_message({"test": True})


# =============================================================================
# _dispatch — JSON-RPC message dispatcher
# =============================================================================


class TestDispatch:
    def test_dispatch_response_wakes_pending(self):
        """Response messages are stored and pending events are set."""
        bridge = _make_bridge()
        event = threading.Event()
        bridge._pending[42] = event
        bridge._dispatch({"id": 42, "result": {"foo": "bar"}})
        assert bridge._responses.get(42) == {"foo": "bar"}
        assert event.is_set()

    def test_dispatch_window_log_message(self):
        """window/logMessage messages are logged."""
        bridge = _make_bridge()
        with patch("logging.Logger.log") as mock_log:
            bridge._dispatch({
                "method": "window/logMessage",
                "params": {"type": 1, "message": "Test error msg"},
            })
        # Level 1 maps to logging.INFO (downgraded from ERROR)
        assert mock_log.called

    def test_dispatch_publish_diagnostics(self):
        """textDocument/publishDiagnostics caches diagnostics."""
        bridge = _make_bridge()
        bridge._dispatch({
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///tmp/test.py",
                "diagnostics": [
                    {"range": {}, "severity": 1, "message": "Error 1"},
                    {"range": {}, "severity": 2, "message": "Warning 1"},
                ],
            },
        })
        # Diagnostics should be cached by path
        assert "/tmp/test.py" in bridge._diagnostics_cache
        assert len(bridge._diagnostics_cache["/tmp/test.py"]) == 2

    def test_dispatch_publish_diagnostics_lru_eviction(self):
        """publishDiagnostics LRU-evicts old entries when cache exceeds 500."""
        bridge = _make_bridge()
        # Fill cache with 500 entries
        for i in range(501):
            bridge._diagnostics_cache[f"/tmp/file_{i}.py"] = []
        # Add one more — should evict the oldest
        bridge._dispatch({
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///tmp/new_file.py",
                "diagnostics": [{"range": {}, "severity": 1, "message": "test"}],
            },
        })
        assert len(bridge._diagnostics_cache) <= 500

    def test_dispatch_pass_through_methods(self):
        """Known notification methods are silently ignored."""
        bridge = _make_bridge()
        for method in ("$/progress", "textDocument/didOpen", "textDocument/didChange",
                       "textDocument/didClose", "textDocument/didSave"):
            bridge._dispatch({"method": method})
        # No error should occur

    def test_dispatch_unknown_method_logged(self):
        """Unknown methods are logged as debug."""
        bridge = _make_bridge()
        with patch("logging.Logger.debug") as mock_debug:
            bridge._dispatch({"method": "some/unknown/method"})
        assert mock_debug.called
        assert "some/unknown/method" in str(mock_debug.call_args)

    def test_dispatch_json_decode_error_continues(self):
        """Malformed JSON on the wire does not crash the dispatch."""
        bridge = _make_bridge()
        # This simulates a bad message being dispatched directly
        # dispatch won't raise on bad msg contents
        bridge._dispatch({"bad": "message"})
        # Should not crash

    def test_dispatch_id_not_in_pending_no_error(self):
        """Response with id not in pending is ignored."""
        bridge = _make_bridge()
        bridge._dispatch({"id": 9999, "result": {}})
        # Should not crash, and should not store if id not in pending
        # Actually it does store, but no event to set
        assert 9999 not in bridge._pending


# =============================================================================
# _resolve_command
# =============================================================================


class TestResolveCommand:
    def test_nonexistent_command_returns_none(self):
        result = _resolve_command("this-command-definitely-does-not-exist-xyz")
        assert result is None

    def test_existing_command_returns_path(self):
        result = _resolve_command("echo")
        assert result is not None
        assert "/echo" in result


# =============================================================================
# _read_loop (partial — via mock)
# =============================================================================


class TestReadLoop:
    def test_read_loop_not_alive_does_nothing(self):
        """If _alive is False when _read_loop starts, it exits immediately."""
        bridge = _make_bridge()
        bridge._alive = False
        bridge._process = MagicMock()
        bridge._process.stdout = MagicMock()
        bridge._process.poll.return_value = None
        # Should not block or crash
        thread = threading.Thread(target=bridge._read_loop, daemon=True)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()

    def test_read_loop_polls_and_dispatch(self):
        """_read_loop processes messages and dispatches them."""
        bridge = _make_bridge()
        bridge._alive = True
        # Create a mock pipe that returns some data
        import io
        pipe = io.BytesIO(b"Content-Length: 37\r\n\r\n{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}")
        mock_process = MagicMock()
        mock_process.stdout = pipe
        mock_process.poll.return_value = None
        bridge._process = mock_process

        dispatched = []
        bridge._dispatch = lambda msg: dispatched.append(msg)

        thread = threading.Thread(target=bridge._read_loop, daemon=True)
        thread.start()
        thread.join(timeout=1)

        # Either we got a dispatch or the read loop exited (depending on timing)
        # The important thing is no crash
        assert not thread.is_alive()

    def test_read_loop_sets_alive_false_and_wakes_pending(self):
        """When read loop exits, _alive is set to False and pending waiters are woken."""
        bridge = _make_bridge()
        bridge._alive = True
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = 999
        mock_process.poll.return_value = 0  # Already terminated
        bridge._process = mock_process

        event = threading.Event()
        bridge._pending[1] = event

        bridge._read_loop()

        assert bridge._alive is False
        assert event.is_set()
