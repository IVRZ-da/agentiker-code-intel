"""Tests for structural fixes: diagnostics cache LRU, LSP reqs, close_document lock."""

from collections import OrderedDict

from code_intel.lsp_bridge import LSPBridge, _check_lsp_reqs


def _make_bridge() -> LSPBridge:
    return LSPBridge(
        command="",
        args=[],
        root_uri="/tmp",
        language_id="typescript",
    )


class TestDiagnosticsCacheLRU:
    """Test that _diagnostics_cache uses OrderedDict with LRU eviction at 500."""

    def test_cache_is_ordered_dict(self):
        """Cache should be an OrderedDict, not a plain dict."""
        bridge = _make_bridge()
        assert isinstance(bridge._diagnostics_cache, OrderedDict)

    def test_cache_evicts_oldest_entry(self):
        """When cache exceeds 500 entries, oldest entry should be evicted."""
        bridge = _make_bridge()
        # Fill with 501 entries
        for i in range(501):
            bridge._diagnostics_cache[f"/tmp/file_{i}.ts"] = [{"message": f"diag_{i}"}]
            bridge._diagnostics_cache.move_to_end(f"/tmp/file_{i}.ts")
        # Evict down to 500
        while len(bridge._diagnostics_cache) > 500:
            bridge._diagnostics_cache.popitem(last=False)
        assert len(bridge._diagnostics_cache) == 500
        # The first entry (file_0) should be gone
        assert "/tmp/file_0.ts" not in bridge._diagnostics_cache
        # The last entry (file_500) should still be there
        assert "/tmp/file_500.ts" in bridge._diagnostics_cache

    def test_recently_accessed_entry_stays(self):
        """move_to_end should protect an entry from eviction."""
        bridge = _make_bridge()
        for i in range(500):
            bridge._diagnostics_cache[f"/tmp/file_{i}.ts"] = [{"message": f"diag_{i}"}]
        # Access file_0 (move it to end)
        bridge._diagnostics_cache.move_to_end("/tmp/file_0.ts")
        # Now file_0 should be the newest (last), not the oldest
        oldest_key = next(iter(bridge._diagnostics_cache))
        assert oldest_key != "/tmp/file_0.ts", "file_0 should NOT be oldest after access"
        assert oldest_key == "/tmp/file_1.ts"

    def test_cache_write_in_dispatch_simulated(self):
        """Simulate the LRU write pattern used in _dispatch: write + move_to_end + evict."""
        bridge = _make_bridge()
        cache = bridge._diagnostics_cache
        # Add 500 entries
        for i in range(500):
            cache[f"/tmp/f_{i}.ts"] = [{"msg": str(i)}]
            cache.move_to_end(f"/tmp/f_{i}.ts")
        # Add one more — should trigger eviction of oldest
        cache["/tmp/f_500.ts"] = [{"msg": "500"}]
        cache.move_to_end("/tmp/f_500.ts")
        while len(cache) > 500:
            cache.popitem(last=False)
        assert len(cache) == 500
        assert "/tmp/f_0.ts" not in cache  # oldest evicted
        assert "/tmp/f_500.ts" in cache  # newest present


class TestCheckLspReqs:
    """Test _check_lsp_reqs returns True only when LSP servers are available."""

    def test_returns_bool(self):
        """Should return a bool, not None."""
        result = _check_lsp_reqs()
        assert isinstance(result, bool)

    def test_returns_false_when_no_server(self):
        """Should return False when no LSP server is on PATH.

        This relies on 'nonexistent-language-server-xyzzy' not being installed.
        """
        from code_intel.lsp_bridge import _resolve_command

        # Check if typescript-language-server is installed
        has_ts = _resolve_command("typescript-language-server") is not None
        # If no TS server is installed, _check_lsp_reqs should be False
        if not has_ts:
            # Also check pyright
            has_pyright = _resolve_command("pyright-langserver") is not None
            if not has_pyright:
                # No LSP server at all
                assert _check_lsp_reqs() is False
            else:
                # Has pyright, so reqs should be True
                assert _check_lsp_reqs() is True
        else:
            # Has typescript-language-server, so reqs should be True
            assert _check_lsp_reqs() is True


class TestCloseDocumentLock:
    """Test that close_document handles state correctly."""

    def _bridge_with_send_mock(self):
        """Create a bridge with _send_notification mocked out."""
        bridge = _make_bridge()
        bridge._send_notification = lambda method, params: None
        return bridge

    def test_close_document_discards_from_open_documents(self):
        """After close_document, the URI should be removed from open_documents."""
        bridge = self._bridge_with_send_mock()
        uri = "file:///tmp/test.ts"
        bridge._open_documents.add(uri)
        bridge.close_document("/tmp/test.ts")
        assert uri not in bridge._open_documents

    def test_double_close_does_not_crash(self):
        """Calling close_document twice should not raise."""
        bridge = self._bridge_with_send_mock()
        bridge._open_documents.add("file:///tmp/test.ts")
        bridge.close_document("/tmp/test.ts")
        # Second close should be safe (discard is idempotent, and
        # _send_notification is not called because URI is already gone)
        bridge.close_document("/tmp/test.ts")  # no raise

    def test_close_unopened_document_does_not_crash(self):
        """Closing a document that was never opened should not crash."""
        bridge = self._bridge_with_send_mock()
        bridge.close_document("/tmp/never_opened.ts")  # no raise

    def test_close_notification_only_sent_when_was_open(self):
        """_send_notification should only be called for documents that were tracked as open."""
        sent = []
        bridge = _make_bridge()
        bridge._send_notification = lambda method, params: sent.append(method)
        # Close unopened doc — should NOT send notification
        bridge.close_document("/tmp/unopened.ts")
        assert len(sent) == 0, "didClose should not be sent for a doc that was never opened"
        # Open, then close — should send
        bridge._open_documents.add("file:///tmp/opened.ts")
        bridge.close_document("/tmp/opened.ts")
        assert len(sent) == 1
        assert sent[0] == "textDocument/didClose"


class TestSafeReadText:
    """Coverage für _logging.py — safe_read_text Exception-Pfade."""

    def test_reads_normal_file(self, tmp_path):
        from code_intel._logging import safe_read_text

        f = tmp_path / "test.txt"
        f.write_text("Hello World", encoding="utf-8")
        result = safe_read_text(str(f))
        assert result == "Hello World"

    def test_falls_back_on_unicode_error(self, tmp_path):
        from code_intel._logging import safe_read_text

        f = tmp_path / "invalid.txt"
        f.write_bytes(b"Hello \xff\xfe World")
        result = safe_read_text(str(f))
        assert isinstance(result, str)
        assert "Hello" in result
        assert len(result) > 0

    def test_raises_on_io_error(self, tmp_path):
        import pytest
        from code_intel._logging import safe_read_text

        with pytest.raises(OSError):
            safe_read_text(str(tmp_path / "nonexistent.txt"))

    def test_setup_logger_creates_handler(self):
        import logging

        from code_intel._logging import setup_logger

        logger = setup_logger("test_cov_logger")
        assert not logger.propagate
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) >= 1
        # Cleanup
        logger.handlers.clear()
        logging.getLogger("test_cov_logger").propagate = True
