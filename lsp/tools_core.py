"""lsp/core.py — extracted from lsp/tools.py."""
# ruff: noqa: E402, F401, F405
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._fmt import fmt_err, fmt_ok
from .bridge import (
    LSPBridge,
    _cached_read_lines,
    _detect_language_for_lsp,
    _find_workspace_root,
    _location_to_dict,
    _read_context_lines,
    get_lsp_manager,
    logger,
)


def code_definition_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Go to definition: find where a symbol is defined.

    Uses LSP (pyright/pylsp) for Python files with automatic fallback
    to AST-based search if the server is unavailable.

    Args:
        path: Absolute file path.
        line: 1-based line number (where the symbol reference is).
        character: 1-based column (optional, will auto-detect the identifier).
        language: Language override (default: auto-detect from extension).

    Returns:
        JSON with definition locations.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_definition_tool: %s:%d:%s lang=%s", path, line, character or "auto", lang)

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            logger.warning("code_definition: no LSP bridge for lang=%s file=%s", lang, path)
        elif not bridge.ensure_initialized():
            logger.warning("code_definition: LSP bridge failed to initialize (server=%s)", bridge.command)
        else:
            logger.debug("code_definition: using LSP bridge: %s (rootUri=%s)", bridge.command, bridge.root_uri)
            locations = bridge.goto_definition(str(target), lsp_line, lsp_char)
            if locations:
                logger.info("code_definition: LSP returned %d locations", len(locations))
                defs = [_location_to_dict(loc) for loc in locations]
                return fmt_ok({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "definition_count": len(defs),
                    "definitions": defs,
                    "formatted": _format_definitions(defs),
                })
            else:
                logger.info("code_definition: LSP returned 0 locations, falling back to AST")

    # Fallback: AST-based definition search
    logger.debug("code_definition: using AST fallback")
    return _ast_fallback_definition(str(target), line, character, lang)


def code_highlight_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Find ALL occurrences of a symbol in the current file (file-local).

    Faster than code_references when you only need file-local matches.
    Returns ranges with kind (1=text, 2=read, 3=write) and surrounding context.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (optional, will auto-detect the identifier).
        language: Language override (default: auto-detect from extension).

    Returns:
        JSON with highlight locations.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_highlight_tool: %s:%d:%s lang=%s", path, line, character, lang)

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            logger.warning("code_highlight: no LSP bridge for lang=%s file=%s", lang, path)
        elif not bridge.ensure_initialized():
            logger.warning("code_highlight: LSP bridge failed to initialize (server=%s)", bridge.command)
        else:
            logger.debug("code_highlight: using LSP bridge: %s (rootUri=%s)", bridge.command, bridge.root_uri)
            highlights = bridge.document_highlight(str(target), lsp_line, lsp_char)
            if highlights:
                logger.info("code_highlight: LSP returned %d highlights", len(highlights))
                # Format highlights with context
                formatted = []
                for h in highlights:
                    rng = h.get("range", {})
                    start = rng.get("start", {})
                    end = rng.get("end", {})
                    hl_line = start.get("line", 0)
                    context_lines = _read_context_lines(str(target), hl_line, context=2)
                    fmt = {
                        "line": hl_line + 1,
                        "start_column": start.get("character", 0) + 1,
                        "end_line": end.get("line", 0) + 1,
                        "end_column": end.get("character", 0) + 1,
                        "kind": h.get("kind", 0),
                        "kind_label": {1: "text", 2: "read", 3: "write"}.get(h.get("kind", 0), "unknown"),
                        "text": context_lines[1].strip()[:200] if len(context_lines) > 1 else "",
                        "context": context_lines,
                    }
                    formatted.append(fmt)

                return fmt_ok({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "highlight_count": len(formatted),
                    "highlights": formatted,
                })

    # No LSP — documentHighlight has no AST fallback (it's LSP-only)
    return fmt_ok({
        "path": str(target),
        "query": {"line": line, "character": character},
        "method": "none",
        "highlight_count": 0,
        "highlights": [],
        "note": "documentHighlight requires LSP — no AST fallback available",
    })


def code_inlay_hints_tool(
    path: str,
    start_line: int = 1,
    end_line: int = 0,
) -> str:
    """Get inferred type hints (inlay hints) for a code range.

    Shows types for variables, parameters, and return values inline.
    Like VSCode's type hints but accessible from the terminal.

    Args:
        path: Absolute file path.
        start_line: 1-based start line (default: 1).
        end_line: 1-based end line (default: 0 = full file).

    Returns:
        JSON with inlay hints.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    logger.info("code_inlay_hints_tool: %s lines=%d-%d lang=%s", path, start_line, end_line or "EOF", lang)

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    hints = bridge.inlay_hints(str(target), start_line=start_line, end_line=end_line)
    if not hints:
        return fmt_ok({
            "path": str(target),
            "range": {"start_line": start_line, "end_line": end_line},
            "hint_count": 0,
            "hints": [],
            "note": "No inlay hints returned (LSP server may not support textDocument/inlayHint)",
        })

    # Format hints
    formatted = []
    for h in hints:
        pos = h.get("position", {})
        label_parts = h.get("label", [])
        # label can be a string or an array of InlayHintLabelPart
        if isinstance(label_parts, list):
            label = "".join(p.get("value", str(p)) for p in label_parts)
        else:
            label = str(label_parts)
        formatted.append({
            "line": pos.get("line", 0) + 1,
            "column": pos.get("character", 0) + 1,
            "label": label[:200],
            "kind": h.get("kind", 0),
            "kind_label": {1: "type", 2: "parameter"}.get(h.get("kind", 0), "unknown"),
        })

    return fmt_ok({
        "path": str(target),
        "range": {"start_line": start_line, "end_line": end_line},
        "method": "lsp",
        "lsp_server": bridge.command,
        "hint_count": len(formatted),
        "hints": formatted,
    })


def code_document_symbols_tool(
    path: str,
    language: Optional[str] = None,
) -> str:
    """Get all symbols in a file via LSP textDocument/documentSymbol.

    Returns functions, classes, variables, constants, type aliases, and
    other symbols with their hierarchy (children nesting). Supplements the
    AST-based code_symbols with LSP-level information including constants,
    type aliases, and proper nesting that pure AST parsing may miss.

    Args:
        path: Absolute file path.
        language: Language override (default: auto-detect from extension).

    Returns:
        JSON with document symbols tree.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    logger.info("code_document_symbols_tool: %s lang=%s", path, lang)

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"No LSP bridge available for {lang}")

    symbols = bridge.document_symbols(str(target))
    if not symbols:
        return fmt_ok({
            "path": str(target),
            "method": "lsp",
            "lsp_server": bridge.command,
            "symbol_count": 0,
            "symbols": [],
            "note": "No document symbols returned (LSP server may not support textDocument/documentSymbol)",
        })

    # Format with kind names for readability
    _SYMBOL_KIND_NAMES = {
        1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
        6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
        11: "interface", 12: "function", 13: "variable", 14: "constant",
        15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
        20: "key", 21: "null", 22: "enumMember", 23: "struct", 24: "event",
        25: "operator", 26: "typeParameter",
    }

    def _format_symbol(sym: dict, depth: int = 0) -> dict:
        """Recursively format a DocumentSymbol with kind name."""
        kind_val = sym.get("kind", 0)
        rng = sym.get("selectionRange", {})
        start = rng.get("start", {}) if rng else {}
        formatted_sym = {
            "name": sym.get("name", ""),
            "kind": kind_val,
            "kind_name": _SYMBOL_KIND_NAMES.get(kind_val, "unknown"),
            "detail": sym.get("detail", ""),
            "line": start.get("line", 0) + 1,
        }
        children = sym.get("children")
        if children:
            formatted_sym["children"] = [
                _format_symbol(c, depth + 1) for c in children
            ]
            formatted_sym["child_count"] = len(children)
        return formatted_sym

    formatted = [_format_symbol(s) for s in symbols]

    return fmt_ok({
        "path": str(target),
        "method": "lsp",
        "lsp_server": bridge.command,
        "symbol_count": len(formatted),
        "symbols": formatted,
    })


def code_references_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
    include_declaration: bool = True,
    group_by_file: bool = False,
    max_results: int = 0,
) -> str:
    """Find all references to a symbol across the project.

    Uses LSP (pyright/pylsp) for Python files with automatic fallback
    to AST-based search if the server is unavailable.

    Args:
        path: Absolute file path.
        line: 1-based line number (where the symbol is).
        character: 1-based column (optional, will auto-detect the identifier).
        language: Language override (default: auto-detect from extension).
        include_declaration: Include the symbol's own declaration (default: True).
        group_by_file: Return references grouped by file instead of a flat list (default: False).
            Reduces token usage for large codebases.
        max_results: Maximum references to return (default: 0 = unlimited).

    Returns:
        JSON with reference locations.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character position if not provided
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_references_tool: %s:%d:%s lang=%s includeDecl=%s",
        path, line, character, lang, include_declaration)

    # Try LSP first
    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            logger.warning("code_references: no LSP bridge for lang=%s file=%s", lang, path)
        elif not bridge.ensure_initialized():
            logger.warning("code_references: LSP bridge failed to initialize (server=%s)", bridge.command)
        else:
            logger.debug("code_references: using LSP bridge: %s (rootUri=%s)", bridge.command, bridge.root_uri)
            locations = bridge.find_references(
                str(target), lsp_line, lsp_char, include_declaration
            )
            if locations:
                logger.info("code_references: LSP returned %d locations", len(locations))
                refs = [_location_to_dict(loc) for loc in locations]
                # Apply max_results cap
                if max_results > 0:
                    refs = refs[:max_results]
                # Group by file
                by_file: Dict[str, List[dict]] = {}
                for r in refs:
                    by_file.setdefault(r["file"], []).append(r)

                if not group_by_file:
                    return fmt_ok({
                        "path": str(target),
                        "query": {"line": line, "character": character},
                        "method": "lsp",
                        "lsp_server": bridge.command,
                        "reference_count": len(refs),
                        "files_affected": len(by_file),
                        "references": refs,
                        "by_file": by_file,
                        "formatted": _format_references(refs, by_file),
                    })
                # Compact group-by-file mode (token-saving)
                compact_by_file = {
                    f: [{"line": r["line"], "column": r.get("column"), "text": r.get("text", "")[:80]}
                         for r in file_refs]
                    for f, file_refs in sorted(by_file.items())
                }
                return fmt_ok({
                    "path": str(target),
                    "query": {"line": line, "character": character},
                    "method": "lsp",
                    "lsp_server": bridge.command,
                    "reference_count": len(refs),
                    "files_affected": len(by_file),
                    "by_file": compact_by_file,
                    "formatted": _format_references(refs, by_file),
                })
            else:
                logger.info("code_references: LSP returned 0 locations, falling back to AST")

    # Fallback: AST-based references search
    logger.debug("code_references: using AST fallback")
    return _ast_fallback_references(str(target), line, character, lang)


def code_diagnostics_tool(
    path: str,
    language: Optional[str] = None,
) -> str:
    """Fetch LSP diagnostics (errors, warnings, info) for a file.

    Falls back to lightweight AST heuristic if no LSP server is available.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("No implementations found at position")

    lang = language or _detect_language_for_lsp(str(target))
    diagnostics: list[dict] = []
    bridge: Optional[Any] = None

    manager = get_lsp_manager()
    if lang:
        bridge = manager.get_bridge(lang, str(target))
        if bridge and bridge.ensure_initialized():
            # Open the document first so the LSP server sends publishDiagnostics
            bridge.open_document(str(target))
            bridge._wait_for_document_ready(is_first_request=True)

            # Try cached LSP diagnostics (populated by textDocument/publishDiagnostics)
            cached = bridge.get_cached_diagnostics(str(target))
            if cached:
                diagnostics = cached
                logger.info("code_diagnostics: got %d cached diagnostics for %s", len(cached), str(target))

    diagnostics = _pull_lsp_diagnostics(diagnostics, bridge, str(target))

    if diagnostics:
        summary = {
            "path": str(target),
            "method": "lsp",
            "lsp_server": bridge.command if bridge else None,
            "diagnostic_count": len(diagnostics),
            "errors": len([d for d in diagnostics if d.get("severity", 1) == 1]),
            "warnings": len([d for d in diagnostics if d.get("severity", 2) == 2]),
            "diagnostics": diagnostics[:20],  # Cap to avoid token bloat
        }
        return fmt_ok(summary)

    # Fallback: AST heuristic
    logger.debug("code_diagnostics: using AST fallback")
    return _ast_fallback_diagnostics(str(target), lang)



