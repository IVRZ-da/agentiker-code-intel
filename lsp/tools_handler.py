"""lsp/handler.py — extracted from lsp/tools.py."""
# ruff: noqa: E402, F401, F405
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from .._fmt import fmt_err, fmt_ok
from .bridge import (
    _apply_edits_by_file,
    _build_rename_preview,
    _detect_language_for_lsp,
    _parse_workspace_edit,
    get_lsp_manager,
    logger,
)
from .tools_core import *  # noqa: F401, F403
from .tools_core import _auto_detect_identifier_column  # noqa: F401
from .tools_extra import *  # noqa: F401, F403


def _handle_code_highlight(args, **kw):
    return code_highlight_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_inlay_hints(args, **kw):
    return code_inlay_hints_tool(
        path=args.get("path", ""),
        start_line=args.get("start_line", 1),
        end_line=args.get("end_line", 0),
    )


def _handle_code_type_hierarchy(args, **kw):
    return code_type_hierarchy_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        direction=args.get("direction", "both"),
        language=args.get("language"),
    )


def _handle_code_call_hierarchy(args, **kw):
    return code_call_hierarchy_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        direction=args.get("direction", "both"),
        max_depth=args.get("max_depth", 3),
        max_callers_per_level=args.get("max_callers_per_level", 20),
        language=args.get("language"),
    )


def _handle_code_document_symbols(args, **kw):
    return code_document_symbols_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_definition(args, **kw):
    return code_definition_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


def _handle_code_references(args, **kw):
    return code_references_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
        include_declaration=args.get("include_declaration", True),
        group_by_file=args.get("group_by_file", False),
        max_results=args.get("max_results", 0),
    )


def _handle_code_diagnostics(args, **kw):
    return code_diagnostics_tool(
        path=args.get("path", ""),
        language=args.get("language"),
    )


def _handle_code_callers(args, **kw):
    return code_callers_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
        group_by_file=args.get("group_by_file", False),
    )


def _handle_code_callees(args, **kw):
    return code_callees_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# code_workspace_symbols — LSP workspace/symbol (monorepo-wide symbol search)
# ---------------------------------------------------------------------------


def _wss_find_anchor_file(anchor: Path) -> Path:
    """Wenn anchor ein Dir ist, finde eine passende Source-Datei für LSP-Seeding.

    Bevorzugt bekannte Projektverzeichnisse (packages, apps, src, lib, app)
    mit gängigen Source-Extensions.
    """
    if not anchor.is_dir():
        return anchor
    _PREFERRED_ANCHOR_DIRS = ("packages", "apps", "src", "lib", "app")
    _SMART_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs")
    hit = None
    for pref_dir in _PREFERRED_ANCHOR_DIRS:
        candidate = anchor / pref_dir
        if candidate.is_dir():
            for ext in _SMART_EXTENSIONS:
                hit = next(candidate.rglob(f"*{ext}"), None)
                if hit:
                    break
        if hit:
            break
    if not hit:
        for ext in _SMART_EXTENSIONS:
            hit = next(anchor.rglob(f"*{ext}"), None)
            if hit:
                break
    return hit if hit else anchor


_LSP_KIND_NAMES = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
    25: "operator", 26: "type_parameter",
}


def _wss_format_symbol_results(raw: list, kind: Optional[str], max_results: int) -> tuple:
    """Formatiere raw LSP workspace/symbol Response in Hermes-Dicts.

    Returns (symbols, truncated).
    """
    _KIND_NAMES = _LSP_KIND_NAMES
    symbols: List[dict] = []
    for sym in raw:
        loc = sym.get("location") or {}
        uri = loc.get("uri", "")
        file_path = uri[7:] if uri.startswith("file://") else uri
        rng = loc.get("range") or {}
        start = rng.get("start") or {}
        kind_num = sym.get("kind", 0)
        kind_name = _KIND_NAMES.get(kind_num, f"kind_{kind_num}")

        if kind and kind.lower() != kind_name:
            continue

        symbols.append({
            "name": sym.get("name", ""),
            "kind": kind_name,
            "container": sym.get("containerName") or "",
            "file": file_path,
            "line": start.get("line", 0) + 1 if start else None,
            "character": start.get("character", 0) + 1 if start else None,
        })

    truncated = len(symbols) > max_results
    symbols = symbols[:max_results]
    return symbols, truncated


