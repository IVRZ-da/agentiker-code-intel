"""Extracted from code_tools.py — symbols_extractor."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger
from .base import (
    _EXT_TO_LANG,
    _SYMBOL_QUERIES,
    _classify_symbol_kind,
    _detect_if_method,
    _get_language,
    _get_parser,
    detect_language,
)
from .cache import _DIR_SYMBOL_CACHE, _SYMBOL_CACHE, _set_cache, _set_dir_cache

logger = _setup_code_intel_logger(__name__)

def _setup_query(lang_key: str):
    """Load parser and language, then compile symbol query.

    Returns ``(parser, language, query)`` or ``None`` on failure.
    """
    from tree_sitter import Query
    parser = _get_parser(lang_key)
    lang = _get_language(lang_key)
    if parser is None or lang is None:
        return None
    query_text = _SYMBOL_QUERIES.get(lang_key)
    if not query_text:
        # Fallback: generic query for common definitions
        query_text = (
            "(function_definition name: (identifier) @name) @def\n"
            "(class_definition name: (identifier) @name) @def\n"
            "(function_declaration name: (identifier) @name) @def\n"
            "(class_declaration name: (type_identifier) @name) @def\n"
        )
    try:
        query = Query(lang, query_text)
    except Exception as e:
        logger.debug("Query compile error for %s: %s", lang_key, e)
        return None
    return parser, lang, query



def _extract_candidate(def_node, name_node, source, source_lines, kind, include_body):
    """Build a single symbol dict from an AST match."""
    name_text = name_node.text.decode("utf-8", errors="replace")
    start_line = def_node.start_point[0] + 1
    end_line = def_node.end_point[0] + 1
    sig_start = def_node.start_point[0]
    sig_end = min(def_node.end_point[0], sig_start + 2)
    signature = b"\n".join(source_lines[sig_start:sig_end]).decode("utf-8", errors="replace").strip()
    sym = {
        "name": name_text,
        "kind": kind,
        "line": start_line,
        "end_line": end_line,
        "signature": signature,
    }
    if include_body:
        sym["body"] = source[def_node.start_byte:def_node.end_byte].decode("utf-8", errors="replace")
    return sym


def extract_symbols(
    source: bytes,
    lang_key: str,
    pattern_filter: Optional[str] = None,
    kind_filter: Optional[str] = None,
    include_body: bool = False,
) -> List[dict]:
    """Extract symbols from source code using tree-sitter queries.

    Returns a list of dicts with keys:
        - name: symbol name
        - kind: function, class, method, interface, type, enum, struct, trait, etc.
        - line: start line (1-indexed)
        - end_line: end line (1-indexed)
        - signature: first line text
        - body: source text of the body (if include_body=True)
    """
    from tree_sitter import QueryCursor

    result = _setup_query(lang_key)
    if result is None:
        return []
    parser, lang, query = result

    tree = parser.parse(source)
    qc = QueryCursor(query)
    seen: set = set()
    symbols: List[dict] = []
    source_lines = source.split(b"\\n")

    for _pattern_idx, captures_dict in qc.matches(tree.root_node):
        name_nodes = captures_dict.get("name", [])
        directive_nodes = captures_dict.get("directive")
        def_nodes = (
            captures_dict.get("def")
            or captures_dict.get("constant")
            or captures_dict.get("field")
            or captures_dict.get("arrow")
        )

        # Handle directives ("use client", "use server")
        if directive_nodes and not name_nodes and not def_nodes:
            directive_node = directive_nodes[0]
            dir_text = directive_node.text.decode("utf-8", errors="replace").strip('"')
            if dir_text in ("use client", "use server"):
                sym = {
                    "name": dir_text,
                    "kind": "directive",
                    "line": directive_node.start_point[0] + 1,
                    "end_line": directive_node.end_point[0] + 1,
                    "signature": dir_text,
                }
                symbols.append(sym)
            continue

        if not name_nodes:
            continue

        name_node = name_nodes[0]
        if def_nodes:
            def_node = def_nodes[0]
        else:
            def_node = name_node.parent
            if def_node is None:
                continue

        name_text = name_node.text.decode("utf-8", errors="replace")
        key = (name_text, def_node.start_point[0])
        if key in seen:
            continue
        seen.add(key)

        kind = _classify_symbol_kind(def_node)
        kind = _detect_if_method(def_node, kind)

        # React-specific classification for TSX:
        # PascalCase function → component
        # useXxx function → hook
        if lang_key == "tsx" and kind == "function":
            if name_text[0].isupper():
                kind = "component"
            elif name_text.startswith("use") and len(name_text) > 3 and name_text[3].isupper():
                kind = "hook"

        if kind_filter and kind_filter != "all" and kind != kind_filter:
            continue
        if pattern_filter and pattern_filter.lower() not in name_text.lower():
            continue

        sym = _extract_candidate(def_node, name_node, source, source_lines, kind, include_body)
        symbols.append(sym)

    symbols.sort(key=lambda s: s["line"])
    return symbols


def _format_symbols_output(
    file_path: str,
    symbols: List[dict],
    total_lines: int,
    lang_key: str,
) -> str:
    """Format extracted symbols into a compact, token-efficient string."""
    if not symbols:
        return fmt_ok({
            "path": file_path,
            "language": lang_key,
            "total_lines": total_lines,
            "symbols": [],
            "message": "No symbols found. File may be empty or language not supported.",
        })

    lines = []
    lines.append(f"{file_path} ({total_lines} lines, {lang_key})")

    # Group by kind for readability
    current_kind = None
    for sym in symbols:
        if sym["kind"] != current_kind:
            current_kind = sym["kind"]
            lines.append(f"  [{current_kind}]")
        sig = sym["signature"]
        # Truncate long signatures
        if len(sig) > 120:
            sig = sig[:117] + "..."
        lines.append(f"  L{sym['line']:>4d}  {sym['name']}  {sig}")

    return fmt_ok({
        "path": file_path,
        "language": lang_key,
        "total_lines": total_lines,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "formatted": "\n".join(lines),
    })


# ---------------------------------------------------------------------------
# code_symbols tool implementation
# ---------------------------------------------------------------------------

def code_symbols_tool(
    path: str,
    pattern: Optional[str] = None,
    kind: Optional[str] = None,
    include_body: bool = False,
    language: Optional[str] = None,
    max_results: int = 200,
) -> str:
    """Extract symbols from source files using tree-sitter AST parsing."""
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Code intelligence dependencies are not installed. Please run: uv pip install 'hermes-agent[code-intel]'")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_dir():
        return _symbols_scan_directory(target, language, pattern, kind, max_results)

    # Single file
    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return fmt_err(f"Unsupported language for '{path}'. "
                f"Supported extensions: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            )

    symbols, total_lines = _symbols_extract_single(target, lang_key, pattern, kind, include_body, max_results)
    return _format_symbols_output(str(target), symbols, total_lines, lang_key)


def _symbols_extract_single(
    target: Path, lang_key: str,
    pattern: Optional[str], kind: Optional[str], include_body: bool,
    max_results: Optional[int] = None,
) -> tuple[List[dict], int]:
    """Extract symbols from a single file with caching."""
    mtime = target.stat().st_mtime
    cache_key = f"{str(target)}|{mtime}|{lang_key}|{pattern or ''}|{kind or ''}|{include_body}"

    if cache_key in _SYMBOL_CACHE:
        symbols = _SYMBOL_CACHE[cache_key]
        total_lines = target.read_bytes().count(b"\n") + 1
    else:
        source = target.read_bytes()
        total_lines = source.count(b"\n") + 1
        symbols = extract_symbols(
            source, lang_key,
            pattern_filter=pattern,
            kind_filter=kind,
            include_body=include_body,
        )
        _set_cache(cache_key, symbols)
    if max_results is not None and max_results > 0 and len(symbols) > max_results:
        symbols = symbols[:max_results]
    return symbols, total_lines


def _symbols_scan_directory(
    target: Path, language: Optional[str],
    pattern: Optional[str], kind: Optional[str],
    max_results: int = 200,
) -> str:
    """Scan all supported files in a directory for symbols."""
    # --- Directory-level caching ---
    dir_cache_key = f"{str(target.resolve())}|{str(language)}|{str(pattern or '')}|{str(kind or '')}|{max_results}"

    if dir_cache_key in _DIR_SYMBOL_CACHE:
        entry = _DIR_SYMBOL_CACHE[dir_cache_key]
        all_valid = True
        for fp_str, cached_mtime in entry["files"].items():
            try:
                if Path(fp_str).stat().st_mtime != cached_mtime:
                    all_valid = False
                    break
            except OSError:
                all_valid = False
                break
        if all_valid:
            return entry["result"]
        # Stale cache entry — discard
        del _DIR_SYMBOL_CACHE[dir_cache_key]

    results = []
    all_symbols = []
    count = 0
    done = False
    dir_files = {}  # {file_path: mtime} for directory-level caching

    for ext in _EXT_TO_LANG:
        if done:
            break
        for file_path in sorted(target.rglob(f"*{ext}")):
            if not file_path.is_file():
                continue
            file_lang = detect_language(str(file_path), language)
            if file_lang is None:
                continue
            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                continue

            # Track for dir cache
            dir_files[str(file_path)] = mtime

            cache_key = f"{str(file_path)}|{mtime}|{file_lang}|{pattern or ''}|{kind or ''}|False"
            if cache_key in _SYMBOL_CACHE:
                syms = _SYMBOL_CACHE[cache_key]
                try:
                    source = file_path.read_bytes()
                except OSError:
                    continue
            else:
                try:
                    source = file_path.read_bytes()
                except OSError:
                    continue
                syms = extract_symbols(
                    source, file_lang,
                    pattern_filter=pattern,
                    kind_filter=kind,
                    include_body=False,
                )
                _set_cache(cache_key, syms)

            if not syms:
                continue

            # Apply max_results limit per batch of symbols
            if max_results > 0:
                available = max_results - count
                if available <= 0:
                    done = True
                    break
                if len(syms) > available:
                    syms = syms[:available]

            results.append({
                "path": str(file_path),
                "language": file_lang,
                "total_lines": source.count(b"\n") + 1,
                "symbol_count": len(syms),
                "symbols": syms,
            })
            for s in syms:
                s["file"] = str(file_path)
                all_symbols.append(s)
            count += len(syms)

            if max_results > 0 and count >= max_results:
                done = True
                break

    if not results:
        return fmt_ok({
            "path": str(target),
            "message": "No symbols found in directory scan.",
            "supported_extensions": sorted(set(_EXT_TO_LANG.values())),
        })

    lines = [f"Directory: {target} ({len(results)} files with symbols)"]
    for r in results:
        lines.append(f"\n{r['path']} ({r['total_lines']} lines, {r['language']})")
        for sym in r["symbols"]:
            sig = sym["signature"]
            if len(sig) > 100:
                sig = sig[:97] + "..."
            lines.append(f"  L{sym['line']:>4d}  [{sym['kind']}] {sym['name']}  {sig}")

    result_str = fmt_ok({
        "path": str(target),
        "file_count": len(results),
        "total_symbols": len(all_symbols),
        "results": results,
        "formatted": "\n".join(lines),
    })

    # --- Populate directory cache ---
    _set_dir_cache(dir_cache_key, {
        "files": dir_files,
        "result": result_str,
    })

    return result_str


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

CODE_SYMBOLS_SCHEMA = {
    "name": "code_symbols",
    "description": (
        "AST-powered symbol extraction — get a structured index of functions, classes, "
        "methods, interfaces, types, enums, structs, traits from any source file. "
        "Use this INSTEAD of read_file when you need to understand what a file contains "
        "(what functions exist, what classes define which methods, where things are). "
        "Returns line numbers, signatures, and symbol kinds. Pass a directory to index "
        "all files at once. Supports Python, TypeScript, TSX, JavaScript, Rust, Go, Java."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory path to extract symbols from"},
            "pattern": {"type": "string", "description": "Fuzzy symbol name filter (optional, substring match)"},
            "kind": {
                "type": "string",
                "enum": ["all", "function", "class", "method", "interface", "type", "enum", "struct", "trait", "constant", "variable", "module"],
                "description": "Filter by symbol kind (default: all)",
            },
            "include_body": {"type": "boolean", "description": "Include function/method body text (default: false, only for single file)"},
            "language": {"type": "string", "description": "Override language auto-detection (e.g. 'python', 'typescript')"},
            "max_results": {"type": "integer", "description": "Maximum results to return (default: 200, use 0 for unlimited)"},
        },
        "required": ["path"],
    },
}


def _check_code_intel_reqs() -> bool:
    """Always return True so the tools are visible, but fail gracefully."""
    return True


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def _handle_code_symbols(args, **kw):
    return code_symbols_tool(
        path=args.get("path", ""),
        pattern=args.get("pattern"),
        kind=args.get("kind"),
        include_body=args.get("include_body", False),
        language=args.get("language"),
        max_results=args.get("max_results", 200),
    )
