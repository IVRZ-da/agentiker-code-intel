"""Extracted from code_tools.py — duplicates_extractor."""
from __future__ import annotations

import re
from pathlib import Path

from .._fmt import fmt_json
from .._logging import setup_logger as _setup_code_intel_logger
from .base import (
    _SYMBOL_QUERIES,
    _get_language,
    _get_parser,
    detect_language,
)

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# B1: code_workspace_summary — Monorepo/Project overview
# ---------------------------------------------------------------------------


# Extension-to-language mapping for workspace summary
_EXT_LANG = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "typescript",
}
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# B2a: code_search_by_error — Find error handling sites
# ---------------------------------------------------------------------------

# Language-specific error detection queries
_ERROR_QUERIES: dict = {
    "python": """
; raise ValueError("msg")
(raise_statement
    (call
        function: (identifier) @error_name
    )
) @raise_site

; except ValueError:
(except_clause
    (identifier) @error_name
) @catch_site

; custom class(MyError):
(class_definition
    name: (identifier) @custom_name
    (argument_list
        (identifier) @error_name
    )
) @custom_site

; raise SomeException (without args)
(raise_statement
    (identifier) @error_name
) @raise_site
""",
    "typescript": """
; throw new Error("msg")
(throw_statement
    (new_expression
        constructor: (identifier) @error_name
    )
) @throw_site

; catch (e: Error)
(catch_clause
    (catch_parameter
        type: (type_identifier) @error_name
    )
) @catch_site

; class MyError extends Error
(class_declaration
    name: (type_identifier) @custom_name
    (class_heritage
        (identifier) @error_name
    )
) @custom_site

; throw SomeError
(throw_statement
    (identifier) @error_name
) @throw_site
""",
    "tsx": """
(throw_statement
    (new_expression
        constructor: (identifier) @error_name
    )
) @throw_site
(catch_clause
    (catch_parameter
        type: (type_identifier) @error_name
    )
) @catch_site
(class_declaration
    name: (type_identifier) @custom_name
    (class_heritage
        (identifier) @error_name
    )
) @custom_site
(throw_statement
    (identifier) @error_name
) @throw_site
""",
    "go": """
; return fmt.Errorf("msg")
(call_expression
    function: (selector_expression
        field: (field_identifier) @error_name
    )
) @return_site
""",
    "rust": """
; Err(MyError)
(match_pattern
    (identifier) @error_name
) @return_site
""",
}


# Files to exclude from search
_ERROR_EXCLUDE_DIRS = {"node_modules", ".venv", "__pycache__", ".git", ".next", "dist", "build", "target"}




# ---------------------------------------------------------------------------
# B3a: code_hot_paths — Hot Path Detection via ImportGraph
# ---------------------------------------------------------------------------
# C1: code_replace_body — Replace symbol body via AST
# ---------------------------------------------------------------------------
# C2: code_safe_delete — Delete symbol if unreferenced
# C3: code_insert_before — Insert code before a symbol
# ---------------------------------------------------------------------------
# C4: code_insert_after — Insert code after a symbol
# ---------------------------------------------------------------------------
# C5: code_overview — Compact file overview
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Unused Imports Detection — moved to tools/unused.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C12: code_move_tool — Move a symbol between files via AST extraction
# ---------------------------------------------------------------------------
# Duplicate Code Detection (C13) — AST-based duplicate/similar code finder
# ---------------------------------------------------------------------------


# ── Helper: function collection ───────────────────────────────────


