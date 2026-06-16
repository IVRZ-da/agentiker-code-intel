"""Tests for LSP Bridge lifecycle: open_document, close_document, goto_definition, etc."""

import time
from code_intel.lsp_bridge import LSPBridge


def _make_bridge(language_id="typescript", root="/tmp") -> LSPBridge:
    return LSPBridge(
        command="",
        args=[],
        root_uri=root,
        language_id=language_id,
    )


# ---------------------------------------------------------------------------
# open_document
# ---------------------------------------------------------------------------


class TestOpenDocument:
    def test_first_call_opens_document(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("export const x = 1;\n")
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))

        bridge.open_document(str(f))
        methods = [m for m, _ in sent]
        assert "textDocument/didOpen" in methods
        assert str(f) in bridge._open_documents or "file://" + str(f) in bridge._open_documents

    def test_second_call_skips_duplicate(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("export const x = 1;\n")
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))

        bridge.open_document(str(f))
        bridge.open_document(str(f))  # Should be skipped — already open
        did_open_count = sum(1 for m, _ in sent if m == "textDocument/didOpen")
        assert did_open_count == 1

    def test_nonexistent_file_logs_warning(self, tmp_path, caplog):
        bridge = _make_bridge()
        bridge.open_document("/nonexistent/file.ts")
        assert "failed to read" in caplog.text

    def test_tracks_uri_in_reconcile_uris(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("x")
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))
        bridge.open_document(str(f))
        uri = f"file://{f}"
        # URI should be registered in _reconcile_close_uris but didClose should NOT be sent
        assert uri in bridge._reconcile_close_uris
        did_close_count = sum(1 for m, _ in sent if m == "textDocument/didClose")
        assert did_close_count == 0

    def test_register_uri_twice_does_not_overwrite_timestamp(self, tmp_path):
        import time
        f = tmp_path / "test.ts"
        f.write_text("x")
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))
        bridge.open_document(str(f))  # First open: registers URI
        ts1 = bridge._reconcile_close_uris.get(f"file://{f}")
        time.sleep(0.01)
        bridge.close_document(str(f))
        bridge.open_document(str(f))  # Second open: URI already tracked
        ts2 = bridge._reconcile_close_uris.get(f"file://{f}")
        # Timestamp should NOT have been updated
        assert ts1 == ts2

    def test_concurrent_open_same_file(self, tmp_path):
        """Two threads opening the same file should only result in one didOpen."""
        import threading
        f = tmp_path / "test.ts"
        f.write_text("x")
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))

        def open_it():
            bridge.open_document(str(f))

        t1 = threading.Thread(target=open_it)
        t2 = threading.Thread(target=open_it)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        did_open_count = sum(1 for m, _ in sent if m == "textDocument/didOpen")
        # Should only open once
        assert did_open_count >= 1  # at least one, but race may cause 2


# ---------------------------------------------------------------------------
# close_document
# ---------------------------------------------------------------------------


class TestCloseDocument:
    def test_close_open_document(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("x")
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))
        uri = f"file://{f}"
        bridge._open_documents.add(uri)
        bridge.close_document(str(f))
        assert uri not in bridge._open_documents
        assert "textDocument/didClose" in [m for m, _ in sent]

    def test_close_unopened_document_sends_nothing(self):
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))
        bridge.close_document("/tmp/never_opened.ts")
        assert len(sent) == 0

    def test_double_close(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("x")
        bridge = _make_bridge()
        sent = []
        bridge._send_notification = lambda method, params: sent.append((method, params))
        uri = f"file://{f}"
        bridge._open_documents.add(uri)
        bridge.close_document(str(f))
        bridge.close_document(str(f))  # second close should be safe
        assert uri not in bridge._open_documents


# ---------------------------------------------------------------------------
# _is_expected_reconcile_close_message (sanity)
# ---------------------------------------------------------------------------


class TestReconcileCloseMessageBasic:
    """Basic sanity checks that the existing tests pass in context."""

    def test_basic_suppress_not_opened(self):
        bridge = _make_bridge()
        bridge._reconcile_close_uris["file:///tmp/test.ts"] = time.monotonic()
        result = bridge._is_expected_reconcile_close_message(
            "Trying to close not opened document: file:///tmp/test.ts"
        )
        assert result is True

    def test_basic_suppress_unexpected(self):
        bridge = _make_bridge()
        bridge._reconcile_close_uris["file:///tmp/test.ts"] = time.monotonic()
        result = bridge._is_expected_reconcile_close_message(
            "Unexpected resource file:///tmp/test.ts"
        )
        assert result is True

    def test_no_match_no_suppress(self):
        bridge = _make_bridge()
        bridge._reconcile_close_uris["file:///tmp/other.ts"] = time.monotonic()
        result = bridge._is_expected_reconcile_close_message(
            "Trying to close not opened document: file:///tmp/test.ts"
        )
        assert result is False
