"""lsp/tools.py — Code intelligence tool functions extracted from lsp_bridge.py."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._fmt import fmt_err, fmt_info, fmt_ok
from ..code_tools import detect_language
from .bridge import (
    _LANGUAGE_SERVERS,
    LSPBridge,
    _apply_edits_by_file,
    _build_rename_preview,
    _cached_read_lines,
    _detect_language_for_lsp,
    _find_workspace_root,
    _location_to_dict,
    _parse_workspace_edit,
    _read_context_lines,
    _resolve_command,
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


def _try_lsp_callers(target, lang, line, col):
    """Try LSP callHierarchy/incomingCalls, return ``(callers, None)`` or ``(None, error_json)``."""
    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None
    if not bridge or not bridge.ensure_initialized():
        return None, None
    try:
        lsp_results = bridge.incoming_calls(str(target), line - 1, col - 1)
        if not lsp_results:
            return None, None
        callers = []
        for item in lsp_results:
            file_path = LSPBridge._uri_to_path(item.get("uri", ""))
            rng = item.get("range", {})
            start = rng.get("start", {}) if isinstance(rng, dict) else {}
            sl = start.get("line", 0) + 1
            callers.append({
                "file": file_path, "line": sl,
                "name": item.get("name", ""), "kind": item.get("kind", 0),
            })
        return callers, None
    except Exception as exc:
        logger.debug("code_callers: LSP callHierarchy failed: %s", exc)
        return None, None


def _fallback_reference_callers(target, line, character, lang):
    """Fallback: use ``code_references_tool`` + heuristic filter to find callers."""
    refs_json = code_references_tool(
        path=str(target), line=line, character=character,
        language=lang, include_declaration=False, group_by_file=True,
    )
    try:
        refs_data = _json.loads(refs_json)
    except Exception:
        return fmt_err("No implementations found at position")
    if "error" in refs_data:
        return refs_json

    by_file = refs_data.get("by_file", {})
    callers = []
    for file_path, locations in by_file.items():
        try:
            lines_list = _cached_read_lines(file_path)
            for loc in locations:
                ln = loc.get("line", 0)
                if 1 <= ln <= len(lines_list):
                    line_text = lines_list[ln - 1]
                    stripped = line_text.strip()
                    if '(' in stripped or '=' in stripped or 'return' in stripped:
                        callers.append({
                            "file": file_path, "line": ln,
                            "column": loc.get("column"), "text": line_text[:120],
                        })
        except Exception as e:
            logger.debug("code_references_tool: processing LSP locations: %s", e)
            continue
    return callers


def code_callers_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
    group_by_file: bool = False,
) -> str:
    """Find call sites of a symbol (where it is invoked).

    Uses LSP ``callHierarchy/incomingCalls`` when a language server is
    available, falls back to reference-based heuristic filtering.
    """

    target, lang, col_or_error = _resolve_target_and_lang(path, line, character, language)
    if target is None:
        return str(col_or_error)  # error JSON

    col = int(col_or_error)  # type: ignore[arg-type]

    # ── Try LSP callHierarchy first ──
    callers, _ = _try_lsp_callers(target, lang, line, col)
    if callers is not None:
        result = {
            "path": str(target), "method": "lsp_call_hierarchy",
            "query": {"line": line, "character": col},
            "caller_count": len(callers),
            "files_affected": len({c["file"] for c in callers}),
            "callers": callers,
        }
        if group_by_file:
            result["by_file"] = _group_by_file(callers)  # noqa: F821
        return fmt_ok(result)

    # ── Fallback: reference-based heuristic ──
    fallback = _fallback_reference_callers(str(target), line, character, lang)
    if isinstance(fallback, str):
        return fallback  # error JSON
    if not fallback:
        return fmt_ok({
            "path": str(target), "query": {"line": line},
            "callers": [],
            "note": "Could not identify call sites via LSP/AST. Use code_references for raw usages.",
        })
    result = {
        "path": str(target), "method": "fallback_heuristic",
        "query": {"line": line, "character": character},
        "caller_count": len(fallback),
        "files_affected": len({c["file"] for c in fallback}),
        "callers": fallback,
    }
    if group_by_file:
        result["by_file"] = _group_by_file(fallback)  # noqa: F821
    return fmt_ok(result)


def code_callees_tool(
    path: str,
    line: int,
    language: Optional[str] = None,
) -> str:
    """Find symbols CALLED BY a specific function/method.

    Uses LSP ``callHierarchy/outgoingCalls`` when available, falls back
    to AST extraction (call expressions inside the function body).
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))

    # ── Try LSP callHierarchy first ──
    col = _auto_detect_identifier_column(str(target), line) or 1
    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None
    if bridge and bridge.ensure_initialized():
        try:
            lsp_results = bridge.outgoing_calls(str(target), line - 1, col - 1)
            if lsp_results:
                callees = []
                for item in lsp_results:
                    file_path = LSPBridge._uri_to_path(item.get("uri", ""))
                    rng = item.get("range", {})
                    start = rng.get("start", {}) if isinstance(rng, dict) else {}
                    sl = start.get("line", 0) + 1
                    callees.append({
                        "file": file_path,
                        "line": sl,
                        "name": item.get("name", ""),
                        "kind": item.get("kind", 0),
                    })
                return fmt_ok({
                    "path": str(target),
                    "method": "lsp_call_hierarchy",
                    "query": {"line": line, "character": col},
                    "callee_count": len(callees),
                    "callees": callees,
                })
        except Exception as exc:
            logger.debug("code_callees: LSP callHierarchy failed: %s", exc)

    # ── Fallback: AST extraction ──
    return _ast_fallback_callees(str(target), line, lang)


def code_call_hierarchy_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    direction: str = "both",
    max_depth: int = 3,
    max_callers_per_level: int = 20,
    language: Optional[str] = None,
) -> str:
    """Find call hierarchy — incoming calls (who calls this) and outgoing calls (what this calls).

    Uses LSP callHierarchy with configurable transitive depth.
    Returns a formatted tree. Faster than code_callers + code_callees for
    understanding the full call graph.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (auto-detected if omitted).
        direction: "incoming", "outgoing", or "both" (default).
        max_depth: Maximum transitive depth (default: 3, max: 5).
        max_callers_per_level: Max callers shown per level (default: 20).
        language: Language override.

    Returns:
        Formatted tree string.
    """

    target, lang, col_or_error = _resolve_target_and_lang(path, line, character, language)
    if target is None:
        return str(col_or_error)

    col = int(col_or_error)
    max_depth = min(max_depth, 5)  # hard cap at 5

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None
    if not bridge or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    from pathlib import Path
    seen: set = set()
    warnings: list[str] = []

    def _walk_incoming(file_path: str, ln: int, ch: int, depth: int) -> list[str]:
        """Rekursiv incoming callers mit Tiefensteuerung."""
        if depth <= 0:
            return []
        key = f"{file_path}:{ln}"
        if key in seen:
            return [f"    {'  ' * (max_depth - depth)}↺ {Path(file_path).name}:{ln} (cycle)"]
        seen.add(key)

        lsp_items = bridge.incoming_calls(file_path, ln - 1, ch - 1)
        if not lsp_items:
            return []

        if len(lsp_items) > max_callers_per_level:
            warnings.append(f"Level {max_depth - depth}: >{max_callers_per_level} callers at {Path(file_path).name}:{ln}, truncated")
            lsp_items = lsp_items[:max_callers_per_level]

        lines = []
        for i, item in enumerate(lsp_items):
            caller_file = LSPBridge._uri_to_path(item.get("uri", ""))
            caller_name = item.get("name", "?")
            rng = item.get("range", {})
            start = rng.get("start", {}) if isinstance(rng, dict) else {}
            caller_line = start.get("line", 0)
            connector = "├── " if i < len(lsp_items) - 1 else "└── "
            indent = "    " if i < len(lsp_items) - 1 else "    "
            lines.append(f"{'  ' * depth}{connector}{Path(caller_file).name}:{caller_line + 1} — {caller_name}")
            children = _walk_incoming(caller_file, caller_line + 1, 1, depth - 1)
            for child in children:
                lines.append(f"{'  ' * depth}{indent}{child}")
        return lines

    def _walk_outgoing(file_path: str, ln: int, ch: int, depth: int) -> list[str]:
        """Rekursiv outgoing calls mit Tiefensteuerung."""
        if depth <= 0:
            return []
        key = f"out:{file_path}:{ln}"
        if key in seen:
            return []
        seen.add(key)

        lsp_items = bridge.outgoing_calls(file_path, ln - 1, ch - 1)
        if not lsp_items:
            return []

        if len(lsp_items) > max_callers_per_level:
            warnings.append(f"Level {max_depth - depth}: >{max_callers_per_level} outgoing at {Path(file_path).name}:{ln}, truncated")
            lsp_items = lsp_items[:max_callers_per_level]

        lines = []
        for i, item in enumerate(lsp_items):
            callee_file = LSPBridge._uri_to_path(item.get("uri", ""))
            callee_name = item.get("name", "?")
            rng = item.get("range", {})
            start = rng.get("start", {}) if isinstance(rng, dict) else {}
            callee_line = start.get("line", 0)
            connector = "├── " if i < len(lsp_items) - 1 else "└── "
            indent = "    " if i < len(lsp_items) - 1 else "    "
            lines.append(f"{'  ' * depth}{connector}{Path(callee_file).name}:{callee_line + 1} — {callee_name}")
            children = _walk_outgoing(callee_file, callee_line + 1, 1, depth - 1)
            for child in children:
                lines.append(f"{'  ' * depth}{indent}{child}")
        return lines

    result_lines = []
    sym_name = Path(str(target)).name

    if direction in ("incoming", "both"):
        result_lines.append(f"Incoming Calls ({sym_name}:{line}):")
        incoming = _walk_incoming(str(target), line - 1, col - 1, max_depth)
        if incoming:
            result_lines.extend(incoming)
        else:
            result_lines.append("  (none)")

    if direction == "both":
        result_lines.append("")

    if direction in ("outgoing", "both"):
        result_lines.append(f"Outgoing Calls ({sym_name}:{line}):")
        outgoing = _walk_outgoing(str(target), line - 1, col - 1, max_depth)
        if outgoing:
            result_lines.extend(outgoing)
        else:
            result_lines.append("  (none)")

    if warnings:
        result_lines.append("")
        for w in warnings:
            result_lines.append(f"⚠️ {w}")

    return "\n".join(result_lines)


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


