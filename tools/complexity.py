#!/usr/bin/env python3
"""tools/complexity.py — Cyclomatic complexity analysis tool.

Native module (not a re-export facade).
Provides code_complexity_tool, CODE_COMPLEXITY_SCHEMA, and _handle_code_complexity
with all helper functions defined inline.
"""

from __future__ import annotations

from .._fmt import fmt_err, fmt_json
from .._logging import setup_logger as _setup_code_intel_logger
from .base import (
    _get_language,
    _get_parser,
    detect_language,
)  # noqa: E402

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# Language -> AST node types for complexity counting
# ---------------------------------------------------------------------------

_COMPLEXITY_NODE_TYPES: dict = {
    "python": {
        "branches": ["if_statement", "elif_clause"],
        "loops": ["for_statement", "while_statement"],
        "exceptions": ["except_clause", "finally_clause"],
        "return_type": "return_statement",
    },
    "typescript": {
        "branches": ["if_statement", "switch_case", "ternary_expression"],
        "loops": ["for_statement", "for_in_statement", "while_statement", "do_statement"],
        "exceptions": ["catch_clause", "finally_clause"],
        "return_type": "return_statement",
    },
    "tsx": {
        "branches": ["if_statement", "switch_case", "ternary_expression"],
        "loops": ["for_statement", "for_in_statement", "while_statement", "do_statement"],
        "exceptions": ["catch_clause", "finally_clause"],
        "return_type": "return_statement",
    },
    "go": {
        "branches": ["if_statement", "switch_statement", "select_statement"],
        "loops": ["for_statement", "range_clause"],
        "exceptions": [],
        "return_type": "return_statement",
    },
    "rust": {
        "branches": ["if_expression", "match_expression", "match_arm"],
        "loops": ["for_expression", "loop_expression", "while_expression"],
        "exceptions": [],
        "return_type": "return_expression",
    },
}

# Function-finding queries per language
_FUNCTION_QUERIES: dict = {
    "python": """
(function_definition
    name: (identifier) @name
) @def
""",
    "typescript": """
(function_declaration
    name: (identifier) @name
) @def
(method_definition
    name: (property_identifier) @name
) @def
""",
    "tsx": """
(function_declaration
    name: (identifier) @name
) @def
(method_definition
    name: (property_identifier) @name
) @def
""",
    "go": """
(function_declaration
    name: (identifier) @name
) @def
(method_declaration
    name: (field_identifier) @name
) @def
""",
    "rust": """
(function_item
    name: (identifier) @name
) @def
""",
}


def _count_nodes(node, types: list) -> int:
    """Count descendants with matching node types."""
    count = 0
    if node.type in types:
        count += 1
    for child in node.named_children:
        count += _count_nodes(child, types)
    return count


def _count_early_returns(node, body_node, return_type: str) -> int:
    """Count returns that are NOT the last statement in the function body."""
    count = 0
    if node.type == return_type:
        try:
            children = list(body_node.named_children)
            if node is not children[-1]:
                count += 1
        except Exception:
            logger.debug("_count_early_returns: AST parse error for node type %s", node.type)
            count += 1
    for child in node.named_children:
        count += _count_early_returns(child, body_node, return_type)
    return count


def _scan_directory_for_complexity(target, ext, lang_key, all_results):
    """Scan a single file extension across target directory for complexity."""
    ntypes = _COMPLEXITY_NODE_TYPES.get(lang_key)
    if not ntypes:
        return
    parser = _get_parser(lang_key)
    lang_obj = _get_language(lang_key)
    if parser is None or lang_obj is None:
        return
    for fpath in sorted(target.rglob(f"*{ext}")):
        parts = fpath.parts
        if any(p in parts for p in ("node_modules", ".git", "__pycache__", "build", "dist", ".venv")):
            continue
        try:
            source_bytes = fpath.read_bytes()
        except OSError as e:
            logger.debug("_scan_directory_for_complexity: reading file: %s", e)
            continue
        tree = parser.parse(source_bytes)
        if tree is None:
            continue
        from tree_sitter import Query, QueryCursor
        fq = _FUNCTION_QUERIES.get(lang_key)
        if not fq:
            continue
        try:
            func_query = Query(lang_obj, fq)
        except Exception as e:
            logger.debug("_scan_directory_for_complexity: compiling Query: %s", e)
            continue
        for _pi, cd in QueryCursor(func_query).matches(tree.root_node):
            name = ""
            for nn in cd.get("name", []):
                try:
                    name = source_bytes[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
                except Exception:
                    name = "?"
                break
            for dn in cd.get("def", []):
                branches = _count_nodes(dn, ntypes.get("branches", []))
                loops = _count_nodes(dn, ntypes.get("loops", []))
                exceptions = _count_nodes(dn, ntypes.get("exceptions", []))
                early_returns = _count_early_returns(dn, dn, ntypes.get("return_type", "return_statement"))
                total = 1 + branches + loops + exceptions + early_returns
                rank = "A" if total <= 10 else "B" if total <= 20 else "C" if total <= 30 else "D" if total <= 40 else "E"
                all_results.append({
                    "function": name,
                    "file": str(fpath),
                    "line": dn.start_point[0] + 1,
                    "total": total,
                    "rank": rank,
                })
                break



def _analyze_file_complexity_single(target, lang_key):
    """Analyze complexity of a single file. Returns formatted result."""
    ntypes = _COMPLEXITY_NODE_TYPES[lang_key]
    parser = _get_parser(lang_key)
    lang_obj = _get_language(lang_key)
    if parser is None or lang_obj is None:
        return fmt_err(f"Parser init failed for {lang_key}")
    try:
        with open(str(target), "rb") as f:
            source_bytes = f.read()
    except OSError as exc:
        return fmt_err(f"Cannot read: {exc}")
    tree = parser.parse(source_bytes)
    if tree is None:
        return fmt_err("Parse failed")
    from tree_sitter import Query, QueryCursor
    fq = _FUNCTION_QUERIES.get(lang_key)
    if not fq:
        return fmt_err(f"No function query for {lang_key}")
    try:
        func_query = Query(lang_obj, fq)
    except Exception as exc:
        return fmt_err(f"Query failed: {exc}")
    functions = []
    qc = QueryCursor(func_query)
    for _pi, cd in qc.matches(tree.root_node):
        name = ""
        for nn in cd.get("name", []):
            try:
                name = source_bytes[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
            except Exception:
                name = "?"
            break
        for dn in cd.get("def", []):
            branches = _count_nodes(dn, ntypes.get("branches", []))
            loops = _count_nodes(dn, ntypes.get("loops", []))
            exceptions = _count_nodes(dn, ntypes.get("exceptions", []))
            early_returns = _count_early_returns(dn, dn, ntypes.get("return_type", "return_statement"))
            total = 1 + branches + loops + exceptions + early_returns
            rank = "A" if total <= 10 else "B" if total <= 20 else "C" if total <= 30 else "D" if total <= 40 else "E"
            functions.append({
                "function": name,
                "file": str(target),
                "line": dn.start_point[0] + 1,
                "total": total,
                "rank": rank,
            })
            break
    if not functions:
        return fmt_err("No functions found")
    if len(functions) == 1:
        return fmt_json(functions[0])
    functions.sort(key=lambda r: r["total"], reverse=True)
    return fmt_json({"functions": functions[:20], "total": len(functions)})


def _select_complexity_target(
    functions: list[dict],
    function_name: str = "",
    target_line: int = 0,
) -> dict | None:
    """Select a specific function from a list by name or line number."""
    if not functions:
        return None
    if function_name:
        for f in functions:
            if f.get("name") == function_name or f.get("function") == function_name:
                return f
    elif target_line:
        for f in functions:
            f_line = f.get("line", 0)
            f_end = f.get("end_line", f_line + 1)
            if f_line <= target_line <= f_end:
                return f
    return functions[0]


def _format_complexity_result(
    selected: dict,
    fn_node,
    ntypes: dict,
    target: str,
) -> dict:
    """Build the final complexity result dict with breakdown + recommendation."""
    branches = _count_nodes(fn_node, ntypes.get("branches", []))
    loops = _count_nodes(fn_node, ntypes.get("loops", []))
    exceptions = _count_nodes(fn_node, ntypes.get("exceptions", []))
    early_returns = _count_early_returns(fn_node, fn_node, ntypes.get("return_type", "return_statement"))
    total = 1 + branches + loops + exceptions + early_returns

    rank = "A" if total <= 10 else "B" if total <= 20 else "C" if total <= 30 else "D" if total <= 40 else "E"
    recommendation = ""
    if total > 20:
        recommendation = "Consider extracting sub-functions to reduce complexity."
    if total > 30:
        recommendation = "High complexity — refactoring strongly recommended."

    return {
        "function": selected.get("name") or selected.get("function", "?"),
        "path": str(target),
        "line": selected.get("line", 0),
        "total": total,
        "rank": rank,
        "breakdown": {
            "base": 1,
            "branches": branches,
            "loops": loops,
            "exceptions": exceptions,
            "early_returns": early_returns,
        },
        "recommendation": recommendation,
    }


def _select_and_format_complexity(target, lang_key, function_name, target_line):
    """Parse AST, extract functions, select target, and format result.

    Returns formatted result dict or None if no function found.
    """
    ntypes = _COMPLEXITY_NODE_TYPES[lang_key]
    parser = _get_parser(lang_key)
    lang_obj = _get_language(lang_key)
    if parser is None or lang_obj is None:
        return None

    try:
        with open(str(target), "rb") as f:
            source_bytes = f.read()
    except OSError:
        return None

    tree = parser.parse(source_bytes)
    if tree is None:
        return None

    from tree_sitter import Query, QueryCursor
    fq = _FUNCTION_QUERIES.get(lang_key)
    if not fq:
        return None

    try:
        func_query = Query(lang_obj, fq)
    except Exception:
        return None

    all_functions = []
    for _pi, cd in QueryCursor(func_query).matches(tree.root_node):
        name = ""
        for nn in cd.get("name", []):
            try:
                name = source_bytes[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
            except Exception:
                name = "?"
            break
        for dn in cd.get("def", []):
            all_functions.append({
                "name": name,
                "node": dn,
                "line": dn.start_point[0] + 1,
                "end_line": dn.end_point[0] + 1,
            })

    if not all_functions:
        return None

    selected = _select_complexity_target(all_functions, function_name, target_line)
    if selected is None:
        return None
    return _format_complexity_result(selected, selected["node"], ntypes, target)


def code_complexity_tool(
    path: str,
    function: str = "",
    line: int = 0,
    language: str = "",
    directory: bool = False,
) -> str:
    """Calculate cyclomatic complexity for a function.

    Analyzes branches, loops, exceptions, and early returns.
    Reports total complexity with breakdown and rank (A-E).

    When directory=True, scans all source files recursively and returns
    a sorted project-level hotspot report (functions with highest complexity first).
    """
    from pathlib import Path as _Path

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # ── Directory mode: scan all source files ──────────────────────
    if directory:
        if not target.is_dir():
            return fmt_err(f"Not a directory: {path}")

        _EXT_MAP = {
            ".py": "python", ".ts": "typescript", ".tsx": "tsx",
            ".js": "typescript", ".jsx": "tsx",
            ".go": "go", ".rs": "rust",
        }

        all_results = []
        for ext, lang_key in _EXT_MAP.items():
            _scan_directory_for_complexity(target, ext, lang_key, all_results)

        if not all_results:
            return fmt_err("No functions found in directory")

        all_results.sort(key=lambda r: r["total"], reverse=True)
        top = all_results[:50]
        summary = {
            "mode": "directory",
            "path": str(target),
            "total_functions": len(all_results),
            "total_files": len(set(r["file"] for r in all_results)),
            "hotspots": top,
        }
        return fmt_json(summary)

    # ── Single-file mode ──────────────────────────
    lang_key = language or detect_language(str(target))
    if not lang_key:
        return fmt_err("Could not detect language")
    if lang_key not in _COMPLEXITY_NODE_TYPES:
        return fmt_err(f"Unsupported language: {lang_key}")

    # 1. Parse file via shared helper
    raw = _analyze_file_complexity_single(target, lang_key)

    # 2. Select target function
    import json as _json
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        return raw  # Error path — pass through fmt_err message

    # fmt_err results have "status": "error"
    if isinstance(data, dict) and data.get("status") == "error":
        return raw

    # 3. Parse AST + select target function
    result = _select_and_format_complexity(target, lang_key, function, line)
    if result is None:
        return fmt_err("No matching function found in file")
    return fmt_json(result)


# Schema + Handler
CODE_COMPLEXITY_SCHEMA = {
    "name": "code_complexity",
    "description": "Calculate cyclomatic complexity for a function or scan for hotspots."
                   "Analyzes branches, loops, exceptions, and early returns. "
                   "Reports total complexity with breakdown and rank (A-E). "
                   "Set directory=True for project-level hotspot analysis.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "function": {"type": "string", "description": "Function name to analyze (optional, analyzes first if omitted)"},
            "line": {"type": "integer", "description": "1-based line number (optional, finds function at this line)"},
            "language": {"type": "string", "description": "Language override"},
            "directory": {"type": "boolean", "description": "Scan directory recursively for complexity hotspots (default: false)"},
        },
        "required": ["path"],
    },
}


def _handle_code_complexity(args, **kw):
    return code_complexity_tool(
        path=args.get("path", ""),
        function=args.get("function", ""),
        line=args.get("line", 0),
        language=args.get("language", ""),
        directory=args.get("directory", False),
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "CODE_COMPLEXITY_SCHEMA",
    "code_complexity_tool",
    "_handle_code_complexity",
]
