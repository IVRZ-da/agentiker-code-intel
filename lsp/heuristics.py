"""lsp/heuristics.py — AST fallback heuristics for LSP tools.

Extracted from tools_core.py.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Dict, List, Optional

from .._fmt import fmt_ok
from .bridge import (
    _cached_read_lines,
    _find_workspace_root,
    logger,
)

# =========================================================================
# Column detection helpers
# =========================================================================


def _auto_detect_paren_column(file_path: str, lsp_line: int) -> int:
    """Auto-detect column to land cursor inside the first '(' on the given line."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        src_line = lines[lsp_line] if 0 <= lsp_line < len(lines) else ""
    except Exception:
        src_line = ""
    idx = src_line.find("(")
    return (idx + 2) if idx >= 0 else 1


def _auto_detect_identifier_column(file_path: str, line: int) -> Optional[int]:
    """Find the column of the first meaningful identifier on *line* (0-based).

    Skips common language keywords (import, export, from, const, etc.) to land
    on actual symbol names like ``createLogger`` or ``PropertyService``.
    """
    _KEYWORDS = frozenset({
        "import", "export", "from", "const", "let", "var", "class", "function",
        "return", "async", "await", "type", "interface", "if", "else", "for",
        "while", "new", "throw", "try", "catch", "finally", "switch", "case",
        "break", "continue", "default", "extends", "implements", "super",
        "this", "static", "public", "private", "protected", "readonly",
        "declare", "enum", "namespace", "module", "require", "as",
        "void", "null", "undefined", "true", "false", "of", "in",
    })

    try:
        lines = _cached_read_lines(file_path)
        if line < 0 or line >= len(lines):
            return None
        text = lines[line]
        # Extract word-like tokens and skip keywords
        i = 0
        while i < len(text):
            ch = text[i]
            if ch.isalpha() or ch == '_':
                # Found start of a word
                start = i
                while i < len(text) and (text[i].isalnum() or text[i] == '_'):
                    i += 1
                word = text[start:i]
                if word not in _KEYWORDS:
                    return start + 1  # 1-based
                # else: skip this keyword, continue scanning
            elif ch in ('"', "'", '`'):
                # Skip string literals
                quote = ch
                i += 1
                while i < len(text) and text[i] != quote:
                    if text[i] == '\\':
                        i += 1
                    i += 1
                i += 1  # skip closing quote
            else:
                i += 1
    except OSError as e:
        logger.debug("_extract_identifier: reading file: %s", e)
        pass
    return None


# =========================================================================
# Language detection (via tools_core facade)
# =========================================================================
# Language detection (delegates to tools_core)
# =========================================================================


def _import_detect_language():
    """4-stage import fallback for detect_language from code_intel.

    Direct import (not via tools_core) to avoid circular imports.
    """
    try:
        from ..code_tools import detect_language as _detect
        return _detect
    except ImportError as e:
        logger.debug("_import_detect_language: import ..code_tools failed: %s", e)
        pass
    try:
        from tools.code_tools import detect_language as _detect
        return _detect
    except ImportError as e:
        logger.debug("_import_detect_language: import tools.code_tools failed: %s", e)
        pass
    try:
        from hermes_plugins.code_intel.code_tools import detect_language as _detect
        return _detect
    except ImportError as e:
        logger.debug("_import_detect_language: import hermes_plugins.code_intel failed: %s", e)
        pass
    try:
        import importlib.util as _ilu
        _mod_path = str(Path(__file__).parent / "code_tools.py")
        _spec = _ilu.spec_from_file_location("code_intel_standalone", _mod_path)
        if _spec is None or _spec.loader is None:
            logger.debug("_import_detect_language: spec_from_file_location failed for %s", _mod_path)
            return None
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.detect_language
    except Exception as e:
        logger.debug("_import_detect_language: spec_from_file_location failed: %s", e)
        pass
    return None

# =========================================================================
# Identifier extraction
# =========================================================================


def _extract_identifier(file_path: str, line: int, character: Optional[int]) -> str:
    """Extrahiere Identifier aus einer bestimmten Zeile/Spalte."""
    try:
        lines = _cached_read_lines(file_path)
        text_line = lines[line - 1] if 0 < line <= len(lines) else ""
    except (OSError, IndexError):
        text_line = ""
    if not character or not text_line or character > len(text_line):
        return ""
    idx = character - 1
    start = idx
    while start > 0 and (text_line[start - 1].isalnum() or text_line[start - 1] == '_'):
        start -= 1
    end = idx
    while end < len(text_line) and (text_line[end].isalnum() or text_line[end] == '_'):
        end += 1
    return text_line[start:end]


