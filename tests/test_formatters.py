"""Tests for _fmt.py formatting helpers."""
from __future__ import annotations

import json

from code_intel._fmt import fmt_compact, fmt_ok


class TestFmtCompact:
    """Tests for fmt_compact() — token-arme JSON-Ausgabe ohne rich.Panel-Overhead."""

    def test_fmt_compact_returns_valid_json(self):
        """fmt_compact() should return valid JSON without rich Panel wrappers."""
        result = fmt_compact({"status": "ok", "data": "test"})
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["data"] == "test"

    def test_fmt_compact_has_no_rich_overhead(self):
        """fmt_compact() should NOT contain rich Panel border characters or ANSI codes."""
        result = fmt_compact({"key": "value"})
        # Panel border chars like ╭─╮╰╯ should NOT be present
        assert "╭" not in result
        assert "╰" not in result
        assert "│" not in result
        # JSON should be parseable
        data = json.loads(result)
        assert data["key"] == "value"

    def test_fmt_compact_is_smaller_than_fmt_ok(self):
        """fmt_compact() should produce shorter output than fmt_ok() for same data."""
        data = {"result": "hello", "count": 42}
        compact = fmt_compact(data)
        ok_result = fmt_ok(data)
        assert len(compact) < len(ok_result)

    def test_fmt_compact_nested_data(self):
        """fmt_compact() should handle nested dicts and lists."""
        data = {
            "items": [
                {"id": 1, "name": "foo"},
                {"id": 2, "name": "bar"},
            ],
            "total": 2,
        }
        result = fmt_compact(data)
        parsed = json.loads(result)
        assert parsed["total"] == 2
        assert len(parsed["items"]) == 2

    def test_fmt_compact_empty_dict(self):
        """fmt_compact() should handle empty dict gracefully."""
        result = fmt_compact({})
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert len(parsed) == 0

    def test_fmt_compact_with_message(self):
        """fmt_compact() should accept optional msg/title params."""
        result = fmt_compact({"data": "test"}, msg="done", title="Result")
        parsed = json.loads(result)
        # msg and title should be merged into the output
        assert parsed.get("message") == "done"

    def test_fmt_compact_is_deterministic(self):
        """fmt_compact() should produce stable, predictable output."""
        data = {"status": "ok", "value": 123}
        r1 = fmt_compact(data)
        r2 = fmt_compact(data)
        assert r1 == r2