def _pull_lsp_diagnostics(diagnostics: list, bridge, target: str) -> list:
    """Try LSP 3.17+ diagnostic pull, return updated diagnostics list."""
    if diagnostics or not bridge or not bridge.ensure_initialized():
        return diagnostics
    try:
        resp = bridge._send_request("textDocument/diagnostic", {
            "textDocument": {"uri": f"file://{target}"},
            "identifier": "code_intel",
            "previousResultId": None,
        }, timeout=10)
        if resp and "items" in resp:
            diagnostics = resp["items"]
            logger.info("code_diagnostics: LSP pull returned %d items", len(diagnostics))
    except Exception as exc:
        logger.debug("textDocument/diagnostic not supported by %s: %s", bridge.command, exc)
    return diagnostics


def _resolve_target_and_lang(
    path: str, line: int, character: Optional[int] = None, language: Optional[str] = None,
):
    """Resolve path, detect language, auto-detect identifier column.

    Returns ``(target: Path | None, lang: str | None, col_or_error: int | str)``.
    On failure ``target`` is ``None`` and ``col_or_error`` holds the error JSON string.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return None, None, fmt_err(f"Path not found: {path}")
    lang = language or _detect_language_for_lsp(str(target))
    character_resolved = character
    if character_resolved is None:
        character_resolved = _auto_detect_identifier_column(str(target), line)
    col = character_resolved if character_resolved is not None else 1
    return target, lang, col




# ── Re-exports from extracted modules ───────────────────────
from .call_hierarchy import (  # noqa: F401
    CODE_CALL_HIERARCHY_SCHEMA,
    CODE_CALLEES_SCHEMA,
    CODE_CALLERS_SCHEMA,
    _fallback_reference_callers,
    _try_lsp_callers,
    code_call_hierarchy_tool,
    code_callees_tool,
    code_callers_tool,
)


def _import_detect_language():
    """Delegate to heuristics for test compat (tests patch tools_core._import_detect_language)."""
    from .heuristics import _import_detect_language as _idl
    return _idl()


# ── Schemas ──────────────────────────────────────────────
CODE_HIGHLIGHT_SCHEMA = {
    "name": "code_highlight",
    "description": "Find ALL occurrences of a symbol in the current file."
                   "Faster than code_references when you only need file-local matches. "
                   "Returns ranges with kind (1=text, 2=read, 3=write) and surrounding context.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
        },
        "required": ["path", "line"],
    },
}

CODE_INLAY_HINTS_SCHEMA = {
    "name": "code_inlay_hints",
    "description": "Get inferred type hints (inlay hints) for a code range. "
                   "Shows types for variables, parameters, and return values inline. "
                   "Like VSCode's type hints but accessible from the terminal.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "start_line": {"type": "integer", "description": "1-based start line (default: 1)"},
            "end_line": {"type": "integer", "description": "1-based end line (default: 0 = full file)"},
        },
        "required": ["path"],
    },
}

CODE_TYPE_HIERARCHY_SCHEMA = {
    "name": "code_type_hierarchy",
    "description": "Find type hierarchy — supertypes and subtypes for a symbol."
                   "(child types). Uses LSP typeHierarchy when available (Java, C#, Swift), "
                   "falls back to AST-based analysis for Python/TypeScript.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
            "direction": {"type": "string", "enum": ["supertypes", "subtypes", "both"], "description": "Direction of hierarchy (default: both)"},
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path", "line"],
    },
}

CODE_DOCUMENT_SYMBOLS_SCHEMA = {
    "name": "code_document_symbols",
    "description": "Get ALL symbols in a file via LSP — functions, classes, variables, types."
                   "constants, type aliases, and nested hierarchy. Supplements the AST-based code_symbols with "
                   "LSP-level information including constants, type aliases, and proper nesting.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Language override (auto-detected from extension)"},
        },
        "required": ["path"],
    },
}

CODE_DEFINITION_SCHEMA = {
    "name": "code_definition",
    "description": (
        "Navigate to the original declaration/definition of a symbol using LSP. "
        "Tells you WHERE a function, class, variable, or type is defined. "
        "Requires a file path and the line where the symbol reference appears. "
        "Uses pyright/pylsp for Python, typescript-language-server for TS/JS (cross-file resolution). "
        "Falls back to AST-based search if LSP is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path containing the symbol reference"},
            "line": {"type": "integer", "description": "1-based line number where the symbol appears"},
            "character": {"type": "integer", "description": "1-based column position of the symbol (optional, auto-detected)"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
        },
        "required": ["path", "line"],
    },
}

CODE_REFERENCES_SCHEMA = {
    "name": "code_references",
    "description": (
        "Find ALL project-wide usages/references of a symbol using LSP. "
        "Shows every file and line where a function, class, variable, or type is used. "
        "Requires a file path and the line where the symbol is defined or referenced. "
        "Uses pyright/pylsp for Python, typescript-language-server for TS/JS (cross-file resolution). "
        "Falls back to text-based search if LSP is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number where the symbol appears"},
            "character": {"type": "integer", "description": "1-based column position of the symbol (optional, auto-detected)"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
            "include_declaration": {"type": "boolean", "description": "Include the symbol's own declaration in results (default: True)"},
            "group_by_file": {"type": "boolean", "description": "Group references by file and truncate line text to save tokens (default: False)"},
            "max_results": {"type": "integer", "description": "Maximum references to return (default: 0 = unlimited)"},
        },
        "required": ["path", "line"],
    },
}

CODE_DIAGNOSTICS_SCHEMA = {
    "name": "code_diagnostics",
    "description": (
        "Fetch LSP diagnostics (errors, warnings, info) for a source file. "
        "Falls back to a lightweight AST lint heuristic if no LSP server is active."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
        },
        "required": ["path"],
    },
}

__all__ = [
    "code_definition_tool",
    "code_highlight_tool",
    "code_inlay_hints_tool",
    "code_document_symbols_tool",
    "code_references_tool",
    "code_diagnostics_tool",
    "code_type_hierarchy_tool",
    "code_callers_tool",
    "code_callees_tool",
    "code_call_hierarchy_tool",
    "_pull_lsp_diagnostics",
    "_resolve_target_and_lang",
    "_try_lsp_callers",
    "_fallback_reference_callers",
    "CODE_HIGHLIGHT_SCHEMA",
    "CODE_INLAY_HINTS_SCHEMA",
    "CODE_TYPE_HIERARCHY_SCHEMA",
    "CODE_DOCUMENT_SYMBOLS_SCHEMA",
    "CODE_DEFINITION_SCHEMA",
    "CODE_REFERENCES_SCHEMA",
    "CODE_DIAGNOSTICS_SCHEMA",
]

from .heuristics import (  # noqa: F401
    _ast_fallback_callees,
    _ast_fallback_definition,
    _ast_fallback_diagnostics,
    _ast_fallback_references,
    _auto_detect_identifier_column,
    _auto_detect_paren_column,
    _build_unused_import_diags,
    _extract_identifier,
    _extract_python_callees,
    _extract_ts_callees,
    _format_definitions,
    _format_diagnostics_result,
    _format_references,
    _import_detect_language,
    _python_ast_analyze,
    _read_file_safe,
    _rg_search,
    _tsjs_import_heuristic,
)


def code_type_hierarchy_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    direction: str = "both",
    language: Optional[str] = None,
) -> str:
    """Find type hierarchy — supertypes (parent types) and subtypes (child types).

    Uses LSP typeHierarchy when the server supports it (Java, C#, Swift).
    Falls back to AST-based analysis for Python/TypeScript.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (auto-detected if omitted).
        direction: "supertypes", "subtypes", or "both" (default).
        language: Language override.

    Returns:
        Formatted tree string.
    """
    from pathlib import Path

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Path not found: {path}")

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None

    # LSP Server die TypeHierarchy unterstützen
    _LANG_SUPPORTS_LSP_TYPE_HIERARCHY = {"java", "csharp", "swift"}

    col = character
    if col is None:
        col = _auto_detect_identifier_column(str(target), line - 1) or 1

    result_lines = []
    warnings = []

    supers = None
    subs = None

    # LSP-Versuch (nur für Sprachen die TypeHierarchy unterstützen)
    if bridge and bridge.ensure_initialized() and lang in _LANG_SUPPORTS_LSP_TYPE_HIERARCHY:
        try:
            supers_lsp = bridge.type_supertypes(str(target), line - 1, col - 1)
            subs_lsp = bridge.type_subtypes(str(target), line - 1, col - 1)
            supers = supers_lsp
            subs = subs_lsp
            if supers or subs:
                warnings.append("via LSP TypeHierarchy")
        except Exception as e:
            logger.debug("code_type_hierarchy_tool: LSP type hierarchy failed: %s", e)
            pass

    # AST-Fallback (Python/TypeScript)
    if supers is None and subs is None:
        try:
            from .code_tools import _ast_type_hierarchy_subtypes, _ast_type_hierarchy_supertypes
            supers = _ast_type_hierarchy_supertypes(str(target), line)
            subs = _ast_type_hierarchy_subtypes(str(target), line)
            if supers or subs:
                warnings.append("via AST analysis (LSP typeHierarchy not available for this language)")
        except Exception as e:
            logger.debug("code_type_hierarchy_tool: AST fallback failed: %s", e)
            pass

    # Output
    if direction in ("supertypes", "both"):
        result_lines.append(f"Supertypes ({Path(target).name}:{line}):")
        if supers:
            for s in supers:
                result_lines.append(f"  ├── {s.get('name', '?')} ({s.get('kind', '?')}) — line {s.get('line', '?')}")
        else:
            result_lines.append("  (none)")

    if direction == "both":
        result_lines.append("")

    if direction in ("subtypes", "both"):
        result_lines.append(f"Subtypes ({Path(target).name}:{line}):")
        if subs:
            for s in subs:
                result_lines.append(f"  ├── {s.get('name', '?')} ({s.get('kind', '?')}) — line {s.get('line', '?')}")
        else:
            result_lines.append("  (none)")

    if warnings:
        result_lines.append("")
        for w in warnings:
            result_lines.append(f"ℹ️ {w}")

    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# AST-based fallback
# ---------------------------------------------------------------------------
