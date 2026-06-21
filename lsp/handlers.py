"""lsp/handlers.py — Tool schemas, handlers, and registration extracted from lsp_bridge.py."""

from __future__ import annotations

from .bridge import logger
from .tools import (
    CODE_ACTION_SCHEMA,
    CODE_CALL_HIERARCHY_SCHEMA,
    CODE_CALLEES_SCHEMA,
    CODE_CALLERS_SCHEMA,
    CODE_DEFINITION_SCHEMA,
    CODE_DIAGNOSTICS_SCHEMA,
    CODE_DOCUMENT_SYMBOLS_SCHEMA,
    CODE_FORMAT_SCHEMA,
    CODE_HIGHLIGHT_SCHEMA,
    CODE_HOVER_SCHEMA,
    CODE_IMPLEMENTATIONS_SCHEMA,
    CODE_INLAY_HINTS_SCHEMA,
    CODE_REFERENCES_SCHEMA,
    CODE_RENAME_SCHEMA,
    CODE_SIGNATURES_SCHEMA,
    CODE_TYPE_DEFINITION_SCHEMA,
    CODE_TYPE_HIERARCHY_SCHEMA,
    CODE_WORKSPACE_SYMBOLS_SCHEMA,
    _handle_code_action,
    _handle_code_call_hierarchy,
    _handle_code_callees,
    _handle_code_callers,
    _handle_code_definition,
    _handle_code_diagnostics,
    _handle_code_document_symbols,
    _handle_code_format,
    _handle_code_highlight,
    _handle_code_hover,
    _handle_code_implementations,
    _handle_code_inlay_hints,
    _handle_code_references,
    _handle_code_rename,
    _handle_code_signatures,
    _handle_code_type_definition,
    _handle_code_type_hierarchy,
    _handle_code_workspace_symbols,
)


def _register_lsp_tool(ctx, schema, handler):
    """Register a single LSP tool via ctx.register_tool()."""
    try:
        ctx.register_tool(
            name=schema["name"],
            toolset="agentiker_code_intel",
            schema=schema["parameters"],
            handler=handler,
            description=schema["description"],
        )
    except Exception as e:
        logger.warning("Failed to register tool '%s': %s", schema.get("name", "?"), e)


_lsp_tool_registrations = [
    (CODE_DEFINITION_SCHEMA, _handle_code_definition),
    (CODE_TYPE_HIERARCHY_SCHEMA, _handle_code_type_hierarchy),
    (CODE_CALL_HIERARCHY_SCHEMA, _handle_code_call_hierarchy),
    (CODE_HIGHLIGHT_SCHEMA, _handle_code_highlight),
    (CODE_INLAY_HINTS_SCHEMA, _handle_code_inlay_hints),
    (CODE_DOCUMENT_SYMBOLS_SCHEMA, _handle_code_document_symbols),
    (CODE_REFERENCES_SCHEMA, _handle_code_references),
    (CODE_DIAGNOSTICS_SCHEMA, _handle_code_diagnostics),
    (CODE_CALLERS_SCHEMA, _handle_code_callers),
    (CODE_CALLEES_SCHEMA, _handle_code_callees),
    (CODE_WORKSPACE_SYMBOLS_SCHEMA, _handle_code_workspace_symbols),
    (CODE_RENAME_SCHEMA, _handle_code_rename),
    (CODE_HOVER_SCHEMA, _handle_code_hover),
    (CODE_FORMAT_SCHEMA, _handle_code_format),
    (CODE_TYPE_DEFINITION_SCHEMA, _handle_code_type_definition),
    (CODE_IMPLEMENTATIONS_SCHEMA, _handle_code_implementations),
    (CODE_SIGNATURES_SCHEMA, _handle_code_signatures),
    (CODE_ACTION_SCHEMA, _handle_code_action),
]


def register_lsp_tools(ctx) -> None:
    """Register LSP-backed tools via PluginContext.

    Called from ``__init__.py`` to make tools available to the agent.
    """
    for schema, handler in _lsp_tool_registrations:
        _register_lsp_tool(ctx, schema, handler)

    logger.info("code_intel: 18 LSP tools registered via ctx.register_tool()")
