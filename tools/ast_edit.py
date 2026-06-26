"""
tools/ast_edit.py — Re-export Facade

All code moved to tools/ast_edit_tools/ subpackage for better modularity.
This file re-exports everything for backward compatibility.
"""
from __future__ import annotations

from .ast_edit_tools import (  # noqa: F401
    CODE_INSERT_AFTER_SCHEMA,
    CODE_INSERT_BEFORE_SCHEMA,
    CODE_MOVE_SCHEMA,
    CODE_REPLACE_BODY_SCHEMA,
    CODE_SAFE_DELETE_SCHEMA,
    _ast_search_references,
    _build_move_diff,
    _compute_new_file_contents,
    _find_symbol_in_ast,
    _handle_code_insert_after,
    _handle_code_insert_before,
    _handle_code_move,
    _handle_code_replace_body,
    _handle_code_safe_delete,
    _read_source_bytes,
    _read_target_bytes,
    _safe_write_with_backup,
    _validate_move_inputs,
    code_insert_after_tool,
    code_insert_before_tool,
    code_move_tool,
    code_replace_body_tool,
    code_safe_delete_tool,
)