def _auto_detect_paren_column(file_path: str, lsp_line: int) -> int:
    """Auto-detect column to land cursor inside the first '(' on the given line."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        src_line = lines[lsp_line] if 0 <= lsp_line < len(lines) else ""
    except Exception:
        src_line = ""
    idx = src_line.find("(")
    return (idx + 2) if idx >= 0 else 1


def _auto_detect_identifier_column(file_path: str, line: int) -> Optional[int]:
    """Find the column of the first meaningful identifier on *line* (0-based).

    Skips common language keywords (import, export, from, const, etc.) to land
    on actual symbol names like ``createLogger`` or ``PropertyService``.
    """
    _KEYWORDS = frozenset({
        "import", "export", "from", "const", "let", "var", "class", "function",
        "return", "async", "await", "type", "interface", "if", "else", "for",
        "while", "new", "throw", "try", "catch", "finally", "switch", "case",
        "break", "continue", "default", "extends", "implements", "super",
        "this", "static", "public", "private", "protected", "readonly",
        "declare", "enum", "namespace", "module", "require", "as",
        "void", "null", "undefined", "true", "false", "of", "in",
    })

    try:
        lines = _cached_read_lines(file_path)
        if line < 0 or line >= len(lines):
            return None
        text = lines[line]
        # Extract word-like tokens and skip keywords
        i = 0
        while i < len(text):
            ch = text[i]
            if ch.isalpha() or ch == '_':
                # Found start of a word
                start = i
                while i < len(text) and (text[i].isalnum() or text[i] == '_'):
                    i += 1
                word = text[start:i]
                if word not in _KEYWORDS:
                    return start + 1  # 1-based
                # else: skip this keyword, continue scanning
            elif ch in ('"', "'", '`'):
                # Skip string literals
                quote = ch
                i += 1
                while i < len(text) and text[i] != quote:
                    if text[i] == '\\':
                        i += 1
                    i += 1
                i += 1  # skip closing quote
            else:
                i += 1
    except OSError as e:
        logger.debug("_extract_identifier: reading file: %s", e)
        pass
    return None


def _ast_fallback_definition(
    file_path: str, line: int, character: Optional[int], lang: Optional[str]
) -> str:
    """Fallback: use tree-sitter AST to find a definition."""

    _detect = _import_detect_language()
    if _detect is None:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    # Read the identifier at the cursor position
    identifier = _extract_identifier(file_path, line, character)
    if not identifier:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line, "character": character},
            "method": "fallback",
            "warning": "Could not extract an identifier at the given position.",
            "suggestion": "Ensure line and character point to a valid identifier.",
        })

    # Search for the definition in the file tree
    root = _find_workspace_root(file_path)
    from ..code_tools import code_search_tool  # late import: avoids circular import at module load
    result_str = code_search_tool(
        path=root,
        query="(function_definition name: (identifier) @name) @def\n(class_definition name: (identifier) @name) @def",
        pattern=identifier,
        language=detected,
        max_results=20,
        _raw=True,
    )

    try:
        result = _json.loads(result_str)
    except _json.JSONDecodeError:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "raw_search_result": result_str,
        })

    defs = []
    for r in result.get("results", []):
        defs.append({
            "file": r.get("file", file_path),
            "line": r.get("line"),
            "kind": r.get("kind", "unknown"),
            "text": r.get("text", ""),
        })

    return fmt_ok({
        "path": file_path,
        "query": {"line": line, "character": character, "identifier": identifier},
        "method": "fallback_ast",
        "warning": "LSP server unavailable, using AST-based search. Results may be incomplete.",
        "definition_count": len(defs),
        "definitions": defs,
    })


def _import_detect_language():
    """4-stufiger Import-Fallback für detect_language aus code_intel.py."""
    try:
        from .code_tools import detect_language as _detect
        return _detect
    except ImportError as e:
        logger.debug("_import_detect_language: import .code_tools failed: %s", e)
        pass
    try:
        from tools.code_tools import detect_language as _detect
        return _detect
    except ImportError as e:
        logger.debug("_import_detect_language: import tools.code_tools failed: %s", e)
        pass
    try:
        from hermes_plugins.code_intel.code_tools import detect_language as _detect
        return _detect
    except ImportError as e:
        logger.debug("_import_detect_language: import hermes_plugins.code_intel failed: %s", e)
        pass
    try:
        import importlib.util as _ilu
        _mod_path = str(Path(__file__).parent / "code_tools.py")
        _spec = _ilu.spec_from_file_location("code_intel_standalone", _mod_path)
        if _spec is None or _spec.loader is None:
            return None
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.detect_language
    except Exception as e:
        logger.debug("_import_detect_language: spec_from_file_location failed: %s", e)
        pass
    return None


def _extract_identifier(file_path: str, line: int, character: Optional[int]) -> str:
    """Extrahiere Identifier aus einer bestimmten Zeile/Spalte."""
    try:
        lines = _cached_read_lines(file_path)
        text_line = lines[line - 1] if 0 < line <= len(lines) else ""
    except (OSError, IndexError):
        text_line = ""
    if not character or not text_line or character > len(text_line):
        return ""
    idx = character - 1
    start = idx
    while start > 0 and (text_line[start - 1].isalnum() or text_line[start - 1] == '_'):
        start -= 1
    end = idx
    while end < len(text_line) and (text_line[end].isalnum() or text_line[end] == '_'):
        end += 1
    return text_line[start:end]


def _rg_search(identifier: str, root: str) -> list:
    """Führe ripgrep-Suche aus und parse Ergebnisse."""
    import subprocess as _sp
    try:
        result = _sp.run(
            ["rg", "--no-heading", "--line-number", "-n", "-w", identifier, root],
            capture_output=True, text=True, timeout=15,
        )
        refs = []
        for match_line in result.stdout.strip().split("\n"):
            if not match_line:
                continue
            parts = match_line.split(":", 2)
            if len(parts) >= 3:
                refs.append({
                    "file": parts[0],
                    "line": int(parts[1]),
                    "text": parts[2].strip()[:200],
                })
        return refs
    except Exception:
        return []


def _ast_fallback_references(
    file_path: str, line: int, character: Optional[int], lang: Optional[str]
) -> str:
    """Fallback: use grep-style search for references."""

    _detect = _import_detect_language()
    if _detect is None:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    identifier = _extract_identifier(file_path, line, character)
    if not identifier:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line, "character": character},
            "method": "fallback",
            "warning": "Could not extract an identifier at the given position.",
        })

    root = _find_workspace_root(file_path)
    refs = _rg_search(identifier, root)

    by_file: Dict[str, List[dict]] = {}
    for r in refs:
        by_file.setdefault(r["file"], []).append(r)

    return fmt_ok({
        "path": file_path,
        "query": {"line": line, "character": character, "identifier": identifier},
        "method": "fallback_text",
        "warning": "LSP server unavailable, using text-based search. May include false positives.",
        "reference_count": len(refs),
        "files_affected": len(by_file),
        "references": refs,
        "by_file": by_file,
    })


def _read_file_safe(file_path: str):
    """Read file content, returning ``(content, None)`` or ``(None, error_json)``."""
    try:
        content = Path(file_path).read_text("utf-8", errors="replace")
        return content, None
    except Exception as exc:
        return None, _json.dumps({
            "path": file_path, "method": "fallback", "warning": str(exc),
        })


def _python_ast_analyze(content: str):
    """Walk Python AST, collect imported/used/defined names.

    Returns ``(imported, used, defined)`` sets, or ``None`` on syntax error.
    """
    import ast
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    except Exception:
        return None
    imported: set[str] = set()
    used: set[str] = set()
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                used.add(node.id)
    return imported, used, defined


def _build_unused_import_diags(
    imported: set, used: set, defined: set, content: str,
) -> list[dict]:
    """Build diagnostics for imports that are neither used nor re-defined."""
    diagnostics: list[dict] = []
    for name in sorted(imported - used - defined):
        for i, line_text in enumerate(content.split("\n"), 1):
            if name in line_text and ("import" in line_text or "from " in line_text):
                diagnostics.append({
                    "severity": 2,
                    "message": f"Possibly unused import: {name}",
                    "range": {"start": {"line": i - 1, "character": 0},
                              "end":   {"line": i - 1, "character": len(line_text)}},
                    "source": "ast_heuristic",
                })
                break
    return diagnostics


def _tsjs_import_heuristic(content: str) -> list[dict]:
    """Token-based import-unused heuristic for TypeScript / JavaScript."""
    diagnostics: list[dict] = []
    lines = content.split("\n")
    for i, line_text in enumerate(lines, 1):
        stripped = line_text.strip()
        if stripped.startswith("import ") and "from " in stripped:
            imp = stripped.split("from")[0].split("{")[-1].split("}")[0]
            imp = imp.replace("import ", "").replace("* as ", "").strip()
            if imp and not any(imp in ln for ln in lines[i:]):
                diagnostics.append({
                    "severity": 2,
                    "message": f"Possibly unused import: {imp}",
                    "range": {"start": {"line": i - 1, "character": 0},
                              "end":   {"line": i - 1, "character": len(line_text)}},
                    "source": "ast_heuristic",
                })
    return diagnostics


def _format_diagnostics_result(file_path: str, diagnostics: list[dict]) -> str:
    """Build the final JSON string for a diagnostics response."""
    return fmt_ok({
        "path": file_path,
        "method": "ast_heuristic",
        "warning": "LSP server unavailable. Using lightweight AST heuristic.",
        "diagnostic_count": len(diagnostics),
        "errors": len([d for d in diagnostics if d.get("severity", 1) == 1]),
        "warnings": len([d for d in diagnostics if d.get("severity", 2) == 2]),
        "diagnostics": diagnostics,
    })


def _ast_fallback_diagnostics(file_path: str, lang: Optional[str]) -> str:
    """Lightweight AST-based heuristic for common issues: unused imports, undefined names."""
    content, error = _read_file_safe(file_path)
    if error:
        return error
    assert content is not None  # help pyright narrow the type
    diagnostics: list[dict] = []
    if lang == "python":
        result = _python_ast_analyze(content)
        if result is not None:
            imported, used, defined = result
            diagnostics = _build_unused_import_diags(imported, used, defined, content)
        else:
            try:
                import ast as _ast_mod
                _ast_mod.parse(content)  # raises SyntaxError
            except SyntaxError as exc:
                diagnostics.append({
                    "severity": 1,
                    "message": f"Syntax error: {exc.msg}",
                    "range": {"start": {"line": (exc.lineno or 1) - 1, "character": 0},
                              "end":   {"line": (exc.lineno or 1) - 1, "character": 0}},
                    "source": "ast_heuristic",
                })
            except Exception as e:
                logger.debug("_python_import_miss_heuristic: AST parse failed: %s", e)
                pass
    elif lang in ("typescript", "javascript"):
        diagnostics = _tsjs_import_heuristic(content)
    return _format_diagnostics_result(file_path, diagnostics)


def _ast_fallback_callees(file_path: str, line: int, lang: Optional[str]) -> str:
    """AST fallback: extract call expressions from the function/method at *line*."""
    content, error = _read_file_safe(file_path)
    if error:
        return error
    assert content is not None

    callees: list[dict] = []

    if lang == "python":
        callees = _extract_python_callees(content, line)
    elif lang in ("typescript", "javascript"):
        callees = _extract_ts_callees(content, line)

    if not callees:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line},
            "method": "ast_heuristic",
            "warning": "Could not extract callees via AST. Ensure line points to a function/method.",
            "callees": [],
        })

    return fmt_ok({
        "path": file_path,
        "query": {"line": line},
        "method": "ast_heuristic",
        "callee_count": len(callees),
        "callees": callees,
    })



def _extract_python_callees(content: str, line: int) -> list:
    """Extract function calls from a Python function/method at given line."""
    import ast as _ast
    callees = []
    try:
        tree = _ast.parse(content)
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                func_start = getattr(node, "lineno", 1)
                func_end = getattr(node, "end_lineno", func_start)
                if func_start <= line <= func_end:
                    for child in _ast.walk(node):
                        if isinstance(child, _ast.Call):
                            name = ""
                            if isinstance(child.func, _ast.Name):
                                name = child.func.id
                            elif isinstance(child.func, _ast.Attribute):
                                name = child.func.attr
                            if name:
                                callees.append({
                                    "name": name,
                                    "line": getattr(child, "lineno", func_start),
                                    "type": "call",
                                })
                    break
    except SyntaxError as e:
        logger.debug("_python_callee_heuristic: syntax error: %s", e)
        pass
    except Exception as e:
        logger.debug("_python_callee_heuristic: unexpected error: %s", e)
        pass
    return callees


def _extract_ts_callees(content: str, line: int) -> list:
    """Extract function calls from a TypeScript/JS function region."""
    import re as _re
    callees = []
    lines = content.split("\n")
    if 0 < line <= len(lines):
        for i in range(line - 1, min(len(lines), line + 200)):
            ln = lines[i]
            for mtch in _re.finditer(r'([A-Za-z_]\w*)\s*\(', ln):
                cname = mtch.group(1)
                if cname not in {"if", "while", "for", "switch", "catch", "function", "return", "new"}:
                    callees.append({
                        "name": cname,
                        "line": i + 1,
                        "type": "call",
                    })
    return callees


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_definitions(defs: List[dict]) -> str:
    """Format definition results for display."""
    if not defs:
        return "No definition found."

    lines = []
    for i, d in enumerate(defs, 1):
        if not isinstance(d, dict):
            lines.append(f"{i}. <malformed entry>")
            continue
        file_path = d.get("file", d.get("path", "<unknown>"))
        line_no = d.get("line", d.get("row", 0))
        lines.append(f"{i}. {file_path}:{line_no}")
        if d.get("text"):
            lines.append(f"   {d['text']}")
        if d.get("context"):
            for ctx_line in d["context"]:
                if ctx_line.strip():
                    lines.append(f"   {ctx_line}")
    return "\n".join(lines)


def _format_references(refs: List[dict], by_file: Dict[str, List[dict]]) -> str:
    """Format references results for display."""
    if not refs:
        return "No references found."

    lines = [f"Found {len(refs)} references across {len(by_file)} file(s):"]

    for file_path, file_refs in sorted(by_file.items()):
        # Shorten path if it's within the workspace
        short = file_path
        lines.append(f"\n  {short} ({len(file_refs)} ref(s))")
        for r in file_refs:
            text = r.get("text", "") if isinstance(r, dict) else str(r)[:120]
            if not isinstance(r, dict):
                lines.append("    <malformed ref>")
                continue
            line_no = r.get("line", r.get("row", 0))
            if len(text) > 120:
                text = text[:117] + "..."
            lines.append(f"    L{line_no:>4d}  {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool schemas & registration
# ---------------------------------------------------------------------------

CODE_HIGHLIGHT_SCHEMA = {
    "name": "code_highlight",
    "description": "Find ALL occurrences of a symbol in the current file (file-local). "
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
    "description": "Find type hierarchy for a symbol — supertypes (parent types) and subtypes "
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

CODE_CALL_HIERARCHY_SCHEMA = {
    "name": "code_call_hierarchy",
    "description": "Find call hierarchy for a symbol — incoming calls (who calls this) and outgoing calls "
                   "(what does this call). Uses LSP callHierarchy with configurable transitive depth. "
                   "Returns a formatted tree.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
            "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "description": "Direction of hierarchy (default: both)"},
            "max_depth": {"type": "integer", "description": "Maximum transitive depth (default: 3, max: 5)"},
            "max_callers_per_level": {"type": "integer", "description": "Max callers shown per level (default: 20)"},
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path", "line"],
    },
}

CODE_DOCUMENT_SYMBOLS_SCHEMA = {
    "name": "code_document_symbols",
    "description": "Get ALL symbols in a file via LSP textDocument/documentSymbol — functions, classes, variables, "
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

CODE_CALLERS_SCHEMA = {
    "name": "code_callers",
    "description": (
        "Find call sites of a symbol — files and lines WHERE it is invoked. "
        "Requires a file path and line where the callee is defined. Uses LSP references with heuristics."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number where the callee is defined"},
            "character": {"type": "integer", "description": "1-based column position (optional, auto-detected)"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
            "group_by_file": {"type": "boolean", "description": "Group results by file to save tokens (default: False)"},
        },
        "required": ["path", "line"],
    },
}

CODE_CALLEES_SCHEMA = {
    "name": "code_callees",
    "description": (
        "Find symbols CALLED BY a specific function or method. "
        "Requires a file path and the line where the function is defined. "
        "Uses AST-based extraction for Python/TS/JS; LSP fallback if available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number where the function is defined"},
            "language": {"type": "string", "description": "Language override (e.g. 'python'). Auto-detected from extension."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_highlight(args, **kw):
    return code_highlight_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_inlay_hints(args, **kw):
    return code_inlay_hints_tool(
        path=args.get("path", ""),
        start_line=args.get("start_line", 1),
        end_line=args.get("end_line", 0),
    )


def _handle_code_type_hierarchy(args, **kw):
    return code_type_hierarchy_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        direction=args.get("direction", "both"),
        language=args.get("language"),
    )


def _handle_code_call_hierarchy(args, **kw):
    return code_call_hierarchy_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        direction=args.get("direction", "both"),
        max_depth=args.get("max_depth", 3),
        max_callers_per_level=args.get("max_callers_per_level", 20),
        language=args.get("language"),
    )


def _handle_code_document_symbols(args, **kw):
    return code_document_symbols_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_definition(args, **kw):
    return code_definition_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_references(args, **kw):
    return code_references_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
        include_declaration=args.get("include_declaration", True),
        group_by_file=args.get("group_by_file", False),
        max_results=args.get("max_results", 0),
    )


def _handle_code_diagnostics(args, **kw):
    return code_diagnostics_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_callers(args, **kw):
    return code_callers_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
        group_by_file=args.get("group_by_file", False),
    )


def _handle_code_callees(args, **kw):
    return code_callees_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# code_workspace_symbols — LSP workspace/symbol (monorepo-wide symbol search)
# ---------------------------------------------------------------------------


def _wss_find_anchor_file(anchor: Path) -> Path:
    """Wenn anchor ein Dir ist, finde eine passende Source-Datei für LSP-Seeding.

    Bevorzugt bekannte Projektverzeichnisse (packages, apps, src, lib, app)
    mit gängigen Source-Extensions.
    """
    if not anchor.is_dir():
        return anchor
    _PREFERRED_ANCHOR_DIRS = ("packages", "apps", "src", "lib", "app")
    _SMART_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs")
    hit = None
    for pref_dir in _PREFERRED_ANCHOR_DIRS:
        candidate = anchor / pref_dir
        if candidate.is_dir():
            for ext in _SMART_EXTENSIONS:
                hit = next(candidate.rglob(f"*{ext}"), None)
                if hit:
                    break
        if hit:
            break
    if not hit:
        for ext in _SMART_EXTENSIONS:
            hit = next(anchor.rglob(f"*{ext}"), None)
            if hit:
                break
    return hit if hit else anchor


_LSP_KIND_NAMES = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
    25: "operator", 26: "type_parameter",
}


def _wss_format_symbol_results(raw: list, kind: Optional[str], max_results: int) -> tuple:
    """Formatiere raw LSP workspace/symbol Response in Hermes-Dicts.

    Returns (symbols, truncated).
    """
    _KIND_NAMES = _LSP_KIND_NAMES
    symbols: List[dict] = []
    for sym in raw:
        loc = sym.get("location") or {}
        uri = loc.get("uri", "")
        file_path = uri[7:] if uri.startswith("file://") else uri
        rng = loc.get("range") or {}
        start = rng.get("start") or {}
        kind_num = sym.get("kind", 0)
        kind_name = _KIND_NAMES.get(kind_num, f"kind_{kind_num}")

        if kind and kind.lower() != kind_name:
            continue

        symbols.append({
            "name": sym.get("name", ""),
            "kind": kind_name,
            "container": sym.get("containerName") or "",
            "file": file_path,
            "line": start.get("line", 0) + 1 if start else None,
            "character": start.get("character", 0) + 1 if start else None,
        })

    truncated = len(symbols) > max_results
    symbols = symbols[:max_results]
    return symbols, truncated


def code_workspace_symbols_tool(
    query: str,
    path: Optional[str] = None,
    language: Optional[str] = None,
    kind: Optional[str] = None,
    max_results: int = 50,
) -> str:
    """Search symbols across the workspace using LSP workspace/symbol.

    Much faster than search_files for finding classes/functions/interfaces by name
    in large projects — returns only real symbols (not comments/strings) with
    their kind (class, function, interface, etc.) pre-indexed by the LSP server.

    Note for monorepos: The LSP server indexes symbols based on open documents.
    For best results, pass a specific source file as ``path`` (not a directory).
    When a directory is given, the tool picks an anchor file from packages/ or apps/.
    If results are empty, the LSP server may not have indexed that part of the monorepo
    — use code_search (AST-based) as an alternative that works without LSP indexing.

    Args:
        query: Fuzzy symbol name (e.g. 'UserService', 'createLogger').
        path: Optional file in the workspace to anchor the LSP root detection.
            For monorepos, prefer passing a specific source file for best results.
            Defaults to cwd.
        language: Language override ('typescript', 'python', etc.). Auto-detected
            from ``path`` if provided.
        kind: Optional filter: class, function, method, interface, enum, variable,
            constant, module, struct.
        limit: Max results to return (default 50).

    Returns:
        JSON string with matched symbols (name, kind, file, line, container).
    """

    anchor = Path(path).expanduser().resolve() if path else Path.cwd().resolve()
    if not anchor.exists():
        return fmt_err("No type definition found at position")

    probe_file = _wss_find_anchor_file(anchor)

    lang = language or _detect_language_for_lsp(str(probe_file))
    if not lang:
        return fmt_ok({
            "error": "Could not auto-detect language. Pass language= explicitly.",
            "query": query,
        })

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(probe_file))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "query": query,
            "hint": "Use search_files (target='content') as fallback",
        })

    logger.info("code_workspace_symbols: query=%r lang=%s root=%s",
                query, lang, bridge.root_uri)
    raw = bridge.workspace_symbol(query, anchor_file=str(probe_file))
    if raw is None:
        return fmt_ok({
            "error": "LSP workspace/symbol request failed or not supported",
            "query": query,
            "lsp_server": bridge.command,
        })

    symbols, truncated = _wss_format_symbol_results(raw, kind, max_results)

    return fmt_ok({
        "query": query,
        "language": lang,
        "lsp_server": bridge.command,
        "total_returned": len(symbols),
        "truncated": truncated,
        "symbols": symbols,
    })


CODE_WORKSPACE_SYMBOLS_SCHEMA = {
    "name": "code_workspace_symbols",
    "description": (
        "Fuzzy search symbols (classes, functions, interfaces, etc.) across the entire "
        "workspace via LSP workspace/symbol. Sub-second monorepo-wide lookup that returns "
        "ONLY real symbols (not comments or string matches) with their kind and location. "
        "Use this INSTEAD of search_files when looking for a named entity like 'UserService' "
        "or 'createLogger' across many apps — it is faster, semantic, and avoids false positives."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Fuzzy symbol name to search for."},
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "language": {"type": "string", "description": "Language override: typescript, python, go, rust, etc."},
            "kind": {"type": "string", "description": "Filter by symbol kind: class, function, method, interface, enum, variable, constant, module, struct."},
            "limit": {"type": "integer", "description": "Max results (default 50)."},
        },
        "required": ["query"],
    },
}


def _handle_code_workspace_symbols(args, **kw):
    return code_workspace_symbols_tool(
        query=args.get("query", ""),
        path=args.get("path"),
        language=args.get("language"),
        kind=args.get("kind"),
        max_results=args.get("max_results", 50),
    )


# ---------------------------------------------------------------------------
# code_rename — LSP textDocument/rename (semantic, cross-file)
# ---------------------------------------------------------------------------


def code_rename_tool(
    path: str,
    line: int,
    new_name: str,
    character: Optional[int] = None,
    language: Optional[str] = None,
    dry_run: bool = True,
) -> str:
    """Semantically rename a symbol across all files using LSP textDocument/rename.

    Unlike code_refactor (pure AST text match), this understands types, scopes, and
    imports — it only renames references to THIS specific symbol (not unrelated ones
    that happen to have the same name).

    Args:
        path: Absolute file path where the symbol appears.
        line: 1-based line number.
        new_name: New symbol name.
        character: 1-based column (auto-detected if omitted).
        language: Language override.
        dry_run: Preview changes without writing. Default TRUE — always preview first.

    Returns:
        JSON with per-file edit list and (if dry_run=False) applied diff.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "hint": "LSP server is required for semantic rename. Falls-back refactor available via code_refactor (text-AST).",
        })

    logger.info("code_rename: %s:%d:%s -> %r (dry_run=%s)",
                path, line, character, new_name, dry_run)
    workspace_edit = bridge.rename(str(target), lsp_line, lsp_char, new_name)
    if not workspace_edit:
        return fmt_ok({
            "error": "LSP rename returned no edits (symbol not renameable or not found)",
            "query": {"path": str(target), "line": line, "character": character, "new_name": new_name},
        })

    edits_by_file = _parse_workspace_edit(workspace_edit)
    preview = _build_rename_preview(edits_by_file)

    result = {
        "dry_run": dry_run,
        "new_name": new_name,
        "files_affected": len(edits_by_file),
        "total_edits": sum(p["edit_count"] for p in preview),
        "preview": preview,
        "lsp_server": bridge.command,
    }

    if dry_run:
        result["hint"] = "Re-run with dry_run=False to apply. Changes are NOT written."
        return fmt_ok(result)

    # Apply edits: sort per-file by (line, char) DESC to avoid offset drift
    applied = _apply_edits_by_file(edits_by_file)
    result["applied"] = applied
    return fmt_ok(result)


CODE_RENAME_SCHEMA = {
    "name": "code_rename",
    "description": (
        "Semantically rename a symbol across all files using LSP (understands types, scopes, imports). "
        "Only renames references to THIS symbol — not unrelated identifiers with the same name. "
        "Use this INSTEAD of code_refactor when renaming a class/function/variable across a monorepo. "
        "DRY-RUN by default — always preview before applying. Requires an LSP server (pyright, tsserver, gopls, etc.)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path where the symbol appears."},
            "line": {"type": "integer", "description": "1-based line number."},
            "new_name": {"type": "string", "description": "New symbol name."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
            "dry_run": {"type": "boolean", "description": "Preview without writing. Default: true."},
        },
        "required": ["path", "line", "new_name"],
    },
}


def _handle_code_rename(args, **kw):
    return code_rename_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        new_name=args.get("new_name", ""),
        character=args.get("character"),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
    )


# ---------------------------------------------------------------------------
# code_hover — LSP textDocument/hover (signatures, docstrings, types)
# ---------------------------------------------------------------------------




def _normalize_hover_contents(contents: Any) -> List[str]:
    """Normalize LSP hover response to text list."""
    text_parts: List[str] = []
    if isinstance(contents, str):
        text_parts.append(contents)
    elif isinstance(contents, dict):
        text_parts.append(contents.get("value", ""))
    elif isinstance(contents, list):
        for c in contents:
            if isinstance(c, str):
                text_parts.append(c)
            elif isinstance(c, dict):
                text_parts.append(c.get("value", ""))
    return text_parts


