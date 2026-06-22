"""tools/test_coverage.py — TestsForSymbol tool.

Extracted from code_tools.py to reduce module size.
Follows the same pattern as tools/impact.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)


# ---------------------------------------------------------------------------
# C6: code_tests_for_symbol — Find tests covering a specific symbol
# ---------------------------------------------------------------------------

CODE_TESTS_FOR_SYMBOL_SCHEMA = {
    "name": "code_tests_for_symbol",
    "description": (
        "Find tests that cover a specific symbol. "
        "Returns prioritized test files with relevance scores. "
        "Use before making changes to ensure safe refactoring."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path containing the symbol"},
            "line": {"type": "integer", "description": "1-based line number where the symbol is defined"},
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path", "line"],
    },
}


def _tests_find_references(target: str, line: int, lang: Optional[str]) -> dict:
    """Hole LSP References und gruppiere by_file."""
    try:
        from ..lsp_bridge import code_references_tool

        refs_json = code_references_tool(
            target, line,
            language=lang,
            include_declaration=False,
            group_by_file=True,
        )
        refs_data = json.loads(refs_json)
        return refs_data.get("by_file", {}) if isinstance(refs_data, dict) else {}
    except Exception as exc:
        logger.debug("code_tests_for_symbol: refs err: %s", exc)
        return {}


def _tests_find_symbol_name(target: str, line: int, lang: Optional[str]) -> Optional[str]:
    """Ermittle den Symbol-Namen aus code_symbols."""
    try:
        from ..code_tools import code_symbols_tool

        sym_data = json.loads(code_symbols_tool(target, pattern=None, kind=None, language=lang, include_body=True))
        for sym in (sym_data.get("symbols", []) if isinstance(sym_data, dict) else []):
            sl = sym.get("start_line", 0)
            if sl <= line <= (sym.get("end_line", sl)):
                return sym.get("name")
    except Exception:
        pass
    return None


def _calc_test_score(fpath: str, target: Path, symbol_name: Optional[str], ref_count: int) -> int:
    """Berechne Relevanz-Score einer Test-Datei für ein Symbol."""
    score = ref_count
    if str(target.parent) == str(Path(fpath).parent):
        score += 1
    if symbol_name:
        stem = Path(fpath).stem.lower()
        if symbol_name.lower() in stem or symbol_name.lower() in fpath.lower():
            score += 2
        try:
            if symbol_name in Path(fpath).read_text("utf-8", errors="replace"):
                score += 1
        except Exception:
            pass
    return score


def _tests_filter_and_score(by_file: dict, target: Path, symbol_name: Optional[str]) -> list:
    """Filtere by_file auf Test-Dateien und berechne Relevanz-Scores."""
    test_pat = re.compile(r'(?:test|spec|__tests__|\.test\.|\.spec\.)', re.IGNORECASE)
    test_entries = []
    for fpath, locations in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
        if not test_pat.search(fpath):
            continue
        ref_count = len(locations)
        score = _calc_test_score(fpath, target, symbol_name, ref_count)
        # Describe-Blöcke lesen
        describe_blocks = []
        try:
            lines = Path(fpath).read_text("utf-8", errors="replace").split("\n")
            describe_blocks = [ln.strip() for ln in lines[:30]
                              if any(kw in ln.lower() for kw in ("describe", "it(", "test(", "context"))][:5]
        except Exception:
            pass
        test_entries.append({
            "path": fpath, "score": score,
            "relevance": "direct" if score >= 5 else ("high" if score >= 3 else ("medium" if score >= 2 else "low")),
            "test_count": ref_count,
            "describe_blocks": describe_blocks,
        })
    test_entries.sort(key=lambda t: -t["score"])
    return test_entries


def _tests_calc_coverage(test_entries: list) -> str:
    """Berechne Coverage-Estimate aus max Score."""
    if not test_entries:
        return "none"
    ms = test_entries[0]["score"]
    return "high" if ms >= 6 else ("medium" if ms >= 3 else "low")


def code_tests_for_symbol_tool(path: str, line: int, language: Optional[str] = None) -> str:
    """Find and prioritize tests related to a symbol. Returns test files with relevance scores."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    try:
        from ..lsp_bridge import code_references_tool  # noqa: F401
    except ImportError:
        return fmt_err("lsp_bridge not available")

    from ..code_tools import detect_language

    lang = language or detect_language(str(target))

    # 1. Get all references
    by_file = _tests_find_references(str(target), line, lang)

    # 2. Identify symbol name
    symbol_name = _tests_find_symbol_name(str(target), line, lang) if by_file else None

    # 3. Filter + score for test files
    test_entries = _tests_filter_and_score(by_file, target, symbol_name) if by_file else []

    # 4. Coverage estimate
    coverage = _tests_calc_coverage(test_entries)

    return fmt_ok({
        "symbol": symbol_name,
        "path": str(target),
        "test_files": test_entries[:10],
        "total_tests_found": len(test_entries),
        "coverage_estimate": coverage,
    })


def _handle_code_tests_for_symbol(args, **kw):
    return code_tests_for_symbol_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        language=args.get("language"),
    )
