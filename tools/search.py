"""tools/search.py — Search tools wrapping code_tools implementations."""

from __future__ import annotations

from code_intel.code_tools import (
    code_search_tool,
    code_search_by_error_tool,
    code_hot_paths_tool,
)

__all__ = ["code_search_tool", "code_search_by_error_tool", "code_hot_paths_tool"]
