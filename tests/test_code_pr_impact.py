"""Tests for code_pr_impact_tool — PR Impact Analysis."""

from code_intel.code_tools import code_pr_impact_tool


class TestCodePrImpact:
    def test_default_params(self):
        """Should not crash with default params."""
        result = code_pr_impact_tool(path=".")
        assert isinstance(result, str)

    def test_nonexistent_path(self):
        result = code_pr_impact_tool(path="/nonexistent")
        assert "error" in result

    def test_custom_base_branch(self):
        """Non-existent branch should fail gracefully."""
        result = code_pr_impact_tool(base_branch="nonexistent-branch-xyz", path=".")
        # Either git diff error or no changes
        assert isinstance(result, str)

    def test_max_files_param(self):
        result = code_pr_impact_tool(path=".", max_files=5)
        assert isinstance(result, str)

    def test_returns_json(self):
        result = code_pr_impact_tool(path="/tmp")
        assert len(result) > 0
        assert "error" in result or "info" in result or "files_changed" in result
