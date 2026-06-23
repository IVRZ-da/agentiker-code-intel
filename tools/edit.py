"""tools/edit.py — Refactoring tools wrapping code_tools implementations."""
from __future__ import annotations

from .ast_edit import (
    code_insert_after_tool,
    code_insert_before_tool,
    code_replace_body_tool,
    code_safe_delete_tool,
)

__all__ = [
    "code_refactor_tool", "code_replace_body_tool",
    "code_safe_delete_tool", "code_insert_before_tool",
    "code_insert_after_tool",
]


def code_refactor_tool(*args, **kwargs):
    """Lazy wrapper: avoids circular import with code_tools."""
    from ..code_tools import code_refactor_tool as _real
    return _real(*args, **kwargs)