def _collect_function_definitions(root: Path, max_files: int, timeout: int, min_lines: int):
    """Sammle alle Function-Definitionen via AST.

    Returns (functions, start_time, total_files) mit timeout-Handling.
    Falls < 2 Funktionen: gibt (None, start_time, None) für Early-Exit.
    """
    import time
    start_time = time.time()

    source_files = []
    if root.is_file():
        source_files = [root]
    elif root.is_dir():
        for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"):
            for f in sorted(root.rglob(f"*{ext}")):
                rel = f.relative_to(root)
                parts = rel.parts
                if any(p in ("node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", "target") for p in parts):
                    continue
                source_files.append(f)

    source_files = source_files[:max_files]

    functions = []
    for f in source_files:
        if time.time() - start_time > timeout:
            break
        try:
            fpath = str(f)
            lang_key = detect_language(fpath)
            if not lang_key:
                continue
            parser = _get_parser(lang_key)
            lang_obj = _get_language(lang_key)
            if parser is None or lang_obj is None:
                continue
            with open(fpath, "rb") as fh:
                source_bytes = fh.read()
            if not source_bytes:
                continue
            source_text = source_bytes.decode("utf-8", errors="replace")
            if source_text.count("\n") > 5000:
                continue

            from tree_sitter import Query, QueryCursor

            func_query_text = _SYMBOL_QUERIES.get(lang_key, """\
                (function_definition name: (identifier) @name) @def
                (function_declaration name: (identifier) @name) @def
                (method_definition name: (property_identifier) @name) @def
            """)
            try:
                query = Query(lang_obj, func_query_text)
            except Exception:
                try:
                    query = Query(lang_obj, """\
                        (function_definition name: (identifier) @name) @def
                        (function_declaration name: (identifier) @name) @def
                    """)
                except Exception:
                    continue

            tree = parser.parse(source_bytes)
            if not tree or not tree.root_node:
                continue

            seen_names = set()
            qc = QueryCursor(query)
            for _pattern_idx, captures_dict in qc.matches(tree.root_node):
                def_nodes = captures_dict.get("def", [])
                name_nodes = captures_dict.get("name", [])
                if not def_nodes or not name_nodes:
                    continue
                def_node = def_nodes[0]
                name_node = name_nodes[0]
                name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                func_text = source_bytes[def_node.start_byte:def_node.end_byte].decode("utf-8", errors="replace")
                lines = func_text.split("\n")
                if len(lines) < min_lines:
                    continue

                start_line = source_text[:def_node.start_byte].count("\n") + 1
                functions.append({
                    "name": name,
                    "file": fpath,
                    "line": start_line,
                    "text": func_text,
                })
        except Exception:
            continue
    return functions, start_time, source_files


# ── Helper: normalization ─────────────────────────────────────────


def _normalize_duplicate_text(text: str) -> str:
    """Normalize function text for duplicate detection.

    Removes string literals, numbers, collapses whitespace.
    """
    text = re.sub(r'"""[\s\S]*?"""', '"""..."""', text)
    text = re.sub(r"'''[\s\S]*?'''", "'''...'''", text)
    text = re.sub(r'"[^"]*"', '"..."', text)
    text = re.sub(r"'[^']*'", "'...'", text)
    text = re.sub(r'\b\d+\b', 'N', text)
    lines_norm = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            lines_norm.append(line)
    return "\n".join(lines_norm)


# ── Helper: duplicate groups ──────────────────────────────────────


def _detect_duplicate_groups(normalized: list, similarity_threshold: float) -> list:
    """Detect exact and near-duplicate groups from normalized function list."""
    import difflib
    import hashlib

    for fn in normalized:
        ntext = _normalize_duplicate_text(fn["text"])
        h = hashlib.md5(ntext.encode()).hexdigest()
        fn["normalized"] = ntext
        fn["hash"] = h

    hash_groups: dict = {}
    for fn in normalized:
        h = fn["hash"]
        if h not in hash_groups:
            hash_groups[h] = []
        hash_groups[h].append(fn)

    exact_groups = [g for g in hash_groups.values() if len(g) >= 2]
    singletons = [g[0] for g in hash_groups.values() if len(g) == 1]

    similar_groups = []
    processed = set()
    for i, a in enumerate(singletons):
        if i in processed:
            continue
        group = [a]
        processed.add(i)
        for j, b in enumerate(singletons):
            if j <= i or j in processed:
                continue
            ratio = difflib.SequenceMatcher(None, a["normalized"], b["normalized"]).ratio()
            if ratio >= similarity_threshold:
                group.append(b)
                processed.add(j)
                break
        if len(group) >= 2:
            similar_groups.append(group)

    return exact_groups + similar_groups


