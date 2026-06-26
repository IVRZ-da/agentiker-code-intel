"""lsp/extra/ — LSP signatures tool + helpers."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..._fmt import fmt_err, fmt_ok
from ..bridge import (
    _LANGUAGE_SERVERS,
    _detect_language_for_lsp,
    _resolve_command,
    get_lsp_manager,
)
from ..tools_core import (
    _auto_detect_paren_column,
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
