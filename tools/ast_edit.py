#!/usr/bin/env python3
"""tools/ast_edit.py — AST editing tools extracted from code_tools.py.

Provides ReplaceBody, SafeDelete, InsertBefore, InsertAfter, Move
operations using tree-sitter AST-accurate boundaries.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

CODE_REPLACE_BODY_SCHEMA = {
    "name": "code_replace_body",
    "description": (
        "Replace the full definition of a symbol (function, method, class) in a "
        "source file using AST-accurate boundaries. Supports name_path syntax "
        "(e.g. 'MyClass/my_method'). dry_run=True (default) shows a diff without "
        "writing. include_decorators=True replaces decorators with the definition."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "new_body": {
                "type": "string",
                "description": (
                    "The new source code replacing the entire symbol definition "
                    "(signature + body)."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a diff without modifying the file."
                ),
                "default": True,
            },
            "include_decorators": {
                "type": "boolean",
                "description": (
                    "When True (default), the replacement includes decorators. "
                    "When False, only the definition body (after decorators) is replaced."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "new_body"],
    },
}


CODE_SAFE_DELETE_SCHEMA = {
    "name": "code_safe_delete",
    "description": (
        "Delete a symbol (function, method, class) ONLY if it has no external "
        "references. Uses AST-based reference search across the project. "
        "Set force=True to delete even if referenced. "
        "dry_run=True (default) shows what would be deleted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path containing the symbol.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "force": {
                "type": "boolean",
                "description": (
                    "When True, delete the symbol even if it has external "
                    "references. Default: False (refuse if referenced)."
                ),
                "default": False,
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), shows what would be deleted without "
                    "modifying the file."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol"],
    },
}


CODE_INSERT_BEFORE_SCHEMA = {
    "name": "code_insert_before",
    "description": (
        "Insert code before a symbol's definition in a source file. Uses "
        "AST-accurate boundaries to find the insertion point. Supports "
        "name_path syntax (e.g. 'MyClass/my_method'). "
        "dry_run=True (default) shows a preview without writing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "The source code to insert before the symbol definition."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a preview without modifying the file."
                ),
                "default": True,
            },
            "newline": {
                "type": "boolean",
                "description": (
                    "When True (default), adds a newline after the inserted code "
                    "to separate it from the symbol."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "code"],
    },
}


CODE_INSERT_AFTER_SCHEMA = {
    "name": "code_insert_after",
    "description": (
        "Insert code after a symbol's definition in a source file. Uses "
        "AST-accurate boundaries to find the insertion point. Supports "
        "name_path syntax (e.g. 'MyClass/my_method'). "
        "dry_run=True (default) shows a preview without writing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "The source code to insert after the symbol definition."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a preview without modifying the file."
                ),
                "default": True,
            },
            "newline": {
                "type": "boolean",
                "description": (
                    "When True (default), adds a newline before the inserted code "
                    "to separate it from the symbol."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "code"],
    },
}


CODE_MOVE_SCHEMA = {
    "name": "code_move",
    "description": "Move a symbol between files via AST extraction and insertion.",
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Source file path",
            },
            "symbol": {
                "type": "string",
                "description": "Symbol name to move",
            },
            "target": {
                "type": "string",
                "description": "Target file path",
            },
            "language": {
                "type": "string",
                "description": "Language override",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview changes without writing (default: true)",
            },
        },
        "required": ["source", "symbol", "target"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_symbol_in_ast(
    path: str,
    symbol_name: str,
    language: Optional[str] = None,
) -> Optional[dict]:
    """Find a symbol in a source file using tree-sitter AST.

    Returns a dict with byte-exact boundaries:

        {name, kind, start_byte, end_byte, start_line, end_line, body}

    Supports name_path syntax: ``"ClassName/method_name"``.
    Returns ``None`` if the symbol is not found.
    """
    # Lazy imports from code_tools for shared infrastructure
    from pathlib import Path as _Path

    from ..code_tools import (
        _classify_symbol_kind,
        _detect_if_method,
        _setup_query,
        detect_language,
    )

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return None

    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return None

    # Parse name_path
    name_parts = symbol_name.strip().split("/")
    leaf_name = name_parts[-1]
    parent_filter = name_parts[:-1]

    try:
        source = target.read_bytes()
    except (OSError, IOError) as e:
        logger.debug("Cannot read file %s: %s", target, e)
        return None

    from tree_sitter import QueryCursor as _QC

    setup = _setup_query(lang_key)
    if setup is None:
        return None
    parser, lang, query = setup

    tree = parser.parse(source)
    qc = _QC(query)

    for _pidx, caps in qc.matches(tree.root_node):
        name_nodes = caps.get("name", [])
        def_nodes = (
            caps.get("def")
            or caps.get("constant")
            or caps.get("field")
            or caps.get("arrow")
        )
        if not name_nodes or not def_nodes:
            continue

        name_node = name_nodes[0]
        def_node = def_nodes[0]

        try:
            name_text = name_node.text.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, IndexError, AttributeError) as e:
            logger.debug('find_node_by_name decode name_text: %s', e)
            continue

        if name_text != leaf_name:
            continue

        # If parent_filter specified, check parent hierarchy
        if parent_filter:
            _cur = def_node.parent
            _depth = 0
            _matched_parents = []
            while _cur and _depth < 10:
                try:
                    pname_node = None
                    for child in _cur.children:
                        if child.type in (
                            "identifier", "type_identifier",
                            "property_identifier",
                        ):
                            pname_node = child
                            break
                    if pname_node:
                        pn = pname_node.text.decode("utf-8", errors="replace")
                        _matched_parents.insert(0, pn)
                except (UnicodeDecodeError, IndexError) as e:
                    logger.debug('find_node_by_name decode parent_name: %s', e)
                    pass
                _cur = _cur.parent
                _depth += 1

            # Check if parents match the filter
            expected = list(parent_filter)  # e.g., ["ClassName"]
            match = True
            for i, exp in enumerate(expected):
                if i < len(_matched_parents):
                    if _matched_parents[-(i + 1)] != exp:
                        match = False
                        break
                else:
                    match = False
                    break
            if not match:
                continue

        # Found it — extract byte boundaries
        start_byte = def_node.start_byte
        end_byte = def_node.end_byte
        start_line = def_node.start_point[0] + 1
        end_line = def_node.end_point[0] + 1
        kind = _classify_symbol_kind(def_node)
        kind = _detect_if_method(def_node, kind)
        body = source[start_byte:end_byte].decode("utf-8", errors="replace")

        return {
            "name": name_text,
            "kind": kind,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "start_line": start_line,
            "end_line": end_line,
            "body": body,
        }

    return None


def _ast_search_references(
    project_root: str,
    symbol_name: str,
    language: Optional[str] = None,
) -> List[dict]:
    """Search for references to a symbol across a project.

    Returns a list of {file, line, context} for each reference found.
    Uses grep -rn with code-file extensions.
    """
    import re
    import subprocess as _sp

    references = []
    root = Path(project_root)
    if not root.is_dir():
        root = root.parent
    if not root.exists():
        return references

    ext_list = [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h"]
    include_args = []
    for ext in ext_list:
        include_args.extend(["--include", f"*{ext}"])
    escaped = re.escape(symbol_name)

    try:
        cmd = ["grep", "-rn", "-C", "1"] + include_args + ["-e", escaped, str(root)]
        result = _sp.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        for line in result.stdout.splitlines():
            if not line.strip() or line.startswith("--"):
                continue
            parts = line.split(":", 2)
            if len(parts) >= 2:
                fpath = parts[0]
                try:
                    linenum = int(parts[1])
                except ValueError as e:
                    logger.debug('parse_ref_line int(linenum): %s', e)
                    continue
                context = parts[2] if len(parts) > 2 else ""
                references.append({
                    "file": fpath,
                    "line": linenum,
                    "context": context.strip(),
                })
    except (_sp.TimeoutExpired, OSError) as e:
        logger.debug("Reference search failed for %s: %s", symbol_name, e)

    return references


# ---------------------------------------------------------------------------
# code_replace_body — Replace symbol body via AST
# ---------------------------------------------------------------------------


def code_replace_body_tool(
    path: str,
    symbol: str,
    new_body: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    include_decorators: bool = True,
) -> str:
    """Replace the full definition of a symbol using AST-accurate boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path (e.g. 'MyClass/my_method').
        new_body: Replacement source code.
        language: Language override.
        dry_run: When True, return diff without writing.
        include_decorators: When True, replace decorators too.

    Returns:
        JSON result with success/error message and optional diff.

    """
    from ..code_tools import (
        _get_language,
        _get_parser,
        _invalidate_cache,
        detect_language,
    )

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")

    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    new_body_bytes = new_body.encode("utf-8")

    if not include_decorators:
        lang_key2 = detect_language(str(target), language)
        if lang_key2:
            # imports not needed — _get_parser/_get_language handles this
            _p2 = _get_parser(lang_key2)
            _l2 = _get_language(lang_key2)
            if _p2 and _l2:
                _tree2 = _p2.parse(source_bytes)
            # Walk from root to find the exact node at start_byte
            _node_at = _tree2.root_node.named_descendant_for_byte_range(
                start_byte, start_byte + 1
            )
            # If it's a decorated_definition, find the inner definition
            if _node_at and _node_at.type == "decorated_definition":
                for _child in _node_at.children:
                    if _child.type in (
                        "function_definition", "class_definition",
                        "function_declaration", "class_declaration",
                        "method_definition",
                    ):
                        start_byte = _child.start_byte
                        break

    old_text = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")

    if dry_run:
        import difflib as _dl
        _diff_lines = list(_dl.unified_diff(
            old_text.splitlines(keepends=True),
            new_body.splitlines(keepends=True),
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
            n=3,
        ))
        diff_text = "".join(_diff_lines)
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "line": symbol_info["start_line"],
            "diff": diff_text,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # --- Apply ---
    new_content = source_bytes[:start_byte] + new_body_bytes + source_bytes[end_byte:]

    # Create backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        # Restore backup
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    # Clean up backup on success
    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (replace_body): %s', e)
        pass

    # Invalidate symbol cache for this file
    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "line": symbol_info["start_line"],
        "end_line": symbol_info["end_line"],
        "message": f"Replaced {symbol_info['kind']} '{symbol_info['name']}' "
                   f"(lines {symbol_info['start_line']}-{symbol_info['end_line']}).",
    })


def _handle_code_replace_body(args, **kw):
    return code_replace_body_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        new_body=args.get("new_body", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        include_decorators=args.get("include_decorators", True),
    )


# ---------------------------------------------------------------------------
# code_safe_delete — Delete symbol if unreferenced
# ---------------------------------------------------------------------------


def code_safe_delete_tool(
    path: str,
    symbol: str,
    language: Optional[str] = None,
    force: bool = False,
    dry_run: bool = True,
) -> str:
    """Delete a symbol ONLY if it has no external references.

    Uses AST-based reference search. Set force=True to bypass the check.

    Args:
        path: File containing the symbol.
        symbol: Symbol name or name_path.
        language: Language override.
        force: Delete even if referenced.
        dry_run: Preview without writing.

    Returns:
        JSON with result message and reference info.

    """
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    from ..code_tools import _invalidate_cache

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    leaf_name = symbol.strip().split("/")[-1]

    # --- Reference check ---
    ext_refs = []
    if not force:
        refs = _ast_search_references(str(target.parent), leaf_name, language)
        definition_path = str(target)
        for ref in refs:
            # Skip self-references (the definition itself)
            if ref["file"] == definition_path and ref["line"] == symbol_info["start_line"]:
                continue
            ext_refs.append(ref)

    if ext_refs and not force:
        ref_summary = "\n".join(
            f"  {r['file']}:{r['line']}  {r['context'][:80]}"
            for r in ext_refs[:20]
        )
        if len(ext_refs) > 20:
            ref_summary += f"\n  ... and {len(ext_refs) - 20} more"
        return fmt_ok({
            "safe": False,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "references_found": len(ext_refs),
            "message": (
                f"Cannot delete '{leaf_name}': {len(ext_refs)} external "
                f"reference(s) found. Use force=True to override."
            ),
            "references": ref_summary,
        })

    # --- Dry-run ---
    if dry_run:
        return fmt_ok({
            "dry_run": True,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "line": symbol_info["start_line"],
            "end_line": symbol_info["end_line"],
            "body_preview": symbol_info["body"][:200],
            "external_references": len(ext_refs),
            "references_found": len(ext_refs) > 0,
            "message": f"Would delete {symbol_info['kind']} '{leaf_name}' "
                       f"(lines {symbol_info['start_line']}-{symbol_info['end_line']})."
                       f" Set dry_run=False to apply.",
        })

    # --- Apply: delete symbol range ---
    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    new_content = source_bytes[:start_byte] + source_bytes[end_byte:]

    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (safe_delete): %s', e)
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": leaf_name,
        "kind": symbol_info["kind"],
        "line": symbol_info["start_line"],
        "end_line": symbol_info["end_line"],
        "external_references": len(ext_refs),
        "message": f"Deleted {symbol_info['kind']} '{leaf_name}' "
                   f"(lines {symbol_info['start_line']}-{symbol_info['end_line']}).",
    })


def _handle_code_safe_delete(args, **kw):
    return code_safe_delete_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        language=args.get("language"),
        force=args.get("force", False),
        dry_run=args.get("dry_run", True),
    )


# ---------------------------------------------------------------------------
# code_insert_before — Insert code before a symbol
# ---------------------------------------------------------------------------


def code_insert_before_tool(
    path: str,
    symbol: str,
    code: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    newline: bool = True,
) -> str:
    """Insert code before a symbol's definition using AST boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path.
        code: Source code to insert.
        language: Language override.
        dry_run: Preview without writing.
        newline: Add newline after inserted code.

    Returns:
        JSON result.

    """
    from ..code_tools import _invalidate_cache

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    insert_at = symbol_info["start_byte"]
    code_bytes = code.encode("utf-8")
    if newline:
        code_bytes += b"\n"

    if dry_run:
        preview = source_bytes[:insert_at].decode("utf-8", errors="replace")
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "insert_before_line": symbol_info["start_line"],
            "insertion": code,
            "preview_context": preview[-200:] if len(preview) > 200 else preview,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # Backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    new_content = source_bytes[:insert_at] + code_bytes + source_bytes[insert_at:]

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (insert_before): %s', e)
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "insert_before_line": symbol_info["start_line"],
        "message": f"Inserted code before {symbol_info['kind']} "
                   f"'{symbol_info['name']}' (line {symbol_info['start_line']}).",
    })


def _handle_code_insert_before(args, **kw):
    return code_insert_before_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        code=args.get("code", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        newline=args.get("newline", True),
    )


# ---------------------------------------------------------------------------
# code_insert_after — Insert code after a symbol
# ---------------------------------------------------------------------------


def code_insert_after_tool(
    path: str,
    symbol: str,
    code: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    newline: bool = True,
) -> str:
    """Insert code after a symbol's definition using AST boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path.
        code: Source code to insert.
        language: Language override.
        dry_run: Preview without writing.
        newline: Add newline before inserted code.

    Returns:
        JSON result.

    """
    from ..code_tools import _invalidate_cache

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    insert_at = symbol_info["end_byte"]
    code_bytes = code.encode("utf-8")
    if newline:
        code_bytes = b"\n" + code_bytes

    if dry_run:
        preview = source_bytes[insert_at:].decode("utf-8", errors="replace")
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "insert_after_line": symbol_info["end_line"],
            "insertion": code,
            "preview_context": preview[:200] if len(preview) > 200 else preview,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # Backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    new_content = source_bytes[:insert_at] + code_bytes + source_bytes[insert_at:]

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (insert_after): %s', e)
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "insert_after_line": symbol_info["end_line"],
        "message": f"Inserted code after {symbol_info['kind']} "
                   f"'{symbol_info['name']}' (line {symbol_info['end_line']}).",
    })


def _handle_code_insert_after(args, **kw):
    return code_insert_after_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        code=args.get("code", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        newline=args.get("newline", True),
    )


# ---------------------------------------------------------------------------
# code_move_tool — Move a symbol between files via AST extraction
# ---------------------------------------------------------------------------


def code_move_tool(
    source: str,
    symbol: str,
    target: str,
    language: str = "",
    dry_run: bool = True,
) -> str:
    """Move a symbol between files. AST-based extraction + insertion.

    Phase 1: Functions only, no import-reference updating.

    Args:
        source: Source file path containing the symbol to move.
        symbol: Symbol name or name_path (e.g. 'MyClass/my_method').
        target: Target file path where the symbol should be inserted.
        language: Language override (auto-detected from extension).
        dry_run: When True, return diff without writing (default: True).

    Returns:
        JSON result with success/error message and optional diff.

    """
    from ..code_tools import (
        _invalidate_cache,
        detect_language,
    )

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        return fmt_err(f"Source file not found: {source}")
    if not source_path.is_file():
        return fmt_err(f"Not a file: {source}")

    target_path = Path(target).expanduser().resolve()
    if not target_path.exists():
        return fmt_err(f"Target file not found: {target}")
    if not target_path.is_file():
        return fmt_err(f"Not a file: {target}")

    # Resolve language — prefer explicit override, else auto-detect from source
    lang_key = language if language else detect_language(str(source_path))
    if lang_key is None:
        return fmt_err(
            f"Cannot detect language for source file: {source}. "
            "Set 'language' explicitly."
        )

    # ------------------------------------------------------------------
    # 1. Find the symbol in the source file via AST
    # ------------------------------------------------------------------
    symbol_info = _find_symbol_in_ast(str(source_path), symbol, lang_key)
    if symbol_info is None:
        return fmt_err(
            f"Symbol '{symbol}' not found in {source}"
        )

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    leaf_name = symbol.strip().split("/")[-1]

    # Read source file content
    try:
        source_bytes = source_path.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read source file: {e}")

    # Extract the symbol's source code
    symbol_code = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # 2. Compute insertion point in target file (before last line)
    # ------------------------------------------------------------------
    try:
        target_bytes = target_path.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read target file: {e}")

    target_text = target_bytes.decode("utf-8", errors="replace")
    # Strip trailing whitespace, append the new symbol + newline
    target_stripped = target_text.rstrip()
    insert_pos = len(target_stripped.encode("utf-8"))
    # Insert with a blank line separator
    insertion_code = "\n\n" + symbol_code + "\n"

    # ------------------------------------------------------------------
    # 3. Compute the new file contents
    # ------------------------------------------------------------------
    # Target: content before insertion point + insertion + trailing content
    new_target_content = (
        target_bytes[:insert_pos]
        + insertion_code.encode("utf-8")
        + target_bytes[insert_pos:].lstrip(b"\n")
    )

    # Source: remove the symbol range
    new_source_content = source_bytes[:start_byte] + source_bytes[end_byte:]

    # ------------------------------------------------------------------
    # 4. Dry-run: return a unified diff for both files
    # ------------------------------------------------------------------
    if dry_run:
        import difflib as _dl

        # Source diff
        source_old = source_bytes.decode("utf-8", errors="replace")
        source_new = new_source_content.decode("utf-8", errors="replace")
        source_diff_lines = list(_dl.unified_diff(
            source_old.splitlines(keepends=True),
            source_new.splitlines(keepends=True),
            fromfile=f"a/{source_path.name}",
            tofile=f"b/{source_path.name}",
            n=3,
        ))

        # Target diff
        target_old = target_bytes.decode("utf-8", errors="replace")
        target_new = new_target_content.decode("utf-8", errors="replace")
        target_diff_lines = list(_dl.unified_diff(
            target_old.splitlines(keepends=True),
            target_new.splitlines(keepends=True),
            fromfile=f"a/{target_path.name}",
            tofile=f"b/{target_path.name}",
            n=3,
        ))

        diff_text = "".join(source_diff_lines) + "\n" + "".join(target_diff_lines)

        return fmt_ok({
            "dry_run": True,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "source_line": symbol_info["start_line"],
            "source": str(source_path),
            "target": str(target_path),
            "diff": diff_text,
            "message": (
                f"Dry-run mode. Would move {symbol_info['kind']} '{leaf_name}' "
                f"from {source_path.name}:{symbol_info['start_line']} "
                f"to {target_path.name}. "
                "Set dry_run=False to apply."
            ),
        })

    # ------------------------------------------------------------------
    # 5. Apply: write both files (with backup)
    # ------------------------------------------------------------------

    # --- Write source file (remove symbol) ---
    source_backup = source_path.with_suffix(source_path.suffix + ".bak")
    try:
        source_backup.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create source backup: {e}")

    try:
        source_path.write_bytes(new_source_content)
    except (OSError, IOError) as e:
        source_backup.write_bytes(source_bytes)
        return fmt_err(f"Cannot write source file: {e}")

    try:
        source_backup.unlink()
    except OSError as e:
        logger.debug('cleanup source_backup unlink (move): %s', e)
        pass

    # --- Write target file (insert symbol) ---
    target_backup = target_path.with_suffix(target_path.suffix + ".bak")
    try:
        target_backup.write_bytes(target_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create target backup: {e}")

    try:
        target_path.write_bytes(new_target_content)
    except (OSError, IOError) as e:
        target_backup.write_bytes(target_bytes)
        return fmt_err(f"Cannot write target file: {e}")

    try:
        target_backup.unlink()
    except OSError as e:
        logger.debug('cleanup target_backup unlink (move): %s', e)
        pass

    # Invalidate caches so subsequent AST ops see fresh content
    _invalidate_cache(str(source_path))
    _invalidate_cache(str(target_path))

    return fmt_ok({
        "success": True,
        "symbol": leaf_name,
        "kind": symbol_info["kind"],
        "source": str(source_path),
        "source_line": symbol_info["start_line"],
        "target": str(target_path),
        "message": (
            f"Moved {symbol_info['kind']} '{leaf_name}' "
            f"from {source_path.name}:{symbol_info['start_line']} "
            f"to {target_path.name}."
        ),
    })


def _handle_code_move(args, **kw):
    return code_move_tool(
        source=args.get("source", ""),
        symbol=args.get("symbol", ""),
        target=args.get("target", ""),
        language=args.get("language", ""),
        dry_run=args.get("dry_run", True),
    )
