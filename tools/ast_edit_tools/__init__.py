"""ast-edit/ — AST-based code editing tools.

Sub-modules:
  schemas.py      — Tool schemas (CODE_REPLACE_BODY_SCHEMA, etc.)
  base.py          — Shared helpers (_find_symbol_in_ast, _ast_search_references)
  replace_body.py  — code_replace_body_tool + handler
  safe_delete.py   — code_safe_delete_tool + handler
  insert.py        — code_insert_before_tool/after_tool + handlers
  move.py          — code_move_tool + move helpers + handler
"""
from __future__ import annotations

from .base import (  # noqa: F401
    _ast_search_references,
    _find_symbol_in_ast,
)
from .insert import (  # noqa: F401
    _handle_code_insert_after,
    _handle_code_insert_before,
    code_insert_after_tool,
    code_insert_before_tool,
)
from .move import (  # noqa: F401
    _build_move_diff,
    _compute_new_file_contents,
    _handle_code_move,
    _read_source_bytes,
    _read_target_bytes,
    _safe_write_with_backup,
    _validate_move_inputs,
    code_move_tool,
)
from .replace_body import (  # noqa: F401
    _handle_code_replace_body,
    code_replace_body_tool,
)
from .safe_delete import (  # noqa: F401
    _handle_code_safe_delete,
    code_safe_delete_tool,
)
from .schemas import (  # noqa: F401
    CODE_INSERT_AFTER_SCHEMA,
    CODE_INSERT_BEFORE_SCHEMA,
    CODE_MOVE_SCHEMA,
    CODE_REPLACE_BODY_SCHEMA,
    CODE_SAFE_DELETE_SCHEMA,
)
