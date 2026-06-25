"""lsp/call_hierarchy.py — Call hierarchy tools (callers, callees, call hierarchy).

Extracted from tools_core.py.
"""
# ruff: noqa: E402, F401, F405
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._fmt import fmt_err, fmt_ok
from .bridge import (
    LSPBridge,
    _cached_read_lines,
    _find_workspace_root,
    _location_to_dict,
    _read_context_lines,
    get_lsp_manager,
    logger,
)
from .heuristics import _auto_detect_identifier_column
from .tools_core import _detect_language_for_lsp, _resolve_target_and_lang, code_references_tool


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

    from .bridge import _detect_language_for_lsp as _dl
    lang = language or _dl(str(target))

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
    from .heuristics import _ast_fallback_callees as _afc
    return _afc(str(target), line, lang)


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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

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


__all__ = [
    "code_callers_tool",
    "code_callees_tool",
    "code_call_hierarchy_tool",
    "_try_lsp_callers",
    "_fallback_reference_callers",
    "CODE_CALL_HIERARCHY_SCHEMA",
    "CODE_CALLERS_SCHEMA",
    "CODE_CALLEES_SCHEMA",
]
