"""Tests for LSP Bridge environment setup: _build_env, _get_initialization_options,
ensure_initialized, _send_request, _send_notification."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

from code_intel.lsp_bridge import (
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
# _build_env
# =============================================================================


class TestBuildEnv:
    def test_build_env_python(self):
        """_build_env adds PYTHONWARNINGS for Python."""
        bridge = _make_bridge(language_id="python")
        env = bridge._build_env()
        assert "PYTHONWARNINGS" in env
        assert env["PYTHONWARNINGS"] == "ignore"
        # Should NOT have TSS_LOG for Python
        assert "TSS_LOG" not in env
        # Should copy from os.environ
        for key in ("PATH", "HOME"):
            if key in os.environ:
                assert env[key] == os.environ[key]

    def test_build_env_typescript(self):
        """_build_env adds TSS_LOG for TypeScript."""
        bridge = _make_bridge(language_id="typescript")
        env = bridge._build_env()
        assert "TSS_LOG" in env
        assert env["TSS_LOG"] == "-"

    def test_build_env_javascript(self):
        """_build_env adds TSS_LOG for JavaScript."""
        bridge = _make_bridge(language_id="javascript")
        env = bridge._build_env()
        assert "TSS_LOG" in env

    def test_build_env_javascriptreact(self):
        """_build_env adds TSS_LOG for JSX."""
        bridge = _make_bridge(language_id="javascriptreact")
        env = bridge._build_env()
        assert "TSS_LOG" in env

    def test_build_env_unknown_language(self):
        """_build_env works for unknown languages without adding special vars."""
        bridge = _make_bridge(language_id="go")
        env = bridge._build_env()
        assert "PYTHONWARNINGS" not in env
        assert "TSS_LOG" not in env


# =============================================================================
# _get_initialization_options
# =============================================================================


class TestGetInitializationOptions:
    def test_typescript_options(self):
        bridge = _make_bridge(language_id="typescript")
        opts = bridge._get_initialization_options()
        assert "preferences" in opts
        assert opts.get("maxTsServerMemory") == 8192

    def test_typescriptreact_options(self):
        bridge = _make_bridge(language_id="typescriptreact")
        opts = bridge._get_initialization_options()
        assert "preferences" in opts

    def test_javascript_options(self):
        bridge = _make_bridge(language_id="javascript")
        opts = bridge._get_initialization_options()
        assert "preferences" in opts

    def test_python_options(self):
        bridge = _make_bridge(language_id="python")
        opts = bridge._get_initialization_options()
        assert "python" in opts
        assert opts["python"]["analysis"]["diagnosticMode"] == "openFilesOnly"

    def test_unknown_language_returns_empty_dict(self):
        bridge = _make_bridge(language_id="go")
        opts = bridge._get_initialization_options()
        assert opts == {}


# =============================================================================
# ensure_initialized
# =============================================================================


class TestEnsureInitialized:
    def test_already_initialized_returns_true(self):
        """If _alive and _initialized, return True immediately."""
        bridge = _make_bridge()
        bridge._alive = True
        bridge._initialized = True
        assert bridge.ensure_initialized() is True

    def test_alive_but_not_initialized_calls_shutdown_and_start(self):
        """If _alive but not _initialized, shutdown then start."""
        bridge = _make_bridge()
        bridge._alive = True
        bridge._initialized = False
        shutdown_called = []
        bridge.shutdown = lambda: shutdown_called.append(True)
        bridge._start_and_init = lambda: False
        result = bridge.ensure_initialized()
        assert result is False
        assert len(shutdown_called) == 1

    def test_not_alive_calls_start_and_init(self):
        """If not _alive, call _start_and_init."""
        bridge = _make_bridge()
        bridge._alive = False
        bridge._start_and_init = lambda: True
        result = bridge.ensure_initialized()
        assert result is True
        # _initialized should be set by mock, but ensure_initialized returns start_and_init's result
        # Since our mock returns True, result should be True


# =============================================================================
# JSON-RPC: _send_request, _send_notification
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
