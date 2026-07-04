"""lsp/extra/ — LSP completion + code lens tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from ..._fmt import fmt_err, fmt_ok  # noqa: E402
from ..bridge import (  # noqa: E402
    _detect_language_for_lsp,
    get_lsp_manager,
)

_LSP_COMPLETION_KIND = {
    1: "Text",
    2: "Method",
    3: "Function",
    4: "Constructor",
    5: "Field",
    6: "Variable",
    7: "Class",
    8: "Interface",
    9: "Module",
    10: "Property",
    11: "Unit",
    12: "Value",
    13: "Enum",
    14: "Keyword",
    15: "Snippet",
    16: "Color",
    17: "File",
    18: "Reference",
    19: "Folder",
    20: "EnumMember",
    21: "Constant",
    22: "Struct",
    23: "Event",
    24: "Operator",
    25: "TypeParameter",
}


def code_completion_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get completion suggestions at cursor position via LSP.

    Returns a list of completion items with label, kind, and detail.
    Useful for exploring available API surface at a given position.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    lsp_char = (character or 0) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.completion(str(target), lsp_line, max(0, lsp_char))
    if not result:
        return fmt_err("No completions at position")

    items = result.get("items") if isinstance(result, dict) else result
    if not items:
        return fmt_err("No completions at position")

    max_items = 20
    completions = []
    for item in items[:max_items]:
        completions.append(
            {
                "label": item.get("label", "?"),
                "kind": _LSP_COMPLETION_KIND.get(item.get("kind", 0), "unknown"),
                "detail": item.get("detail", ""),
                "documentation": item.get("documentation", ""),
            }
        )

    return fmt_ok(
        {
            "path": str(target),
            "line": line,
            "character": character,
            "language": lang,
            "total": len(items),
            "completions": completions,
            "lsp_server": bridge.command,
        }
    )


CODE_COMPLETION_SCHEMA = {
    "name": "code_completion",
    "description": "Get completion suggestions at a cursor position via LSP.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _ast_code_lens(target: Path, lang: str) -> list:
    """AST-based fallback for code lens: count references per symbol.

    Uses tree-sitter to extract function/class symbols and their
    occurrence count within the same file (quick estimate).
    """
    try:
        from ...tools.base import _get_parser, detect_language
        from ...tools.symbols import extract_symbols

        lang_key = detect_language(str(target))
        if not lang_key:
            return []
        parser = _get_parser(lang_key)
        if parser is None:
            return []

        source = target.read_bytes()
        if not source:
            return []
        source_text = source.decode("utf-8", errors="replace")

        symbols = extract_symbols(source, lang_key, kind_filter=None, include_body=False)
        if not symbols:
            return []

        import re as _re
        lens_items = []
        for sym in symbols[:50]:
            name = sym.get("name", "")
            if not name or len(name) < 2:
                continue
            # Count occurrences via word-boundary regex
            refs = list(_re.finditer(r"\b" + _re.escape(name) + r"\b", source_text))
            ref_count = len(refs)
            if ref_count > 0:
                lens_items.append({
                    "range": {
                        "start_line": sym.get("line", 1),
                        "end_line": sym.get("end_line", sym.get("line", 1)),
                    },
                    "title": f"{ref_count} reference{'s' if ref_count != 1 else ''}",
                    "command": "",
                })
        return lens_items
    except Exception:
        logger.debug("completion: error, returning []")
        return []


def code_code_lens_tool(
    path: str,
    language: Optional[str] = None,
) -> str:
    """Get code lens items (reference counts, test status) for a file.

    Uses LSP textDocument/codeLens to return decorations per symbol.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        # AST fallback when no LSP server available
        ast_items = _ast_code_lens(target, lang)
        if ast_items:
            return fmt_ok(
                {
                    "path": str(target),
                    "language": lang,
                    "total": len(ast_items),
                    "lens_items": ast_items,
                    "source": "ast-fallback",
                }
            )
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.code_lens(str(target))
    if not result:
        # AST fallback when LSP returns nothing
        ast_items = _ast_code_lens(target, lang)
        if ast_items:
            return fmt_ok(
                {
                    "path": str(target),
                    "language": lang,
                    "total": len(ast_items),
                    "lens_items": ast_items,
                    "source": "ast-fallback",
                }
            )
        return fmt_err("No code lens items available — requires an active LSP server (pyright) for the file language")

    lens_items = []
    for item in result[:50]:
        rng = item.get("range", {})
        command = item.get("command", {})
        lens_items.append(
            {
                "range": {
                    "start_line": rng.get("start", {}).get("line", 0) + 1,
                    "end_line": rng.get("end", {}).get("line", 0) + 1,
                },
                "title": command.get("title", ""),
                "command": command.get("command", ""),
            }
        )

    return fmt_ok(
        {
            "path": str(target),
            "language": lang,
            "total": len(result),
            "lens_items": lens_items,
            "lsp_server": bridge.command,
        }
    )


