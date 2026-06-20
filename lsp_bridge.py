"""lsp_bridge.py — Re-export facade for code_intel.lsp subpackage.

All real code has been moved to lsp/ subpackage modules:
- lsp/bridge.py    — LSPBridge, LSPManager, config, workspace discovery
- lsp/tools.py     — All code_*_tool functions + schemas + AST fallbacks
- lsp/handlers.py  — register_lsp_tools() registration function

This module re-exports everything for backward compatibility.
Uses wildcard imports from lsp.bridge and lsp.tools to keep all
private symbols available for existing test imports.
"""

from __future__ import annotations

# Re-export EVERYTHING from bridge and tools for backward compat
from code_intel.lsp.bridge import *  # noqa: F401, F403 — LSPBridge, LSPManager, config, helpers
from code_intel.lsp.tools import *   # noqa: F401, F403 — all tool functions, schemas, helpers

# Registration function (from handlers.py)
from code_intel.lsp.handlers import register_lsp_tools  # noqa: F401

# Keep logging setup for backward compatibility
import logging
logging.raiseExceptions = False

__all__ = [
    "LSPBridge", "LSPManager", "get_lsp_manager",
    "register_lsp_tools", "_safe_register",
    "code_definition_tool", "code_references_tool",
    "code_diagnostics_tool", "code_hover_tool",
    "code_rename_tool", "code_format_tool",
    "code_type_definition_tool", "code_implementations_tool",
    "code_signatures_tool", "code_action_tool",
    "code_highlight_tool", "code_inlay_hints_tool",
    "code_document_symbols_tool", "code_callers_tool",
    "code_callees_tool", "code_call_hierarchy_tool",
    "code_type_hierarchy_tool", "code_workspace_symbols_tool",
]
