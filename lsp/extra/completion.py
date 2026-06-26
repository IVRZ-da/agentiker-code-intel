"""lsp/extra/ — LSP completion + code lens tools."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..._fmt import fmt_err, fmt_ok
from ..bridge import (
    _detect_language_for_lsp,
    get_lsp_manager,
)

_LSP_COMPLETION_KIND = {
    1: "Text", 2: "Method", 3: "Function", 4: "Constructor",
    5: "Field", 6: "Variable", 7: "Class", 8: "Interface",
    9: "Module", 10: "Property", 11: "Unit", 12: "Value",
    13: "Enum", 14: "Keyword", 15: "Snippet", 16: "Color",
    17: "File", 18: "Reference", 19: "Folder", 20: "EnumMember",
    21: "Constant", 22: "Struct", 23: "Event", 24: "Operator",
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
        completions.append({
            "label": item.get("label", "?"),
            "kind": _LSP_COMPLETION_KIND.get(item.get("kind", 0), "unknown"),
            "detail": item.get("detail", ""),
            "documentation": item.get("documentation", ""),
        })

    return fmt_ok({
        "path": str(target),
        "line": line,
        "character": character,
        "language": lang,
        "total": len(items),
        "completions": completions,
        "lsp_server": bridge.command,
    })


CODE_COMPLETION_SCHEMA = {
    "name": "code_completion",
    "description": (
        "Get completion suggestions at a cursor position via LSP. "
        "Returns a list of labels, kinds (Function/Variable/Keyword/Class), "
        "and detail text. Useful for exploring API surface without reading documentation."
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
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.code_lens(str(target))
    if not result:
        return fmt_err("No code lens items available")

    lens_items = []
    for item in result[:50]:
        rng = item.get("range", {})
        command = item.get("command", {})
        lens_items.append({
            "range": {
                "start_line": rng.get("start", {}).get("line", 0) + 1,
                "end_line": rng.get("end", {}).get("line", 0) + 1,
            },
            "title": command.get("title", ""),
            "command": command.get("command", ""),
        })

    return fmt_ok({
        "path": str(target),
        "language": lang,
        "total": len(result),
        "lens_items": lens_items,
        "lsp_server": bridge.command,
    })


CODE_CODE_LENS_SCHEMA = {
    "name": "code_code_lens",
    "description": (
        "Get code lens items for a file via LSP. Returns reference counts, "
        "test run status, and clickable commands per symbol. "
        "Useful for quickly seeing which functions are tested and how often they're referenced."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path"],
    },
}


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
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.folding_range(str(target))
    if not result:
        return fmt_err("No folding ranges available")

    folding_kinds = {1: "comments", 2: "imports", 3: "region"}
    ranges = []
    for rng in result[:100]:
        ranges.append({
            "start_line": rng.get("startLine", 0) + 1,
            "end_line": rng.get("endLine", 0) + 1,
            "kind": folding_kinds.get(rng.get("kind", 0), "other"),
        })

    return fmt_ok({
        "path": str(target),
        "language": lang,
        "total": len(result),
        "ranges": ranges,
        "lsp_server": bridge.command,
    })


CODE_FOLDING_RANGE_SCHEMA = {
    "name": "code_folding_range",
    "description": (
        "Get foldable regions in a file via LSP. Returns ranges with kind "
        "(comments, imports, region) for collapsing/expanding code blocks. "
        "Useful for understanding file structure at a glance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path"],
    },
}


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
        return fmt_err(f"No LSP bridge available for {lang}")

    result = bridge.selection_range(str(target), lsp_line, max(0, lsp_char))
    if not result:
        return fmt_err("No selection ranges at position")

    ranges = []
    for idx, sr in enumerate(result):
        rng = sr.get("range", {})
        sr.get("parent", {})
        ranges.append({
            "level": idx,
            "start_line": rng.get("start", {}).get("line", 0) + 1,
            "end_line": rng.get("end", {}).get("line", 0) + 1,
        })

    return fmt_ok({
        "path": str(target),
        "line": line,
        "character": character or 0,
        "language": lang,
        "selection_levels": len(ranges),
        "ranges": ranges,
        "lsp_server": bridge.command,
    })


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

    return fmt_ok({
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
    })


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
        return fmt_ok({
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
        })

    # If LSP returned a response but no "range" key, symbol is not renameable
    return fmt_ok({
        "path": str(target),
        "line": line,
        "character": character or 0,
        "language": lang,
        "renameable": False,
        "lsp_server": getattr(bridge, "command", "unknown"),
    })


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