CODE_CODE_LENS_SCHEMA = {
    "name": "code_code_lens",
    "description": "Get code lens items (reference counts, test status) via LSP.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path"],
    },
}


def _detect_import_blocks(lines: list) -> list:
    """Detect import blocks at the top of a file."""
    ranges = []
    in_imports = False
    import_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        is_import = any(stripped.startswith(kw) for kw in
                        ["import ", "from ", "#include", "use ", "package "])
        if is_import and not in_imports:
            in_imports = True
            import_start = i + 1
        elif not is_import and in_imports and i - import_start > 1:
            ranges.append({"start_line": import_start, "end_line": i, "kind": "imports"})
            in_imports = False
        elif not is_import:
            in_imports = False
    if in_imports and len(lines) - import_start > 1:
        ranges.append({"start_line": import_start, "end_line": len(lines), "kind": "imports"})
    return ranges


def _walk_ast_for_foldable_blocks(tree) -> list:
    """Walk tree-sitter AST to find foldable function/class bodies."""
    ranges = []

    def _walk(node, depth=0):
        if depth > 20:
            return
        node_type = node.type
        if node_type in ("function_definition", "class_definition",
                          "method_definition", "module"):
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            if end - start >= 3:
                kind = "class" if node_type == "class_definition" else "function"
                ranges.append({"start_line": start, "end_line": end, "kind": kind})
        for child in node.children:
            _walk(child, depth + 1)

    _walk(tree.root_node)
    return ranges


def _detect_comment_blocks(lines: list) -> list:
    """Detect consecutive comment blocks (3+ lines)."""
    import re as _re
    ranges = []
    comment_lines = []
    for i, line in enumerate(lines):
        if _re.match(r"^\s*#", line):
            comment_lines.append(i + 1)
    if not comment_lines:
        return ranges
    block = [comment_lines[0]]
    prev = comment_lines[0]
    for cl in comment_lines[1:]:
        if cl == prev + 1:
            block.append(cl)
            prev = cl
        else:
            if len(block) >= 3:
                ranges.append({"start_line": block[0], "end_line": block[-1] + 1, "kind": "comments"})
            block = [cl]
            prev = cl
    if len(block) >= 3:
        ranges.append({"start_line": block[0], "end_line": block[-1] + 1, "kind": "comments"})
    return ranges


def _deduplicate_ranges(ranges: list) -> list:
    """Deduplicate and sort folding ranges, keep max 100."""
    seen = set()
    unique = []
    for r in sorted(ranges, key=lambda x: (x["start_line"], x["end_line"])):
        key = (r["start_line"], r["end_line"], r.get("kind", ""))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:100]


def _ast_folding_range(target: Path, lang: str) -> list:
    """AST-based fallback for folding ranges: detect foldable blocks.

    Identifies docstrings, class/function bodies, import blocks,
    and long comment blocks via tree-sitter.
    """
    try:
        from ...tools.base import _get_language, _get_parser, detect_language  # noqa: I001

        lang_key = detect_language(str(target))
        if not lang_key:
            return []
        parser = _get_parser(lang_key)
        lang_obj = _get_language(lang_key)
        if parser is None or lang_obj is None:
            return []

        source = target.read_bytes()
        if not source:
            return []
        source_text = source.decode("utf-8", errors="replace")
        lines = source_text.split("\n")
        tree = parser.parse(source)
        if not tree or not tree.root_node:
            return []

        ranges = []
        ranges.extend(_detect_import_blocks(lines))
        ranges.extend(_walk_ast_for_foldable_blocks(tree))
        ranges.extend(_detect_comment_blocks(lines))
        return _deduplicate_ranges(ranges)
    except Exception:
        logger.debug("completion: error, returning []")
        return []


