"""lsp/ — LSP bridge subpackage extracted from lsp_bridge.py.

Modules:
    bridge.py   — LSPBridge, LSPManager, language config, workspace discovery
    tools.py    — All code_*_tool functions + schemas + AST fallbacks
    handlers.py — register_lsp_tools() registration function
"""

from . import bridge
from .bridge import LSPBridge, LSPManager, get_lsp_manager
from .tools import (
    code_definition_tool, code_references_tool, code_diagnostics_tool,
    code_hover_tool, code_rename_tool, code_format_tool,
    code_type_definition_tool, code_implementations_tool,
    code_signatures_tool, code_action_tool,
    code_highlight_tool, code_inlay_hints_tool,
    code_document_symbols_tool, code_callers_tool,
    code_callees_tool, code_call_hierarchy_tool,
    code_type_hierarchy_tool, code_workspace_symbols_tool,
    # New LSP 3.18 tools
    code_completion_tool, code_code_lens_tool,
    code_folding_range_tool, code_selection_range_tool,
    code_linked_editing_tool, code_prepare_rename_tool,
)
from .handlers import register_lsp_tools

__all__ = [
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
]
