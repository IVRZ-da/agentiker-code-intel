"""lsp/extra/ — LSP semantic tokens + document links tools."""
from __future__ import annotations

from typing import Optional

from ..._fmt import fmt_err, fmt_info, fmt_ok
from ...code_tools import detect_language
from ..bridge import (
    _detect_language_for_lsp,
    get_lsp_manager,
)


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
