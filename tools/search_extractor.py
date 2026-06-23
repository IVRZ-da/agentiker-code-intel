"""Extracted from code_tools.py — search_extractor."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger
from .base import (
    _EXT_TO_LANG,
    _get_language,
    _get_parser,
    detect_language,
)

logger = _setup_code_intel_logger(__name__)


# ---------------------------------------------------------------------------
# code_search — AST-aware structural search via tree-sitter Query
# ---------------------------------------------------------------------------

# Preset queries for common patterns (user can also pass raw queries)
_CODE_SEARCH_PRESETS = {
    "function_calls": {
        "python": "(call function: (identifier) @func) @call",
        "typescript": "(call_expression function: (identifier) @func) @call",
        "javascript": "(call_expression function: (identifier) @func) @call",
        "rust": "(call_expression function: (identifier) @func) @call",
        "go": "(call_expression function: (identifier) @func) @call",
        "java": "(method_invocation name: (identifier) @func) @call",
    },
    "string_literals": {
        "python": '(string) @str',
        "typescript": '(string) @str',
        "javascript": '(string) @str',
        "rust": '(string_literal) @str',
        "go": '(interpreted_string_literal) @str',
        "java": '(string_literal) @str',
    },
    "imports": {
        "python": "(import_statement) @import\n(import_from_statement) @import",
        "typescript": "(import_statement) @import",
        "javascript": "(import_statement) @import",
        "rust": "(use_declaration) @import",
        "go": "(import_declaration) @import",
        "java": "(import_declaration) @import",
    },
    "decorator_calls": {
        "python": "(decorator) @deco",
        "typescript": "(decorator) @deco",
        "javascript": "(decorator) @deco",
    },
    "try_catch": {
        "python": "(try_statement) @tc",
        "typescript": "(try_statement) @tc",
        "javascript": "(try_statement) @tc",
        "java": "(try_statement) @tc",
    },
    "return_stmts": {
        "python": "(return_statement) @ret",
        "typescript": "(return_statement) @ret",
        "javascript": "(return_statement) @ret",
        "rust": "(return_expression) @ret",
        "go": "(return_statement) @ret",
        "java": "(return_statement) @ret",
    },
    "assignments": {
        "python": "(assignment left: (_) @lhs right: (_) @rhs) @assign",
        "typescript": "(assignment_expression left: (_) @lhs right: (_) @rhs) @assign",
        "javascript": "(assignment_expression left: (_) @lhs right: (_) @rhs) @assign",
        "go": "(short_var_declaration left: (_) @lhs right: (_) @rhs) @assign",
    },
    "member_calls": {
        "typescript": "(call_expression function: (member_expression property: (property_identifier) @method) @call) @member_call",
        "javascript": "(call_expression function: (member_expression property: (property_identifier) @method) @call) @member_call",
        "python": "(call function: (attribute attr: (identifier) @method) @call) @member_call",
        "go": "(call_expression function: (selector_expression field: (field_identifier) @method) @call) @member_call",
        "java": "(method_invocation name: (identifier) @method) @member_call",
        "rust": "(call_expression function: (field_expression field: (field_identifier) @method) @call) @member_call",
    },
}

# Alias presets to common names
_PRESET_ALIASES = {
    "calls": "function_calls",
    "strings": "string_literals",
    "imports": "imports",
    "decorators": "decorator_calls",
    "try": "try_catch",
    "catch": "try_catch",
    "methods": "member_calls",
    "returns": "return_stmts",
    "assigns": "assignments",
}


def _resolve_preset(preset: str, lang_key: str) -> Optional[str]:
    """Resolve a preset name to a tree-sitter query string."""
    canonical = _PRESET_ALIASES.get(preset, preset)
    lang_queries = _CODE_SEARCH_PRESETS.get(canonical)
    if lang_queries is None:
        return None
    return lang_queries.get(lang_key)


def code_search_tool(
    path: str,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
    _raw: bool = False,
) -> str:

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Code intelligence dependencies are not installed. Please run: uv pip install 'hermes-agent[code-intel]'")
    """AST-aware structural code search using tree-sitter Query API.

    Supports three modes:
    1. Raw tree-sitter query (via 'query' param)
    2. Named preset like 'function_calls', 'imports', 'try_catch', etc.
    3. Simple text pattern filter on captured nodes (via 'pattern' param)

    Accepts both files and directories (recursive scan of supported files).
    """
    target = Path(path).expanduser().resolve()

    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_file():
        return _code_search_single_file(target, query, preset, pattern, language, max_results, _raw=_raw)

    # Directory: scan all supported files recursively
    return _code_search_directory(target, query, preset, pattern, language, max_results, _raw=_raw)


def _code_search_single_file(
    target: Path,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
    _raw: bool = False,
) -> str:
    """Run code_search on a single file."""
    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return fmt_err(f"Unsupported language for '{target}'. "
                f"Supported: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            )

    query_str = _resolve_query(query, preset, pattern, lang_key, str(target))
    if isinstance(query_str, str) and query_str.startswith("{"):
        return query_str  # error JSON

    parser = _get_parser(lang_key)
    lang = _get_language(lang_key)
    if parser is None or lang is None:
        return fmt_err(f"No tree-sitter grammar for {lang_key}")

    source = target.read_bytes()
    tree = parser.parse(source)

    try:
        from tree_sitter import Query, QueryCursor
        ts_query = Query(lang, query_str)
    except Exception as e:
        return fmt_err(f"Invalid tree-sitter query: {e}")

    qc = QueryCursor(ts_query)
    results = []
    seen_spans = set()

    for _pat_idx, captures_dict in qc.matches(tree.root_node):
        for cap_name, nodes in captures_dict.items():
            for node in nodes:
                row, col = node.start_point
                end_row, end_col = node.end_point
                span = (row, col, end_row, end_col)

                if span in seen_spans:
                    continue
                seen_spans.add(span)

                text = node.text.decode("utf-8", errors="replace")

                if pattern and pattern.lower() not in text.lower():
                    continue

                display = text if len(text) <= 200 else text[:197] + "..."

                results.append({
                    "capture": cap_name,
                    "text": display,
                    "line": row + 1,
                    "end_line": end_row + 1,
                    "column": col,
                    "kind": node.type,
                })

                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    truncated = len(results) >= max_results
    data = {
        "path": str(target),
        "language": lang_key,
        "query": query_str[:200],
        "match_count": len(results),
        "truncated": truncated,
        "results": results,
    }
    return json.dumps(data) if _raw else fmt_ok(data)


def _code_search_directory(
    target: Path,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
    _raw: bool = False,
) -> str:
    """Run code_search across all supported files in a directory."""
    results = []
    files_scanned = 0
    remaining = max_results

    for ext in _EXT_TO_LANG:
        if remaining <= 0:
            break
        for file_path in sorted(target.rglob(f"*{ext}")):
            if remaining <= 0:
                break
            if not file_path.is_file():
                continue
            file_lang = detect_language(str(file_path), language)
            if file_lang is None:
                continue

            file_matches = _search_single_file(file_path, file_lang, query, preset,
                                                pattern, remaining)
            if file_matches is None:
                continue

            files_scanned += 1
            results.extend(file_matches["results"])
            remaining = file_matches["remaining"]

    if not results:
        data = {
            "path": str(target),
            "message": "No matches found in directory.",
            "files_scanned": files_scanned,
            "files_with_matches": 0,
            "match_count": 0,
        }
        return json.dumps(data) if _raw else fmt_ok(data)

    data = {
        "path": str(target),
        "files_scanned": files_scanned,
        "files_with_matches": len({r["file"] for r in results}),
        "match_count": len(results),
        "results": results,
    }
    return json.dumps(data) if _raw else fmt_ok(data)


def _search_single_file(
    file_path: Path, file_lang: str,
    query: Optional[str], preset: Optional[str],
    pattern: Optional[str], remaining: int,
) -> Optional[dict]:
    """Search a single file with the given query. Returns result dict or None."""
    query_str = _resolve_query(query, preset, pattern, file_lang, str(file_path))
    if isinstance(query_str, str) and query_str.startswith("{"):
        return None

    parser = _get_parser(file_lang)
    lang = _get_language(file_lang)
    if parser is None or lang is None:
        return None

    try:
        source = file_path.read_bytes()
    except (OSError, PermissionError):
        return None

    tree = parser.parse(source)
    try:
        from tree_sitter import Query, QueryCursor
        ts_query = Query(lang, query_str)
    except Exception:
        return None

    qc = QueryCursor(ts_query)
    file_results = []

    for _pat_idx, captures_dict in qc.matches(tree.root_node):
        if remaining <= 0:
            break
        file_results, remaining = _process_match_captures(
            captures_dict, file_path, pattern, file_results, remaining
        )
        if remaining <= 0:
            break

    if not file_results:
        return None

    return {"results": file_results, "remaining": remaining}


def _process_match_captures(
    captures_dict: dict, file_path: Path,
    pattern: Optional[str], file_results: list, remaining: int,
) -> tuple[list, int]:
    """Process captures from a tree-sitter match. Returns (results, remaining)."""
    seen_spans = set()
    for cap_name, nodes in captures_dict.items():
        if remaining <= 0:
            break
        for node in nodes:
            if remaining <= 0:
                break

            row, col = node.start_point
            end_row, end_col = node.end_point
            span = (row, col, end_row, end_col)
            if span in seen_spans:
                continue
            seen_spans.add(span)

            text = node.text.decode("utf-8", errors="replace")
            if pattern and pattern.lower() not in text.lower():
                continue

            display = text if len(text) <= 200 else text[:197] + "..."
            file_results.append({
                "file": str(file_path),
                "capture": cap_name,
                "text": display,
                "line": row + 1,
                "end_line": end_row + 1,
                "column": col,
                "kind": node.type,
            })
            remaining -= 1
    return file_results, remaining
def _resolve_query(
    query: Optional[str],
    preset: Optional[str],
    pattern: Optional[str],
    lang_key: str,
    file_path: str,
) -> str:
    """Resolve query string from query/preset/pattern. Returns JSON error string on failure."""
    if query:
        return query
    elif preset:
        query_str = _resolve_preset(preset, lang_key)
        if query_str is None:
            available = sorted(_CODE_SEARCH_PRESETS.keys()) + sorted(_PRESET_ALIASES.keys())
            return fmt_ok({
                "error": f"Unknown preset '{preset}' for {lang_key} ({file_path}). "
                         f"Available: {', '.join(available)}",
            })
        return query_str
    elif pattern:
        return "(_) @node"
    else:
        return fmt_err("Provide 'query', 'preset', or 'pattern'. "
                     "Presets: function_calls, string_literals, imports, "
                     "decorator_calls, try_catch, return_stmts, assignments.")


CODE_SEARCH_SCHEMA = {
    "name": "code_search",
    "description": (
        "AST-aware structural code search — find function calls, imports, decorators, "
        "try/catch blocks, return statements, assignments by their semantic structure, "
        "not just text. Use this INSTEAD of search_files (grep) when searching for code "
        "patterns inside source files — it understands syntax and won't match comments "
        "or strings by accident. Accepts files and directories (recursive scan). "
        "Use named presets: function_calls, imports, decorator_calls, try_catch, "
        "return_stmts, string_literals, assignments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "query": {"type": "string", "description": "Raw tree-sitter query string (e.g. '(call function: (identifier) @func) @call')"},
            "preset": {"type": "string", "description": "Named preset: function_calls, string_literals, imports, decorator_calls, try_catch, return_stmts, assignments"},
            "pattern": {"type": "string", "description": "Simple text pattern to filter captured nodes (substring match)"},
            "language": {"type": "string", "description": "Override language auto-detection"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default: 50)"},
        },
        "required": ["path"],
    },
}


def _handle_code_search(args, **kw):
    return code_search_tool(
        path=args.get("path", ""),
        query=args.get("query"),
        preset=args.get("preset"),
        pattern=args.get("pattern"),
        language=args.get("language"),
        max_results=args.get("max_results", 50),
    )
