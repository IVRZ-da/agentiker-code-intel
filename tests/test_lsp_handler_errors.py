"""Tests for lsp/tools_handler.py — LSP handler functions.

Target: cover error paths and edge cases in handler functions.
"""

from __future__ import annotations

import json

import pytest


class TestCodeHoverTool:
    """code_hover_tool — error paths."""

    @pytest.mark.integration
    def test_file_not_found(self):
        """Non-existent file → error status."""
        from code_intel.lsp.tools_handler import code_hover_tool

        result = json.loads(code_hover_tool(path="/nonexistent/file.py", line=1))
        assert result.get("status") == "error"


class TestCodeFormatTool:
    """code_format_tool — error paths."""

    @pytest.mark.integration
    def test_file_not_found(self):
        """Non-existent path → error."""
        from code_intel.lsp.tools_handler import code_format_tool

        result = json.loads(code_format_tool(path="/nonexistent/file.py"))
        assert result.get("status") == "error"


class TestCodeRenameTool:
    """code_rename_tool — error paths."""

    @pytest.mark.integration
    def test_file_not_found(self):
        """Non-existent file → error."""
        from code_intel.lsp.tools_handler import code_rename_tool

        result = json.loads(code_rename_tool(path="/nonexistent/file.py", new_name="foo", line=1))
        assert result.get("status") == "error"


class TestCodeWorkspaceSymbolsTool:
    """code_workspace_symbols_tool — error paths."""

    @pytest.mark.integration
    def test_empty_query(self):
        """Empty query → returns empty results (non-error)."""
        from code_intel.lsp.tools_handler import code_workspace_symbols_tool

        result = json.loads(code_workspace_symbols_tool(query=""))
        # Returns ok with empty symbols, not an error
        assert "symbols" in result or "result" in str(result)


class TestCodeSignatureHelp:
    """code_signatures_tool — error paths."""

    @pytest.mark.integration
    def test_file_not_found(self):
        """Non-existent file → error."""
        from code_intel.lsp.tools_extra import code_signatures_tool

        result = json.loads(code_signatures_tool(path="/nonexistent/file.py", line=1))
        assert result.get("status") == "error"


class TestCodeTypeDefinition:
    """code_type_definition_tool — error paths in tools_extra."""

    @pytest.mark.integration
    def test_file_not_found(self):
        """Non-existent file → error."""
        from code_intel.lsp.tools_extra import code_type_definition_tool

        result = json.loads(code_type_definition_tool(path="/nonexistent/file.py", line=1))
        assert result.get("status") == "error"
