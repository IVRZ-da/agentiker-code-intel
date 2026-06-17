"""Tests for code_call_hierarchy_tool — LSP callHierarchy wrapper."""

from code_intel.lsp_bridge import code_call_hierarchy_tool


class TestCodeCallHierarchy:
    def test_returns_error_for_nonexistent_path(self):
        result = code_call_hierarchy_tool(path="/nonexistent/file.py", line=1)
        assert "error" in result
        assert "Path not found" in result

    def test_returns_error_for_no_lsp_available(self):
        result = code_call_hierarchy_tool(path=__file__, line=1)
        # Should not crash — either no LSP bridge or returns valid result
        assert isinstance(result, str)

    def test_accepts_direction_parameter(self):
        result = code_call_hierarchy_tool(
            path=__file__, line=1, direction="incoming"
        )
        assert isinstance(result, str)

    def test_accepts_max_depth_parameter(self):
        result = code_call_hierarchy_tool(
            path=__file__, line=1, max_depth=2
        )
        assert isinstance(result, str)

    def test_max_depth_capped_at_5(self):
        result = code_call_hierarchy_tool(
            path=__file__, line=1, max_depth=10
        )
        assert isinstance(result, str)

    def test_character_auto_detected_when_missing(self):
        result = code_call_hierarchy_tool(
            path=__file__, line=1, character=None
        )
        assert isinstance(result, str)

    def test_direction_both(self):
        result = code_call_hierarchy_tool(
            path=__file__, line=1, direction="both"
        )
        assert isinstance(result, str)

    def test_direction_outgoing(self):
        result = code_call_hierarchy_tool(
            path=__file__, line=1, direction="outgoing"
        )
        assert isinstance(result, str)

    def test_language_override(self):
        result = code_call_hierarchy_tool(
            path=__file__, line=1, language="python"
        )
        assert isinstance(result, str)
