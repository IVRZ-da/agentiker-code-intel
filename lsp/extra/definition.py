"""lsp/extra/ — LSP type definition/implementation tools."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..._fmt import fmt_err, fmt_info, fmt_ok
from ..bridge import (
    _detect_language_for_lsp,
    _location_to_dict,
    get_lsp_manager,
    logger,
)
from ..heuristics import _auto_detect_identifier_column


def _ast_fallback_type_definition(target, line):
    """Simple AST fallback: look for type annotations in function signatures."""
    try:
        import ast
        source = target.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check if the line matches
                node_line = node.lineno if hasattr(node, 'lineno') else 0
                if abs(node_line - line) <= 2:
                    # Return type annotation if available
                    if node.returns:
                        return [{"file": str(target), "line": node_line,
                                 "type": ast.dump(node.returns)[:80]}]
                    # Parameter type annotations
                    for arg in node.args.args:
                        if arg.arg and arg.annotation:
                            return [{"file": str(target), "line": node_line,
                                     "parameter": arg.arg,
                                     "type": ast.dump(arg.annotation)[:80]}]
    except Exception:
        pass
    return []


def _ast_fallback_implementations(target):
    """Simple AST fallback: find class definitions in the file."""
    try:
        import ast
        source = target.read_text()
        tree = ast.parse(source)
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [ast.dump(b)[:40] for b in node.bases]
                classes.append({
                    "file": str(target),
                    "line": node.lineno if hasattr(node, 'lineno') else 0,
                    "name": node.name,
                    "bases": bases,
                })
        return classes[:10]
    except Exception:
        pass
    return []


def code_type_definition_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Jump to the TYPE of a symbol (not its declaration).

    For `const user = getUser()` at `user`, code_definition lands on
    `getUser()`'s implementation, but code_type_definition lands on the
    `User` interface/class. Crucial for understanding shape before refactor.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        _raw = _ast_fallback_type_definition(target, lsp_line)
        if _raw:
            return fmt_ok({"type_definitions": _raw, "source": "ast-fallback"})
        return fmt_info("Type definition requires LSP — no AST fallback available", title="Type Definition")

    try:
        locs = bridge.type_definition(str(target), lsp_line, lsp_char)
    except Exception as exc:
        logger.debug("type_definition error for %s:%d: %s", str(target), line, exc)
        _raw = _ast_fallback_type_definition(target, lsp_line)
        if _raw:
            return fmt_ok({"type_definitions": _raw, "source": "ast-fallback"})
        return fmt_info("Type definition requires LSP — no AST fallback available", title="Type Definition")

    if not locs:
        return fmt_err("No type definition found at position")

    out = []
    for loc in locs:
        try:
            d = _location_to_dict(loc)
            # _location_to_dict now returns both "path" and "file" keys
            out.append(d)
        except Exception as exc:
            logger.debug("Skipping malformed type_definition location: %s", exc)
            continue
    if not out:
        return fmt_err("No type definition found at position")
    return fmt_ok({"type_definitions": out, "lsp_server": bridge.command})


CODE_TYPE_DEFINITION_SCHEMA = {
    "name": "code_type_definition",
    "description": (
        "Jump to the TYPE definition of a symbol (interface/class/type alias), "
        "not its value declaration. Use this when you need to understand the SHAPE "
        "of a value before refactoring — e.g. for `const u = getUser()`, this lands on "
        "the `User` interface, while code_definition lands on `getUser()`'s body. "
        "Requires LSP (most useful for TypeScript/Go/Rust)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_type_definition(args, **kw):
    return code_type_definition_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def code_implementations_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Find implementations of a symbol (interface, abstract class, method override).

    Uses LSP textDocument/implementation. Helps find where interfaces are
    implemented, abstract methods are overridden, or virtual methods are defined
    in concrete classes.
    """

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err("Could not auto-detect language")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err(f"Path not found: {path}")

    lsp_line = line - 1
    if character is None:
        character = _auto_detect_identifier_column(str(target), lsp_line)
    lsp_char = (character or 1) - 1

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        _raw = _ast_fallback_implementations(target)
        if _raw:
            return fmt_ok({"implementations": _raw, "source": "ast-fallback"})
        return fmt_info("Implementations require LSP — no AST fallback available", title="Implementations")

    try:
        locs = bridge.implementations(str(target), lsp_line, lsp_char)
    except Exception as exc:
        logger.debug("implementations error for %s:%d: %s", str(target), line, exc)
        _raw = _ast_fallback_implementations(target)
        if _raw:
            return fmt_ok({"implementations": _raw, "source": "ast-fallback"})
        return fmt_info("Implementations require LSP — no AST fallback available", title="Implementations")

    if not locs:
        return fmt_err("Failed to resolve references for caller analysis")

    out = []
    for loc in locs:
        try:
            d = _location_to_dict(loc)
            out.append(d)
        except Exception as exc:
            logger.debug("Skipping malformed implementation location: %s", exc)
            continue
    if not out:
        return fmt_err(f"Path not found: {path}")
    return fmt_ok({"implementations": out, "lsp_server": bridge.command})


CODE_IMPLEMENTATIONS_SCHEMA = {
    "name": "code_implementations",
    "description": (
        "Find implementations of a symbol via LSP textDocument/implementation. "
        "Useful for finding where interfaces are implemented, abstract methods "
        "are overridden, or concrete classes extend a base type."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_implementations(args, **kw):
    return code_implementations_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )
