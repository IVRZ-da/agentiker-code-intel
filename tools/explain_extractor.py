"""Extracted from code_tools.py — explain_extractor."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .._fmt import _strip_ansi, fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger
from .capsule import code_capsule_tool
from .complexity import code_complexity_tool

logger = _setup_code_intel_logger(__name__)

# Combines capsule info + complexity into a single structured output.
# ---------------------------------------------------------------------------

def code_explain_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get a structured explanation of a symbol.

    Combines: signature (from AST/code_symbols), docstring, complexity,
    caller count, and key references into a single structured output.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # 1. Symbol capsule — signature, doc, references, definition
    capsule_raw = code_capsule_tool(path, line, language=language)
    capsule = {}
    try:
        plain = _strip_ansi(capsule_raw)
        capsule = json.loads(plain)
    except Exception:
        capsule = {}

    # 2. Complexity analysis
    complexity_raw = code_complexity_tool(path, line=line, language=language or "")
    complexity = {}
    try:
        plain = _strip_ansi(complexity_raw)
        complexity = json.loads(plain)
    except Exception:
        complexity = {}

    # 3. Build structured output
    comp_data = complexity.get("breakdown", {}) if isinstance(complexity, dict) else {}
    explain = {
        "symbol": capsule.get("symbol"),
        "kind": capsule.get("kind"),
        "signature": capsule.get("signature"),
        "doc_preview": capsule.get("doc_preview", ""),
        "definition": capsule.get("definition"),
        "reference_count": capsule.get("reference_count", 0),
        "files_affected": capsule.get("files_affected", 0),
        "top_references": capsule.get("top_references", []),
        "complexity": {
            "total": complexity.get("total", 0) if isinstance(complexity, dict) else 0,
            "rank": complexity.get("rank", "N/A") if isinstance(complexity, dict) else "N/A",
            "breakdown": {
                "base": comp_data.get("base", 1),
                "branches": comp_data.get("branches", 0),
                "loops": comp_data.get("loops", 0),
                "exceptions": comp_data.get("exceptions", 0),
                "early_returns": comp_data.get("early_returns", 0),
            },
        },
    }

    return fmt_ok(explain, title="📖 Symbol Explanation")


CODE_EXPLAIN_SCHEMA = {
    "name": "code_explain",
    "description": (
        "Get a structured explanation of a symbol at a given location. "
        "Combines signature, docstring, cyclomatic complexity, caller count, "
        "and key references into a single structured output."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path containing the symbol"},
            "line": {"type": "integer", "description": "1-based line number where the symbol appears"},
            "character": {
                "type": "integer",
                "description": "1-based column (optional, for disambiguation)",
            },
            "language": {
                "type": "string",
                "description": "Language override. Auto-detected from extension.",
            },
        },
        "required": ["path", "line"],
    },
}


def _handle_code_explain(args, **kw):
    return code_explain_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )
