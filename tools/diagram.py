#!/usr/bin/env python3
"""tools/diagram.py — Mermaid call graph diagram for a symbol.

Extracted from code_tools.py for modularity.
Provides code_diagram_symbol_tool, CODE_DIAGRAM_SYMBOL_SCHEMA,
and _handle_code_diagram_symbol with helper functions defined inline.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger
from ..code_tools import detect_language

logger = _setup_code_intel_logger(__name__)


# D1: code_diagram_symbol — Mermaid call graph diagram for a symbol
# ---------------------------------------------------------------------------


def code_diagram_symbol_tool(
    path: str,
    line: int,
    character: Optional[int] = None,
    depth: int = 2,
    language: Optional[str] = None,
) -> str:
    """Generate a Mermaid call graph diagram for a symbol.

    Uses LSP call hierarchy (incoming_calls + outgoing_calls) to show
    who calls a function and who it calls, formatted as a Mermaid flowchart.
    Falls back to AST-based analysis if LSP is unavailable.

    Args:
        path: Absolute file path.
        line: 1-based line number where the symbol is defined/used.
        character: 1-based column (optional, auto-detected from identifier).
        depth: Max call chain depth (default: 2, max: 5).
        language: Language override (auto-detected from extension).

    Returns:
        Formatted response with "mermaid" key containing the diagram string.
    """
    from pathlib import Path as _Path

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Read file content early for symbol extraction
    _src_lines = []
    try:
        _src_lines = target.read_text("utf-8", errors="replace").split("\n")
    except Exception:
        _src_lines = []

    # Resolve language
    lang = language
    if not lang:
        lang = detect_language(str(target))
    if not lang:
        from ..lsp.bridge import _detect_language_for_lsp as _lsp_lang

        lang = _lsp_lang(str(target))
    if not lang:
        return fmt_err(f"Could not detect language for: {path}")

    lsp_line = line - 1  # Convert to 0-based

    # Auto-detect character column if not provided
    if character is None:
        try:
            if 0 <= lsp_line < len(_src_lines):
                src_line = _src_lines[lsp_line]
                # Find the start of the word at cursor (approximate middle of line)
                col = len(src_line) // 2
                # Walk left to find word boundary
                while col > 0 and (src_line[col - 1].isalnum() or src_line[col - 1] == "_"):
                    col -= 1
                character = col + 1
            else:
                character = 1
        except Exception:
            character = 1
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info(
        "code_diagram_symbol_tool: %s:%d:%s lang=%s depth=%d",
        path,
        line,
        character or "auto",
        lang,
        depth,
    )

    # Try LSP call hierarchy
    from ..lsp.bridge import get_lsp_manager

    manager = get_lsp_manager()
    bridge = manager.get_bridge(lang, str(target)) if lang else None

    incoming = None
    outgoing = None
    lsp_server = None

    if bridge and bridge.ensure_initialized():
        lsp_server = bridge.command
        try:
            incoming = bridge.incoming_calls(str(target), lsp_line, lsp_char)
            outgoing = bridge.outgoing_calls(str(target), lsp_line, lsp_char)
            logger.info(
                "code_diagram_symbol: LSP returned %s incoming, %s outgoing",
                len(incoming) if incoming else 0,
                len(outgoing) if outgoing else 0,
            )
        except Exception as e:
            logger.warning("code_diagram_symbol: LSP call hierarchy failed: %s", e)

    # Build Mermaid diagram
    symbol_name = _Path(path).stem
    try:
        if 0 <= lsp_line < len(_src_lines):
            line_text = _src_lines[lsp_line]
            # Try to extract symbol name from line text at character position
            col = max(0, min(lsp_char, len(line_text) - 1))
            start = col
            while start > 0 and (line_text[start - 1].isalnum() or line_text[start - 1] == "_"):
                start -= 1
            end = col
            while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
                end += 1
            extracted = line_text[start:end]
            if extracted:
                symbol_name = extracted
    except Exception:
        pass

    # Deduplicate and format nodes
    lines_seen = set()
    diagram_lines = ["graph LR"]

    # Helper to generate a safe node ID from a name
    def _node_id(name: str) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in name)
        if not safe or safe[0].isdigit():
            safe = "n" + safe
        return safe

    # Helper to add an edge
    def _add_edge(from_name: str, to_name: str, from_title: str = "", to_title: str = ""):
        if not from_name or not to_name:
            return
        f_id = _node_id(from_name)
        t_id = _node_id(to_name)
        key = f"{f_id}-->{t_id}"
        if key in lines_seen:
            return
        lines_seen.add(key)
        f_label = from_title or from_name
        t_label = to_title or to_name
        diagram_lines.append(f'    {f_id}["{f_label}"] --> {t_id}["{t_label}"]')

    # Mark the symbol node so it's always in the graph
    sym_id = _node_id(symbol_name)
    sym_node = f'    {sym_id}["<b>{symbol_name}</b>"]'
    # Add symbol node if not already added via edges
    _ = sym_node  # will be included implicitly when edges reference it

    # Add incoming callers
    if incoming:
        for inc in incoming:
            caller_name = inc.get("name", "") or _Path(inc.get("uri", "")).stem
            caller_title = inc.get("name", "") or caller_name
            if caller_name and caller_name != symbol_name:
                _add_edge(caller_name, symbol_name, caller_title, symbol_name)

    # Add outgoing callees
    if outgoing:
        for outg in outgoing:
            callee_name = outg.get("name", "") or _Path(outg.get("uri", "")).stem
            callee_title = outg.get("name", "") or callee_name
            if callee_name and callee_name != symbol_name:
                _add_edge(symbol_name, callee_name, symbol_name, callee_title)

    # If no LSP results, try fallback
    if not incoming and not outgoing:
        logger.info("code_diagram_symbol: LSP returned no results, trying AST fallback")
        try:
            # Simple AST fallback: search for function definitions and calls
            ext = target.suffix.lower()
            if ext in (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".rs",
                ".go",
                ".java",
                ".c",
                ".cpp",
            ):
                import re as _re

                func_patterns = {
                    "python": _re.compile(r"^\s*def\s+(\w+)\s*\("),
                    "typescript": _re.compile(
                        r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
                    ),
                    "tsx": _re.compile(
                        r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
                    ),
                    "javascript": _re.compile(
                        r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
                    ),
                    "rust": _re.compile(
                        r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(",
                    ),
                    "go": _re.compile(r"^\s*(?:func\s+)(\w+)\s*\("),
                    "java": _re.compile(
                        r"^\s*(?:public|private|protected|static|\s)*\s+(\w+)\s*\(",
                    ),
                    "c": _re.compile(r"^\s*\w+\s+(\w+)\s*\("),
                    "cpp": _re.compile(r"^\s*\w+\s+(\w+)\s*\("),
                }
                pattern = func_patterns.get(lang)
                if pattern:
                    for line_no, src_line in enumerate(_src_lines, 1):
                        m = pattern.search(src_line)
                        if m and m.group(1) != symbol_name:
                            fn_name = m.group(1)
                            fn_id = _node_id(fn_name)
                            edge_key = f"{fn_id}-->{sym_id}"
                            if edge_key not in lines_seen:
                                lines_seen.add(edge_key)
                                diagram_lines.append(
                                    f'    {fn_id}["{fn_name}"] -.-> {sym_id}["{symbol_name}"]',
                                )
                                if len([item for item in lines_seen if "-->" in item]) >= depth * 3:
                                    break
        except Exception as e:
            logger.debug("code_diagram_symbol: AST fallback failed: %s", e)

    # Ensure symbol node is included even if no edges
    if not any(sym_id in item for item in diagram_lines):
        diagram_lines.append(sym_node)

    # Add depth note
    diagram_lines.append(f"    %% depth={depth} | LSP={'yes' if lsp_server else 'no'}")

    diagram = "\n".join(diagram_lines)

    result: Dict[str, Any] = {"mermaid": diagram}
    if lsp_server:
        result["lsp_server"] = lsp_server
    result["depth"] = depth
    result["symbol"] = symbol_name
    result["path"] = str(target)

    return fmt_ok(result, title=f"Call Graph: {symbol_name}")


CODE_DIAGRAM_SYMBOL_SCHEMA = {
    "name": "code_diagram_symbol",
    "description": (
        "Generate a Mermaid call graph diagram for a symbol. "
        "Uses LSP call hierarchy (incoming_calls + outgoing_calls) to show "
        "who calls a function and who it calls, formatted as a Mermaid flowchart. "
        "Falls back to AST-based analysis if LSP is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {
                "type": "integer",
                "description": "1-based column (auto-detected if omitted)",
            },
            "depth": {
                "type": "integer",
                "description": "Max call chain depth (default: 2, max: 5)",
            },
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_diagram_symbol(args, **kw):
    return code_diagram_symbol_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        character=args.get("character"),
        depth=args.get("depth", 2),
        language=args.get("language"),
    )