def code_hover_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get type signature + docstring for symbol at position (LSP hover).

    Faster than code_capsule when you only need the signature/type info
    (no references, no definition jump). Use BEFORE editing call sites to
    confirm parameter names/types match what you're passing.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    result = bridge.hover(str(target), lsp_line, lsp_char)
    if not result:
        return fmt_err("No hover info at position")

    text_parts = _normalize_hover_contents(result.get("contents"))

    return fmt_ok({
        "path": str(target),
        "line": line,
        "character": character,
        "hover": "\n".join(t for t in text_parts if t).strip(),
        "lsp_server": bridge.command,
    })


CODE_HOVER_SCHEMA = {
    "name": "code_hover",
    "description": (
        "Get type signature, parameter info, and docstring for a symbol via LSP hover. "
        "Use this BEFORE calling/editing a function to confirm its exact signature without "
        "reading the full definition. Faster + cheaper than code_capsule when you only need "
        "the type info. Requires LSP server (pyright/tsserver/gopls/etc)."
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

CODE_FORMAT_SCHEMA = {
    "name": "code_format",
    "description": (
        "Format a file using the LSP server's textDocument/formatting. "
        "Automatically formats indentation, spacing, and style according to the "
        "language's formatter (pyright/pylsp for Python, tsserver for TypeScript, "
        "gopls for Go, rust-analyzer for Rust). "
        "Writes formatted content back to the file. "
        "Falls back to a safety check if LSP formatting is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path to format."},
            "language": {"type": "string", "description": "Language override (auto-detected from extension)."},
            "dry_run": {"type": "boolean", "description": "Preview changes without writing (default: true)."},
        },
        "required": ["path"],
    },
}


