"""Tests for LSP Bridge lifecycle: _build_env, _get_initialization_options,
ensure_initialized, _start_and_init, shutdown, is_alive, circuit breaker, resource limits."""

from __future__ import annotations

import os
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
# Lifecycle: _build_env
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
# Lifecycle: _get_initialization_options
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
# Lifecycle: ensure_initialized
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
        # Should have sent initialize request and initialized notification
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
        # Should not crash
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
        bridge._send_request = lambda m, p, timeout=5: (
            send_req_calls.append((m, p)) or {}
        )
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
        bridge._send_request = lambda m, p, timeout=5: (
            send_req_calls.append((m, p)) or {}
        )
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


# =============================================================================
# Circuit Breaker
# =============================================================================


class TestCircuitBreaker:
    """Tests for LSPBridge circuit breaker (_record_lsp_failure, _lsp_circuit_open)."""

    def test_circuit_breaker_starts_closed(self):
        """A fresh bridge must have the circuit breaker closed."""
        import tempfile

        from code_intel.lsp_bridge import LSPBridge
        bridge = LSPBridge(
            command="test", args=[], root_uri=tempfile.mkdtemp(), language_id="python",
        )
        assert bridge._lsp_circuit_open() is False
        assert bridge._failure_count == 0

    def test_circuit_breaker_opens_after_threshold(self):
        """After N failures, the circuit breaker must open."""
        import tempfile

        from code_intel.lsp_bridge import LSPBridge
        bridge = LSPBridge(
            command="test", args=[], root_uri=tempfile.mkdtemp(), language_id="python",
        )
        # Record failures up to threshold
        for _ in range(bridge._CIRCUIT_THRESHOLD):
            bridge._record_lsp_failure()
        assert bridge._lsp_circuit_open() is True

    def test_circuit_breaker_backoff_increases(self):
        """Each failure beyond threshold must increase backoff."""
        import tempfile
        import time

        from code_intel.lsp_bridge import LSPBridge
        bridge = LSPBridge(
            command="test", args=[], root_uri=tempfile.mkdtemp(), language_id="python",
        )
        # Record threshold+1 failures
        for _ in range(bridge._CIRCUIT_THRESHOLD + 1):
            bridge._record_lsp_failure()
        # First backoff should be 2^1 * base = 2*30 = 60s
        expected = bridge._CIRCUIT_BACKOFF_BASE * (2 ** 1)
        remaining = bridge._circuit_open_until - time.monotonic()
        assert remaining > expected - 5, f"Expected ~{expected}s backoff, got ~{remaining:.0f}s"

    def test_circuit_breaker_resets_after_backoff(self):
        """After the backoff period expires, the circuit must close."""
        import tempfile
        import time

        from code_intel.lsp_bridge import LSPBridge
        bridge = LSPBridge(
            command="test", args=[], root_uri=tempfile.mkdtemp(), language_id="python",
        )
        for _ in range(bridge._CIRCUIT_THRESHOLD):
            bridge._record_lsp_failure()
        assert bridge._lsp_circuit_open() is True
        # Manually set backoff to expired
        bridge._circuit_open_until = time.monotonic() - 1
        assert bridge._lsp_circuit_open() is False
        assert bridge._failure_count == 0

    def test_circuit_breaker_ensure_initialized_skips_when_open(self):
        """ensure_initialized() must return False when circuit is open."""
        import tempfile

        from code_intel.lsp_bridge import LSPBridge
        bridge = LSPBridge(
            command="nonexistent", args=[], root_uri=tempfile.mkdtemp(), language_id="python",
        )
        # Force circuit open
        bridge._circuit_open_until = 1e18  # Far in the future
        assert bridge.ensure_initialized() is False


# =============================================================================
# Resource Limits
# =============================================================================


class TestResourceLimits:
    """Tests for resource limits in _start_and_init."""

    def test_nonexistent_binary_returns_false(self):
        """Starting a nonexistent binary must return False."""
        import tempfile

        from code_intel.lsp_bridge import LSPBridge
        bridge = LSPBridge(
            command="/nonexistent-binary-xy12",
            args=[], root_uri=tempfile.mkdtemp(), language_id="python",
        )
        result = bridge.ensure_initialized()
        assert result is False

    def test_resource_limits_import(self):
        """The resource module must be importable."""
        import resource
        # Sanity check: setrlimit symbols exist
        assert hasattr(resource, 'RLIMIT_AS')
        assert hasattr(resource, 'RLIMIT_RSS')
        assert hasattr(resource, 'RLIMIT_CPU')
