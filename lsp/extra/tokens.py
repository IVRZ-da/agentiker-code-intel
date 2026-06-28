"""lsp/extra/ — LSP semantic tokens + document links tools.

AST fallbacks für tools ohne LSP-Verbindung.
- code_semantic_tokens: Leere Token-Liste zurückgeben
- code_document_links: Regex-basierte URL-Extraktion aus Kommentaren/Strings
- code_inline_values: Graceful fmt_info
"""
from __future__ import annotations

import re
from typing import Optional

from ..._fmt import fmt_err, fmt_info, fmt_ok
from ...code_tools import detect_language
from ..bridge import (
    _detect_language_for_lsp,
    get_lsp_manager,
)

# URL-Erkennung für document_links Fallback
_URL_RE = re.compile(r'https?://[^\s"\'\]\)>}]+', re.IGNORECASE)


def _ast_fallback_document_links(target) -> list:
    """Find URLs in source file via Regex (AST-Fallback)."""
    from pathlib import Path as _Path
    try:
        lines = _Path(str(target)).read_text().splitlines()
    except Exception:
        return []
    links = []
    for i, line in enumerate(lines):
        for match in _URL_RE.finditer(line):
            url = match.group()
            links.append({
                "url": url,
                "line": i + 1,
                "column": match.start(),
                "source": "ast-fallback",
            })
    return links


# ---- semantic_tokens ----

def code_semantic_tokens_tool(path: str, language: Optional[str] = None) -> str:
    """Get semantic tokens for a document (LSP textDocument/semanticTokens/full).

    Falls back to empty token list if LSP is unavailable.
    """
    from pathlib import Path as _Path
    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")
    lang = language or detect_language(str(target))
    if not lang:
        lang = _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_info("Could not detect language — semantic tokens require LSP")
    try:
        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            return fmt_info("No LSP bridge available — semantic tokens require LSP")
        result = bridge.semantic_tokens_full(str(target))
        if result is None:
            return fmt_info("No semantic tokens available")
        return fmt_ok({"data": result.get("data", [])}, title="Semantic Tokens")
    except Exception as e:
        return fmt_err(f"semantic_tokens failed: {e}")


CODE_SEMANTIC_TOKENS_SCHEMA = {
    "name": "code_semantic_tokens",
    "description": "Get semantic tokens for a document. Falls back to empty result if LSP is unavailable.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["path"],
    },
}


def _handle_code_semantic_tokens(args, **kw):
    return code_semantic_tokens_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


# ---- document_link ----

def code_document_links_tool(path: str, language: Optional[str] = None) -> str:
    """Get document links (LSP textDocument/documentLink).

    Falls back to Regex-based URL extraction if LSP is unavailable.
    """
    from pathlib import Path as _Path
    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Try LSP first
    lang = language or detect_language(str(target))
    if not lang:
        lang = _detect_language_for_lsp(str(target))
    if lang:
        try:
            manager = get_lsp_manager()
            bridge = manager.get_bridge(lang, str(target))
            if bridge is not None:
                links = bridge.document_link(str(target))
                if links:
                    return fmt_ok({"links": links}, title="Document Links")
        except Exception:
            pass

    # AST-Fallback: Regex-URLs aus Quelltext
    ast_links = _ast_fallback_document_links(target)
    if ast_links:
        return fmt_ok({"links": ast_links, "source": "ast-fallback"}, title="Document Links")

    return fmt_info("No document links found")


CODE_DOCUMENT_LINKS_SCHEMA = {
    "name": "code_document_links",
    "description": "Get document links (URLs/references) from code. Falls back to Regex extraction if LSP unavailable.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["path"],
    },
}


def _handle_code_document_links(args, **kw):
    return code_document_links_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


# ---- inline_value ----

def code_inline_values_tool(path: str, language: Optional[str] = None) -> str:
    """Get inline variable values (LSP textDocument/inlineValue).

    Requires LSP — no AST fallback available.
    """
    from pathlib import Path as _Path
    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")
    lang = language or detect_language(str(target))
    if not lang:
        lang = _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_info("Inline values require LSP — no AST fallback available")
    try:
        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, str(target))
        if bridge is None:
            return fmt_info("Inline values require LSP — no AST fallback available")
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
    "description": "Get inline variable values from LSP. Requires active LSP server.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["path"],
    },
}


def _handle_code_inline_values(args, **kw):
    return code_inline_values_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )
