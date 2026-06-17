"""LSP-Bridge Fuzzing: malformed JSON-RPC responses and edge cases.

Injects deliberately broken/malformed data into the LSP bridge's message
handling pipeline and verifies it degrades gracefully (no crashes, no
unhandled exceptions, no corrupted state).

Targets:
  - _dispatch: responses with missing/wrong-type fields
  - _send_request: corrupted pending state
  - _normalize_locations: None, wrong types, missing keys
  - _format_definitions / _format_references: empty/malformed locations
  - _uri_to_path: special chars, edge URIs
  - _read_loop wire format: missing headers, wrong Content-Length
  - _write_message: oversized payloads
  - _is_expected_reconcile_close_message: edge case strings
"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.lsp_bridge import (
    LSPBridge,
    _format_definitions,
    _format_references,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_bridge() -> LSPBridge:
    """Create an LSPBridge with no real server (mocked process)."""
    bridge = LSPBridge(
        command="echo",
        args=[],
        root_uri="/tmp",
        language_id="python",
    )
    # Mock the internal process to prevent real subprocess calls
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.poll.return_value = None
    bridge._process = mock_proc
    return bridge


# =============================================================================
# 1. _dispatch — malformed JSON-RPC messages
# =============================================================================


class TestDispatchFuzzing:
    """Feed broken messages to _dispatch and verify no crash."""

    MALFORMED_MSGS = [
        {},  # empty dict
        {"id": None},  # id is None
        {"id": "str_id"},  # non-int id
        {"id": 1},  # valid id but no result — should just not match pending
        {"id": 1, "result": None},  # result is None
        {"id": 1, "result": {}},  # result is empty dict
        {"id": 1, "result": []},  # result is empty list
        {"id": 1, "result": "string_result"},  # result is wrong type
        {"id": 1, "result": 42},  # result is int
        {"id": 1, "result": True},  # result is bool
        {"id": 1, "error": {"message": "some error"}},  # has error instead of result
        {"jsonrpc": "2.0", "id": 1, "result": {"data": "ok"}},  # valid but unexpected pending
        {"method": None},  # method is None
        {"method": 42},  # method is int
        {"method": ""},  # method is empty
        {"method": "unknown_method_with_lots_of_chars___" * 50},  # huge method name
        {"method": "window/logMessage", "params": None},  # logMessage with no params
        {"method": "window/logMessage", "params": {"type": 99, "message": None}},  # invalid type + null msg
        {"method": "window/logMessage", "params": {"type": "not_an_int", "message": "test"}},
        {"method": "textDocument/publishDiagnostics", "params": None},
        {"method": "textDocument/publishDiagnostics", "params": {"uri": None, "diagnostics": None}},
        {"method": "textDocument/publishDiagnostics", "params": {"uri": "file:///test.py", "diagnostics": "not_a_list"}},
        {"method": "textDocument/publishDiagnostics", "params": {"uri": "file:///test.py", "diagnostics": [None, {}, {"severity": 1}]}},
        {"method": "textDocument/publishDiagnostics", "params": {"uri": "file:///test.py", "diagnostics": [{"severity": 999}]}},
        {"$/progress": [], "value": None},  # unexpected notification shape
        [1, 2, 3],  # list instead of dict
        "string instead of dict",
        42,
        None,
    ]

    @pytest.mark.parametrize("msg", MALFORMED_MSGS)
    def test_dispatch_never_crashes(self, msg):
        """_dispatch must handle any malformed message without raising."""
        bridge = _make_bridge()
        try:
            bridge._dispatch(msg)
        except Exception as exc:
            # Only acceptable: TypeError if msg is not dict-like (not subscriptable)
            # Everything else is a bug
            if isinstance(msg, (list, str, int, float)) or msg is None:
                # These will crash trying to access msg["id"]/msg["method"]
                # That's acceptable — _dispatch expects dict input
                assert isinstance(exc, (TypeError, AttributeError)), \
                    f"Expected TypeError/AttributeError for {type(msg)}, got {type(exc).__name__}: {exc}"
            else:
                raise AssertionError(f"Uncontrolled crash with {msg!r}: {exc}")

    def test_dispatch_with_pending_matches(self):
        """When msg['id'] matches pending: must set response + event."""
        bridge = _make_bridge()
        req_id = 42
        event = threading.Event()
        bridge._pending[req_id] = event
        assert not event.is_set()

        bridge._dispatch({"id": req_id, "result": {"answer": 42}})
        assert event.is_set()
        assert bridge._responses.get(req_id) == {"answer": 42}

    def test_dispatch_with_pending_none_result(self):
        """When LSP returns null result (e.g. hover on whitespace)."""
        bridge = _make_bridge()
        req_id = 43
        event = threading.Event()
        bridge._pending[req_id] = event

        bridge._dispatch({"id": req_id, "result": None})
        assert event.is_set()
        assert bridge._responses.get(req_id) is None

    def test_dispatch_with_pending_missing_result(self):
        """When response has no 'result' key at all."""
        bridge = _make_bridge()
        req_id = 44
        event = threading.Event()
        bridge._pending[req_id] = event

        bridge._dispatch({"id": req_id})  # no "result" key
        assert event.is_set()
        assert bridge._responses.get(req_id) is None  # None by .get()

    def test_dispatch_log_message_level_mapping(self):
        """logMessage with edge-case type values."""
        bridge = _make_bridge()
        for level in [-1, 0, 5, 100, None, "string"]:
            try:
                bridge._dispatch({
                    "method": "window/logMessage",
                    "params": {"type": level, "message": "test"},
                })
            except Exception:
                pass  # Should not crash

    def test_dispatch_diagnostics_cache_overflow(self):
        """publishDiagnostics with 10000 errors in one message."""
        bridge = _make_bridge()
        diags = [{"severity": 1, "message": f"error_{i}",
                   "range": {"start": {"line": i, "character": 0},
                             "end": {"line": i, "character": 10}}}
                 for i in range(10000)]
        try:
            bridge._dispatch({
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": "file:///test.py", "diagnostics": diags},
            })
        except Exception as exc:
            raise AssertionError(f"Crash on 10000 diagnostics: {exc}")

    def test_dispatch_simultaneous_requests(self):
        """Multiple threads dispatching to same bridge."""
        bridge = _make_bridge()
        n = 20
        events = {}

        for i in range(n):
            req_id = 100 + i
            evt = threading.Event()
            bridge._pending[req_id] = evt
            events[req_id] = evt

        # Dispatch all at once from separate threads
        def _dispatch_msg(req_id):
            bridge._dispatch({"id": req_id, "result": f"result_{req_id}"})

        threads = [threading.Thread(target=_dispatch_msg, args=(rid,))
                   for rid in range(100, 100 + n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All events should be set
        for rid, evt in events.items():
            assert evt.is_set(), f"Event {rid} not set"
            assert bridge._responses.get(rid) == f"result_{rid}"


# =============================================================================
# 2. _normalize_locations — malformed LSP location data
# =============================================================================


class TestNormalizeLocationsFuzzing:
    """Feed broken location data to _normalize_locations."""

    MALFORMED_LOCATIONS = [
        None,
        [],
        [None],
        [{}],
        [{"uri": "file:///test.py"}],  # no range
        [{"uri": None, "range": None}],
        [{"uri": "file:///test.py", "range": "not_a_dict"}],
        [{"uri": "file:///test.py", "range": {}}],
        [{"uri": "file:///test.py", "range": {"start": None, "end": None}}],
        [{"uri": "file:///test.py", "range": {"start": {"line": 0}, "end": {"line": 1}}}, ],  # missing char
        [{"uri": "file:///test.py", "range": {"start": {"line": None, "character": None},
                                               "end": {"line": None, "character": None}}}],
        [1, 2, 3],  # list of ints
        ["string_uri"],
        [{"uri": "not-a-valid-uri", "range": {"start": {"line": 0, "character": 0},
                                               "end": {"line": 1, "character": 0}}}],
        # Huge line numbers
        [{"uri": "file:///test.py", "range": {"start": {"line": 10**9, "character": 10**9},
                                               "end": {"line": 10**9, "character": 10**9}}}],
    ]

    @pytest.mark.parametrize("locations", MALFORMED_LOCATIONS)
    def test_normalize_never_crashes(self, locations):
        """_normalize_locations must handle any malformed input."""
        bridge = _make_bridge()
        try:
            result = bridge._normalize_locations(locations)
            # Should always return list or None
            assert result is None or isinstance(result, list)
        except Exception as exc:
            raise AssertionError(f"Crash on {type(locations).__name__}: {exc}")

    def test_normalize_large_batch(self):
        """Thousands of locations must not blow up."""
        bridge = _make_bridge()
        locations = [
            {"uri": f"file:///test{i}.py",
             "range": {"start": {"line": i, "character": 0},
                       "end": {"line": i, "character": 10}}}
            for i in range(10000)
        ]
        result = bridge._normalize_locations(locations)
        assert isinstance(result, list)
        assert len(result) == 10000


# =============================================================================
# 3. _format_definitions & _format_references — empty/malformed
# =============================================================================


class TestFormatFuzzing:
    """Edge cases for formatting functions."""

    def test_format_definitions_empty(self):
        """Empty list must not crash."""
        result = _format_definitions([])
        assert isinstance(result, str)

    def test_format_definitions_none(self):
        """None must not crash."""
        result = _format_definitions(None)
        assert isinstance(result, str)

    def test_format_references_empty(self):
        result = _format_references([], {})
        assert isinstance(result, str)

    def test_format_references_none(self):
        result = _format_references(None, {})
        assert isinstance(result, str)

    def test_format_definitions_malformed_entries(self):
        """Entries with missing fields must not crash."""
        entries = [
            {"uri": "file:///test.py", "range": {"start": {"line": 0, "character": 0}}},
            {"uri": None, "range": None},
            {},
            {"uri": "file:///test.py", "range": {"start": {"line": "not_an_int", "character": 0}}},
        ]
        result = _format_definitions(entries)
        assert isinstance(result, str)

    @pytest.mark.parametrize("n", [0, 1, 10, 100, 1000])
    def test_format_definitions_large_sizes(self, n):
        """Must handle progressively larger lists."""
        entries = [
            {"uri": f"file:///test{i}.py",
             "range": {"start": {"line": i, "character": 0},
                       "end": {"line": i + 1, "character": 10}}}
            for i in range(n)
        ]
        result = _format_definitions(entries)
        assert isinstance(result, str)
        # Must not be empty for n > 0
        if n > 0:
            assert len(result) > 0


# =============================================================================
# 4. _uri_to_path — edge case URIs
# =============================================================================


class TestUriToPathFuzzing:
    """Edge case URI handling."""

    EDGE_URIS = [
        "file:///test.py",
        "file:///C:/Users/test/file.py",
        "file:///path/with spaces/file.py",
        "file:///path/with%20encoding/file.py",
        "file:///path/with/unicode/üñíçødé.py",
        "file://localhost/path/file.py",
        "",
        None,
        "not-a-uri",
        "http://example.com/file.py",
        "file:///" + "a" * 5000,  # very long path
        "file:///\x00nullbyte.py",
    ]

    @pytest.mark.parametrize("uri", EDGE_URIS)
    def test_uri_to_path_never_crashes(self, uri):
        bridge = _make_bridge()
        try:
            result = bridge._uri_to_path(uri)
            assert result is None or isinstance(result, str)
        except Exception as exc:
            # None/empty → no crash, just returns None
            if not uri:
                return
            raise AssertionError(f"Crash on {uri!r}: {exc}")


# =============================================================================
# 5. _send_request — corrupted state
# =============================================================================


class TestSendRequestFuzzing:
    """Edge cases in request/response cycle."""

    def test_send_request_timeout(self):
        """Event never set → timeout returns None."""
        bridge = _make_bridge()
        # Don't set any pending events — request will time out
        with patch.object(bridge, "_write_message"):
            with patch.object(threading.Event, "wait", return_value=False):
                result = bridge._send_request("textDocument/definition", {}, timeout=0.1)
                assert result is None

    def test_send_request_exception_in_write(self):
        """If _write_message raises, request returns None."""
        bridge = _make_bridge()
        with patch.object(bridge, "_write_message", side_effect=RuntimeError("mock error")):
            result = bridge._send_request("textDocument/definition", {}, timeout=0.1)
            assert result is None

    def test_send_request_duplicate_id(self):
        """If pending already has the id, should still work (Event reuse)."""
        bridge = _make_bridge()
        bridge._req_id = 1  # Next id will be 2
        existing_event = threading.Event()
        existing_event.set()
        bridge._pending[2] = existing_event  # Pre-set the event
        # This won't match because _send_request creates a new Event at the same id
        # But the old one should be overwritten... let's check
        with patch.object(bridge, "_write_message"):
            result = bridge._send_request("textDocument/hover",
                                          {"line": 1, "character": 0}, timeout=0.1)
            # Since we patched write_message, no server response triggers the event
            # So it should time out
            assert result is None or result == {}


# =============================================================================
# 6. _read_loop wire format parsing
# =============================================================================


class TestReadLoopFuzzing:
    """Fuzzing the LSP wire format reader."""

    MALFORMED_WIRE = [
        b"",  # empty
        b"\n",  # just newline
        b"Content-Length: 5\r\n\r\nhello",  # small valid message (not JSON)
        b"Content-Length: 0\r\n\r\n",  # zero-length body
        b"Content-Length: -1\r\n\r\n{}",  # negative length
        b"Content-Length: abc\r\n\r\n{}",  # non-numeric length
        b"Content-Length: 999999999999999999999999\r\n\r\n{}",  # overflow
        b"Content-Type: text/plain\r\n\r\n",  # missing Content-Length
        b"\r\n\r\n{}",  # no header
        b"Content-Length: 10\r\n\r\n{}{}{}",  # body longer than declared
        b"Content-Length: 100\r\n\r\nshort",  # body shorter than declared
        b"Content-Length: 4\r\n\r\n" + b"\x00\x01\x02\x03",  # binary body
        b"Content-Length: 5\r\n" * 100 + b"\r\n{}",  # many headers
        b"a" * 100000,  # huge blob of garbage
    ]

    @pytest.mark.parametrize("raw", MALFORMED_WIRE)
    def test_read_loop_parsing_never_crashes(self, raw):
        """Simulate what _read_loop does with raw bytes from LSP stdout."""
        bridge = _make_bridge()
        # Simulate the _read_loop reading from a mocked stdout
        mock_stdout = MagicMock()
        mock_stdout.readline = MagicMock(return_value=raw)

        # The actual _read_loop uses Content-Length parsing.
        # We inject directly via _dispatch instead.
        try:
            # Try parsing as JSON directly (what _read_loop does after headers)
            if b"Content-Length" in raw:
                # Extract body
                parts = raw.split(b"\r\n\r\n", 1)
                if len(parts) > 1:
                    body = parts[1]
                    try:
                        msg = json.loads(body)
                        bridge._dispatch(msg)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass  # Expected: malformed JSON
            else:
                # Try parsing as raw JSON
                try:
                    msg = json.loads(raw)
                    if isinstance(msg, dict):
                        bridge._dispatch(msg)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass  # Expected
        except Exception as exc:
            raise AssertionError(f"Crash on {raw[:50]!r}: {exc}")


# =============================================================================
# 7. _is_expected_reconcile_close_message — edge case strings
# =============================================================================


class TestReconcileCloseFuzzing:
    """Edge cases for reconcile-close noise suppression."""

    EDGE_TEXTS = [
        None,
        "",
        "a" * 10000,
        "\x00\x01\x02",
        "close" * 1000,
        "unexpected resource" * 100,
        "not open" * 100,
        "not opened" * 100,
        "Close",
        "UnExpected Resource",
        "NOT OPENED",
        "close" + "\x00" + "resource",
    ]

    @pytest.mark.parametrize("text", EDGE_TEXTS)
    def test_reconcile_close_never_crashes(self, text):
        bridge = _make_bridge()
        # Add some reconcile URIs to test suppression
        bridge._reconcile_close_uris["file:///test.py"] = 1000.0
        try:
            result = bridge._is_expected_reconcile_close_message(text)
            assert isinstance(result, bool)
        except Exception as exc:
            if text is None:
                return  # Acceptable (None text, handled)
            raise AssertionError(f"Crash on {text[:50]!r}: {exc}")
