"""lsp/tools_extra.py — Re-Export Facade

All code moved to lsp/extra/ subpackage.
This file re-exports everything for backward compatibility.
"""
from __future__ import annotations

from .bridge import _resolve_command  # noqa: F401
from .extra.actions import (  # noqa: F401
    CODE_ACTION_SCHEMA,
    _apply_workspace_edit,
    _filter_diagnostics_in_range,
    _handle_code_action,
    _summarize_actions,
    code_action_tool,
)
from .extra.completion import (  # noqa: F401
    CODE_CODE_LENS_SCHEMA,
    CODE_COMPLETION_SCHEMA,
    CODE_FOLDING_RANGE_SCHEMA,
    CODE_LINKED_EDITING_SCHEMA,
    CODE_PREPARE_RENAME_SCHEMA,
    CODE_SELECTION_RANGE_SCHEMA,
    _handle_code_code_lens,
    _handle_code_completion,
    _handle_code_folding_range,
    _handle_code_linked_editing,
    _handle_code_prepare_rename,
    _handle_code_selection_range,
    code_code_lens_tool,
    code_completion_tool,
    code_folding_range_tool,
    code_linked_editing_tool,
    code_prepare_rename_tool,
    code_selection_range_tool,
)
from .extra.definition import (  # noqa: F401
    CODE_IMPLEMENTATIONS_SCHEMA,
    CODE_TYPE_DEFINITION_SCHEMA,
    _handle_code_implementations,
    _handle_code_type_definition,
    code_implementations_tool,
    code_type_definition_tool,
)
from .extra.registration import (  # noqa: F401
    _safe_register,
)
from .extra.signatures import (  # noqa: F401
    CODE_SIGNATURES_SCHEMA,
    _check_lsp_reqs,
    _extract_md,
    _format_signatures,
    _handle_code_signatures,
    code_signatures_tool,
)
from .extra.tokens import (  # noqa: F401
    CODE_DOCUMENT_LINKS_SCHEMA,
    CODE_INLINE_VALUES_SCHEMA,
    CODE_SEMANTIC_TOKENS_SCHEMA,
    _handle_code_document_links,
    _handle_code_inline_values,
    _handle_code_semantic_tokens,
    code_document_links_tool,
    code_inline_values_tool,
    code_semantic_tokens_tool,
)
