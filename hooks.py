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


def _pre_llm_call_inject_context(**kwargs: Any) -> Optional[str]:
    """Before the LLM prompt, inject compact context for files in the message."""
    try:
        messages = kwargs.get("messages", [])
        if not messages:
            return None
        last_msg = ""
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    last_msg = content
                    break
        if not last_msg:
            return None

        # Quick-Check: nur bei Code-Keywords weitermachen
        if not any(kw in last_msg.lower() for kw in _code_keywords):
            return None

        file_refs = re.findall(
            r'(?:^|[\s"\'`({])((?:@/|\./|/)?[\w/_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|css|scss))',
            last_msg
        )
        if not file_refs:
            file_refs = re.findall(
                r'(?:^|[\s"\'`({])((?:@/|\./)?[\w/_.-]+/(?:[\w/_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java)))',
                last_msg
            )
        if not file_refs:
            return None
        file_refs = file_refs[:3]
        from .code_tools import code_symbols_tool, detect_language
        from .lsp_bridge import code_diagnostics_tool as _diag_tool
        context_parts = []
        for fref in file_refs:
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
                continue
            abs_path = os.path.abspath(path)
            cached = _pre_llm_call_cache.get(abs_path)
            if cached:
                cached_text, cached_ts = cached
                if time.monotonic() - cached_ts < _PRE_LLM_CALL_CACHE_TTL:
                    context_parts.append(cached_text)
                    continue
                # TTL expired — discard and re-fetch
                del _pre_llm_call_cache[abs_path]
            lang = detect_language(path)
            if not lang:
                continue
            try:
                symbols_json = code_symbols_tool(path=path, pattern="", include_body=False)
                symbols = json.loads(symbols_json) if isinstance(symbols_json, str) else symbols_json
                sym_list = symbols if isinstance(symbols, list) else symbols.get("symbols", [])
                parts = []
                if sym_list:
                    summary = f"[auto-context] {fref}: {len(sym_list)} symbols"
                    for s in sym_list[:8]:
                        name = s.get("name", "?")
                        kind = s.get("kind", "")
                        line = s.get("line", "")
                        summary += f"\n  L{line} {kind} {name}"
                    parts.append(summary)
                try:
                    diag_json = _diag_tool(path=path)
                    diag = json.loads(diag_json) if isinstance(diag_json, str) else diag_json
                    if isinstance(diag, dict):
                        errs = diag.get("errors", 0) or diag.get("diagnostic_count", 0)
                        if errs:
                            parts.append(f"  ⚠ {errs} diagnostics")
                except Exception:
                    pass
                cached = "\n".join(parts) if parts else None
                if cached:
                    context_parts.append(cached)
                    _pre_llm_call_cache[abs_path] = (cached, time.monotonic())
                    if len(_pre_llm_call_cache) > _PRE_LLM_CALL_CACHE_MAX:
                        _pre_llm_call_cache.pop(next(iter(_pre_llm_call_cache)), None)
            except Exception:
                pass
        if context_parts:
            return "\n".join(context_parts)
        return None
    except Exception as e:
        logging.getLogger("agentiker_code_intel").debug("pre_llm_call hook error: %s", e)
        return None


def on_pre_llm_call(**kwargs: Any) -> Optional[str]:
    """Public wrapper for the pre_llm_call hook — delegates to the internal implementation."""
    return _pre_llm_call_inject_context(**kwargs)
