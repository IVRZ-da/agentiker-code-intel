"""tools/query.py — Native query router for code intelligence.

Contains code_query_tool, _handle_code_query, CODE_QUERY_SCHEMA, and the
internal _QUERY_INTENT_MAP. Extracted from code_tools.py (lines 4294-4423)
to make tools/query.py self-contained instead of a re-export facade.
"""

from __future__ import annotations

from typing import Optional

from .._fmt import fmt_ok

_QUERY_INTENT_MAP = {
    "find_usage": ("code_references", "search_files"),
    "find_usages": ("code_references", "search_files"),
    "references": ("code_references", "search_files"),
    "definition": ("code_definition", "code_symbols"),
    "go_to_def": ("code_definition", "code_symbols"),
    "where_defined": ("code_definition", "code_symbols"),
    "rename": ("code_rename", "code_refactor"),
    "semantic_rename": ("code_rename", "code_refactor"),
    "refactor": ("code_refactor", "patch"),
    "safe_edit": ("code_rename", "code_refactor"),
    "understand": ("code_capsule", "code_symbols"),
    "what_is": ("code_capsule", "code_symbols"),
    "overview": ("code_workspace_summary", "code_symbols"),
    "structure": ("code_symbols", "read_file"),
    "symbols": ("code_symbols", "read_file"),
    "functions": ("code_symbols", "read_file"),
    "classes": ("code_symbols", "read_file"),
    "tests": ("code_tests_for_symbol", "search_files"),
    "test_coverage": ("code_tests_for_symbol", "search_files"),
    "impact": ("code_impact", "code_references"),
    "blast_radius": ("code_impact", "code_references"),
    "diagnostics": ("code_diagnostics", "code_symbols"),
    "errors": ("code_diagnostics", "search_files"),
    "warnings": ("code_diagnostics", "search_files"),
    "callers": ("code_callers", "code_references"),
    "who_calls": ("code_callers", "code_references"),
    "callees": ("code_callees", "code_symbols"),
    "what_calls": ("code_callees", "code_symbols"),
    "search_pattern": ("code_search", "search_files"),
    "find_pattern": ("code_search", "search_files"),
    "structural": ("code_search", "search_files"),
    # -- New LSP tools --
    "hover": ("code_hover", "code_capsule"),
    "type_info": ("code_hover", "code_capsule"),
    "docstring": ("code_hover", "code_capsule"),
    "signature": ("code_signatures", "code_hover"),
    "params": ("code_signatures", "code_hover"),
    "arguments": ("code_signatures", "code_hover"),
    "type_definition": ("code_type_definition", "code_definition"),
    "type_of": ("code_type_definition", "code_definition"),
    "interface": ("code_type_definition", "code_definition"),
    "quick_fix": ("code_action", "code_refactor"),
    "organize_imports": ("code_action", "code_refactor"),
    "auto_fix": ("code_action", "code_refactor"),
    "code_action": ("code_action", "code_refactor"),
    "find_symbol": ("code_workspace_symbols", "code_search"),
    "workspace_search": ("code_workspace_symbols", "code_search"),
    "cmd_t": ("code_workspace_symbols", "code_search"),
    # -- Symbol-level editing tools (v0.28.11) --
    "replace_body": ("code_replace_body", "code_refactor"),
    "replace_function": ("code_replace_body", "code_refactor"),
    "replace_method": ("code_replace_body", "code_refactor"),
    "safe_delete": ("code_safe_delete", "patch"),
    "delete_symbol": ("code_safe_delete", "patch"),
    "insert_before": ("code_insert_before", "patch"),
    "insert_after": ("code_insert_after", "patch"),
    "insert_symbol_before": ("code_insert_before", "patch"),
    "insert_symbol_after": ("code_insert_after", "patch"),
    # -- code_overview (v0.28.11) --
    "file_overview": ("code_overview", "code_symbols"),
    "symbol_overview": ("code_overview", "code_symbols"),
    "file_summary": ("code_overview", "code_symbols"),
}

CODE_QUERY_SCHEMA = {
    "name": "code_query",
    "description": (
        "Smart query router for code intelligence. Describe what you want to find "
        "(e.g. 'find_usage', 'definition', 'rename', 'impact', 'tests', "
        "'replace_body', 'safe_delete', 'insert_before') and it auto-selects "
        "the best tool. Returns routing decision + recommended args. "
        "If you already know which tool to call, call it directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "What you want: find_usage, definition, rename, understand, overview, tests, impact, diagnostics, callers, callees, structure, search_pattern, replace_body, safe_delete, insert_before, insert_after, file_overview",
            },
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "Optional 1-based line number"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["intent"],
    },
}


def code_query_tool(intent: str, path: Optional[str] = None, line: int = 0, language: Optional[str] = None) -> str:
    """Route a code intelligence query to the best available tool."""
    intent_lower = intent.lower().strip().replace(" ", "_")
    matched = _QUERY_INTENT_MAP.get(intent_lower)
    if not matched:
        for key, val in _QUERY_INTENT_MAP.items():
            if key in intent_lower or intent_lower in key:
                matched = val
                break
    if not matched:
        return fmt_ok({
            "intent": intent,
            "routed_to": "search_files",
            "reason": f"No match for '{intent}'. Falling back.",
            "available_intents": sorted(set(_QUERY_INTENT_MAP.keys())),
        })
    primary, fallback = matched
    args = {}
    if path:
        args["path"] = path
    if line and line > 0:
        args["line"] = line
    if language:
        args["language"] = language
    if primary == "code_search":
        args.setdefault("preset", "function_calls")
    return fmt_ok({
        "intent": intent,
        "routed_to": primary,
        "fallback": fallback,
        "recommended_args": args,
    })


def _handle_code_query(args, **kw):
    return code_query_tool(
        intent=args.get("intent", ""),
        path=args.get("path"),
        line=int(args.get("line", 0)),
        language=args.get("language"),
    )


__all__ = [
    "code_query_tool",
    "_handle_code_query",
    "CODE_QUERY_SCHEMA",
]