def _handle_code_hover(args, **kw):
    return code_hover_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# CLI formatter fallback for code_format_tool
# ---------------------------------------------------------------------------


def _try_cli_formatter(path: str, lang: str) -> Optional[str]:
    """Try to format a file using a CLI formatter (ruff/prettier).

    Returns a formatted result dict or None if no CLI formatter is available.
    """
    import subprocess as _sp

    ext_to_cli = {
        "py": ["ruff", "format", "--stdin-filename", path, "-"],
        "python": ["ruff", "format", "--stdin-filename", path, "-"],
        "js": ["prettier", "--stdin-filepath", path],
        "jsx": ["prettier", "--stdin-filepath", path],
        "ts": ["prettier", "--stdin-filepath", path],
        "tsx": ["prettier", "--stdin-filepath", path],
        "typescript": ["prettier", "--stdin-filepath", path],
        "javascript": ["prettier", "--stdin-filepath", path],
    }

    cmd = ext_to_cli.get(lang)
    if not cmd:
        return None

    import difflib as _difflib

    try:
        target = Path(path).expanduser().resolve()
        original = target.read_text(encoding="utf-8")

        result = _sp.run(
            cmd,
            input=original,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return fmt_ok({
                "path": path,
                "language": lang,
                "method": "cli_fallback",
                "formatter": cmd[0],
                "error": result.stderr.strip(),
                "hint": f"{cmd[0]} exited with code {result.returncode}",
            })

        formatted = result.stdout
        if formatted == original:
            return fmt_ok({
                "path": path,
                "language": lang,
                "method": "cli_fallback",
                "formatter": cmd[0],
                "info": "No changes needed",
            })

        # Generate diff
        original_lines = original.splitlines(keepends=True)
        formatted_lines = formatted.splitlines(keepends=True)
        diff_lines = list(_difflib.unified_diff(
            original_lines, formatted_lines,
            fromfile=f"a/{target.name}", tofile=f"b/{target.name}",
            lineterm="",
        ))

        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "diff": diff_lines[:100],
            "has_changes": True,
        })

    except FileNotFoundError:
        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "error": f"{cmd[0]} not found — install it to enable CLI formatting fallback",
        })
    except _sp.TimeoutExpired:
        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "error": f"{cmd[0]} timed out",
        })
    except Exception as e:
        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "error": str(e),
        })


