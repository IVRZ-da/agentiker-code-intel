"""lsp/extra.py — extracted from lsp/tools.py."""
# ruff: noqa: E402, F401, F405
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .._fmt import fmt_err, fmt_info, fmt_ok
from ..code_tools import detect_language
from .bridge import (
    _LANGUAGE_SERVERS,
    _detect_language_for_lsp,
    _location_to_dict,
    _resolve_command,
    get_lsp_manager,
    logger,
)
from .tools_core import (  # noqa: E402, F401
    _auto_detect_identifier_column,
    _auto_detect_paren_column,
    _resolve_target_and_lang,
)
from .tools_handler import (  # noqa: E402, F401
    code_format_tool,
    code_hover_tool,
    code_rename_tool,
    code_workspace_symbols_tool,
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
