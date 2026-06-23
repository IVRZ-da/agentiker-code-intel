"""tools/search.py — Search tools wrapping code_tools implementations."""
from __future__ import annotations

from .graph_analysis import code_hot_paths_tool  # noqa: E402
from .search_by_error import code_search_by_error_tool  # noqa: E402

__all__ = ["code_search_tool", "code_search_by_error_tool", "code_hot_paths_tool"]


def code_search_tool(*args, **kwargs):
    """Lazy wrapper: avoids circular import with code_tools."""
    from ..code_tools import code_search_tool as _real
    return _real(*args, **kwargs)
