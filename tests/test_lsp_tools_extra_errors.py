"""Tests for lsp/tools_extra.py — LSP extra tool error paths."""

from __future__ import annotations

import json

from code_intel.lsp.tools_extra import (
    code_action_tool,
    code_code_lens_tool,
    code_completion_tool,
    code_document_links_tool,
    code_folding_range_tool,
    code_implementations_tool,
    code_inline_values_tool,
    code_linked_editing_tool,
    code_prepare_rename_tool,
    code_selection_range_tool,
    code_semantic_tokens_tool,
    code_signatures_tool,
    code_type_definition_tool,
)


class TestExtraToolErrorPaths:
    """All LSP extra tools — file_not_found returns error."""

    def test_type_definition_not_found(self):
        result = json.loads(code_type_definition_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_implementations_not_found(self):
        result = json.loads(code_implementations_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_signatures_not_found(self):
        result = json.loads(code_signatures_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_action_not_found(self):
        result = json.loads(code_action_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_completion_not_found(self):
        result = json.loads(code_completion_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_code_lens_not_found(self):
        result = json.loads(code_code_lens_tool(path="/nope.py"))
        assert result.get("status") == "error"

    def test_folding_range_not_found(self):
        result = json.loads(code_folding_range_tool(path="/nope.py"))
        assert result.get("status") == "error"

    def test_selection_range_not_found(self):
        result = json.loads(code_selection_range_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_linked_editing_not_found(self):
        result = json.loads(code_linked_editing_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_prepare_rename_not_found(self):
        result = json.loads(code_prepare_rename_tool(path="/nope.py", line=1))
        assert result.get("status") == "error"

    def test_inline_values_not_found(self):
        result = json.loads(code_inline_values_tool(file_path="/nope.py"))
        assert result.get("status") == "error"

    def test_document_links_not_found(self):
        result = json.loads(code_document_links_tool(file_path="/nope.py"))
        assert result.get("status") == "error"

    def test_semantic_tokens_not_found(self):
        result = json.loads(code_semantic_tokens_tool(file_path="/nope.py"))
        assert result.get("status") == "error"
