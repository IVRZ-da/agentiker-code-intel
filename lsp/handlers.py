"""lsp/handlers.py — Tool schemas, handlers, and registration extracted from lsp_bridge.py."""

from __future__ import annotations

from typing import Any, Optional

from .._fmt import fmt_ok, fmt_err  # unused after subpackage split
from .bridge import logger
from .tools import (
    _safe_register, _check_lsp_reqs,
    CODE_DEFINITION_SCHEMA, CODE_REFERENCES_SCHEMA,
    CODE_DIAGNOSTICS_SCHEMA, CODE_HOVER_SCHEMA,
    CODE_RENAME_SCHEMA, CODE_FORMAT_SCHEMA,
    CODE_TYPE_DEFINITION_SCHEMA, CODE_IMPLEMENTATIONS_SCHEMA,
    CODE_SIGNATURES_SCHEMA, CODE_ACTION_SCHEMA,
    CODE_HIGHLIGHT_SCHEMA, CODE_INLAY_HINTS_SCHEMA,
    CODE_DOCUMENT_SYMBOLS_SCHEMA, CODE_CALLERS_SCHEMA,
    CODE_CALLEES_SCHEMA, CODE_CALL_HIERARCHY_SCHEMA,
    CODE_TYPE_HIERARCHY_SCHEMA, CODE_WORKSPACE_SYMBOLS_SCHEMA,
    _handle_code_definition, _handle_code_type_hierarchy,
    _handle_code_call_hierarchy, _handle_code_highlight,
    _handle_code_inlay_hints, _handle_code_document_symbols,
    _handle_code_references, _handle_code_diagnostics,
    _handle_code_callers, _handle_code_callees,
    _handle_code_workspace_symbols, _handle_code_rename,
    _handle_code_hover, _handle_code_format,
    _handle_code_type_definition, _handle_code_implementations,
    _handle_code_signatures, _handle_code_action,
)


def register_lsp_tools() -> None:
    """Register code_definition and code_references with the tool registry.

    Called from ``code_tools.py`` to keep registration in one place.
    """
    from tools.registry import registry

    _safe_register(
        name="code_definition",
        toolset="agentiker_code_intel",
        schema=CODE_DEFINITION_SCHEMA,
        handler=_handle_code_definition,
        check_fn=_check_lsp_reqs,
        emoji="📍",
    )

    _safe_register(
        name="code_type_hierarchy",
        toolset="agentiker_code_intel",
        schema=CODE_TYPE_HIERARCHY_SCHEMA,
        handler=_handle_code_type_hierarchy,
        check_fn=_check_lsp_reqs,
        emoji="🏛️",
    )

    _safe_register(
        name="code_call_hierarchy",
        toolset="agentiker_code_intel",
        schema=CODE_CALL_HIERARCHY_SCHEMA,
        handler=_handle_code_call_hierarchy,
        check_fn=_check_lsp_reqs,
        emoji="🌳",
    )

    _safe_register(
        name="code_highlight",
        toolset="agentiker_code_intel",
        schema=CODE_HIGHLIGHT_SCHEMA,
        handler=_handle_code_highlight,
        check_fn=_check_lsp_reqs,
        emoji="🟡",
    )

    _safe_register(
        name="code_inlay_hints",
        toolset="agentiker_code_intel",
        schema=CODE_INLAY_HINTS_SCHEMA,
        handler=_handle_code_inlay_hints,
        check_fn=_check_lsp_reqs,
        emoji="🔍",
    )

    _safe_register(
        name="code_document_symbols",
        toolset="agentiker_code_intel",
        schema=CODE_DOCUMENT_SYMBOLS_SCHEMA,
        handler=_handle_code_document_symbols,
        check_fn=_check_lsp_reqs,
        emoji="📋",
    )

    _safe_register(
        name="code_references",
        toolset="agentiker_code_intel",
        schema=CODE_REFERENCES_SCHEMA,
        handler=_handle_code_references,
        check_fn=_check_lsp_reqs,
        emoji="🔗",
    )

    _safe_register(
        name="code_diagnostics",
        toolset="agentiker_code_intel",
        schema=CODE_DIAGNOSTICS_SCHEMA,
        handler=_handle_code_diagnostics,
        check_fn=_check_lsp_reqs,
        emoji="🩺",
    )

    _safe_register(
        name="code_callers",
        toolset="agentiker_code_intel",
        schema=CODE_CALLERS_SCHEMA,
        handler=_handle_code_callers,
        check_fn=_check_lsp_reqs,
        emoji="📤",
    )

    _safe_register(
        name="code_callees",
        toolset="agentiker_code_intel",
        schema=CODE_CALLEES_SCHEMA,
        handler=_handle_code_callees,
        check_fn=_check_lsp_reqs,
        emoji="📥",
    )

    _safe_register(
        name="code_workspace_symbols",
        toolset="agentiker_code_intel",
        schema=CODE_WORKSPACE_SYMBOLS_SCHEMA,
        handler=_handle_code_workspace_symbols,
        check_fn=_check_lsp_reqs,
        emoji="🔎",
    )

    _safe_register(
        name="code_rename",
        toolset="agentiker_code_intel",
        schema=CODE_RENAME_SCHEMA,
        handler=_handle_code_rename,
        check_fn=_check_lsp_reqs,
        emoji="✏️",
    )

    _safe_register(
        name="code_hover",
        toolset="agentiker_code_intel",
        schema=CODE_HOVER_SCHEMA,
        handler=_handle_code_hover,
        check_fn=_check_lsp_reqs,
        emoji="💡",
    )

    _safe_register(
        name="code_format",
        toolset="agentiker_code_intel",
        schema=CODE_FORMAT_SCHEMA,
        handler=_handle_code_format,
        check_fn=_check_lsp_reqs,
        emoji="🎨",
    )

    _safe_register(
        name="code_type_definition",
        toolset="agentiker_code_intel",
        schema=CODE_TYPE_DEFINITION_SCHEMA,
        handler=_handle_code_type_definition,
        check_fn=_check_lsp_reqs,
        emoji="🧬",
    )

    _safe_register(
        name="code_implementations",
        toolset="agentiker_code_intel",
        schema=CODE_IMPLEMENTATIONS_SCHEMA,
        handler=_handle_code_implementations,
        check_fn=_check_lsp_reqs,
        emoji="🔨",
    )

    _safe_register(
        name="code_signatures",
        toolset="agentiker_code_intel",
        schema=CODE_SIGNATURES_SCHEMA,
        handler=_handle_code_signatures,
        check_fn=_check_lsp_reqs,
        emoji="📝",
    )

    _safe_register(
        name="code_action",
        toolset="agentiker_code_intel",
        schema=CODE_ACTION_SCHEMA,
        handler=_handle_code_action,
        check_fn=_check_lsp_reqs,
        emoji="🔧",
    )

    logger.info("LSP tools registered: code_definition, code_references, code_diagnostics, code_callers, code_callees, code_workspace_symbols, code_rename, code_hover, code_type_definition, code_signatures, code_action, code_format, code_implementations, code_type_hierarchy, code_call_hierarchy, code_highlight, code_inlay_hints, code_document_symbols")
