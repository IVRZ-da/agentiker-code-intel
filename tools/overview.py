#!/usr/bin/env python3
"""
tools/overview.py — Compact overview of symbols in a file or directory.

AST-powered tree view of symbols using tree-sitter queries.
Native module (not a re-export facade).
"""

from pathlib import Path
from typing import List, Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger
from .base import (
    _EXT_TO_LANG,
    _classify_symbol_kind,
    _detect_if_method,
    _setup_query,
    detect_language,
)

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CODE_OVERVIEW_SCHEMA = {
    "name": "code_overview",
    "description": (
        "Get a compact overview of all symbols in a source file or directory. "
        "Shows a tree view with symbol names, kinds, line numbers, and "
        "hierarchy. More token-efficient than code_symbols or "
        "code_document_symbols for quick orientation. "
        "Use depth=0 for top-level only, depth=1 (default) for methods/fields, "
        "depth=2 for deeper nesting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file or directory path.",
            },
            "depth": {
                "type": "integer",
                "description": (
                    "How deep to expand: 0 = top-level only, "
                    "1 (default) = include class members, "
                    "2 = include nested members."
                ),
                "default": 1,
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_overview_tree(
    source: bytes,
    lang_key: str,
    depth: int = 1,
) -> List[dict]:
    """Build a hierarchy of symbols from source code.

    Returns a list of dicts with nesting via 'children' key.
    Each entry: {name, kind, line, end_line, signature, children}
    """
    from tree_sitter import QueryCursor as _QC

    setup = _setup_query(lang_key)
    if setup is None:
        return []
    parser, lang, query = setup

    tree = parser.parse(source)
    qc = _QC(query)
    source_lines = source.split(b"\n")

    top_level: List[dict] = []

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
        except (UnicodeDecodeError, IndexError, AttributeError):
            continue

        start_line = def_node.start_point[0] + 1
        end_line = def_node.end_point[0] + 1
        sig_start = def_node.start_point[0]
        sig_end = min(def_node.end_point[0], sig_start + 2)
        signature = (
            b"\n".join(source_lines[sig_start:sig_end])
            .decode("utf-8", errors="replace")
            .strip()
        )
        kind = _classify_symbol_kind(def_node)
        kind = _detect_if_method(def_node, kind)

        entry = {
            "name": name_text,
            "kind": kind,
            "line": start_line,
            "end_line": end_line,
            "signature": signature,
            "children": [],
        }

        # Determine nesting level based on parent chain
        _parent = def_node.parent
        _depth = 0
        _is_nested = False
        while _parent and _depth < 10:
            if _parent.type in (
                "class_definition", "class_declaration",
                "block", "body", "declaration_list",
                "impl_item", "interface_declaration",
                "module_body", "program",
            ):
                pass  # structural parents
            _parent = _parent.parent
            _depth += 1

        # Simple heuristic: if parents include a class/impl, it's nested
        _p = def_node.parent
        _found_class = False
        while _p and _p.type != "program" and _p.type != "module":
            if _p.type in (
                "class_definition", "class_declaration",
                "impl_item", "interface_declaration",
                "decorated_definition",
            ):
                _found_class = True
                break
            _p = _p.parent

        if _found_class and depth > 0:
            # Add to last top-level symbol's children
            if top_level:
                top_level[-1]["children"].append(entry)
            else:
                top_level.append(entry)
        elif not _found_class:
            top_level.append(entry)
        # else: depth=0 and inside a class — skip (no children at depth=0)

    return top_level


def _format_overview_tree(
    path: str,
    symbols: List[dict],
    lang_key: str,
    total_lines: int,
    depth: int = 1,
) -> str:
    """Format the symbol tree as a compact string."""
    lines = []

    # Header
    p = Path(path)
    lang_display = lang_key or "unknown"
    lines.append(f"📄 {p.name} ({lang_display}, {total_lines} lines)")

    if not symbols:
        lines.append("  (no symbols found)")
        return "\n".join(lines)

    for i, sym in enumerate(symbols):
        is_last = i == len(symbols) - 1
        prefix = "└── " if is_last else "├── "
        _line_info = f"line {sym['line']}" + (
            f"-{sym['end_line']}" if sym['end_line'] != sym['line'] else ""
        )
        icon = {
            "function": "ƒ", "method": "ƒ", "class": "⊞",
            "interface": "⊟", "struct": "⊡", "enum": "⊡",
            "type": "τ", "variable": "v", "constant": "c",
            "module": "⊟", "trait": "τ", "impl": "⊞",
        }.get(sym["kind"], "•")
        lines.append(f"{prefix}{icon} {sym['kind']} {sym['name']} ({_line_info})")

        # Children (methods, fields)
        if sym["children"] and depth > 0:
            for j, child in enumerate(sym["children"]):
                is_last_child = j == len(sym["children"]) - 1
                c_prefix = "    " + ("└── " if is_last_child else "├── ")
                _c_line = f"line {child['line']}" + (
                    f"-{child['end_line']}" if child['end_line'] != child['line'] else ""
                )
                c_icon = {
                    "function": "ƒ", "method": "ƒ",
                    "class": "⊞", "field": "•",
                    "variable": "v", "constant": "c",
                }.get(child["kind"], "•")
                lines.append(f"{c_prefix}{c_icon} {child['kind']} {child['name']} ({_c_line})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def code_overview_tool(
    path: str,
    depth: int = 1,
    language: Optional[str] = None,
) -> str:
    """Get a compact overview of symbols in a file or directory.

    Args:
        path: Absolute file or directory path.
        depth: How deep to expand (0=top-level, 1=members, 2=nested).
        language: Language override.

    Returns:
        Formatted overview string.
    """

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_dir():
        # Scan directory
        results = []
        for ext, lang_key in sorted(_EXT_TO_LANG.items(), key=lambda x: x[0]):
            for f in sorted(target.rglob(f"*{ext}")):
                if not f.is_file():
                    continue
                overview = code_overview_tool(str(f), depth=depth, language=lang_key)
                results.append(overview)
        return "\n\n".join(results) if results else "No supported files found."

    # Single file
    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return fmt_ok({
            "error": (
                f"Unsupported language. "
                f"Supported: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            ),
        })

    try:
        source = target.read_bytes()
        total_lines = source.count(b"\n") + 1
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    symbols = _build_overview_tree(source, lang_key, depth=depth)
    return _format_overview_tree(str(target), symbols, lang_key, total_lines, depth=depth)


def _handle_code_overview(args, **kw):
    return code_overview_tool(
        path=args.get("path", ""),
        depth=int(args.get("depth", 1)),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "CODE_OVERVIEW_SCHEMA",
    "code_overview_tool",
    "_handle_code_overview",
    "_build_overview_tree",
    "_format_overview_tree",
]
