"""pre_llm_call hook for code_intel — injects symbol context + diagnostics for mentioned files."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

# Cache for pre_llm_call context lookups
_pre_llm_call_cache: dict = {}  # abs_path -> (text, timestamp)
_PRE_LLM_CALL_CACHE_MAX = 50
_PRE_LLM_CALL_CACHE_TTL = 120  # seconds

# Code keywords for quick-check filtering
_code_keywords = (
    'def ', 'class ', 'function ', 'import ', 'const ',
    '.py', '.ts', '.tsx', '.js', '.rs', '.go', '.java',
    ' file', ' code', ' fix', ' refactor', ' test',
)

_logger = logging.getLogger("agentiker_code_intel")


def _extract_last_user_message(messages: list) -> Optional[str]:
    """Find the last user message from the messages list."""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                return content
    return None


def _has_code_keywords(text: str) -> bool:
    """Quick-check: does the text contain code-related keywords?"""
    return any(kw in text.lower() for kw in _code_keywords)


def _find_file_references(text: str) -> list[str]:
    """Extract file references from text using regex patterns."""
    refs = re.findall(
        r'(?:^|[\s"\'`({])((?:@/|\./|/)?[\w/_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|css|scss))',
        text
    )
    if not refs:
        refs = re.findall(
            r'(?:^|[\s"\'`({])((?:@/|\./)?[\w/_.-]+/(?:[\w/_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java)))',
            text
        )
    return refs[:3]


def _resolve_file_path(fref: str) -> Optional[str]:
    """Resolve a file reference to an absolute path."""
    path = fref
    if path.startswith("@/"):
        cwd = os.getcwd()
        for prefix in ("src", "app", "lib", "components"):
            candidate = os.path.join(cwd, prefix, path[2:])
            if os.path.exists(candidate):
                path = candidate
                break
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    if not os.path.exists(path):
        return None
    return os.path.abspath(path)


def _format_symbol_summary(fref: str, sym_list: list) -> str:
    """Build a compact symbol summary string."""
    summary = f"[auto-context] {fref}: {len(sym_list)} symbols"
    for s in sym_list[:8]:
        name = s.get("name", "?")
        kind = s.get("kind", "")
        line = s.get("line", "")
        summary += f"\n  L{line} {kind} {name}"
    return summary


def _fetch_symbol_context(path: str, fref: str) -> Optional[str]:
    """Fetch symbol context + diagnostics for a file path."""
    from .code_tools import code_symbols_tool, detect_language
    from .lsp_bridge import code_diagnostics_tool as _diag_tool

    lang = detect_language(path)
    if not lang:
        return None

    parts = []
    try:
        symbols_json = code_symbols_tool(path=path, pattern="", include_body=False)
        symbols = json.loads(symbols_json) if isinstance(symbols_json, str) else symbols_json
        sym_list = symbols if isinstance(symbols, list) else symbols.get("symbols", [])
        if sym_list:
            parts.append(_format_symbol_summary(fref, sym_list))
    except Exception as e:
        _logger.debug("pre_llm_call: symbol fetch failed: %s", e)
        return None

    try:
        diag_json = _diag_tool(path=path)
        diag = json.loads(diag_json) if isinstance(diag_json, str) else diag_json
        if isinstance(diag, dict):
            errs = diag.get("errors", 0) or diag.get("diagnostic_count", 0)
            if errs:
                parts.append(f"  \u26a0 {errs} diagnostics")
    except Exception as e:
        _logger.debug("pre_llm_call: diagnostics failed: %s", e)

    return "\n".join(parts) if parts else None


def _process_file_ref(fref: str) -> Optional[str]:
    """Process a single file reference: check cache, fetch context, update cache."""
    abs_path = _resolve_file_path(fref)
    if abs_path is None:
        return None

    # Cache hit
    cached = _pre_llm_call_cache.get(abs_path)
    if cached:
        cached_text, cached_ts = cached
        if time.monotonic() - cached_ts < _PRE_LLM_CALL_CACHE_TTL:
            return cached_text
        del _pre_llm_call_cache[abs_path]

    # Fetch fresh context
    context = _fetch_symbol_context(abs_path, fref)
    if context:
        _pre_llm_call_cache[abs_path] = (context, time.monotonic())
        if len(_pre_llm_call_cache) > _PRE_LLM_CALL_CACHE_MAX:
            _pre_llm_call_cache.pop(next(iter(_pre_llm_call_cache)), None)
    return context


def _pre_llm_call_inject_context(**kwargs: Any) -> Optional[str]:
    """Before the LLM prompt, inject compact context for files in the message."""
    try:
        messages = kwargs.get("messages", [])
        if not messages:
            return None

        last_msg = _extract_last_user_message(messages)
        if not last_msg:
            return None

        if not _has_code_keywords(last_msg):
            return None

        file_refs = _find_file_references(last_msg)
        if not file_refs:
            return None

        context_parts = []
        for fref in file_refs:
            ctx = _process_file_ref(fref)
            if ctx:
                context_parts.append(ctx)

        return "\n".join(context_parts) if context_parts else None

    except Exception as e:
        logging.getLogger("agentiker_code_intel").debug("pre_llm_call hook error: %s", e)
        return None


def on_pre_llm_call(**kwargs: Any) -> Optional[str]:
    """Public wrapper for the pre_llm_call hook."""
    return _pre_llm_call_inject_context(**kwargs)
