"""Tests for lsp_bridge._is_expected_reconcile_close_message."""
import logging

from code_intel.lsp_bridge import LSPBridge


def _make_bridge() -> LSPBridge:
    """Create a minimal LSPBridge instance for testing."""
    return LSPBridge(
        command="",
        args=[],
        root_uri="/tmp",
        language_id="typescript",
    )


class TestIsExpectedReconcileCloseMessage:
    """Test the suppression logic for tsserver reconcile-close noise.

    The method should:
    - Suppress "not opened" errors that match a known reconcile URI
    - NOT suppress "not opened" errors where the URI is corrupted
      (e.g. s4ore instead of store) — those get logged as WARNING
    - Handle "unexpected resource" with URI matching
    - Return False for messages without close/open keywords
    - Return False when reconcile_uris is empty
    """

    def _bridge_with_uris(self, uris: list[str]) -> LSPBridge:
        bridge = _make_bridge()
        import time
        now = time.monotonic()
        for u in uris:
            bridge._reconcile_close_uris[u] = now
        return bridge

    # -- "not opened" tests ------------------------------------------------

    def test_not_opened_with_matching_uri(self):
        """Suppress "not opened" when the error URI matches a tracked URI."""
        bridge = self._bridge_with_uris([
            "file:///tmp/store/route.ts"
        ])
        text = "Trying to close not opened document: file:///tmp/store/route.ts"
        assert bridge._is_expected_reconcile_close_message(text) is True

    def test_not_opened_with_matching_uri_partial(self):
        """Suppress when URI is a substring match (correctly includes full path)."""
        bridge = self._bridge_with_uris([
            "file:///tmp/store/route.ts"
        ])
        # Different trailing context but still contains the URI
        text = "Notification handler 'textDocument/didClose' failed with message: Trying to close not opened document: file:///tmp/store/route.ts"
        assert bridge._is_expected_reconcile_close_message(text) is True

    def test_not_opened_with_corrupted_uri(self):
        """DO NOT suppress when URI is corrupted (s4ore instead of store)."""
        bridge = self._bridge_with_uris([
            "file:///tmp/store/route.ts"
        ])
        # Server reports corrupted URI — the actual corruption pattern from tsserver
        text = "Trying to close not opened document: file:///tmp/s4ore/route.ts"
        assert bridge._is_expected_reconcile_close_message(text) is False

    def test_not_opened_with_digit_substitution(self):
        """DO NOT suppress when URI has digit corruption (9oute instead of route)."""
        bridge = self._bridge_with_uris([
            "file:///tmp/store/route.ts"
        ])
        text = "Trying to close not opened document: file:///tmp/store/9oute.ts"
        assert bridge._is_expected_reconcile_close_message(text) is False

    def test_not_opened_with_multiple_uris_one_matches(self):
        """Suppress when at least one of multiple reconcile URIs matches."""
        bridge = self._bridge_with_uris([
            "file:///tmp/other/file.ts",
            "file:///tmp/store/route.ts",
        ])
        text = "Trying to close not opened document: file:///tmp/store/route.ts"
        assert bridge._is_expected_reconcile_close_message(text) is True

    def test_not_opened_with_multiple_uris_none_match_corrupted(self):
        """DO NOT suppress when multiple URIs exist but none match the corrupted path."""
        bridge = self._bridge_with_uris([
            "file:///tmp/other/file.ts",
            "file:///tmp/store/route.ts",
        ])
        text = "Trying to close not opened document: file:///tmp/s6ore/carts/route.ts"
        assert bridge._is_expected_reconcile_close_message(text) is False

    def test_not_opened_but_server_uri_partially_matches(self):
        """Partial string match of a tracked URI segment doesn't count as match.

        For example, if tracked URI is '.../store/carts/route.ts', the server
        reports '.../s4ore/carts/route.ts'. Since 'store' != 's4ore', the full
        URI string won't be contained.
        """
        bridge = self._bridge_with_uris([
            "file:///tmp/project/store/carts/route.ts"
        ])
        # Corrupted: store -> s4ore, but '/project/' is shared
        text = "Trying to close not opened document: file:///tmp/project/s4ore/carts/route.ts"
        # '/project/' is common, but the full tracked URI is NOT a substring of 's4ore'
        tracked = "file:///tmp/project/store/carts/route.ts"
        corrupted = "file:///tmp/project/s4ore/carts/route.ts"
        assert tracked not in corrupted  # Sanity check on the test itself
        assert bridge._is_expected_reconcile_close_message(text) is False

    # -- "unexpected resource" tests ---------------------------------------

    def test_unexpected_resource_with_matching_uri(self):
        """Suppress 'unexpected resource' when it references a tracked URI."""
        bridge = self._bridge_with_uris([
            "file:///tmp/store/route.ts"
        ])
        text = "Unexpected resource file:///tmp/store/route.ts"
        assert bridge._is_expected_reconcile_close_message(text) is True

    def test_unexpected_resource_with_non_matching_uri(self):
        """DO NOT suppress 'unexpected resource' when URI is not tracked."""
        bridge = self._bridge_with_uris([
            "file:///tmp/other/file.ts"
        ])
        text = "Unexpected resource file:///tmp/store/route.ts"
        assert bridge._is_expected_reconcile_close_message(text) is False

    # -- edge cases --------------------------------------------------------

    def test_empty_text_returns_false(self):
        """Empty text should never be considered expected noise."""
        bridge = self._bridge_with_uris(["file:///test.ts"])
        assert bridge._is_expected_reconcile_close_message("") is False

    def test_none_text_returns_false(self):
        """None text should not crash."""
        bridge = self._bridge_with_uris(["file:///test.ts"])
        assert bridge._is_expected_reconcile_close_message(None) is False  # type: ignore[arg-type]

    def test_no_close_keyword_returns_false(self):
        """Text without 'close' or 'resource' keywords should return False."""
        bridge = self._bridge_with_uris(["file:///test.ts"])
        text = "Some random server log message"
        assert bridge._is_expected_reconcile_close_message(text) is False

    def test_no_reconcile_uris_returns_false(self):
        """When no reconcile URIs are tracked, no message should be suppressed."""
        bridge = self._bridge_with_uris([])
        text = "Trying to close not opened document: file:///test.ts"
        assert bridge._is_expected_reconcile_close_message(text) is False

    def test_stale_uri_expires(self):
        """URIs older than 5 seconds should be pruned and no longer suppress."""
        bridge = self._bridge_with_uris([
            "file:///test.ts"
        ])
        import time
        # Manually set the URI timestamp to 10 seconds ago
        bridge._reconcile_close_uris["file:///test.ts"] = time.monotonic() - 10.0
        text = "Trying to close not opened document: file:///test.ts"
        # After calling, the stale URI is pruned
        assert bridge._is_expected_reconcile_close_message(text) is False
        # Verify the URI was actually pruned
        assert "file:///test.ts" not in bridge._reconcile_close_uris

    def test_close_referenced_in_different_context(self):
        """The word 'close' in non-error context should trigger the check but not match."""
        bridge = self._bridge_with_uris(["file:///real.ts"])
        # Contains 'close' but not 'not opened' or 'unexpected resource'
        text = "close enough but not an lsp error"
        assert bridge._is_expected_reconcile_close_message(text) is False

    def test_concurrent_uri_addition_does_not_break(self):
        """Regression: adding URIs while checking should not cause race."""
        bridge = self._bridge_with_uris(["file:///first.ts"])
        text = "Trying to close not opened document: file:///second.ts"
        # Simulate concurrent addition by adding while iterating
        import threading
        import time

        def add_uri():
            bridge._reconcile_close_uris["file:///third.ts"] = time.monotonic()
        t = threading.Thread(target=add_uri)
        t.start()
        result = bridge._is_expected_reconcile_close_message(text)
        t.join()
        # Should not crash, result is False (tracked URI doesn't match)
        assert result is False

    def test_warning_logged_for_corrupted_uri(self, caplog):
        """A WARNING should be logged when URI mismatch is detected."""
        bridge = self._bridge_with_uris(["file:///real/route.ts"])
        text = "Trying to close not opened document: file:///real/9oute.ts"
        caplog.set_level(logging.WARNING)
        bridge._is_expected_reconcile_close_message(text)
        found = any(
            "LSP URI mismatch (possible tsserver corruption)" in record.getMessage()
            for record in caplog.records
        )
        assert found, "Expected WARNING log for URI mismatch"
