"""lsp/ — LSP bridge subpackage extracted from lsp_bridge.py.

Modules:
    bridge.py   — LSPBridge, LSPManager, language config, workspace discovery
    tools.py    — All code_*_tool functions + schemas + AST fallbacks
    handlers.py — register_lsp_tools() registration function
"""
# ruff: noqa: F405

from . import bridge
from .bridge import LSPBridge, LSPManager, get_lsp_manager
from .handlers import register_lsp_tools
from .tools import (
    CODE_DOCUMENT_LINKS_SCHEMA,
    CODE_INLINE_VALUES_SCHEMA,
    # New LSP 3.18 schema exports
    CODE_SEMANTIC_TOKENS_SCHEMA,
    _handle_code_document_links,
    _handle_code_inline_values,
    # New LSP 3.18 handler exports
    _handle_code_semantic_tokens,
    code_action_tool,
    code_call_hierarchy_tool,
    code_callees_tool,
    code_callers_tool,
    code_code_lens_tool,
    # New LSP 3.18 tools
    code_completion_tool,
    code_definition_tool,
    code_diagnostics_tool,
    code_document_links_tool,
    code_document_symbols_tool,
    code_folding_range_tool,
    code_format_tool,
    code_highlight_tool,
    code_hover_tool,
    code_implementations_tool,
    code_inlay_hints_tool,
    code_inline_values_tool,
    code_linked_editing_tool,
    code_prepare_rename_tool,
    code_references_tool,
    code_rename_tool,
    code_selection_range_tool,
    code_semantic_tokens_tool,
    code_signatures_tool,
    code_type_definition_tool,
    code_type_hierarchy_tool,
    code_workspace_symbols_tool,
)

__all__ = [
    "bridge",
    "LSPBridge", "LSPManager", "get_lsp_manager",
    "register_lsp_tools",
    "code_definition_tool", "code_references_tool", "code_diagnostics_tool",
    "code_hover_tool", "code_rename_tool", "code_format_tool",
    "code_type_definition_tool", "code_implementations_tool",
    "code_signatures_tool", "code_action_tool",
    "code_highlight_tool", "code_inlay_hints_tool",
    "code_document_symbols_tool", "code_callers_tool",
    "code_callees_tool", "code_call_hierarchy_tool",
    "code_type_hierarchy_tool", "code_workspace_symbols_tool",
    # New LSP 3.18 tools
    "code_completion_tool", "code_code_lens_tool",
    "code_folding_range_tool", "code_selection_range_tool",
    "code_linked_editing_tool", "code_prepare_rename_tool",
    "code_semantic_tokens_tool", "code_document_links_tool",
    "code_inline_values_tool",
    # New LSP 3.18 schema exports
    "CODE_SEMANTIC_TOKENS_SCHEMA",
    "CODE_DOCUMENT_LINKS_SCHEMA",
    "CODE_INLINE_VALUES_SCHEMA",
    # New LSP 3.18 handler exports
    "_handle_code_semantic_tokens",
    "_handle_code_document_links",
    "_handle_code_inline_values",
]