# ── Helper: formatting ────────────────────────────────────────────


def _format_duplicate_results(all_groups: list, top_n: int, total_functions: int, path: str) -> str:
    """Format duplicate groups into final JSON."""
    all_groups.sort(key=lambda g: len(g), reverse=True)
    grouped_results = []
    for group in all_groups[:top_n]:
        entries = []
        for fn in group:
            entries.append({
                "name": fn["name"],
                "file": fn["file"],
                "line": fn["line"],
            })
        grouped_results.append({
            "size": len(group),
            "functions": entries,
        })

    return fmt_json({
        "project": str(path),
        "total_functions": total_functions,
        "total_duplicate_groups": len(all_groups),
        "duplicates": grouped_results,
    })


def code_duplicates_tool(
    path: str = ".",
    min_lines: int = 5,
    top_n: int = 20,
    max_files: int = 200,
    similarity_threshold: float = 0.8,
    timeout: int = 60,
) -> str:
    """Find duplicate/similar code blocks via AST comparison.

    Uses tree-sitter AST to find all function definitions, normalizes them
    (removing names, string literals, numbers), then detects duplicates via
    exact hash matching and string similarity with difflib.
    """
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return fmt_json({"error": f"Path not found: {path}", "duplicates": [], "total": 0})

    # Step 1: Collect all function definitions via AST
    functions, start_time, source_files = _collect_function_definitions(
        root, max_files, timeout, min_lines
    )

    if len(functions) < 2:
        return fmt_json({
            "project": str(path),
            "total_functions": len(functions),
            "duplicates": [],
            "total_duplicate_groups": 0,
        })

    # Step 2: Detect duplicate groups
    all_groups = _detect_duplicate_groups(functions, similarity_threshold)

    # Step 3: Format and return
    return _format_duplicate_results(all_groups, top_n, len(functions), path)


CODE_DUPLICATES_SCHEMA = {
    "name": "code_duplicates",
    "description": "Find duplicate/similar code blocks via AST comparison.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path"},
            "min_lines": {"type": "integer", "description": "Minimum lines for a duplicate block (default: 5)"},
            "top_n": {"type": "integer", "description": "Number of top results (default: 20)"},
            "max_files": {"type": "integer", "description": "Maximum number of files to scan (default: 200)"},
            "similarity_threshold": {"type": "number", "description": "Similarity ratio threshold for near-duplicate detection (default: 0.8)"},
            "timeout": {"type": "integer", "description": "Maximum seconds for the search (default: 60)"},
        },
        "required": ["path"],
    },
}


def _handle_code_duplicates(args, **kw):
    return code_duplicates_tool(
        path=args.get("path", "."),
        min_lines=args.get("min_lines", 5),
        top_n=args.get("top_n", 20),
        max_files=args.get("max_files", 200),
        similarity_threshold=args.get("similarity_threshold", 0.8),
        timeout=args.get("timeout", 60),
    )


# ---------------------------------------------------------------------------
# LSP-based tools — code_definition & code_references (cross-file resolution)
# ---------------------------------------------------------------------------

# LSP tools are registered via register_lsp_tools() called from __init__.py
# during plugin load — do NOT call register_lsp_tools() at module level
# ---------------------------------------------------------------------------
# D1: code_diagram_symbol — Re-exported from tools/diagram.py
# ---------------------------------------------------------------------------

# code_docstring_generate_tool — Generate docstring template from AST
# ---------------------------------------------------------------------------
# code_dependency_risk_tool — Dependency health analysis
# ── Re-Exports for backward compat (tests + internal callers) ──