# =========================================================================
# ripgrep search
# =========================================================================


def _rg_search(identifier: str, root: str) -> list:
    """Führe ripgrep-Suche aus und parse Ergebnisse."""
    import subprocess as _sp
    try:
        result = _sp.run(
            ["rg", "--no-heading", "--line-number", "-n", "-w", identifier, root],
            capture_output=True, text=True, timeout=15,
        )
        refs = []
        for match_line in result.stdout.strip().split("\n"):
            if not match_line:
                continue
            parts = match_line.split(":", 2)
            if len(parts) >= 3:
                refs.append({
                    "file": parts[0],
                    "line": int(parts[1]),
                    "text": parts[2].strip()[:200],
                })
        return refs
    except Exception:
        return []


# =========================================================================
# AST fallback: definition
# =========================================================================


def _ast_fallback_definition(
    file_path: str, line: int, character: Optional[int], lang: Optional[str]
) -> str:
    """Fallback: use tree-sitter AST to find a definition."""
    from .tools_core import _import_detect_language as _idl
    _detect = _idl()
    if _detect is None:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    # Read the identifier at the cursor position
    identifier = _extract_identifier(file_path, line, character)
    if not identifier:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line, "character": character},
            "method": "fallback",
            "warning": "Could not extract an identifier at the given position.",
            "suggestion": "Ensure line and character point to a valid identifier.",
        })

    # Search for the definition in the file tree
    root = _find_workspace_root(file_path)
    from ..code_tools import code_search_tool  # late import: avoids circular import at module load
    result_str = code_search_tool(
        path=root,
        query="(function_definition name: (identifier) @name) @def\n(class_definition name: (identifier) @name) @def",
        pattern=identifier,
        language=detected,
        max_results=20,
        _raw=True,
    )

    try:
        result = _json.loads(result_str)
    except _json.JSONDecodeError:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "raw_search_result": result_str,
        })

    defs = []
    for r in result.get("results", []):
        defs.append({
            "file": r.get("file", file_path),
            "line": r.get("line"),
            "kind": r.get("kind", "unknown"),
            "text": r.get("text", ""),
        })

    return fmt_ok({
        "path": file_path,
        "query": {"line": line, "character": character, "identifier": identifier},
        "method": "fallback_ast",
        "warning": "LSP server unavailable, using AST-based search. Results may be incomplete.",
        "definition_count": len(defs),
        "definitions": defs,
    })


# =========================================================================
# AST fallback: references
# =========================================================================


def _ast_fallback_references(
    file_path: str, line: int, character: Optional[int], lang: Optional[str]
) -> str:
    """Fallback: use grep-style search for references."""

    _detect = _import_detect_language()
    if _detect is None:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": "detect_language not available — LSP server unavailable and code_intel import failed.",
            "suggestion": "Install a language server: pip install pyright or npm i -g typescript-language-server",
        })

    detected = lang or _detect(file_path)
    if not detected:
        return fmt_ok({
            "path": file_path,
            "method": "fallback",
            "warning": f"Unsupported language for {file_path}",
        })

    identifier = _extract_identifier(file_path, line, character)
    if not identifier:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line, "character": character},
            "method": "fallback",
            "warning": "Could not extract an identifier at the given position.",
        })

    root = _find_workspace_root(file_path)
    refs = _rg_search(identifier, root)

    by_file: Dict[str, List[dict]] = {}
    for r in refs:
        by_file.setdefault(r["file"], []).append(r)

    return fmt_ok({
        "path": file_path,
        "query": {"line": line, "character": character, "identifier": identifier},
        "method": "fallback_text",
        "warning": "LSP server unavailable, using text-based search. May include false positives.",
        "reference_count": len(refs),
        "files_affected": len(by_file),
        "references": refs,
        "by_file": by_file,
    })


# =========================================================================
# File reading
# =========================================================================


def _read_file_safe(file_path: str):
    """Read file content, returning ``(content, None)`` or ``(None, error_json)``."""
    try:
        content = Path(file_path).read_text("utf-8", errors="replace")
        return content, None
    except Exception as exc:
        return None, _json.dumps({
            "path": file_path, "method": "fallback", "warning": str(exc),
        })


# =========================================================================
# Python AST analysis
# =========================================================================


def _python_ast_analyze(content: str):
    """Walk Python AST, collect imported/used/defined names.

    Returns ``(imported, used, defined)`` sets, or ``None`` on syntax error.
    """
    import ast
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    except Exception:
        return None
    imported: set[str] = set()
    used: set[str] = set()
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                used.add(node.id)
    return imported, used, defined


def _build_unused_import_diags(
    imported: set, used: set, defined: set, content: str,
) -> list[dict]:
    """Build diagnostics for imports that are neither used nor re-defined."""
    diagnostics: list[dict] = []
    for name in sorted(imported - used - defined):
        for i, line_text in enumerate(content.split("\n"), 1):
            if name in line_text and ("import" in line_text or "from " in line_text):
                diagnostics.append({
                    "severity": 2,
                    "message": f"Possibly unused import: {name}",
                    "range": {"start": {"line": i - 1, "character": 0},
                              "end":   {"line": i - 1, "character": len(line_text)}},
                    "source": "ast_heuristic",
                })
                break
    return diagnostics


def _tsjs_import_heuristic(content: str) -> list[dict]:
    """Token-based import-unused heuristic for TypeScript / JavaScript."""
    diagnostics: list[dict] = []
    lines = content.split("\n")
    for i, line_text in enumerate(lines, 1):
        stripped = line_text.strip()
        if stripped.startswith("import ") and "from " in stripped:
            imp = stripped.split("from")[0].split("{")[-1].split("}")[0]
            imp = imp.replace("import ", "").replace("* as ", "").strip()
            if imp and not any(imp in ln for ln in lines[i:]):
                diagnostics.append({
                    "severity": 2,
                    "message": f"Possibly unused import: {imp}",
                    "range": {"start": {"line": i - 1, "character": 0},
                              "end":   {"line": i - 1, "character": len(line_text)}},
                    "source": "ast_heuristic",
                })
    return diagnostics


def _format_diagnostics_result(file_path: str, diagnostics: list[dict]) -> str:
    """Build the final JSON string for a diagnostics response."""
    return fmt_ok({
        "path": file_path,
        "method": "ast_heuristic",
        "warning": "LSP server unavailable. Using lightweight AST heuristic.",
        "diagnostic_count": len(diagnostics),
        "errors": len([d for d in diagnostics if d.get("severity", 1) == 1]),
        "warnings": len([d for d in diagnostics if d.get("severity", 2) == 2]),
        "diagnostics": diagnostics,
    })


def _ast_fallback_diagnostics(file_path: str, lang: Optional[str]) -> str:
    """Lightweight AST-based heuristic for common issues: unused imports, undefined names."""
    content, error = _read_file_safe(file_path)
    if error:
        return error
    assert content is not None  # help pyright narrow the type
    diagnostics: list[dict] = []
    if lang == "python":
        result = _python_ast_analyze(content)
        if result is not None:
            imported, used, defined = result
            diagnostics = _build_unused_import_diags(imported, used, defined, content)
        else:
            try:
                import ast as _ast_mod
                _ast_mod.parse(content)  # raises SyntaxError
            except SyntaxError as exc:
                diagnostics.append({
                    "severity": 1,
                    "message": f"Syntax error: {exc.msg}",
                    "range": {"start": {"line": (exc.lineno or 1) - 1, "character": 0},
                              "end":   {"line": (exc.lineno or 1) - 1, "character": 0}},
                    "source": "ast_heuristic",
                })
            except Exception as e:
                logger.debug("_python_import_miss_heuristic: AST parse failed: %s", e)
                pass
    elif lang in ("typescript", "javascript"):
        diagnostics = _tsjs_import_heuristic(content)
    return _format_diagnostics_result(file_path, diagnostics)


# =========================================================================
# AST fallback: callees
# =========================================================================


def _ast_fallback_callees(file_path: str, line: int, lang: Optional[str]) -> str:
    """AST fallback: extract call expressions from the function/method at *line*."""
    content, error = _read_file_safe(file_path)
    if error:
        return error
    assert content is not None

    callees: list[dict] = []

    if lang == "python":
        callees = _extract_python_callees(content, line)
    elif lang in ("typescript", "javascript"):
        callees = _extract_ts_callees(content, line)

    if not callees:
        return fmt_ok({
            "path": file_path,
            "query": {"line": line},
            "method": "ast_heuristic",
            "warning": "Could not extract callees via AST. Ensure line points to a function/method.",
            "callees": [],
        })

    return fmt_ok({
        "path": file_path,
        "query": {"line": line},
        "method": "ast_heuristic",
        "callee_count": len(callees),
        "callees": callees,
    })


def _extract_python_callees(content: str, line: int) -> list:
    """Extract function calls from a Python function/method at given line."""
    import ast as _ast
    callees = []
    try:
        tree = _ast.parse(content)
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                func_start = getattr(node, "lineno", 1)
                func_end = getattr(node, "end_lineno", func_start)
                if func_start <= line <= func_end:
                    for child in _ast.walk(node):
                        if isinstance(child, _ast.Call):
                            name = ""
                            if isinstance(child.func, _ast.Name):
                                name = child.func.id
                            elif isinstance(child.func, _ast.Attribute):
                                name = child.func.attr
                            if name:
                                callees.append({
                                    "name": name,
                                    "line": getattr(child, "lineno", func_start),
                                    "type": "call",
                                })
                    break
    except SyntaxError as e:
        logger.debug("_python_callee_heuristic: syntax error: %s", e)
        pass
    except Exception as e:
        logger.debug("_python_callee_heuristic: unexpected error: %s", e)
        pass
    return callees


def _extract_ts_callees(content: str, line: int) -> list:
    """Extract function calls from a TypeScript/JS function region."""
    import re as _re
    callees = []
    lines = content.split("\n")
    if 0 < line <= len(lines):
        for i in range(line - 1, min(len(lines), line + 200)):
            ln = lines[i]
            for mtch in _re.finditer(r'([A-Za-z_]\w*)\s*\(', ln):
                cname = mtch.group(1)
                if cname not in {"if", "while", "for", "switch", "catch", "function", "return", "new"}:
                    callees.append({
                        "name": cname,
                        "line": i + 1,
                        "type": "call",
                    })
    return callees


# =========================================================================
# Formatters
# =========================================================================


def _format_definitions(defs: List[dict]) -> str:
    """Format definition results for display."""
    if not defs:
        return "No definition found."

    lines = []
    for i, d in enumerate(defs, 1):
        if not isinstance(d, dict):
            lines.append(f"{i}. <malformed entry>")
            continue
        file_path = d.get("file", d.get("path", "<unknown>"))
        line_no = d.get("line", d.get("row", 0))
        lines.append(f"{i}. {file_path}:{line_no}")
        if d.get("text"):
            lines.append(f"   {d['text']}")
        if d.get("context"):
            for ctx_line in d["context"]:
                if ctx_line.strip():
                    lines.append(f"   {ctx_line}")
    return "\n".join(lines)


def _format_references(refs: List[dict], by_file: Dict[str, List[dict]]) -> str:
    """Format references results for display."""
    if not refs:
        return "No references found."

    lines = [f"Found {len(refs)} references across {len(by_file)} file(s):"]

    for file_path, file_refs in sorted(by_file.items()):
        # Shorten path if it's within the workspace
        short = file_path
        lines.append(f"\n  {short} ({len(file_refs)} ref(s))")
        for r in file_refs:
            text = r.get("text", "") if isinstance(r, dict) else str(r)[:120]
            if not isinstance(r, dict):
                lines.append("    <malformed ref>")
                continue
            line_no = r.get("line", r.get("row", 0))
            if len(text) > 120:
                text = text[:117] + "..."
            lines.append(f"    L{line_no:>4d}  {text}")

    return "\n".join(lines)


# =========================================================================
# Exports
# =========================================================================

__all__ = [
    "_auto_detect_paren_column",
    "_auto_detect_identifier_column",
    "_ast_fallback_definition",
    "_import_detect_language",
    "_extract_identifier",
    "_rg_search",
    "_ast_fallback_references",
    "_read_file_safe",
    "_python_ast_analyze",
    "_build_unused_import_diags",
    "_tsjs_import_heuristic",
    "_format_diagnostics_result",
    "_ast_fallback_diagnostics",
    "_ast_fallback_callees",
    "_extract_python_callees",
    "_extract_ts_callees",
    "_format_definitions",
    "_format_references",
]