def code_format_tool(
    path: str,
    dry_run: bool = True,
    language: Optional[str] = None,
) -> str:
    """Format a file using the LSP server's textDocument/formatting.

    Returns a diff-like preview of the changes or applies them.
    Falls back gracefully if no LSP formatter is available for the language.
    """
    import difflib as _difflib

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    # Read original content
    original = target.read_text(encoding="utf-8")

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        # CLI fallback: try ruff for Python, prettier for TS/JS
        cli_result = _try_cli_formatter(str(target), lang)
        if cli_result is not None:
            return cli_result
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "hint": "LSP server is required for formatting. Install the appropriate server.",
        })

    edits = bridge.format_document(str(target))
    if not edits:
        # CLI fallback: also try if LSP returned no edits
        cli_result = _try_cli_formatter(str(target), lang)
        if cli_result is not None:
            return cli_result
        return fmt_ok({
            "info": f"LSP formatter returned no changes for {lang}",
            "path": str(target),
        })

    # Apply TextEdits in reverse order (highest line first) to avoid offset drift
    sorted_edits = sorted(edits, key=lambda e: (
        -e.get("range", {}).get("start", {}).get("line", 0),
        -e.get("range", {}).get("start", {}).get("character", 0)
    ))

    content = list(original)  # character-level list
    edit_info = []
    for edit in sorted_edits:
        range_s = edit.get("range", {})
        start = range_s.get("start", {})
        end = range_s.get("end", {})
        s_line, s_char = start.get("line", 0), start.get("character", 0)
        e_line, e_char = end.get("line", 0), end.get("character", 0)
        new_text = edit.get("newText", "")

        # Convert to absolute offsets (simplified: line-based)
        lines_arr = original.splitlines(keepends=True)
        def _offset(ln: int, ch: int) -> int:
            return sum(len(x) for x in lines_arr[:ln]) + ch

        start_off = _offset(s_line, s_char)
        end_off = _offset(e_line, e_char)

        edit_info.append({
            "range": f"L{s_line+1}:{s_char}–L{e_line+1}:{e_char}",
            "old_len": end_off - start_off,
            "new_len": len(new_text),
        })

        content[start_off:end_off] = list(new_text)

    formatted = "".join(content)

    # Generate a unified diff for preview
    original_lines = original.splitlines(keepends=True)
    formatted_lines = formatted.splitlines(keepends=True)
    diff_lines = list(_difflib.unified_diff(
        original_lines, formatted_lines,
        fromfile=f"a/{target.name}", tofile=f"b/{target.name}",
        lineterm="",
    ))

    result = {
        "path": str(target),
        "language": lang,
        "lsp_server": bridge.command,
        "edit_count": len(edits),
        "edit_details": edit_info,
        "diff": diff_lines,
        "dry_run": dry_run,
        "formatted_length": len(formatted),
        "original_length": len(original),
    }

    if dry_run:
        result["hint"] = "Re-run with dry_run=False to apply formatting."
        return fmt_ok(result)

    # Write formatted content back
    target.write_text(formatted, encoding="utf-8")
    result["applied"] = True
    return fmt_ok(result)


