"""Tests for TTL Cache in LSPBridge."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from code_intel.lsp.bridge.server import LSPBridge


class TestTtlCache:
    """Tests für den TTL-Response-Cache in LSPBridge."""

    def _make_bridge(self, ttl: float = 5.0):
        """Erzeuge minimale Bridge-Instanz für Cache-Tests."""
        bridge = MagicMock(spec=LSPBridge)
        bridge._response_cache = {}
        bridge._CACHE_TTL = ttl

        # Echte Methoden anbinden
        from types import MethodType
        bridge._cache_key = MethodType(LSPBridge._cache_key, bridge)
        bridge._cache_get = MethodType(LSPBridge._cache_get, bridge)
        bridge._cache_set = MethodType(LSPBridge._cache_set, bridge)
        bridge._cache_clear = MethodType(LSPBridge._cache_clear, bridge)
        bridge._cache_invalidate_file = MethodType(LSPBridge._cache_invalidate_file, bridge)
        return bridge

    def test_cache_hit_returns_stored_value(self):
        bridge = self._make_bridge()
        expected = [{"uri": "file:///test.py", "range": {"start": {"line": 0}}}]
        bridge._cache_set("definition", "/test.py", 0, 5, expected)
        result = bridge._cache_get("definition", "/test.py", 0, 5)
        assert result == expected

    def test_cache_miss_returns_none(self):
        bridge = self._make_bridge()
        result = bridge._cache_get("definition", "/nonexistent.py", 0, 5)
        assert result is None

    def test_cache_expires_after_ttl(self):
        bridge = self._make_bridge(ttl=0.01)  # 10ms TTL
        bridge._cache_set("definition", "/test.py", 0, 5, ["result"])
        time.sleep(0.02)
        result = bridge._cache_get("definition", "/test.py", 0, 5)
        assert result is None  # Expired

    def test_cache_clear_removes_all(self):
        bridge = self._make_bridge()
        bridge._cache_set("definition", "/a.py", 0, 5, "a")
        bridge._cache_set("definition", "/b.py", 0, 5, "b")
        bridge._cache_clear()
        assert bridge._cache_get("definition", "/a.py", 0, 5) is None
        assert bridge._cache_get("definition", "/b.py", 0, 5) is None

    def test_cache_invalidate_file(self):
        bridge = self._make_bridge()
        bridge._cache_set("definition", "/test.py", 0, 5, "def")
        bridge._cache_set("references:1", "/test.py", 0, 5, "refs")
        bridge._cache_set("definition", "/other.py", 0, 5, "other")
        bridge._cache_invalidate_file("/test.py")
        # /test.py Einträge sollten weg sein
        assert bridge._cache_get("definition", "/test.py", 0, 5) is None
        assert bridge._cache_get("references:1", "/test.py", 0, 5) is None
        # /other.py Eintrag sollte noch da sein
        assert bridge._cache_get("definition", "/other.py", 0, 5) is not None

    def test_cache_size_limit(self):
        bridge = self._make_bridge()
        # 101 Einträge hinzufügen (limit = 100)
        for i in range(101):
            bridge._cache_set(f"method{i}", f"/f{i}.py", 0, 0, f"result{i}")
        assert len(bridge._response_cache) <= 100
