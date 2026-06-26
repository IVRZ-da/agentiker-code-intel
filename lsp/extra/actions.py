"""lsp/extra/ — LSP code action tool + helpers."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..._fmt import fmt_err, fmt_ok
from ..bridge import (
    _detect_language_for_lsp,
    get_lsp_manager,
    logger,
)


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