def code_workspace_symbols_tool(
    query: str,
    path: Optional[str] = None,
    language: Optional[str] = None,
    kind: Optional[str] = None,
    max_results: int = 50,
) -> str:
    """Search symbols across the workspace using LSP workspace/symbol.

    Much faster than search_files for finding classes/functions/interfaces by name
    in large projects — returns only real symbols (not comments/strings) with
    their kind (class, function, interface, etc.) pre-indexed by the LSP server.

    Note for monorepos: The LSP server indexes symbols based on open documents.
    For best results, pass a specific source file as ``path`` (not a directory).
    When a directory is given, the tool picks an anchor file from packages/ or apps/.
    If results are empty, the LSP server may not have indexed that part of the monorepo
    — use code_search (AST-based) as an alternative that works without LSP indexing.

    Args:
        query: Fuzzy symbol name (e.g. 'UserService', 'createLogger').
        path: Optional file in the workspace to anchor the LSP root detection.
            For monorepos, prefer passing a specific source file for best results.
            Defaults to cwd.
        language: Language override ('typescript', 'python', etc.). Auto-detected
            from ``path`` if provided.
        kind: Optional filter: class, function, method, interface, enum, variable,
            constant, module, struct.
        limit: Max results to return (default 50).

    Returns:
        JSON string with matched symbols (name, kind, file, line, container).
    """

    anchor = Path(path).expanduser().resolve() if path else Path.cwd().resolve()
    if not anchor.exists():
        return fmt_err("No type definition found at position")

    probe_file = _wss_find_anchor_file(anchor)

    lang = language or _detect_language_for_lsp(str(probe_file))
    if not lang:
        return fmt_ok({
            "error": "Could not auto-detect language. Pass language= explicitly.",
            "query": query,
        })

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(probe_file))
    if bridge is None or not bridge.ensure_initialized():
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "query": query,
            "hint": "Use search_files (target='content') as fallback",
        })

    logger.info("code_workspace_symbols: query=%r lang=%s root=%s",
                query, lang, bridge.root_uri)
    raw = bridge.workspace_symbol(query, anchor_file=str(probe_file))
    if raw is None:
        return fmt_ok({
            "error": "LSP workspace/symbol request failed or not supported",
            "query": query,
            "lsp_server": bridge.command,
        })

    symbols, truncated = _wss_format_symbol_results(raw, kind, max_results)

    return fmt_ok({
        "query": query,
        "language": lang,
        "lsp_server": bridge.command,
        "total_returned": len(symbols),
        "truncated": truncated,
        "symbols": symbols,
    })


CODE_WORKSPACE_SYMBOLS_SCHEMA = {
    "name": "code_workspace_symbols",
    "description": (
        "Fuzzy search symbols (classes, functions, interfaces, etc.) across the entire "
        "workspace via LSP workspace/symbol. Sub-second monorepo-wide lookup that returns "
        "ONLY real symbols (not comments or string matches) with their kind and location. "
        "Use this INSTEAD of search_files when looking for a named entity like 'UserService' "
        "or 'createLogger' across many apps — it is faster, semantic, and avoids false positives."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Fuzzy symbol name to search for."},
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "language": {"type": "string", "description": "Language override: typescript, python, go, rust, etc."},
            "kind": {"type": "string", "description": "Filter by symbol kind: class, function, method, interface, enum, variable, constant, module, struct."},
            "limit": {"type": "integer", "description": "Max results (default 50)."},
        },
        "required": ["query"],
    },
}


def _handle_code_workspace_symbols(args, **kw):
    return code_workspace_symbols_tool(
        query=args.get("query", ""),
        path=args.get("path"),
        language=args.get("language"),
        kind=args.get("kind"),
        max_results=args.get("max_results", 50),
    )


# ---------------------------------------------------------------------------
# code_rename — LSP textDocument/rename (semantic, cross-file)
# ---------------------------------------------------------------------------


def code_rename_tool(
    path: str,
    line: int,
    new_name: str,
    character: Optional[int] = None,
    language: Optional[str] = None,
    dry_run: bool = True,
) -> str:
    """Semantically rename a symbol across all files using LSP textDocument/rename.

    Unlike code_refactor (pure AST text match), this understands types, scopes, and
    imports — it only renames references to THIS specific symbol (not unrelated ones
    that happen to have the same name).

    Args:
        path: Absolute file path where the symbol appears.
        line: 1-based line number.
        new_name: New symbol name.
        character: 1-based column (auto-detected if omitted).
        language: Language override.
        dry_run: Preview changes without writing. Default TRUE — always preview first.

    Returns:
        JSON with per-file edit list and (if dry_run=False) applied diff.
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
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "hint": "LSP server is required for semantic rename. Falls-back refactor available via code_refactor (text-AST).",
        })

    logger.info("code_rename: %s:%d:%s -> %r (dry_run=%s)",
                path, line, character, new_name, dry_run)
    workspace_edit = bridge.rename(str(target), lsp_line, lsp_char, new_name)
    if not workspace_edit:
        return fmt_ok({
            "error": "LSP rename returned no edits (symbol not renameable or not found)",
            "query": {"path": str(target), "line": line, "character": character, "new_name": new_name},
        })

    edits_by_file = _parse_workspace_edit(workspace_edit)
    preview = _build_rename_preview(edits_by_file)

    result = {
        "dry_run": dry_run,
        "new_name": new_name,
        "files_affected": len(edits_by_file),
        "total_edits": sum(p["edit_count"] for p in preview),
        "preview": preview,
        "lsp_server": bridge.command,
    }

    if dry_run:
        result["hint"] = "Re-run with dry_run=False to apply. Changes are NOT written."
        return fmt_ok(result)

    # Apply edits: sort per-file by (line, char) DESC to avoid offset drift
    applied = _apply_edits_by_file(edits_by_file)
    result["applied"] = applied
    return fmt_ok(result)


CODE_RENAME_SCHEMA = {
    "name": "code_rename",
    "description": (
        "Semantically rename a symbol across all files using LSP (understands types, scopes, imports). "
        "Only renames references to THIS symbol — not unrelated identifiers with the same name. "
        "Use this INSTEAD of code_refactor when renaming a class/function/variable across a monorepo. "
        "DRY-RUN by default — always preview before applying. Requires an LSP server (pyright, tsserver, gopls, etc.)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path where the symbol appears."},
            "line": {"type": "integer", "description": "1-based line number."},
            "new_name": {"type": "string", "description": "New symbol name."},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)."},
            "language": {"type": "string", "description": "Language override."},
            "dry_run": {"type": "boolean", "description": "Preview without writing. Default: true."},
        },
        "required": ["path", "line", "new_name"],
    },
}


def _handle_code_rename(args, **kw):
    return code_rename_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        new_name=args.get("new_name", ""),
        character=args.get("character"),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
    )


# ---------------------------------------------------------------------------
# code_hover — LSP textDocument/hover (signatures, docstrings, types)
# ---------------------------------------------------------------------------




def _normalize_hover_contents(contents: Any) -> List[str]:
    """Normalize LSP hover response to text list."""
    text_parts: List[str] = []
    if isinstance(contents, str):
        text_parts.append(contents)
    elif isinstance(contents, dict):
        text_parts.append(contents.get("value", ""))
    elif isinstance(contents, list):
        for c in contents:
            if isinstance(c, str):
                text_parts.append(c)
            elif isinstance(c, dict):
                text_parts.append(c.get("value", ""))
    return text_parts


def code_hover_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    language: Optional[str] = None,
) -> str:
    """Get type signature + docstring for symbol at position (LSP hover).

    Faster than code_capsule when you only need the signature/type info
    (no references, no definition jump). Use BEFORE editing call sites to
    confirm parameter names/types match what you're passing.
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
        return fmt_err(f"Path not found: {path}")

    result = bridge.hover(str(target), lsp_line, lsp_char)
    if not result:
        return fmt_err("No hover info at position")

    text_parts = _normalize_hover_contents(result.get("contents"))

    return fmt_ok({
        "path": str(target),
        "line": line,
        "character": character,
        "hover": "\n".join(t for t in text_parts if t).strip(),
        "lsp_server": bridge.command,
    })


