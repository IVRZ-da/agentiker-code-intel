"""tools/unused.py — Unused imports & functions detection.

Native implementation (no re-export from code_tools).
Provides _find_unused_imports_in_file, _find_identifier_occurrences,
_find_unused_imports, _find_unused_functions, code_unused_finder_tool,
CODE_UNUSED_FINDER_SCHEMA, and _handle_code_unused_finder with all
helper functions defined inline.
"""

from __future__ import annotations

from pathlib import Path

from .._fmt import fmt_json
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

from .base import (  # noqa: E402
    _SYMBOL_QUERIES,
    _get_language,
    _get_parser,
    detect_language,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_IMPORT_QUERIES = {
    "python": """
        (import_statement
            name: (dotted_name) @import_name) @import_stmt
        (import_from_statement
            module_name: (dotted_name)? @_from_mod
            name: (dotted_name) @from_name) @import_stmt
    """,
    "typescript": """
        (import_statement
            source: (string) @_source
           ) @import_stmt
    """,
    "tsx": """
        (import_statement
            source: (string) @_source
            ) @import_stmt
    """,
    "javascript": """
        (import_statement
            source: (string) @_source
            ) @import_stmt
    """,
    "jsx": """
        (import_statement
            source: (string) @_source
            ) @import_stmt
    """,
}


# ---------------------------------------------------------------------------
# Unused Imports Detection
# ---------------------------------------------------------------------------


def _extract_import_names(
    source_bytes: bytes,
    source_text: str,
    query,
    tree,
) -> tuple[list, dict]:
    """Extract imported names and their ranges from a tree-sitter AST.

    Returns:
        (import_ranges, imported_names):
        import_ranges: list of (start_byte, end_byte) tuples
        imported_names: dict of name -> [{\"line\", \"statement\", \"name\"}, ...]
    """
    from tree_sitter import QueryCursor

    import_ranges: list = []
    imported_names: dict = {}

    qc = QueryCursor(query)
    for _pattern_idx, captures_dict in qc.matches(tree.root_node):
        stmt_node = captures_dict.get("import_stmt", [None])[0]
        if stmt_node:
            import_ranges.append((stmt_node.start_byte, stmt_node.end_byte))

        for node in captures_dict.get("import_name", []):
            name = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            top_name = name.split(".")[0]
            stmt_text = (
                source_bytes[stmt_node.start_byte : stmt_node.end_byte].decode("utf-8", errors="replace")
                if stmt_node
                else name
            )
            if top_name not in imported_names:
                imported_names[top_name] = []
            line_num = source_text[: node.start_byte].count("\n") + 1
            imported_names[top_name].append({"line": line_num, "statement": stmt_text, "name": top_name})

        for node in captures_dict.get("from_name", []):
            name = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            top_name = name.split(".")[0]
            stmt_text = (
                source_bytes[stmt_node.start_byte : stmt_node.end_byte].decode("utf-8", errors="replace")
                if stmt_node
                else name
            )
            if top_name not in imported_names:
                imported_names[top_name] = []
            line_num = source_text[: node.start_byte].count("\n") + 1
            imported_names[top_name].append({"line": line_num, "statement": stmt_text, "name": top_name})

    return import_ranges, imported_names


def _supplement_ts_imports(source_text: str, imported_names: dict) -> None:
    """Supplement TypeScript import names not captured by tree-sitter query."""
    import re as _re

    ts_imports = _re.findall(
        r"(?:import\s+)(?:type\s+)?(?:\{?\s*(\w+))",
        source_text,
    )
    for name in ts_imports:
        if name not in imported_names and name not in ("from",):
            idx = source_text.find(f"import {name}")
            if idx == -1:
                idx = source_text.find(f"{{{name}")
            line_num = source_text[:idx].count("\n") + 1 if idx >= 0 else 0
            imported_names.setdefault(name, [])
            if not any(n["name"] == name for n in imported_names[name]):
                imported_names[name].append({"line": line_num, "statement": f"import {name}", "name": name})


_SKIP_IMPORT_NAMES = frozenset(
    {
        "typing",
        "TYPE_CHECKING",
        "Any",
        "Optional",
        "List",
        "Dict",
        "Set",
        "Tuple",
    }
)


def _determine_unused_imports(
    imported_names: dict,
    file_path: str,
    source_text: str,
) -> list:
    """Check which imported names have zero non-import references."""
    unused = []
    for name, occurrences in imported_names.items():
        if not name or len(name) < 2:
            continue
        if name in _SKIP_IMPORT_NAMES:
            continue

        ref_count = sum(1 for _ in _find_identifier_occurrences(name, source_text))
        num_imports = len(occurrences)
        if ref_count <= num_imports:
            for occ in occurrences:
                unused.append(
                    {
                        "name": occ["name"],
                        "line": occ["line"],
                        "statement": occ["statement"],
                        "file": file_path,
                        "kind": "import",
                    }
                )
    return unused


def _validate_and_parse_file(file_path: str):
    """Validate file and parse with tree-sitter. Returns (source_text, source_bytes, query, tree, lang_key) or None."""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return None

    lang_key = detect_language(file_path)
    if not lang_key:
        return None

    parser = _get_parser(lang_key)
    lang_obj = _get_language(lang_key)
    if parser is None or lang_obj is None:
        return None

    try:
        from tree_sitter import Query
    except ImportError:
        return None

    query_source = _IMPORT_QUERIES.get(lang_key)
    if not query_source:
        return None

    try:
        query = Query(lang_obj, query_source)
    except Exception:
        return None

    try:
        with open(file_path, "rb") as f:
            source_bytes = f.read()
    except (OSError, IOError):
        return None

    if not source_bytes:
        return None

    tree = parser.parse(source_bytes)
    if not tree or not tree.root_node:
        return None

    source_text = source_bytes.decode("utf-8", errors="replace")
    return source_text, source_bytes, query, tree, lang_key


def _find_unused_imports_in_file(file_path: str) -> list:
    """Find unused imports in a single file using tree-sitter AST analysis."""
    result = _validate_and_parse_file(file_path)
    if result is None:
        return []
    source_text, source_bytes, query, tree, lang_key = result

    import_ranges, imported_names = _extract_import_names(source_bytes, source_text, query, tree)
    if not imported_names:
        return []

    if lang_key in ("typescript", "tsx", "javascript", "jsx"):
        _supplement_ts_imports(source_text, imported_names)

    return _determine_unused_imports(imported_names, file_path, source_text)


def _find_identifier_occurrences(name: str, source_text: str) -> list:
    """Find non-import occurrences of an identifier in source text.

    Uses word-boundary matching to avoid false positives on substrings.

    Returns:
        List of line numbers where the identifier appears.

    """
    import re as _re

    results = []
    # Look for word-boundary-delimited occurrences
    pattern = _re.compile(r"\b" + _re.escape(name) + r"\b")
    for m in pattern.finditer(source_text):
        results.append(source_text[: m.start()].count("\n") + 1)
    return results


def _find_unused_imports(path: str, depth: int = 5, max_files: int = 0) -> list:
    """Find unused imports across a project directory or single file.

    Args:
        path: File or directory path to scan.
        depth: Max scan depth for directories (default: 5).
        max_files: Max files to scan (0 = unlimited).

    Returns:
        List of unused import dicts from _find_unused_imports_in_file.

    """
    from pathlib import Path as _Path

    root = _Path(path).expanduser().resolve()
    if not root.exists():
        return []

    if root.is_file():
        return _find_unused_imports_in_file(str(root))

    if not root.is_dir():
        return []

    results = []
    files_scanned = 0
    limit_reached = False
    for ext in (".py", ".ts", ".tsx", ".js", ".jsx"):
        for f in sorted(root.rglob(f"*{ext}")):
            if max_files > 0 and files_scanned >= max_files:
                limit_reached = True
                break
            # Skip common excluded dirs
            rel = f.relative_to(root)
            parts = rel.parts
            if any(
                p in ("node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next") for p in parts
            ):
                continue
            try:
                file_results = _find_unused_imports_in_file(str(f))
                results.extend(file_results)
                files_scanned += 1
                if files_scanned % 50 == 0:
                    logger.debug("_find_unused_imports: scanned %d files so far (path=%s)", files_scanned, path)
            except Exception as e:
                logger.debug("_find_unused_imports: scanning file: %s", e)
                continue
        if limit_reached:
            break

    if limit_reached:
        logger.warning(
            "_find_unused_imports: reached max_files limit of %d (path=%s)",
            max_files,
            path,
        )

    return results


# ---------------------------------------------------------------------------
# Unused Functions Detection
# ---------------------------------------------------------------------------

_FALLBACK_QUERY = """
    (function_definition name: (identifier) @name) @def
    (function_declaration name: (identifier) @name) @def
"""


def _extract_functions_from_file(
    fpath: str,
    all_texts: dict,
    max_files: int,
    path: str,
) -> tuple[dict, dict, bool]:
    """Scan source files for function definitions via tree-sitter.

    Returns:
        (file_functions, all_texts, limit_reached)
    """
    from pathlib import Path as _Path

    from tree_sitter import Query, QueryCursor

    root = _Path(fpath).expanduser().resolve()
    if not root.exists():
        return {}, {}, False

    source_files = []
    if root.is_file():
        source_files = [root]
    elif root.is_dir():
        for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"):
            for f in sorted(root.rglob(f"*{ext}")):
                rel = f.relative_to(root)
                parts = rel.parts
                if any(
                    p in ("node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", "target")
                    for p in parts
                ):
                    continue
                source_files.append(f)

    file_functions = {}
    files_scanned = 0
    limit_reached = False

    for f in source_files:
        if max_files > 0 and files_scanned >= max_files:
            limit_reached = True
            break
        try:
            fpath_str = str(f)
            lang_key = detect_language(fpath_str)
            if not lang_key:
                continue
            parser = _get_parser(lang_key)
            lang_obj = _get_language(lang_key)
            if parser is None or lang_obj is None:
                continue

            with open(fpath_str, "rb") as fh:
                source_bytes = fh.read()
            if not source_bytes:
                continue
            source_text = source_bytes.decode("utf-8", errors="replace")
            all_texts[fpath_str] = source_text

            func_query_text = _SYMBOL_QUERIES.get(lang_key, _FALLBACK_QUERY)
            try:
                query = Query(lang_obj, func_query_text)
            except Exception:
                try:
                    query = Query(lang_obj, _FALLBACK_QUERY)
                except Exception as e:
                    logger.debug("_extract_functions: fallback Query failed: %s", e)
                    continue

            tree = parser.parse(source_bytes)
            if not tree or not tree.root_node:
                continue

            functions = []
            seen_names = set()
            qc = QueryCursor(query)
            for _pattern_idx, captures_dict in qc.matches(tree.root_node):
                for node in captures_dict.get("name", []):
                    name = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        line_num = source_text[: node.start_byte].count("\n") + 1
                        functions.append((name, line_num))
            if functions:
                file_functions[fpath_str] = functions

            files_scanned += 1
            if files_scanned % 50 == 0:
                logger.debug("_extract_functions: scanned %d files so far (path=%s)", files_scanned, fpath)
        except Exception as e:
            logger.debug("_extract_functions: scanning file: %s", e)
            continue

    return file_functions, all_texts, limit_reached


def _walk_ast_for_function_ref(
    tree,
    source_bytes: bytes,
    func_name: str,
    def_file: str,
    def_line: int,
    search_path: str,
) -> int:
    """Walk tree-sitter AST counting identifier references to a function.

    Skips comments, imports, and type annotations to avoid false positives.
    Excludes the function's own definition line.
    """
    ref_count = 0

    def _walk(node, in_annotation=False, in_import=False):
        nonlocal ref_count
        node_type = node.type

        if node_type in ("comment", "block_comment", "line_comment"):
            return
        if node_type in (
            "type_annotation",
            "type_alias_declaration",
            "type_definition",
            "type_spec",
            "type_parameter",
            "type_parameters",
            "generic_type",
        ):
            in_annotation = True
        if node_type in (
            "import_statement",
            "import_from_statement",
            "import_declaration",
            "import_specifier",
            "import_alias",
            "require_statement",
            "import",
            "from_clause",
        ):
            in_import = True
        if node_type in ("identifier", "property_identifier"):
            if not in_import and not in_annotation:
                try:
                    text = source_bytes[node.start_byte : node.end_byte].decode("utf-8")
                except Exception:
                    text = ""
                if text == func_name:
                    if not (search_path == def_file and node.start_point[0] + 1 == def_line):
                        ref_count += 1
        for child in node.named_children:
            _walk(child, in_annotation, in_import)

    _walk(tree.root_node)
    return ref_count


def _check_functions_for_unused(file_functions: dict, all_texts: dict) -> list:
    """Check extracted functions for unused references across all texts."""
    unused = []
    for fpath, funcs in file_functions.items():
        for func_name, def_line in funcs:
            if len(func_name) < 2 or func_name.startswith("__") or func_name.startswith("test_"):
                continue

            total_refs = 0
            for search_path, search_text in all_texts.items():
                try:
                    lang_key = detect_language(search_path)
                    if not lang_key:
                        continue
                    parser = _get_parser(lang_key)
                    if parser is None:
                        continue

                    source_bytes = search_text.encode("utf-8")
                    tree = parser.parse(source_bytes)
                    if not tree or not tree.root_node:
                        continue

                    total_refs += _walk_ast_for_function_ref(
                        tree,
                        source_bytes,
                        func_name,
                        fpath,
                        def_line,
                        search_path,
                    )
                except Exception as e:
                    logger.debug("_find_unused_functions: walking tree: %s", e)
                    continue

            if total_refs == 0:
                unused.append(
                    {
                        "name": func_name,
                        "file": fpath,
                        "line": def_line,
                        "kind": "function",
                        "total_references": total_refs,
                    }
                )
    return unused


def _find_unused_functions(path: str, depth: int = 5, max_files: int = 0) -> list:
    """Find unused functions across a project."""
    from pathlib import Path as _Path

    root = _Path(path).expanduser().resolve()
    if not root.exists():
        return []

    all_texts: dict = {}
    file_functions, all_texts, limit_reached = _extract_functions_from_file(
        path,
        all_texts,
        max_files,
        path,
    )

    if not file_functions:
        return []

    unused = _check_functions_for_unused(file_functions, all_texts)

    if limit_reached:
        logger.warning(
            "_find_unused_functions: reached max_files limit of %d (path=%s)",
            max_files,
            path,
        )

    return unused


def code_unused_finder_tool(
    path: str,
    kinds: list = None,
    depth: int = 5,
    max_files: int = 500,
    timeout: int = 60,
) -> str:
    """Find unused imports and unused functions in a project.

    Uses tree-sitter AST analysis to detect:
    - Unused imports: names that are imported but never referenced in the file body
    - Unused functions: functions defined but never called project-wide

    Args:
        path: File or directory path to scan.
        kinds: Types of unused code to find: ["imports"], ["functions"], or both.
               (default: ["imports"]).
        depth: Scan depth for directories (default: 5).
        max_files: Max files to scan (default: 500).
        timeout: Max seconds to allow for scanning (default: 60).

    Returns:
        JSON with grouped unused code findings.

    """
    if kinds is None:
        kinds = ["imports"]

    results = []
    timed_out = False

    def _run_scan():
        scan_results = []

        if "imports" in kinds:
            found = _find_unused_imports(path, depth=depth, max_files=max_files)
            scan_results.extend(found)

        if "functions" in kinds:
            found = _find_unused_functions(path, depth=depth, max_files=max_files)
            scan_results.extend(found)

        return scan_results

    # Synchronous execution - avoids tree-sitter signal.signal() crash in sub-thread
    results = _run_scan()

    # Check if any scanner hit the max_files limit
    if max_files > 0:
        pass  # limit_reached tracked within sub-functions via log, but we check above

    # Group by file for a clean output
    by_file: dict = {}
    for r in results:
        fpath = r.get("file", "")
        if fpath not in by_file:
            by_file[fpath] = []
        by_file[fpath].append(r)

    # Sort for deterministic output
    sorted_files = sorted(by_file.keys())
    grouped = []
    for fpath in sorted_files:
        grouped.append(
            {
                "file": fpath,
                "unused": by_file[fpath],
                "total": len(by_file[fpath]),
            }
        )

    total = len(results)
    result = {
        "project": str(path),
        "total_unused": total,
        "files": grouped,
    }

    if timed_out:
        result["warning"] = f"Scan timed out after {timeout}s. Results may be incomplete."
    # We can't directly determine limit_reached from here since it's logged
    # but not returned; we add a note if max_files was set
    if max_files > 0:
        result["max_files_limit"] = max_files

    return fmt_json(result)


CODE_UNUSED_FINDER_SCHEMA = {
    "name": "code_unused_finder",
    "description": "Find unused imports and unused functions in a project. "
    "Uses tree-sitter AST analysis to detect dead code.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory path to scan"},
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": ["imports", "functions"]},
                "description": "Types of unused code to find (default: ['imports'])",
            },
            "depth": {"type": "integer", "description": "Scan depth for directories (default: 5)"},
            "max_files": {"type": "integer", "description": "Max files to scan (default: 500)"},
            "timeout": {"type": "integer", "description": "Max seconds to allow for scanning (default: 60)"},
        },
        "required": ["path"],
    },
}


def _handle_code_unused_finder(args, **kw):
    return code_unused_finder_tool(
        path=args.get("path", ""),
        kinds=args.get("kinds", ["imports"]),
        depth=args.get("depth", 5),
        max_files=args.get("max_files", 500),
        timeout=args.get("timeout", 60),
    )