def code_folding_range_tool(
    path: str,
    language: Optional[str] = None,
) -> str:
    """Get foldable regions in a file via LSP.

    Returns ranges for imports, comments, regions, and other foldable blocks.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        ast_items = _ast_folding_range(target, lang)
        if ast_items:
            return fmt_ok(
                {
                    "path": str(target),
                    "language": lang,
                    "total": len(ast_items),
                    "ranges": ast_items,
                    "source": "ast-fallback",
                }
            )
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.folding_range(str(target))
    if not result:
        ast_items = _ast_folding_range(target, lang)
        if ast_items:
            return fmt_ok(
                {
                    "path": str(target),
                    "language": lang,
                    "total": len(ast_items),
                    "ranges": ast_items,
                    "source": "ast-fallback",
                }
            )
        return fmt_err("No folding ranges available — requires an active LSP server (pyright) for the file language")

    folding_kinds = {1: "comments", 2: "imports", 3: "region"}
    ranges = []
    for rng in result[:100]:
        ranges.append(
            {
                "start_line": rng.get("startLine", 0) + 1,
                "end_line": rng.get("endLine", 0) + 1,
                "kind": folding_kinds.get(rng.get("kind", 0), "other"),
            }
        )

    return fmt_ok(
        {
            "path": str(target),
            "language": lang,
            "total": len(result),
            "ranges": ranges,
            "lsp_server": bridge.command,
        }
    )


CODE_FOLDING_RANGE_SCHEMA = {
    "name": "code_folding_range",
    "description": "Get foldable regions in a file via LSP.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path"],
    },
}


def _ast_selection_range(target: Path, line: int, character: int = 0) -> list:
    """AST-based fallback for selection range: find enclosing nodes.

    Walks the tree-sitter AST to find all nodes that enclose
    the given position, sorted from innermost to outermost.
    """
    try:
        from ...tools.base import _get_parser, detect_language

        lang_key = detect_language(str(target))
        if not lang_key:
            return []
        parser = _get_parser(lang_key)
        if parser is None:
            return []

        source = target.read_bytes()
        if not source:
            return []
        tree = parser.parse(source)
        if not tree or not tree.root_node:
            return []

        zero_line = max(0, line - 1)
        zero_char = max(0, character - 1)

        enclosing = []

        def _walk(node, depth=0):
            if depth > 50:
                return
            if node.start_point[0] <= zero_line <= node.end_point[0]:
                # Check if cursor is within this node's range
                if (node.start_point[0] < zero_line or
                    (node.start_point[0] == zero_line and node.start_point[1] <= zero_char)):
                    if (node.end_point[0] > zero_line or
                        (node.end_point[0] == zero_line and node.end_point[1] >= zero_char)):
                        enclosing.append({
                            "level": depth,
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "kind": node.type,
                        })
            for child in node.children:
                _walk(child, depth + 1)

        _walk(tree.root_node)

        # Return from innermost (deepest) to outermost
        enclosing.sort(key=lambda x: -x["level"])
        return [{"level": i, "start_line": r["start_line"],
                  "end_line": r["end_line"], "kind": r["kind"]}
                for i, r in enumerate(enclosing[:20])]
    except Exception:
        logger.debug("completion: error, returning []")
        return []


def code_selection_range_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get nested selection ranges (expandable scopes) via LSP.

    Returns ranges from innermost (smallest) to outermost (parent block).
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    lsp_char = (character or 0) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        ast_items = _ast_selection_range(target, line, character or 0)
        if ast_items:
            return fmt_ok(
                {
                    "path": str(target),
                    "line": line,
                    "character": character or 0,
                    "language": lang,
                    "selection_levels": len(ast_items),
                    "ranges": ast_items,
                    "source": "ast-fallback",
                }
            )
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.selection_range(str(target), lsp_line, max(0, lsp_char))
    if not result:
        ast_items = _ast_selection_range(target, line, character or 0)
        if ast_items:
            return fmt_ok(
                {
                    "path": str(target),
                    "line": line,
                    "character": character or 0,
                    "language": lang,
                    "selection_levels": len(ast_items),
                    "ranges": ast_items,
                    "source": "ast-fallback",
                }
            )
        return fmt_err(
            "No selection ranges at position — requires an active LSP server (pyright) for the file language"
        )

    ranges = []
    for idx, sr in enumerate(result):
        rng = sr.get("range", {})
        sr.get("parent", {})
        ranges.append(
            {
                "level": idx,
                "start_line": rng.get("start", {}).get("line", 0) + 1,
                "end_line": rng.get("end", {}).get("line", 0) + 1,
            }
        )

    return fmt_ok(
        {
            "path": str(target),
            "line": line,
            "character": character or 0,
            "language": lang,
            "selection_levels": len(ranges),
            "ranges": ranges,
            "lsp_server": bridge.command,
        }
    )


CODE_SELECTION_RANGE_SCHEMA = {
    "name": "code_selection_range",
    "description": (
        "Get nested selection ranges at a position via LSP. Returns scopes "
        "from innermost expression to outermost function/class block. "
        "Use to expand/shrink selection across AST boundaries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def code_linked_editing_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get linked editing ranges (e.g. paired HTML tags) via LSP.

    Returns word range + list of paired positions for simultaneous editing.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    lsp_char = (character or 0) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.linked_editing(str(target), lsp_line, max(0, lsp_char))
    if not result:
        return fmt_err("No linked editing ranges at position")

    word_range = result.get("wordRange", {})
    linked_ranges = result.get("ranges", [])

    return fmt_ok(
        {
            "path": str(target),
            "line": line,
            "character": character or 0,
            "language": lang,
            "word_range": {
                "start_line": word_range.get("start", {}).get("line", 0) + 1,
                "end_line": word_range.get("end", {}).get("line", 0) + 1,
            },
            "linked_ranges_count": len(linked_ranges),
            "lsp_server": bridge.command,
        }
    )


CODE_LINKED_EDITING_SCHEMA = {
    "name": "code_linked_editing",
    "description": (
        "Get linked editing ranges via LSP. For HTML/JSX tags, returns paired "
        "positions where edits should be mirrored (e.g. both opening and closing tag)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def code_prepare_rename_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Check if a symbol is renameable via LSP.

    Returns the range and placeholder for the symbol, or an error if
    renaming would be invalid.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    lsp_char = (character or 0) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.prepare_rename(str(target), lsp_line, max(0, lsp_char))
    if result and isinstance(result, dict) and "range" in result:
        rng = result["range"]
        return fmt_ok(
            {
                "path": str(target),
                "line": line,
                "character": character or 0,
                "language": lang,
                "renameable": True,
                "range": {
                    "start_line": rng.get("start", {}).get("line", 0) + 1,
                    "end_line": rng.get("end", {}).get("line", 0) + 1,
                },
                "placeholder": result.get("placeholder", ""),
                "lsp_server": bridge.command,
            }
        )

    # If LSP returned a response but no "range" key, symbol is not renameable
    return fmt_ok(
        {
            "path": str(target),
            "line": line,
            "character": character or 0,
            "language": lang,
            "renameable": False,
            "lsp_server": getattr(bridge, "command", "unknown"),
        }
    )


CODE_PREPARE_RENAME_SCHEMA = {
    "name": "code_prepare_rename",
    "description": (
        "Check if a symbol is safe to rename via LSP textDocument/prepareRename. "
        "Returns renameable=true/false plus the exact range and placeholder. "
        "Use BEFORE calling code_rename to verify the operation is valid."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


# Handler functions for registry dispatch
def _handle_code_completion(args, **kw):
    return code_completion_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_code_lens(args, **kw):
    return code_code_lens_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_folding_range(args, **kw):
    return code_folding_range_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_selection_range(args, **kw):
    return code_selection_range_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_linked_editing(args, **kw):
    return code_linked_editing_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_prepare_rename(args, **kw):
    return code_prepare_rename_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


# ---- semantic_tokens ----