CODE_HOVER_SCHEMA = {
    "name": "code_hover",
    "description": (
        "Get type signature, parameter info, and docstring for a symbol via LSP hover. "
        "Use this BEFORE calling/editing a function to confirm its exact signature without "
        "reading the full definition. Faster + cheaper than code_capsule when you only need "
        "the type info. Requires LSP server (pyright/tsserver/gopls/etc)."
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

CODE_FORMAT_SCHEMA = {
    "name": "code_format",
    "description": (
        "Format a file using the LSP server's textDocument/formatting. "
        "Automatically formats indentation, spacing, and style according to the "
        "language's formatter (pyright/pylsp for Python, tsserver for TypeScript, "
        "gopls for Go, rust-analyzer for Rust). "
        "Writes formatted content back to the file. "
        "Falls back to a safety check if LSP formatting is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path to format."},
            "language": {"type": "string", "description": "Language override (auto-detected from extension)."},
            "dry_run": {"type": "boolean", "description": "Preview changes without writing (default: true)."},
        },
        "required": ["path"],
    },
}


def _handle_code_hover(args, **kw):
    return code_hover_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        character=args.get("character"),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# CLI formatter fallback for code_format_tool
# ---------------------------------------------------------------------------


def _try_cli_formatter(path: str, lang: str) -> Optional[str]:
    """Try to format a file using a CLI formatter (ruff/prettier).

    Returns a formatted result dict or None if no CLI formatter is available.
    """
    import subprocess as _sp

    ext_to_cli = {
        "py": ["ruff", "format", "--stdin-filename", path, "-"],
        "python": ["ruff", "format", "--stdin-filename", path, "-"],
        "js": ["prettier", "--stdin-filepath", path],
        "jsx": ["prettier", "--stdin-filepath", path],
        "ts": ["prettier", "--stdin-filepath", path],
        "tsx": ["prettier", "--stdin-filepath", path],
        "typescript": ["prettier", "--stdin-filepath", path],
        "javascript": ["prettier", "--stdin-filepath", path],
    }

    cmd = ext_to_cli.get(lang)
    if not cmd:
        return None

    import difflib as _difflib

    try:
        target = Path(path).expanduser().resolve()
        original = target.read_text(encoding="utf-8")

        result = _sp.run(
            cmd,
            input=original,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return fmt_ok({
                "path": path,
                "language": lang,
                "method": "cli_fallback",
                "formatter": cmd[0],
                "error": result.stderr.strip(),
                "hint": f"{cmd[0]} exited with code {result.returncode}",
            })

        formatted = result.stdout
        if formatted == original:
            return fmt_ok({
                "path": path,
                "language": lang,
                "method": "cli_fallback",
                "formatter": cmd[0],
                "info": "No changes needed",
            })

        # Generate diff
        original_lines = original.splitlines(keepends=True)
        formatted_lines = formatted.splitlines(keepends=True)
        diff_lines = list(_difflib.unified_diff(
            original_lines, formatted_lines,
            fromfile=f"a/{target.name}", tofile=f"b/{target.name}",
            lineterm="",
        ))

        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "diff": diff_lines[:100],
            "has_changes": True,
        })

    except FileNotFoundError:
        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "error": f"{cmd[0]} not found — install it to enable CLI formatting fallback",
        })
    except _sp.TimeoutExpired:
        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "error": f"{cmd[0]} timed out",
        })
    except Exception as e:
        return fmt_ok({
            "path": path,
            "language": lang,
            "method": "cli_fallback",
            "formatter": cmd[0],
            "error": str(e),
        })


