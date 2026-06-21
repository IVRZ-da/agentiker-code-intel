"""tools/ — AST code intelligence subpackage.

Split from code_tools.py (5781 lines → tools/*.py) for maintainability.
Follows the same pattern as the analysis plugin's tools/ subpackage.

Modules:
    base.py     — Infrastructure: language registry, caches, helpers
    symbols.py  — code_symbols: AST symbol extraction (direct code)
    search.py   — code_search, code_search_by_error, code_hot_paths (re-exports)
    edit.py     — code_refactor, code_replace_body, etc. (re-exports)
    analysis.py — code_impact, code_complexity, code_blast_radius, etc. (re-exports)
    capsule.py  — code_capsule (re-export)
    overview.py — code_overview (re-export)
    query.py    — code_query (re-export)

Usage:
    from code_intel.tools import code_search_tool, code_symbols_tool
"""

from .symbols import code_symbols_tool, extract_symbols
from .search import code_search_tool, code_search_by_error_tool, code_hot_paths_tool
from .edit import code_refactor_tool, code_replace_body_tool, code_safe_delete_tool
from .capsule import code_capsule_tool
from .overview import code_overview_tool
from .query import code_query_tool
from .git import (code_todo_finder_tool, code_merge_conflict_finder_tool,
                  code_git_log_symbol_tool, code_git_diff_file_tool)
from .analysis import (code_impact_tool, code_complexity_tool,
                       code_cycle_detector_tool, code_dependency_graph_tool,
                       code_blast_radius_tool, code_pr_impact_tool,
                       code_tests_for_symbol_tool, code_unused_finder_tool,
                       code_workspace_summary_tool)

__all__ = [
    "code_symbols_tool", "extract_symbols",
    "code_search_tool", "code_search_by_error_tool", "code_hot_paths_tool",
    "code_refactor_tool", "code_replace_body_tool", "code_safe_delete_tool",
    "code_capsule_tool", "code_overview_tool", "code_query_tool",
    "code_todo_finder_tool", "code_merge_conflict_finder_tool",
    "code_git_log_symbol_tool", "code_git_diff_file_tool",
    "code_impact_tool", "code_complexity_tool",
    "code_cycle_detector_tool", "code_dependency_graph_tool",
    "code_blast_radius_tool", "code_pr_impact_tool",
    "code_tests_for_symbol_tool", "code_unused_finder_tool",
    "code_workspace_summary_tool",
]
