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
