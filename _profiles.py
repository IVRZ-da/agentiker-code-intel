"""_profiles.py — Tool Profile definitions for code_intel plugin.

Extracted from __init__.py for better modularity.
"""

from __future__ import annotations

import os
from typing import Optional

# ---------------------------------------------------------------------------
# Tool Profile System
# ---------------------------------------------------------------------------

_TOOL_PROFILES: dict = {
    "all": [
        "code_symbols", "code_search", "code_refactor",
        "code_definition", "code_references", "code_diagnostics",
        "code_callers", "code_callees", "code_capsule", "code_explain",
        "code_diagram_symbol",
        "code_workspace_summary", "code_impact", "code_tests_for_symbol",
        "code_query", "code_rename", "code_workspace_symbols",
        "code_hover", "code_type_definition",
        "code_signatures", "code_action",
        "code_format", "code_implementations",
        "code_call_hierarchy", "code_complexity",
        "code_type_hierarchy", "code_highlight",
        "code_inlay_hints", "code_document_symbols",
        "code_search_by_error", "code_hot_paths",
        "code_blast_radius", "code_pr_impact",
        "code_replace_body", "code_safe_delete",
        "code_insert_before", "code_insert_after",
        "code_overview", "code_cycle_detector",
        "code_dependency_graph", "code_unused_finder",
        "code_metrics", "code_duplicates", "code_move", "code_export",
        # New LSP 3.18 tools
        "code_completion", "code_code_lens",
        "code_folding_range", "code_selection_range",
        "code_linked_editing", "code_prepare_rename",
        # Additional LSP 3.18 tools
        "code_semantic_tokens",
        "code_document_links",
        "code_inline_values",
        # Git tools
        "code_todo_finder", "code_merge_conflict_finder",
        "code_git_log_symbol", "code_git_diff_file",
        # New AST tools
        "code_docstring_generate", "code_dependency_risk",
        # Batch refactoring
        "code_batch_refactor",
        # Security scanning
        "code_security_scan",
        # Git blame
        "code_git_blame",
        # Test generation
        "code_generate_tests",
        # Migration
        "code_migration",
        # Diff analysis
        "code_diff_analysis",
        # Timeline
        "code_timeline",
        # Knowledge graph
        "code_index",
        "code_graph_query",
        # Code review
        "code_review_assistant",
    ],
    # Core: daily drivers — navigation, search, understanding
    "core": [
        "code_symbols", "code_search", "code_definition",
        "code_references", "code_diagnostics",
        "code_callers", "code_callees", "code_capsule", "code_explain",
        "code_hover", "code_workspace_symbols",
        "code_query", "code_overview",
        # Git tools
        "code_todo_finder", "code_merge_conflict_finder",
        "code_git_diff_file",
        # Git blame
        "code_git_blame",
        # Batch refactoring
        "code_batch_refactor",
        # Diff & Timeline
        "code_diff_analysis",
        "code_timeline",
        # Knowledge graph
        "code_index",
        # Code review
        "code_review_assistant",
    ],
    # Search: AST-based search tools
    "search": [
        "code_search", "code_search_by_error",
        "code_symbols", "code_hot_paths",
        "code_workspace_symbols", "code_query",
        "code_callers", "code_callees",
        "code_git_log_symbol",
        "code_diagram_symbol",
        # Git blame
        "code_git_blame",
        # Security scanning
        "code_security_scan",
        # Diff & Timeline
        "code_diff_analysis",
        "code_timeline",
        # Graph queries
        "code_graph_query",
    ],
    # Edit: refactoring and code modification
    "edit": [
        "code_refactor", "code_replace_body", "code_safe_delete",
        "code_insert_before", "code_insert_after",
        "code_rename", "code_action",
        "code_format",
        "code_batch_refactor",
        "code_migration",
    ],
    # LSP: all LSP-powered tools
    "lsp": [
        "code_definition", "code_references", "code_diagnostics",
        "code_rename", "code_hover", "code_type_definition",
        "code_signatures", "code_action", "code_format",
        "code_implementations", "code_call_hierarchy",
        "code_type_hierarchy", "code_highlight",
        "code_inlay_hints", "code_document_symbols",
        "code_workspace_symbols",
        # New LSP 3.18 tools
        "code_completion", "code_code_lens",
        "code_folding_range", "code_selection_range",
        "code_linked_editing", "code_prepare_rename",
        # Additional LSP 3.18 tools
        "code_semantic_tokens",
        "code_document_links",
        "code_inline_values",
    ],
}


def get_active_profile() -> str:
    """Get the active tool profile from environment variable."""
    profile = os.environ.get("CODE_INTEL_TOOL_PROFILE", "core").lower()
    if profile not in _TOOL_PROFILES:
        profile = "all"
    return profile


def get_profile_tools(profile: Optional[str] = None) -> list:
    """Get the list of tools for a given profile."""
    if profile is None:
        profile = get_active_profile()
    return _TOOL_PROFILES.get(profile, _TOOL_PROFILES["all"])