def _handle_code_format(args: dict, **kw: Any) -> str:
    return code_format_tool(
        path=args.get("path", ""),
        dry_run=args.get("dry_run", True),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# code_type_definition — LSP textDocument/typeDefinition
# ---------------------------------------------------------------------------


def code_type_definition_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Jump to the TYPE of a symbol (not its declaration).

    For `const user = getUser()` at `user`, code_definition lands on
    `getUser()`'s implementation, but code_type_definition lands on the
    `User` interface/class. Crucial for understanding shape before refactor.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    try:
        locs = bridge.type_definition(str(target), lsp_line, lsp_char)
    except Exception as exc:
        logger.debug("type_definition error for %s:%d: %s", str(target), line, exc)
        return fmt_err(f"type_definition failed: {exc}")

    if not locs:
        return fmt_err("No type definition found at position")

    out = []
    for loc in locs:
        try:
            d = _location_to_dict(loc)
            # _location_to_dict now returns both "path" and "file" keys
            out.append(d)
        except Exception as exc:
            logger.debug("Skipping malformed type_definition location: %s", exc)
            continue
    if not out:
        return fmt_err("No type definition found at position")
    return fmt_ok({"type_definitions": out, "lsp_server": bridge.command})


CODE_TYPE_DEFINITION_SCHEMA = {
    "name": "code_type_definition",
    "description": (
        "Jump to the TYPE definition of a symbol (interface/class/type alias), "
        "not its value declaration. Use this when you need to understand the SHAPE "
        "of a value before refactoring — e.g. for `const u = getUser()`, this lands on "
        "the `User` interface, while code_definition lands on `getUser()`'s body. "
        "Requires LSP (most useful for TypeScript/Go/Rust)."
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


def _handle_code_type_definition(args, **kw):
    return code_type_definition_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def code_implementations_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Find implementations of a symbol (interface, abstract class, method override).

    Uses LSP textDocument/implementation. Helps find where interfaces are
    implemented, abstract methods are overridden, or virtual methods are defined
    in concrete classes.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Path not found: {path}")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"No LSP bridge for {lang or 'auto-detected'}")

    try:
        locs = bridge.implementations(str(target), lsp_line, lsp_char)
    except Exception as exc:
        logger.debug("implementations error for %s:%d: %s", str(target), line, exc)
        return fmt_err(f"Path not found: {path}")

    if not locs:
        return fmt_err("Failed to resolve references for caller analysis")

    out = []
    for loc in locs:
        try:
            d = _location_to_dict(loc)
            out.append(d)
        except Exception as exc:
            logger.debug("Skipping malformed implementation location: %s", exc)
            continue
    if not out:
        return fmt_err(f"Path not found: {path}")
    return fmt_ok({"implementations": out, "lsp_server": bridge.command})


CODE_IMPLEMENTATIONS_SCHEMA = {
    "name": "code_implementations",
    "description": (
        "Find implementations of a symbol via LSP textDocument/implementation. "
        "Useful for finding where interfaces are implemented, abstract methods "
        "are overridden, or concrete classes extend a base type."
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


def _handle_code_implementations(args, **kw):
    return code_implementations_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _check_lsp_reqs() -> bool:
    """Return True if at least one LSP server is available on PATH."""
    for lang_configs in _LANGUAGE_SERVERS.values():
        for cfg in lang_configs:
            if _resolve_command(cfg["command"]):
                return True
    return False  # No LSP servers found — tools will use AST fallback


# ---------------------------------------------------------------------------
# Registration — deferred to avoid circular imports
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# code_signatures — LSP textDocument/signatureHelp
# ---------------------------------------------------------------------------


def code_signatures_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get parameter / signature hints for a function call site via LSP signatureHelp.

    Use when generating or editing a call to an unfamiliar function — returns
    the parameter list, types, active parameter index, and inline docs without
    needing to read the source. Massively reduces wrong-args bugs in generated code.

    Args:
        path: Absolute file path of the call site.
        line: 1-based line number of the call (cursor inside the parens).
        character: 1-based column (auto-detected to inside parens if omitted).
        language: Language override.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"No LSP bridge for {lang}")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_paren_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    sig = bridge.signature_help(str(target), lsp_line, lsp_char)
    if not sig or not sig.get("signatures"):
        return fmt_ok({
            "found": False,
            "query": {"path": str(target), "line": line, "character": character},
            "hint": "No signature help — cursor must be INSIDE function call parens.",
        })

    active_sig_idx = sig.get("activeSignature", 0) or 0
    active_param_idx = sig.get("activeParameter", 0) or 0
    out_sigs = _format_signatures(sig, active_sig_idx, active_param_idx)

    return fmt_ok({
        "found": True,
        "lsp_server": bridge.command,
        "signatures": out_sigs,
    })




def _format_signatures(sig: dict, active_sig_idx: int, active_param_idx: int) -> List[dict]:
    """Format LSP signatureHelp response into structured output."""
    out_sigs = []
    for i, s in enumerate(sig.get("signatures", [])):
        params = []
        for p in s.get("parameters", []):
            label = p.get("label")
            if isinstance(label, list) and len(label) == 2:
                sig_label = s.get("label", "")
                label = sig_label[label[0]:label[1]]
            params.append({
                "label": label,
                "doc": _extract_md(p.get("documentation")),
            })
        out_sigs.append({
            "active": i == active_sig_idx,
            "label": s.get("label", ""),
            "doc": _extract_md(s.get("documentation")),
            "active_parameter": active_param_idx,
            "parameters": params,
        })
    return out_sigs


def _extract_md(doc) -> str:
    """Normalize LSP MarkupContent | str to plain text."""
    if not doc:
        return ""
    if isinstance(doc, str):
        return doc
    if isinstance(doc, dict):
        return doc.get("value", "")
    return str(doc)


CODE_SIGNATURES_SCHEMA = {
    "name": "code_signatures",
    "description": (
        "Get parameter / signature hints for a function call site via LSP signatureHelp. "
        "Use BEFORE writing or editing a call to an unfamiliar function — returns the "
        "parameter list, types, active parameter index, and inline docs without reading "
        "source files. Reduces wrong-args bugs in generated code. Cursor MUST be inside "
        "the call's parentheses."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line of the call."},
            "character": {"type": "integer", "description": "1-based column inside parens (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_signatures(args, **kw):
    return code_signatures_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        character=args.get("character"),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# code_action — LSP textDocument/codeAction (quick-fix, organize imports, etc.)
# ---------------------------------------------------------------------------


def _filter_diagnostics_in_range(bridge, file_path: str, lsp_line: int, lsp_end_line: int) -> list:
    """Pull diagnostics from bridge and filter to those overlapping the given range."""
    diags = bridge.publish_diagnostics(file_path) or []
    return [
        d for d in diags
        if d.get("range", {}).get("start", {}).get("line", -1) <= lsp_end_line
        and d.get("range", {}).get("end", {}).get("line", -1) >= lsp_line
    ]


def _summarize_actions(actions: list) -> list:
    """Summarize LSP code actions for display."""
    summary = []
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            continue
        summary.append({
            "index": i,
            "title": a.get("title", ""),
            "kind": a.get("kind", ""),
            "is_preferred": a.get("isPreferred", False),
            "has_edit": bool(a.get("edit")),
            "has_command": bool(a.get("command")),
        })
    return summary


def _apply_workspace_edit(workspace_edit: dict) -> List[dict]:
    """Apply an LSP WorkspaceEdit to the filesystem. Returns per-file status list.

    Shared between code_action and (in future) any tool that produces edits.
    """
    edits_by_file: dict = {}
    for uri, text_edits in (workspace_edit.get("changes") or {}).items():
        fp = uri[7:] if uri.startswith("file://") else uri
        edits_by_file.setdefault(fp, []).extend(text_edits)
    for doc_change in workspace_edit.get("documentChanges") or []:
        if "textDocument" in doc_change:
            uri = doc_change["textDocument"].get("uri", "")
            fp = uri[7:] if uri.startswith("file://") else uri
            edits_by_file.setdefault(fp, []).extend(doc_change.get("edits", []))

    applied = []
    for fp, tedits in edits_by_file.items():
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            lines_arr = content.splitlines(keepends=True)

            def _offset(ln: int, ch: int) -> int:
                return sum(len(line) for line in lines_arr[:ln]) + ch

            edits_sorted = sorted(
                tedits,
                key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]),
                reverse=True,
            )
            new_content = content
            for e in edits_sorted:
                s = e["range"]["start"]
                en = e["range"]["end"]
                start_off = _offset(s["line"], s["character"])
                end_off = _offset(en["line"], en["character"])
                new_content = new_content[:start_off] + e["newText"] + new_content[end_off:]
                lines_arr = new_content.splitlines(keepends=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(new_content)
            applied.append({"file": fp, "edits": len(tedits), "status": "ok"})
        except Exception as exc:
            applied.append({"file": fp, "edits": len(tedits), "status": f"error: {exc}"})
            logger.exception("apply_workspace_edit failed for %s", fp)
    return applied


def code_action_tool(
    path: str,
    line: int,
    end_line: Optional[int] = None,
    only_kinds: Optional[List[str]] = None,
    apply_index: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Request available LSP code actions (quick-fixes, organize imports, source actions).

    Two modes:
      1. apply_index=None (default): list all available actions. Inspect titles + kinds.
      2. apply_index=N: apply the Nth action (0-based) — writes files / runs commands.

    Common kinds:
      - quickfix: fix a diagnostic (e.g. add missing import)
      - source.organizeImports: organize all imports in the file
      - source.fixAll: apply all auto-fixable issues
      - refactor.extract: extract function/variable
      - refactor.inline: inline function/variable

    Args:
        path: Absolute file path.
        line: 1-based line number.
        end_line: 1-based end line for range-based actions (defaults to line).
        only_kinds: Optional filter list (e.g. ["source.organizeImports"]).
        apply_index: If set, apply the Nth action returned (0-based). Otherwise list-only.
        language: Language override.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"No LSP bridge for {lang}")

    lsp_line = line - 1
    lsp_end_line = (end_line - 1) if end_line else lsp_line

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_err(f"Path not found: {path}")

    relevant_diags = _filter_diagnostics_in_range(bridge, str(target), lsp_line, lsp_end_line)

    actions = bridge.code_action(
        str(target), lsp_line, 0, lsp_end_line, 999,
        only_kinds=only_kinds, diagnostics=relevant_diags,
    ) or []

    if not actions:
        return fmt_ok({
            "found": False,
            "query": {"path": str(target), "line": line, "end_line": end_line, "only_kinds": only_kinds},
            "diagnostics_in_range": len(relevant_diags),
            "hint": "No actions available. Try widening range, removing only_kinds filter, or check diagnostics first.",
        })

    summary = _summarize_actions(actions)

    if apply_index is None:
        return fmt_ok({
            "found": True,
            "lsp_server": bridge.command,
            "diagnostics_in_range": len(relevant_diags),
            "actions": summary,
            "hint": "Re-run with apply_index=N to apply. Prefer is_preferred=true actions for safe quick-fixes.",
        })

    if apply_index < 0 or apply_index >= len(actions):
        return fmt_err(f"Path not found: {path}")

    action = actions[apply_index]
    applied_edits = []
    cmd_result = None

    if action.get("edit"):
        applied_edits = _apply_workspace_edit(action["edit"])

    if action.get("command"):
        cmd = action["command"]
        if isinstance(cmd, dict):
            cmd_result = bridge.execute_command(cmd.get("command", ""), cmd.get("arguments"))
            # Some servers send back a WorkspaceEdit via applyEdit instead — already
            # handled by the bridge's incoming dispatch. For now we just record the result.

    return fmt_ok({
        "applied": True,
        "action": {"title": action.get("title", ""), "kind": action.get("kind", "")},
        "edits_applied": applied_edits,
        "command_result": cmd_result,
    })


CODE_ACTION_SCHEMA = {
    "name": "code_action",
    "description": (
        "Request LSP code actions: quick-fixes, organize imports, source.fixAll, refactor.extract/inline. "
        "Two modes — list (default) or apply_index=N. Use this AFTER code_diagnostics to auto-fix errors "
        "(e.g. add missing imports, remove unused vars). Use kind='source.organizeImports' for cleanup. "
        "MUCH safer than manual edits — preserves semantics via the language server."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "end_line": {"type": "integer", "description": "1-based end line (defaults to line)."},
            "only_kinds": {
                "type": "array", "items": {"type": "string"},
                "description": "Filter to specific kinds: quickfix, source.organizeImports, source.fixAll, refactor.extract, etc.",
            },
            "apply_index": {"type": "integer", "description": "0-based index of action to apply. Omit to list-only."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_action(args, **kw):
    return code_action_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        end_line=args.get("end_line"),
        only_kinds=args.get("only_kinds"),
        apply_index=args.get("apply_index"),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# New LSP 3.18 Tools (added 2026-06-21)
# ---------------------------------------------------------------------------


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

def code_semantic_tokens_tool(file_path: str, language: Optional[str] = None) -> str:
    """Get semantic tokens for a document (LSP textDocument/semanticTokens/full)."""
    from pathlib import Path as _Path
    target = _Path(file_path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {file_path}")
    lang = language or detect_language(str(target))
    if not lang:
        lang = _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Could not detect language for: {file_path}")
    try:
        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            return fmt_err(f"No LSP bridge available for {lang}")
        result = bridge.semantic_tokens_full(str(target))
        if result is None:
            return fmt_info("No semantic tokens available")
        return fmt_ok({"data": result.get("data", [])}, title="Semantic Tokens")
    except Exception as e:
        return fmt_err(f"semantic_tokens failed: {e}")


CODE_SEMANTIC_TOKENS_SCHEMA = {
    "name": "code_semantic_tokens",
    "description": "Get semantic tokens for a document. Returns token type/position data for code analysis.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["file_path"],
    },
}


def _handle_code_semantic_tokens(args, **kw):
    return code_semantic_tokens_tool(
        file_path=args.get("file_path", ""),
        language=args.get("language"),
    )


# ---- document_link ----

def code_document_links_tool(file_path: str, language: Optional[str] = None) -> str:
    """Get document links (LSP textDocument/documentLink)."""
    from pathlib import Path as _Path
    target = _Path(file_path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {file_path}")
    lang = language or detect_language(str(target))
    if not lang:
        lang = _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Could not detect language for: {file_path}")
    try:
        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            return fmt_err(f"No LSP bridge available for {lang}")
        links = bridge.document_link(str(target))
        if not links:
            return fmt_info("No document links found")
        return fmt_ok({"links": links}, title="Document Links")
    except Exception as e:
        return fmt_err(f"document_link failed: {e}")


CODE_DOCUMENT_LINKS_SCHEMA = {
    "name": "code_document_links",
    "description": "Get document links (type references, imports) from LSP.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["file_path"],
    },
}


def _handle_code_document_links(args, **kw):
    return code_document_links_tool(
        file_path=args.get("file_path", ""),
        language=args.get("language"),
    )


# ---- inline_value ----

def code_inline_values_tool(file_path: str, language: Optional[str] = None) -> str:
    """Get inline values (LSP textDocument/inlineValue)."""
    from pathlib import Path as _Path
    target = _Path(file_path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {file_path}")
    lang = language or detect_language(str(target))
    if not lang:
        lang = _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Could not detect language for: {file_path}")
    try:
        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            return fmt_err(f"No LSP bridge available for {lang}")
        # Get the file length for range
        lines = _Path(str(target)).read_text().splitlines()
        end_line = max(0, len(lines) - 1)
        end_char = len(lines[-1]) if lines else 0
        values = bridge.inline_value(str(target), 0, 0, end_line, end_char)
        if not values:
            return fmt_info("No inline values found")
        return fmt_ok({"values": values}, title="Inline Values")
    except Exception as e:
        return fmt_err(f"inline_value failed: {e}")


CODE_INLINE_VALUES_SCHEMA = {
    "name": "code_inline_values",
    "description": "Get inline variable values from LSP.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["file_path"],
    },
}


def _handle_code_inline_values(args, **kw):
    return code_inline_values_tool(
        file_path=args.get("file_path", ""),
        language=args.get("language"),
    )


# ---- LSP completion item kind mapping ----

_LSP_COMPLETION_KIND = {
    1: "Text", 2: "Method", 3: "Function", 4: "Constructor",
    5: "Field", 6: "Variable", 7: "Class", 8: "Interface",
    9: "Module", 10: "Property", 11: "Unit", 12: "Value",
    13: "Enum", 14: "Keyword", 15: "Snippet", 16: "Color",
    17: "File", 18: "Reference", 19: "Folder", 20: "EnumMember",
    21: "Constant", 22: "Struct", 23: "Event", 24: "Operator",
    25: "TypeParameter",
}


def _safe_register(name, toolset, schema, handler, check_fn=None, emoji=""):
    """Register a tool with error handling — one failure won't kill all registrations."""
    from tools.registry import registry

    try:
        registry.register(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            emoji=emoji,
        )
    except Exception as e:
        logger.warning("Failed to register tool '%s': %s", name, e)


__all__ = [
    # Public tool functions
    "code_definition_tool", "code_references_tool",
    "code_diagnostics_tool", "code_hover_tool",
    "code_rename_tool", "code_format_tool",
    "code_type_definition_tool", "code_implementations_tool",
    "code_signatures_tool", "code_action_tool",
    "code_highlight_tool", "code_inlay_hints_tool",
    "code_document_symbols_tool", "code_callers_tool",
    "code_callees_tool", "code_call_hierarchy_tool",
    "code_type_hierarchy_tool", "code_workspace_symbols_tool",
    # New LSP 3.18 tools
    "code_completion_tool", "code_code_lens_tool",
    "code_folding_range_tool", "code_selection_range_tool",
    "code_linked_editing_tool", "code_prepare_rename_tool",
    "code_semantic_tokens_tool", "code_document_links_tool",
    "code_inline_values_tool",
    # Schemas
    "CODE_DEFINITION_SCHEMA", "CODE_REFERENCES_SCHEMA",
    "CODE_DIAGNOSTICS_SCHEMA", "CODE_HOVER_SCHEMA",
    "CODE_RENAME_SCHEMA", "CODE_FORMAT_SCHEMA",
    "CODE_TYPE_DEFINITION_SCHEMA", "CODE_IMPLEMENTATIONS_SCHEMA",
    "CODE_SIGNATURES_SCHEMA", "CODE_ACTION_SCHEMA",
    "CODE_HIGHLIGHT_SCHEMA", "CODE_INLAY_HINTS_SCHEMA",
    "CODE_DOCUMENT_SYMBOLS_SCHEMA", "CODE_CALLERS_SCHEMA",
    "CODE_CALLEES_SCHEMA", "CODE_CALL_HIERARCHY_SCHEMA",
    "CODE_TYPE_HIERARCHY_SCHEMA", "CODE_WORKSPACE_SYMBOLS_SCHEMA",
    # New schemas
    "CODE_COMPLETION_SCHEMA", "CODE_CODE_LENS_SCHEMA",
    "CODE_FOLDING_RANGE_SCHEMA", "CODE_SELECTION_RANGE_SCHEMA",
    "CODE_LINKED_EDITING_SCHEMA", "CODE_PREPARE_RENAME_SCHEMA",
    "CODE_SEMANTIC_TOKENS_SCHEMA", "CODE_DOCUMENT_LINKS_SCHEMA",
    "CODE_INLINE_VALUES_SCHEMA",
    # Private helpers
    "_auto_detect_identifier_column", "_auto_detect_paren_column",
    "_ast_fallback_definition", "_ast_fallback_references",
    "_ast_fallback_diagnostics", "_ast_fallback_callees",
    "_format_definitions", "_format_references",
    "_format_diagnostics_result", "_normalize_hover_contents",
    "_format_signatures", "_summarize_actions",
    "_filter_diagnostics_in_range", "_extract_md",
    "_apply_workspace_edit", "_wss_find_anchor_file",
    "_wss_format_symbol_results", "_import_detect_language",
    "_extract_identifier", "_rg_search",
    "_python_ast_analyze", "_build_unused_import_diags",
    "_tsjs_import_heuristic", "_read_file_safe",
    "_extract_python_callees", "_extract_ts_callees",
    "_safe_register", "_check_lsp_reqs",
    # Handler functions needed by tests via lsp_bridge re-export facade
    "_handle_code_highlight", "_handle_code_inlay_hints",
    "_handle_code_type_hierarchy", "_handle_code_call_hierarchy",
    "_handle_code_document_symbols",
    "_handle_code_definition", "_handle_code_references",
    "_handle_code_diagnostics", "_handle_code_callers",
    "_handle_code_callees", "_handle_code_workspace_symbols",
    "_handle_code_rename", "_handle_code_hover",
    "_handle_code_format", "_handle_code_type_definition",
    "_handle_code_implementations", "_handle_code_signatures",
    "_handle_code_action",
    # New LSP 3.18 handlers
    "_handle_code_completion", "_handle_code_code_lens",
    "_handle_code_folding_range", "_handle_code_selection_range",
    "_handle_code_linked_editing", "_handle_code_prepare_rename",
    "_handle_code_semantic_tokens", "_handle_code_document_links",
    "_handle_code_inline_values",
]
