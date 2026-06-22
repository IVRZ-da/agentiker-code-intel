"""tools/search_by_error.py — Search-By-Error Tool for code_intel plugin.

Searches for error handling sites (raise/throw, catch/except, custom error classes).
"""

from __future__ import annotations

from .._fmt import fmt_err, fmt_json

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

_ERROR_SUPPORTED_LANGS = set(_ERROR_QUERIES.keys())


def code_search_by_error_tool(
    path: str,
    error: str,
    language: str = "",
    max_files: int = 500,
    max_findings: int = 100,
    timeout: int = 60,
) -> str:
    """Find all places that handle specific error types.

    Searches for:
    - raise/throw sites
    - catch/except sites
    - custom error class definitions

    Args:
        path: File or directory to search.
        error: Error type name (e.g. "ValidationError", "ValueError").
        language: Language filter (optional).
        max_files: Maximum number of files to scan (default: 500).
        max_findings: Stop after finding this many matches (default: 100).
        timeout: Maximum seconds for the search (default: 60).

    Returns:
        Formatted result with matches grouped by category.

    """
    import time
    from pathlib import Path as _Path

    # Lazy imports from parent module
    from ..code_tools import _get_language, _get_parser, detect_language

    search_path = _Path(path).expanduser().resolve()
    if not search_path.exists():
        return fmt_err(f"Path not found: {path}")

    from tree_sitter import Query, QueryCursor

    start_time = time.time()

    # Collect files to search
    files_to_search = []
    if search_path.is_file():
        files_to_search.append(search_path)
    else:
        for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"]:
            for f in search_path.rglob(f"*{ext}"):
                rel = f.relative_to(search_path)
                parts = rel.parts
                if any(p in _ERROR_EXCLUDE_DIRS for p in parts):
                    continue
                files_to_search.append(f)

    if not files_to_search:
        return fmt_err("No source files found")

    # Truncate file list to max_files
    files_to_search = files_to_search[:max_files]

    # Search each file
    raise_sites: list = []
    catch_sites: list = []
    custom_sites: list = []
    files_scanned = 0

    for f in files_to_search:
        # Check timeout
        if time.time() - start_time > timeout:
            break

        lang_key = language or detect_language(str(f))
        if not lang_key or lang_key not in _ERROR_SUPPORTED_LANGS:
            continue

        query_source = _ERROR_QUERIES.get(lang_key)
        if not query_source:
            continue

        lang_obj = _get_language(lang_key)
        parser = _get_parser(lang_key)
        if parser is None or lang_obj is None:
            continue

        try:
            q = Query(lang_obj, query_source)
        except Exception:
            continue

        try:
            with open(str(f), "rb") as sf:
                source_bytes = sf.read()
        except OSError:
            continue

        tree = parser.parse(source_bytes)
        if tree is None:
            continue

        files_scanned += 1

        qc2 = QueryCursor(q)
        for _pi, cd in qc2.matches(tree.root_node):
            errors_found = set()
            for n in cd.get("error_name", []):
                try:
                    name = source_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
                except Exception:
                    continue
                errors_found.add(name)

            if error not in errors_found:
                continue

            line = 0
            for dn in cd.get("raise_site", cd.get("throw_site", cd.get("return_site", cd.get("catch_site", cd.get("custom_site", []))))):
                line = dn.start_point[0] + 1
                break

            for _rn in cd.get("raise_site", []):
                raise_sites.append({"file": str(f), "line": line})
            for _tn in cd.get("throw_site", []):
                raise_sites.append({"file": str(f), "line": line})
            for _rn2 in cd.get("return_site", []):
                raise_sites.append({"file": str(f), "line": line})
            for _cn in cd.get("catch_site", []):
                catch_sites.append({"file": str(f), "line": line})
            for _cs in cd.get("custom_site", []):
                custom_sites.append({"file": str(f), "line": line})

        # Early-exit: stop scanning after reaching max_findings
        total_findings = len(raise_sites) + len(catch_sites) + len(custom_sites)
        if total_findings >= max_findings:
            break

    total_findings = len(raise_sites) + len(catch_sites) + len(custom_sites)
    result = {
        "error": error,
        "results": {
            "raise/throw": sorted(raise_sites, key=lambda x: x["file"]),
            "catch/except": sorted(catch_sites, key=lambda x: x["file"]),
            "custom_classes": sorted(custom_sites, key=lambda x: x["file"]),
        },
        "total": total_findings,
        "files_scanned": files_scanned,
        "timed_out": time.time() - start_time > timeout,
    }

    return fmt_json(result)


# Schema + Handler
CODE_SEARCH_BY_ERROR_SCHEMA = {
    "name": "code_search_by_error",
    "description": "Find all places that handle specific error types. "
                   "Searches for raise/throw sites, catch/except blocks, "
                   "and custom error class definitions. Supports Python, TypeScript, Go, Rust.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory to search"},
            "error": {"type": "string", "description": "Error type name (e.g. 'ValidationError', 'ValueError')"},
            "language": {"type": "string", "description": "Language filter (optional)"},
            "max_files": {"type": "integer", "description": "Maximum number of files to scan (default: 500)"},
            "max_findings": {"type": "integer", "description": "Stop after finding this many matches (default: 100)"},
            "timeout": {"type": "integer", "description": "Maximum seconds for the search (default: 60)"},
        },
        "required": ["path", "error"],
    },
}


def _handle_code_search_by_error(args, **kw):
    return code_search_by_error_tool(
        path=args.get("path", ""),
        error=args.get("error", ""),
        language=args.get("language", ""),
        max_files=args.get("max_files", 500),
        max_findings=args.get("max_findings", 100),
        timeout=args.get("timeout", 60),
    )
