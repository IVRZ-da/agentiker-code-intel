"""tools/capsule.py — Capsule tool: one-shot symbol overview.

Native implementation (no re-export from code_tools).
Provides code_capsule_tool, CODE_CAPSULE_SCHEMA, and _handle_code_capsule
with all helper functions defined inline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .._fmt import fmt_err, fmt_json
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)


def _capsule_find_symbol(symbols: list, line: int) -> Optional[dict]:
    """Finde den Symbol-Eintrag der die angegebene Zeile enthält."""
    for sym in symbols:
        sl = sym.get("start_line", 0)
        el = sym.get("end_line", sl)
        if sl <= line <= el:
            return sym
    return None


def _capsule_get_definition(target: str, line: int, lang: Optional[str]) -> dict:
    """Rufe LSP Definition für das Symbol ab (direkt via Bridge, kein fmt_ok)."""
    try:
        from ..lsp.bridge import get_lsp_manager

        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, target) if lang else None
        if bridge is None:
            return {"error": f"No LSP bridge for {lang}"}
        locations = bridge.goto_definition(target, line - 1, 0)
        if locations:
            return {"definition": locations[0], "count": len(locations)}
        return {"error": "No definition found"}
    except Exception as exc:
        return {"error": str(exc)}


def _capsule_get_references(
    target: str, line: int, matched: Optional[dict], lang: Optional[str]
) -> dict:
    """Rufe LSP References ab und gruppiere Top-5 (direkt via Bridge, kein fmt_ok)."""
    try:
        from ..lsp.bridge import get_lsp_manager

        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, target) if lang else None
        if bridge is None:
            return {"total": 0, "top": [], "files": 0}
        char = (matched.get("start_column", 0) or 0) - 1 if matched else 0
        refs = bridge.find_references(target, line - 1, char, include_declaration=False)
    except Exception:
        logger.debug("capsule: find_references failed")
        return {"total": 0, "top": [], "files": 0}

    if not refs:
        return {"total": 0, "top": [], "files": 0}

    # Gruppiere nach Datei
    by_file: Dict[str, list] = {}
    for loc in refs:
        fp = loc.get("file", loc.get("uri", ""))
        by_file.setdefault(fp, []).append(loc)

    top_refs = []
    total_refs = 0
    for fpath, locations in sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:5]:
        total_refs += len(locations)
        top_refs.append({
            "file": fpath,
            "lines": [loc.get("line") for loc in locations[:3]],
            "count": len(locations),
        })
    return {"total": total_refs, "top": top_refs, "files": len(by_file)}


def _capsule_extract_doc(target: Path, matched: Optional[dict], line: int) -> str:
    """Extrahiere Docstring/Kommentar oberhalb des Symbols."""
    try:
        file_lines = target.read_text("utf-8", errors="replace").split("\n")
        if matched:
            sym_line = matched.get("start_line", line) - 1
            comment_lines = []
            for i in range(sym_line - 1, -1, -1):
                stripped = file_lines[i].strip()
                if (
                    stripped.startswith("#")
                    or stripped.startswith("//")
                    or stripped.startswith("/*")
                    or stripped.startswith("*")
                ):
                    comment_lines.insert(0, stripped.lstrip("#/* "))
                elif stripped == "" or stripped.startswith("@") or stripped.startswith("["):
                    continue
                else:
                    break
            return " | ".join(comment_lines[:3])
    except Exception as e:
        logger.debug("_capsule_extract_docstring: error extracting docstring: %s", e)
        pass
    return ""


def _capsule_find_tests(
    target: str, line: int, matched: Optional[dict], lang: Optional[str]
) -> list:
    """Finde Test-Dateien die dieses Symbol referenzieren (direkt via Bridge)."""
    try:
        from ..lsp.bridge import get_lsp_manager

        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, target) if lang else None
        if bridge is None:
            return []
        char = (matched.get("start_column", 0) or 0) - 1 if matched else 0
        refs = bridge.find_references(target, line - 1, char, include_declaration=False)
        if not refs:
            return []
        test_files = set()
        for loc in refs:
            fp = loc.get("file", loc.get("uri", ""))
            if "test" in fp.lower() or "spec" in fp.lower():
                test_files.add(fp)
        return sorted(test_files)[:3]
    except Exception:
        logger.debug("capsule: test file collection failed")
        return []


def code_capsule_tool(
    path: str,
    line: int,
    language: Optional[str] = None,
    include_tests: bool = False,
) -> str:
    """One-shot compact symbol capsule: signature, docs, definition, top refs, imports.

    Reduces multiple tool calls (code_symbols + code_definition + code_references
    + read_file) into a single token-efficient JSON block.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Lazy import to avoid circular dependency: tools.symbols loads via tools.__init__
    # and tools.__init__ imports from tools.capsule.
    from ..tools.base import detect_language
    from ..tools.symbols import _symbols_extract_single

    lang = language or detect_language(str(target))

    # 1. Symbol metadata via direkte Extraktion
    if lang is None:
        return fmt_err(f"Unsupported language for '{path}'")
    _symbols, _ = _symbols_extract_single(target, lang, None, None, True, None)
    matched = _capsule_find_symbol(_symbols, line)

    # 2. Definition
    def_data = _capsule_get_definition(str(target), line, lang)

    # 3. Top references
    refs_result = _capsule_get_references(str(target), line, matched, lang)

    # 4. Docstring / heading
    doc_preview = _capsule_extract_doc(target, matched, line)

    capsule = {
        "path": str(target),
        "line": line,
        "symbol": matched.get("name") if matched else None,
        "kind": matched.get("kind") if matched else None,
        "signature": matched.get("signature") if matched else None,
        "doc_preview": doc_preview[:300],
        "definition": def_data.get("definition") if isinstance(def_data, dict) else None,
        "reference_count": refs_result["total"],
        "top_references": refs_result["top"],
        "files_affected": refs_result["files"],
    }

    # 5. Optional: find tests referencing this symbol
    if include_tests:
        capsule["test_files"] = _capsule_find_tests(str(target), line, matched, lang)

    return fmt_json(capsule)


CODE_CAPSULE_SCHEMA = {
    "name": "code_capsule",
    "description": (
        "One-shot compact symbol capsule: returns signature, short doc, "
        "definition location, top references, and imports for a symbol. "
        "Use this INSTEAD of multiple separate calls to code_symbols, code_definition, "
        "and code_references when you need a quick understanding of a symbol."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path containing the symbol",
            },
            "line": {
                "type": "integer",
                "description": "1-based line number where the symbol appears",
            },
            "language": {
                "type": "string",
                "description": "Language override. Auto-detected from extension.",
            },
            "include_tests": {
                "type": "boolean",
                "description": "Include test files referencing this symbol (default: False)",
            },
        },
        "required": ["path", "line"],
    },
}


def _handle_code_capsule(args, **kw):
    return code_capsule_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        language=args.get("language"),
        include_tests=args.get("include_tests", False),
    )


__all__ = [
    "code_capsule_tool",
    "CODE_CAPSULE_SCHEMA",
    "_handle_code_capsule",
]