def code_format_tool(
    path: str,
    dry_run: bool = True,
    language: Optional[str] = None,
) -> str:
    """Format a file using the LSP server's textDocument/formatting.

    Returns a diff-like preview of the changes or applies them.
    Falls back gracefully if no LSP formatter is available for the language.
    """
    import difflib as _difflib

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or _detect_language_for_lsp(str(target))
    if not lang:
        return fmt_err("Could not auto-detect language")

    # Read original content
    original = target.read_text(encoding="utf-8")

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target))
    if bridge is None or not bridge.ensure_initialized():
        # CLI fallback: try ruff for Python, prettier for TS/JS
        cli_result = _try_cli_formatter(str(target), lang)
        if cli_result is not None:
            return cli_result
        return fmt_ok({
            "error": f"No LSP bridge available for language={lang}",
            "hint": "LSP server is required for formatting. Install the appropriate server.",
        })

    edits = bridge.format_document(str(target))
    if not edits:
        # CLI fallback: also try if LSP returned no edits
        cli_result = _try_cli_formatter(str(target), lang)
        if cli_result is not None:
            return cli_result
        return fmt_ok({
            "info": f"LSP formatter returned no changes for {lang}",
            "path": str(target),
        })

    # Apply TextEdits in reverse order (highest line first) to avoid offset drift
    sorted_edits = sorted(edits, key=lambda e: (
        -e.get("range", {}).get("start", {}).get("line", 0),
        -e.get("range", {}).get("start", {}).get("character", 0)
    ))

    content = list(original)  # character-level list
    edit_info = []
    for edit in sorted_edits:
        range_s = edit.get("range", {})
        start = range_s.get("start", {})
        end = range_s.get("end", {})
        s_line, s_char = start.get("line", 0), start.get("character", 0)
        e_line, e_char = end.get("line", 0), end.get("character", 0)
        new_text = edit.get("newText", "")

        # Convert to absolute offsets (simplified: line-based)
        lines_arr = original.splitlines(keepends=True)
        def _offset(ln: int, ch: int) -> int:
            return sum(len(x) for x in lines_arr[:ln]) + ch

        start_off = _offset(s_line, s_char)
        end_off = _offset(e_line, e_char)

        edit_info.append({
            "range": f"L{s_line+1}:{s_char}–L{e_line+1}:{e_char}",
            "old_len": end_off - start_off,
            "new_len": len(new_text),
        })

        content[start_off:end_off] = list(new_text)

    formatted = "".join(content)

    # Generate a unified diff for preview
    original_lines = original.splitlines(keepends=True)
    formatted_lines = formatted.splitlines(keepends=True)
    diff_lines = list(_difflib.unified_diff(
        original_lines, formatted_lines,
        fromfile=f"a/{target.name}", tofile=f"b/{target.name}",
        lineterm="",
    ))

    result = {
        "path": str(target),
        "language": lang,
        "lsp_server": bridge.command,
        "edit_count": len(edits),
        "edit_details": edit_info,
        "diff": diff_lines,
        "dry_run": dry_run,
        "formatted_length": len(formatted),
        "original_length": len(original),
    }

    if dry_run:
        result["hint"] = "Re-run with dry_run=False to apply formatting."
        return fmt_ok(result)

    # Write formatted content back
    target.write_text(formatted, encoding="utf-8")
    result["applied"] = True
    return fmt_ok(result)


def _handle_code_format(args: dict, **kw: Any) -> str:
    return code_format_tool(
        path=args.get("path", ""),
        dry_run=args.get("dry_run", True),
        language=args.get("language"),
    )
