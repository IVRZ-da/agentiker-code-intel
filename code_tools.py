#!/usr/bin/env python3
"""Code Intelligence Tools Module.

AST-aware code analysis tools using tree-sitter and ast-grep.
Provides structural symbol extraction, pattern search, and safe refactoring.

Token-efficient alternative to reading entire files for code navigation.
"""

import json
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

from ._fmt import fmt_err, fmt_json, fmt_ok  # fmt_info unused after split
from ._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# Language registry — maps file extensions → tree-sitter Language objects
# Lazy-loaded on first use to avoid slow imports at module level.
# ---------------------------------------------------------------------------

_LANG_LOCK = threading.Lock()
_LANG_CACHE: Dict[str, object] = {}  # ext → Language
_PARSER_CACHE: Dict[str, object] = {}  # lang_key → Parser
_LANG_READY = False
_SYMBOL_CACHE = OrderedDict()

# Directory-level cache for _symbols_scan_directory.
# Key:   resolved_path|lang|pattern|kind|max_results
# Value: {"mtime": float, "files": {path: mtime}, "result": str}
_DIR_SYMBOL_CACHE = OrderedDict()

# ---------------------------------------------------------------------------
# Persistent symbol index (B5) — saves/loads AST cache to disk
# ---------------------------------------------------------------------------
_PERSIST_DIR = os.path.expanduser("~/.hermes/plugins/code_intel/.cache")
_PERSIST_VERSION = 2  # bump to invalidate stale caches

# ---------------------------------------------------------------------------
# Extension → language key mapping
# ---------------------------------------------------------------------------

_EXT_TO_LANG = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}

# Supported languages for ast-grep (subset — only those with grammars)

# ---------------------------------------------------------------------------
# tree-sitter symbol queries per language
# ---------------------------------------------------------------------------

_SYMBOL_QUERIES = {
    "python": """
        ; Functions (sync and async) — catches top-level AND bare methods
        ; (method detection happens in extract_symbols via parent chain)
        (function_definition
            name: (identifier) @name
        ) @def

        ; Classes
        (class_definition
            name: (identifier) @name
        ) @def

        ; Module-level assignments that look like constants (UPPER_CASE)
        (assignment
            left: (identifier) @name
        ) @constant

        ; Decorated functions/classes (including decorated methods inside classes)
        (decorated_definition
            definition: (function_definition
                "async"? @keyword
                name: (identifier) @name
            ) @def
        )

        (decorated_definition
            definition: (class_definition
                name: (identifier) @name
            ) @def
        )
    """,
    "typescript": """
        ; Functions (sync and async)
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Arrow functions assigned to variables (const/let)
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Arrow functions assigned to variables (var)
        (variable_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Classes
        (class_declaration
            name: (type_identifier) @name
        ) @def

        ; Interfaces
        (interface_declaration
            name: (type_identifier) @name
        ) @def

        ; Type aliases
        (type_alias_declaration
            name: (type_identifier) @name
        ) @def

        ; Enums
        (enum_declaration
            name: (identifier) @name
        ) @def

        ; Export statements wrapping the above
        (export_statement
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            (class_declaration
                name: (type_identifier) @name
            ) @def
        )

        (export_statement
            (interface_declaration
                name: (type_identifier) @name
            ) @def
        )

        ; Class methods (including decorated — decorator is a sibling, not parent)
        (method_definition
            name: (property_identifier) @name
        ) @def
    """,
    "tsx": """
        ; Same as typescript plus component detection
        ; Functions (sync and async)
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Arrow functions (const/let)
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Arrow functions (var)
        (variable_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        (class_declaration
            name: (type_identifier) @name
        ) @def

        (interface_declaration
            name: (type_identifier) @name
        ) @def

        (type_alias_declaration
            name: (type_identifier) @name
        ) @def

        (enum_declaration
            name: (identifier) @name
        ) @def

        ; "use client" / "use server" directives
        (expression_statement
            (string
                (string_fragment) @name
            )
        ) @directive

        ; Export default function/class (has "default" keyword child)
        (export_statement
            "default"
            .
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            "default"
            .
            (class_declaration
                name: (type_identifier) @name
            ) @def
        )

        ; Named exports
        (export_statement
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            (class_declaration
                name: (type_identifier) @name
            ) @def
        )

        (export_statement
            (interface_declaration
                name: (type_identifier) @name
            ) @def
        )

        ; Class methods (including decorated)
        (method_definition
            name: (property_identifier) @name
        ) @def
    """,
    "javascript": """
        ; Functions (sync and async — async is a keyword child, handled automatically)
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Arrow functions (const/let)
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Arrow functions (var)
        (variable_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Classes
        (class_declaration
            name: (identifier) @name
        ) @def

        ; Class methods (including decorated)
        (method_definition
            name: (property_identifier) @name
        ) @def

        ; Export statements
        (export_statement
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            (class_declaration
                name: (identifier) @name
            ) @def
        )
    """,
    "rust": """
        ; Functions (matches both sync and async — async is a function_modifiers child)
        (function_item
            name: (identifier) @name
        ) @def

        ; Structs
        (struct_item
            name: (type_identifier) @name
        ) @def

        ; Enums
        (enum_item
            name: (type_identifier) @name
        ) @def

        ; Traits
        (trait_item
            name: (type_identifier) @name
        ) @def

        ; impl blocks — methods
        (impl_item
            body: (declaration_list
                (function_item
                    name: (identifier) @name
                ) @def
            )
        )

        ; impl blocks for traits
        (impl_item
            trait: (type_identifier) @trait_name
            type: (type_identifier) @impl_for
            body: (declaration_list
                (function_item
                    name: (identifier) @name
                ) @def
            )
        )

        ; Constants
        (const_item
            name: (identifier) @name
        ) @constant

        ; Type aliases
        (type_item
            name: (type_identifier) @name
        ) @def

        ; Mods
        (mod_item
            name: (identifier) @name
        ) @def
    """,
    "go": """
        ; Functions
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Methods (receiver functions)
        (method_declaration
            name: (field_identifier) @name
        ) @def

        ; Structs
        (type_declaration
            (type_spec
                name: (type_identifier) @name
                type: (struct_type)
            )
        ) @def

        ; Interfaces
        (type_declaration
            (type_spec
                name: (type_identifier) @name
                type: (interface_type)
            )
        ) @def

        ; Type aliases
        (type_declaration
            (type_spec
                name: (type_identifier) @name
            )
        ) @def

        ; Variables
        (var_declaration
            (var_spec
                name: (identifier) @name
            )
        ) @constant
    """,
    "java": """
        ; Classes
        (class_declaration
            name: (identifier) @name
        ) @def

        ; Interfaces
        (interface_declaration
            name: (identifier) @name
        ) @def

        ; Enums
        (enum_declaration
            name: (identifier) @name
        ) @def

        ; Methods
        (class_declaration
            body: (class_body
                (method_declaration
                    name: (identifier) @name
                ) @def
            )
        )

        ; Fields
        (class_declaration
            body: (class_body
                (field_declaration
                    (variable_declarator
                        name: (identifier) @name
                    )
                ) @field
            )
        )
    """,
}

# Node types that indicate specific symbol kinds
_NODE_KIND_MAP = {
    "function_definition": "function",
    "function_declaration": "function",
    "function_item": "function",
    "arrow_function": "function",
    "class_definition": "class",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "enum_item": "enum",
    "struct_item": "struct",
    "struct_type": "struct",
    "interface_type": "interface",
    "trait_item": "trait",
    "type_item": "type",
    "type_alias": "type",
    "type_spec": "type",
    "method_definition": "method",
    "method_declaration": "method",
    "impl_item": "impl",
    "mod_item": "module",
    "assignment": "variable",
    "variable_declaration": "variable",
    "const_item": "constant",
    "constant_item": "constant",
    "var_declaration": "variable",
    "var_spec": "variable",
    "field_declaration": "field",
}
# ---------------------------------------------------------------------------

# Language → AST query for class extends/implements detection
_TYPE_HIERARCHY_FALLBACK_LANGS = {"python", "typescript", "tsx", "javascript"}

_PYTHON_CLASS_EXTENDS = """
(class_definition
    name: (identifier) @class_name
    (argument_list
        (identifier) @extends_name
    )
) @class_def
"""

_TS_CLASS_EXTENDS = """
; class Foo extends Bar { }
(class_declaration
    name: (type_identifier) @class_name
    (class_heritage
        (identifier) @extends_name
    )
) @class_def

; interface Foo extends Bar { }
(interface_declaration
    name: (type_identifier) @class_name
    (class_heritage
        (identifier) @extends_name
    )
) @class_def
"""

# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


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


def _classify_symbol_kind(def_node) -> str:
    """Determine symbol kind from AST node type.

    Handles ``decorated_definition`` (unwraps to inner node) and Go
    ``type_spec`` with struct/interface children.
    """
    kind = _NODE_KIND_MAP.get(def_node.type, "symbol")
    if def_node.type == "decorated_definition" and kind == "symbol":
        for child in def_node.children:
            inner_kind = _NODE_KIND_MAP.get(child.type)
            if inner_kind:
                kind = inner_kind
                break
    if def_node.type == "type_spec" and kind == "symbol":
        for child in def_node.children:
            child_kind = _NODE_KIND_MAP.get(child.type)
            if child_kind in ("struct", "interface"):
                kind = child_kind
                break
    return kind


def _detect_if_method(def_node, current_kind: str) -> str:
    """Walk up the parent chain (max 4 levels) to detect if this
    function is actually a method (inside a class/struct/impl body).
    """
    kind = current_kind
    if kind != "function":
        return kind
    _cur = def_node.parent
    _depth = 0
    while _cur and _depth < 4:
        _par = _cur.parent
        if _cur.type == "block" and _par and _par.type == "class_definition":
            kind = "method"
            break
        elif _cur.type in ("class_body", "declaration_list"):
            if _par and _par.type in (
                "class_declaration", "class_definition",
                "impl_item", "struct_item",
            ):
                kind = "method"
            break
        elif _cur.type in ("decorated_definition", "abstract_method_declaration"):
            _cur = _par
            _depth += 1
            continue
        break
    return kind


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


# ---------------------------------------------------------------------------
# code_search — AST-aware structural search via tree-sitter Query
# ---------------------------------------------------------------------------

# Preset queries for common patterns (user can also pass raw queries)
_CODE_SEARCH_PRESETS = {
    "function_calls": {
        "python": "(call function: (identifier) @func) @call",
        "typescript": "(call_expression function: (identifier) @func) @call",
        "javascript": "(call_expression function: (identifier) @func) @call",
        "rust": "(call_expression function: (identifier) @func) @call",
        "go": "(call_expression function: (identifier) @func) @call",
        "java": "(method_invocation name: (identifier) @func) @call",
    },
    "string_literals": {
        "python": '(string) @str',
        "typescript": '(string) @str',
        "javascript": '(string) @str',
        "rust": '(string_literal) @str',
        "go": '(interpreted_string_literal) @str',
        "java": '(string_literal) @str',
    },
    "imports": {
        "python": "(import_statement) @import\n(import_from_statement) @import",
        "typescript": "(import_statement) @import",
        "javascript": "(import_statement) @import",
        "rust": "(use_declaration) @import",
        "go": "(import_declaration) @import",
        "java": "(import_declaration) @import",
    },
    "decorator_calls": {
        "python": "(decorator) @deco",
        "typescript": "(decorator) @deco",
        "javascript": "(decorator) @deco",
    },
    "try_catch": {
        "python": "(try_statement) @tc",
        "typescript": "(try_statement) @tc",
        "javascript": "(try_statement) @tc",
        "java": "(try_statement) @tc",
    },
    "return_stmts": {
        "python": "(return_statement) @ret",
        "typescript": "(return_statement) @ret",
        "javascript": "(return_statement) @ret",
        "rust": "(return_expression) @ret",
        "go": "(return_statement) @ret",
        "java": "(return_statement) @ret",
    },
    "assignments": {
        "python": "(assignment left: (_) @lhs right: (_) @rhs) @assign",
        "typescript": "(assignment_expression left: (_) @lhs right: (_) @rhs) @assign",
        "javascript": "(assignment_expression left: (_) @lhs right: (_) @rhs) @assign",
        "go": "(short_var_declaration left: (_) @lhs right: (_) @rhs) @assign",
    },
}

# Alias presets to common names
_PRESET_ALIASES = {
    "calls": "function_calls",
    "strings": "string_literals",
    "imports": "imports",
    "decorators": "decorator_calls",
    "try": "try_catch",
    "catch": "try_catch",
    "returns": "return_stmts",
    "assigns": "assignments",
}


def _resolve_preset(preset: str, lang_key: str) -> Optional[str]:
    """Resolve a preset name to a tree-sitter query string."""
    canonical = _PRESET_ALIASES.get(preset, preset)
    lang_queries = _CODE_SEARCH_PRESETS.get(canonical)
    if lang_queries is None:
        return None
    return lang_queries.get(lang_key)


def code_search_tool(
    path: str,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
    _raw: bool = False,
) -> str:

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Code intelligence dependencies are not installed. Please run: uv pip install 'hermes-agent[code-intel]'")
    """AST-aware structural code search using tree-sitter Query API.

    Supports three modes:
    1. Raw tree-sitter query (via 'query' param)
    2. Named preset like 'function_calls', 'imports', 'try_catch', etc.
    3. Simple text pattern filter on captured nodes (via 'pattern' param)

    Accepts both files and directories (recursive scan of supported files).
    """
    target = Path(path).expanduser().resolve()

    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_file():
        return _code_search_single_file(target, query, preset, pattern, language, max_results, _raw=_raw)

    # Directory: scan all supported files recursively
    return _code_search_directory(target, query, preset, pattern, language, max_results, _raw=_raw)


def _code_search_single_file(
    target: Path,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
    _raw: bool = False,
) -> str:
    """Run code_search on a single file."""
    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return fmt_err(f"Unsupported language for '{target}'. "
                f"Supported: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            )

    query_str = _resolve_query(query, preset, pattern, lang_key, str(target))
    if isinstance(query_str, str) and query_str.startswith("{"):
        return query_str  # error JSON

    parser = _get_parser(lang_key)
    lang = _get_language(lang_key)
    if parser is None or lang is None:
        return fmt_err(f"No tree-sitter grammar for {lang_key}")

    source = target.read_bytes()
    tree = parser.parse(source)

    try:
        from tree_sitter import Query, QueryCursor
        ts_query = Query(lang, query_str)
    except Exception as e:
        return fmt_err(f"Invalid tree-sitter query: {e}")

    qc = QueryCursor(ts_query)
    results = []
    seen_spans = set()

    for _pat_idx, captures_dict in qc.matches(tree.root_node):
        for cap_name, nodes in captures_dict.items():
            for node in nodes:
                row, col = node.start_point
                end_row, end_col = node.end_point
                span = (row, col, end_row, end_col)

                if span in seen_spans:
                    continue
                seen_spans.add(span)

                text = node.text.decode("utf-8", errors="replace")

                if pattern and pattern.lower() not in text.lower():
                    continue

                display = text if len(text) <= 200 else text[:197] + "..."

                results.append({
                    "capture": cap_name,
                    "text": display,
                    "line": row + 1,
                    "end_line": end_row + 1,
                    "column": col,
                    "kind": node.type,
                })

                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    truncated = len(results) >= max_results
    data = {
        "path": str(target),
        "language": lang_key,
        "query": query_str[:200],
        "match_count": len(results),
        "truncated": truncated,
        "results": results,
    }
    return json.dumps(data) if _raw else fmt_ok(data)


def _code_search_directory(
    target: Path,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
    _raw: bool = False,
) -> str:
    """Run code_search across all supported files in a directory."""
    results = []
    files_scanned = 0
    remaining = max_results

    for ext in _EXT_TO_LANG:
        if remaining <= 0:
            break
        for file_path in sorted(target.rglob(f"*{ext}")):
            if remaining <= 0:
                break
            if not file_path.is_file():
                continue
            file_lang = detect_language(str(file_path), language)
            if file_lang is None:
                continue

            file_matches = _search_single_file(file_path, file_lang, query, preset,
                                                pattern, remaining)
            if file_matches is None:
                continue

            files_scanned += 1
            results.extend(file_matches["results"])
            remaining = file_matches["remaining"]

    if not results:
        data = {
            "path": str(target),
            "message": "No matches found in directory.",
            "files_scanned": files_scanned,
            "files_with_matches": 0,
            "match_count": 0,
        }
        return json.dumps(data) if _raw else fmt_ok(data)

    data = {
        "path": str(target),
        "files_scanned": files_scanned,
        "files_with_matches": len({r["file"] for r in results}),
        "match_count": len(results),
        "results": results,
    }
    return json.dumps(data) if _raw else fmt_ok(data)


def _search_single_file(
    file_path: Path, file_lang: str,
    query: Optional[str], preset: Optional[str],
    pattern: Optional[str], remaining: int,
) -> Optional[dict]:
    """Search a single file with the given query. Returns result dict or None."""
    query_str = _resolve_query(query, preset, pattern, file_lang, str(file_path))
    if isinstance(query_str, str) and query_str.startswith("{"):
        return None

    parser = _get_parser(file_lang)
    lang = _get_language(file_lang)
    if parser is None or lang is None:
        return None

    try:
        source = file_path.read_bytes()
    except (OSError, PermissionError):
        return None

    tree = parser.parse(source)
    try:
        from tree_sitter import Query, QueryCursor
        ts_query = Query(lang, query_str)
    except Exception:
        return None

    qc = QueryCursor(ts_query)
    file_results = []

    for _pat_idx, captures_dict in qc.matches(tree.root_node):
        if remaining <= 0:
            break
        file_results, remaining = _process_match_captures(
            captures_dict, file_path, pattern, file_results, remaining
        )
        if remaining <= 0:
            break

    if not file_results:
        return None

    return {"results": file_results, "remaining": remaining}


def _process_match_captures(
    captures_dict: dict, file_path: Path,
    pattern: Optional[str], file_results: list, remaining: int,
) -> tuple[list, int]:
    """Process captures from a tree-sitter match. Returns (results, remaining)."""
    seen_spans = set()
    for cap_name, nodes in captures_dict.items():
        if remaining <= 0:
            break
        for node in nodes:
            if remaining <= 0:
                break

            row, col = node.start_point
            end_row, end_col = node.end_point
            span = (row, col, end_row, end_col)
            if span in seen_spans:
                continue
            seen_spans.add(span)

            text = node.text.decode("utf-8", errors="replace")
            if pattern and pattern.lower() not in text.lower():
                continue

            display = text if len(text) <= 200 else text[:197] + "..."
            file_results.append({
                "file": str(file_path),
                "capture": cap_name,
                "text": display,
                "line": row + 1,
                "end_line": end_row + 1,
                "column": col,
                "kind": node.type,
            })
            remaining -= 1
    return file_results, remaining
def _resolve_query(
    query: Optional[str],
    preset: Optional[str],
    pattern: Optional[str],
    lang_key: str,
    file_path: str,
) -> str:
    """Resolve query string from query/preset/pattern. Returns JSON error string on failure."""
    if query:
        return query
    elif preset:
        query_str = _resolve_preset(preset, lang_key)
        if query_str is None:
            available = sorted(_CODE_SEARCH_PRESETS.keys()) + sorted(_PRESET_ALIASES.keys())
            return fmt_ok({
                "error": f"Unknown preset '{preset}' for {lang_key} ({file_path}). "
                         f"Available: {', '.join(available)}",
            })
        return query_str
    elif pattern:
        return "(_) @node"
    else:
        return fmt_err("Provide 'query', 'preset', or 'pattern'. "
                     "Presets: function_calls, string_literals, imports, "
                     "decorator_calls, try_catch, return_stmts, assignments.")


CODE_SEARCH_SCHEMA = {
    "name": "code_search",
    "description": (
        "AST-aware structural code search — find function calls, imports, decorators, "
        "try/catch blocks, return statements, assignments by their semantic structure, "
        "not just text. Use this INSTEAD of search_files (grep) when searching for code "
        "patterns inside source files — it understands syntax and won't match comments "
        "or strings by accident. Accepts files and directories (recursive scan). "
        "Use named presets: function_calls, imports, decorator_calls, try_catch, "
        "return_stmts, string_literals, assignments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "query": {"type": "string", "description": "Raw tree-sitter query string (e.g. '(call function: (identifier) @func) @call')"},
            "preset": {"type": "string", "description": "Named preset: function_calls, string_literals, imports, decorator_calls, try_catch, return_stmts, assignments"},
            "pattern": {"type": "string", "description": "Simple text pattern to filter captured nodes (substring match)"},
            "language": {"type": "string", "description": "Override language auto-detection"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default: 50)"},
        },
        "required": ["path"],
    },
}


def _handle_code_search(args, **kw):
    return code_search_tool(
        path=args.get("path", ""),
        query=args.get("query"),
        preset=args.get("preset"),
        pattern=args.get("pattern"),
        language=args.get("language"),
        max_results=args.get("max_results", 50),
    )


# ---------------------------------------------------------------------------
# code_refactor — ast-grep structural search & replace (dry-run default)
# ---------------------------------------------------------------------------

def _code_refactor_single_file(
    target: Path,
    pattern: str,
    rewrite: str,
    lang_key: str,
    dry_run: bool,
    context_lines: int,
) -> dict:
    """Run ast-grep on a single file. Returns result dict (never raises)."""
    ag_lang = _AST_GREP_LANG_MAP.get(lang_key)
    if ag_lang is None:
        return {"path": str(target), "language": lang_key, "error": f"ast-grep does not support {lang_key}"}

    try:
        import ast_grep_py as sg
    except ImportError:
        return {"path": str(target), "error": "ast-grep-py not installed. Please run: uv pip install 'hermes-agent[code-intel]'"}

    source = target.read_text(encoding="utf-8", errors="replace")
    source_lines = source.split("\n")

    try:
        root = sg.SgRoot(source, ag_lang)
    except Exception as e:
        return {"path": str(target), "language": lang_key, "error": f"Failed to parse source: {e}"}

    try:
        matches = list(root.root().find_all(pattern=pattern))
    except Exception as e:
        return {"path": str(target), "language": lang_key, "error": f"Invalid pattern or no matches: {e}"}

    if not matches:
        return {
            "path": str(target),
            "language": lang_key,
            "pattern": pattern,
            "match_count": 0,
            "changes": [],
        }

    changes = _build_refactor_changes(matches, source_lines, pattern, rewrite, context_lines)
    applied = _apply_refactor_changes(changes, matches, source_lines, target, dry_run)
    if isinstance(applied, dict):
        return applied

# Apply changes if not dry-run
    return {
        "path": str(target),
        "language": lang_key,
        "pattern": pattern,
        "rewrite": rewrite,
        "dry_run": dry_run,
        "match_count": len(changes),
        "applied": applied,
        "changes": changes,
    }


def _code_refactor_directory(
    target: Path,
    pattern: str,
    rewrite: str,
    language: Optional[str],
    dry_run: bool,
    context_lines: int,
    file_glob: Optional[str] = None,
) -> str:
    """Recursively refactor files in a directory."""
    files_scanned = 0
    files_changed = 0
    total_matches = 0
    errors = []
    file_results = []

    # Collect files — grouped by language key for efficiency
    ext_lang_map = {}
    for ext, lang in _EXT_TO_LANG.items():
        ext_lang_map.setdefault(lang, []).append(f"*{ext}")

    for lang_key, globs in ext_lang_map.items():
        ag_lang = _AST_GREP_LANG_MAP.get(lang_key)
        if ag_lang is None:
            continue  # Skip languages ast-grep doesn't support
        for glob_pat in globs:
            if file_glob:
                for f in sorted(target.rglob(f"{file_glob}{glob_pat.lstrip('*')}")):
                    if f.is_file():
                        result = _code_refactor_single_file(
                            f, pattern, rewrite, lang_key, dry_run, context_lines,
                        )
                        files_scanned += 1
                        file_results.append(result)
            else:
                for f in sorted(target.rglob(glob_pat)):
                    if f.is_file():
                        result = _code_refactor_single_file(
                            f, pattern, rewrite, lang_key, dry_run, context_lines,
                        )
                        files_scanned += 1
                        file_results.append(result)

    # Summarize results
    for r in file_results:
        if "error" in r:
            errors.append({"path": r["path"], "error": r["error"]})
        else:
            mc = r.get("match_count", 0)
            total_matches += mc
            if mc > 0:
                files_changed += 1

    return fmt_ok({
        "path": str(target),
        "pattern": pattern,
        "rewrite": rewrite,
        "dry_run": dry_run,
        "files_scanned": files_scanned,
        "files_changed": files_changed,
        "match_count": total_matches,
        "errors": len(errors),
        "results": file_results,
    })


def code_refactor_tool(
    path: str,
    pattern: str,
    rewrite: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    context_lines: int = 1,
    file_glob: Optional[str] = None,
) -> str:
    """Structural search and replace using ast-grep.

    Matches AST patterns (not text) and replaces them. Dry-run by default.
    Supports ast-grep meta variables: $NAME for single nodes, $$BODY for multiple nodes.
    Supports both files and directories (recursive scan across supported languages).
    """
    target = Path(path).expanduser().resolve()

    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_dir():
        if language:
            # Language override with directory — warn but proceed (applied per-file)
            pass
        return _code_refactor_directory(
            target, pattern, rewrite, language, dry_run, context_lines, file_glob,
        )

    # Single file path
    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return fmt_err(f"Unsupported language for '{path}'. "
                f"Supported: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            )

    result = _code_refactor_single_file(target, pattern, rewrite, lang_key, dry_run, context_lines)
    return fmt_ok(result)


CODE_REFACTOR_SCHEMA = {
    "name": "code_refactor",
    "description": (
        "AST-aware structural search and replace — matches code by syntax tree structure, "
        "not raw text. Use this INSTEAD of patch when doing bulk refactoring across files or directories "
        "(rename patterns, wrap functions, add parameters, change decorators, etc.). "
        "Supports meta variables: $NAME for single nodes, $$BODY for multi-node captures. "
        "DRY-RUN by default — set dry_run=false to apply. "
        "Supports both files and directories (recursive scan across all supported languages). "
        "Supports Python, TypeScript, TSX, JavaScript, Rust, Go, Java, C, C++."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "pattern": {"type": "string", "description": "ast-grep pattern (e.g. 'console.log($ARG)', 'def $NAME($$$ARGS): $$$BODY')"},
            "rewrite": {"type": "string", "description": "Replacement template with meta variables (e.g. 'console.info($ARG)')"},
            "language": {"type": "string", "description": "Override language auto-detection (single file only)"},
            "dry_run": {"type": "boolean", "description": "Preview changes without writing (default: true)"},
            "context_lines": {"type": "integer", "description": "Lines of context around each match (default: 1)"},
            "file_glob": {"type": "string", "description": "Filter files by glob pattern in directory mode (e.g. '*.service.ts', '*_test.py')"},
        },
        "required": ["path", "pattern", "rewrite"],
    },
}


def _handle_code_refactor(args, **kw):
    return code_refactor_tool(
        path=args.get("path", ""),
        pattern=args.get("pattern", ""),
        rewrite=args.get("rewrite", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        context_lines=args.get("context_lines", 1),
        file_glob=args.get("file_glob"),
    )


# ---------------------------------------------------------------------------
# Composite tools — code_capsule (one-shot symbol summary)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# code_explain_tool — Structured symbol explanation
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
    from ._fmt import _strip_ansi

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


# ---------------------------------------------------------------------------
# B1: code_workspace_summary — Monorepo/Project overview
# ---------------------------------------------------------------------------


# Extension-to-language mapping for workspace summary
_EXT_LANG = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "typescript",
}
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# B2a: code_search_by_error — Find error handling sites
# ---------------------------------------------------------------------------

# Language-specific error detection queries
_ERROR_QUERIES: dict = {
    "python": """
; raise ValueError("msg")
(raise_statement
    (call
        function: (identifier) @error_name
    )
) @raise_site

; except ValueError:
(except_clause
    (identifier) @error_name
) @catch_site

; custom class(MyError):
(class_definition
    name: (identifier) @custom_name
    (argument_list
        (identifier) @error_name
    )
) @custom_site

; raise SomeException (without args)
(raise_statement
    (identifier) @error_name
) @raise_site
""",
    "typescript": """
; throw new Error("msg")
(throw_statement
    (new_expression
        constructor: (identifier) @error_name
    )
) @throw_site

; catch (e: Error)
(catch_clause
    (catch_parameter
        type: (type_identifier) @error_name
    )
) @catch_site

; class MyError extends Error
(class_declaration
    name: (type_identifier) @custom_name
    (class_heritage
        (identifier) @error_name
    )
) @custom_site

; throw SomeError
(throw_statement
    (identifier) @error_name
) @throw_site
""",
    "tsx": """
(throw_statement
    (new_expression
        constructor: (identifier) @error_name
    )
) @throw_site
(catch_clause
    (catch_parameter
        type: (type_identifier) @error_name
    )
) @catch_site
(class_declaration
    name: (type_identifier) @custom_name
    (class_heritage
        (identifier) @error_name
    )
) @custom_site
(throw_statement
    (identifier) @error_name
) @throw_site
""",
    "go": """
; return fmt.Errorf("msg")
(call_expression
    function: (selector_expression
        field: (field_identifier) @error_name
    )
) @return_site
""",
    "rust": """
; Err(MyError)
(match_pattern
    (identifier) @error_name
) @return_site
""",
}


# Files to exclude from search
_ERROR_EXCLUDE_DIRS = {"node_modules", ".venv", "__pycache__", ".git", ".next", "dist", "build", "target"}




# ---------------------------------------------------------------------------
# B3a: code_hot_paths — Hot Path Detection via ImportGraph
# ---------------------------------------------------------------------------
# C1: code_replace_body — Replace symbol body via AST
# ---------------------------------------------------------------------------
# C2: code_safe_delete — Delete symbol if unreferenced
# C3: code_insert_before — Insert code before a symbol
# ---------------------------------------------------------------------------
# C4: code_insert_after — Insert code after a symbol
# ---------------------------------------------------------------------------
# C5: code_overview — Compact file overview
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Unused Imports Detection — moved to tools/unused.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C12: code_move_tool — Move a symbol between files via AST extraction
# ---------------------------------------------------------------------------
# Duplicate Code Detection (C13) — AST-based duplicate/similar code finder
# ---------------------------------------------------------------------------


def code_duplicates_tool(
    path: str = ".",
    min_lines: int = 5,
    top_n: int = 20,
    max_files: int = 200,
    similarity_threshold: float = 0.8,
    timeout: int = 60,
) -> str:
    """Find duplicate/similar code blocks via AST comparison.

    Uses tree-sitter AST to find all function definitions, normalizes them
    (removing names, string literals, numbers), then detects duplicates via
    exact hash matching and string similarity with difflib.

    Args:
        path: Project root path (default: ".").
        min_lines: Minimum lines for a duplicate block (default: 5).
        top_n: Number of top duplicate groups to return (default: 20).
        max_files: Maximum number of files to scan (default: 200).
        similarity_threshold: Similarity ratio threshold for near-duplicate detection (default: 0.8).
        timeout: Maximum seconds for the search (default: 60).

    Returns:
        JSON with grouped duplicate findings.

    """
    import difflib
    import hashlib
    import time

    root = Path(path).expanduser().resolve()
    if not root.exists():
        return fmt_json({"error": f"Path not found: {path}", "duplicates": [], "total": 0})

    start_time = time.time()

    # Collect all source files
    source_files = []
    if root.is_file():
        source_files = [root]
    elif root.is_dir():
        for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"):
            for f in sorted(root.rglob(f"*{ext}")):
                rel = f.relative_to(root)
                parts = rel.parts
                if any(p in ("node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", "target") for p in parts):
                    continue
                source_files.append(f)

    # Apply max_files limit
    source_files = source_files[:max_files]

    # Collect all function definitions with their source text
    functions = []

    for f in source_files:
        # Check timeout
        if time.time() - start_time > timeout:
            break

        try:
            fpath = str(f)
            lang_key = detect_language(fpath)
            if not lang_key:
                continue
            parser = _get_parser(lang_key)
            lang_obj = _get_language(lang_key)
            if parser is None or lang_obj is None:
                continue

            with open(fpath, "rb") as fh:
                source_bytes = fh.read()
            if not source_bytes:
                continue
            source_text = source_bytes.decode("utf-8", errors="replace")

            # Skip files larger than 5000 lines
            if source_text.count("\n") > 5000:
                continue

            from tree_sitter import Query, QueryCursor

            func_query_text = _SYMBOL_QUERIES.get(lang_key, """\
                (function_definition name: (identifier) @name) @def
                (function_declaration name: (identifier) @name) @def
                (method_definition name: (property_identifier) @name) @def
            """)
            try:
                query = Query(lang_obj, func_query_text)
            except Exception:
                try:
                    query = Query(lang_obj, """\
                        (function_definition name: (identifier) @name) @def
                        (function_declaration name: (identifier) @name) @def
                    """)
                except Exception:
                    continue

            tree = parser.parse(source_bytes)
            if not tree or not tree.root_node:
                continue

            seen_names = set()
            qc = QueryCursor(query)
            for _pattern_idx, captures_dict in qc.matches(tree.root_node):
                def_nodes = captures_dict.get("def", [])
                name_nodes = captures_dict.get("name", [])
                if not def_nodes or not name_nodes:
                    continue
                def_node = def_nodes[0]
                name_node = name_nodes[0]
                name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                func_text = source_bytes[def_node.start_byte:def_node.end_byte].decode("utf-8", errors="replace")
                lines = func_text.split("\n")
                if len(lines) < min_lines:
                    continue

                start_line = source_text[:def_node.start_byte].count("\n") + 1
                functions.append({
                    "name": name,
                    "file": fpath,
                    "line": start_line,
                    "text": func_text,
                })
        except Exception:
            continue

    if len(functions) < 2:
        return fmt_json({
            "project": str(path),
            "total_functions": len(functions),
            "duplicates": [],
            "total_duplicate_groups": 0,
        })

    # Normalize function text: remove string literals, numbers, normalize whitespace
    def _normalize(text):
        # Remove function name occurrences
        # Remove string literals (triple-quoted first, then single)
        text = re.sub(r'"""[\s\S]*?"""', '"""..."""', text)
        text = re.sub(r"'''[\s\S]*?'''", "'''...'''", text)
        text = re.sub(r'"[^"]*"', '"..."', text)
        text = re.sub(r"'[^']*'", "'...'", text)
        # Remove numbers
        text = re.sub(r'\b\d+\b', 'N', text)
        # Normalize whitespace: collapse multiple spaces, strip each line
        lines_norm = []
        for line in text.split("\n"):
            line = line.strip()
            if line:
                lines_norm.append(line)
        return "\n".join(lines_norm)

    normalized = []
    for func in functions:
        ntext = _normalize(func["text"])
        h = hashlib.md5(ntext.encode()).hexdigest()
        normalized.append({
            **func,
            "normalized": ntext,
            "hash": h,
        })

    # Step 1: Exact duplicate detection via normalized hash
    hash_groups: dict = {}
    for fn in normalized:
        h = fn["hash"]
        if h not in hash_groups:
            hash_groups[h] = []
        hash_groups[h].append(fn)

    # Step 2: Near-duplicate detection via difflib for singletons
    exact_groups = [g for g in hash_groups.values() if len(g) >= 2]
    singletons = [g[0] for g in hash_groups.values() if len(g) == 1]

    similar_groups = []
    processed = set()
    for i, a in enumerate(singletons):
        if i in processed:
            continue
        group = [a]
        processed.add(i)
        for j, b in enumerate(singletons):
            if j <= i or j in processed:
                continue
            ratio = difflib.SequenceMatcher(None, a["normalized"], b["normalized"]).ratio()
            if ratio >= similarity_threshold:
                group.append(b)
                processed.add(j)
                break
        if len(group) >= 2:
            similar_groups.append(group)

    # Combine and sort all groups by size (descending)
    all_groups = exact_groups + similar_groups
    all_groups.sort(key=lambda g: len(g), reverse=True)

    # Format results
    grouped_results = []
    for group in all_groups[:top_n]:
        entries = []
        for fn in group:
            entries.append({
                "name": fn["name"],
                "file": fn["file"],
                "line": fn["line"],
            })
        grouped_results.append({
            "size": len(group),
            "functions": entries,
        })

    return fmt_json({
        "project": str(path),
        "total_functions": len(functions),
        "total_duplicate_groups": len(all_groups),
        "duplicates": grouped_results,
    })


CODE_DUPLICATES_SCHEMA = {
    "name": "code_duplicates",
    "description": "Find duplicate/similar code blocks via AST comparison.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path"},
            "min_lines": {"type": "integer", "description": "Minimum lines for a duplicate block (default: 5)"},
            "top_n": {"type": "integer", "description": "Number of top results (default: 20)"},
            "max_files": {"type": "integer", "description": "Maximum number of files to scan (default: 200)"},
            "similarity_threshold": {"type": "number", "description": "Similarity ratio threshold for near-duplicate detection (default: 0.8)"},
            "timeout": {"type": "integer", "description": "Maximum seconds for the search (default: 60)"},
        },
        "required": ["path"],
    },
}


def _handle_code_duplicates(args, **kw):
    return code_duplicates_tool(
        path=args.get("path", "."),
        min_lines=args.get("min_lines", 5),
        top_n=args.get("top_n", 20),
        max_files=args.get("max_files", 200),
        similarity_threshold=args.get("similarity_threshold", 0.8),
        timeout=args.get("timeout", 60),
    )


# ---------------------------------------------------------------------------
# LSP-based tools — code_definition & code_references (cross-file resolution)
# ---------------------------------------------------------------------------

# LSP tools are registered via register_lsp_tools() called from __init__.py
# during plugin load — do NOT call register_lsp_tools() at module level
# ---------------------------------------------------------------------------
# D1: code_diagram_symbol — Re-exported from tools/diagram.py
# ---------------------------------------------------------------------------

# code_docstring_generate_tool — Generate docstring template from AST
# ---------------------------------------------------------------------------
# code_dependency_risk_tool — Dependency health analysis
# ── Re-Exports for backward compat (tests + internal callers) ──
from .tools.capsule import (  # noqa: E402, F401
    CODE_CAPSULE_SCHEMA,
    _handle_code_capsule,
    code_capsule_tool,
)
from .tools.diagram import (  # noqa: E402, F401
    CODE_DIAGRAM_SYMBOL_SCHEMA,
    _handle_code_diagram_symbol,
    code_diagram_symbol_tool,
)
from .tools.overview import (  # noqa: E402, F401
    CODE_OVERVIEW_SCHEMA,
    _handle_code_overview,
    code_overview_tool,
)
from .tools.impact import (  # noqa: E402, F401
    CODE_IMPACT_SCHEMA,
    _handle_code_impact,
    code_impact_tool,
    CODE_BLAST_RADIUS_SCHEMA,
    _handle_code_blast_radius,
    code_blast_radius_tool,
    CODE_PR_IMPACT_SCHEMA,
    _handle_code_pr_impact,
    code_pr_impact_tool,
    _find_functions_in_file,
)
from .tools.pattern import (  # noqa: E402, F401
    _AST_GREP_LANG_MAP,
    _AST_GREP_VAR_RE,
    _apply_refactor_changes,
    _ast_grep_rewrite,
    _build_refactor_changes,
    _check_ast_grep_reqs,
)
from .tools.query import (  # noqa: E402, F401
    _QUERY_INTENT_MAP,
    CODE_QUERY_SCHEMA,
    _handle_code_query,
    code_query_tool,
)

from .tools.unused import (  # noqa: E402, F401 — re-exported for __init__.py + tests
    code_unused_finder_tool,
    CODE_UNUSED_FINDER_SCHEMA,
    _handle_code_unused_finder,
)

from .tools.complexity import (  # noqa: E402, F401
    _COMPLEXITY_NODE_TYPES,
    _FUNCTION_QUERIES,
    _count_nodes,
    _count_early_returns,
    code_complexity_tool,
    CODE_COMPLEXITY_SCHEMA,
    _handle_code_complexity,
)
from .tools.batch import (  # noqa: E402, F401
    CODE_BATCH_REFACTOR_SCHEMA,
    _handle_code_batch_refactor,
    code_batch_refactor_tool,
)
from .tools.security import (  # noqa: E402, F401
    CODE_SECURITY_SCHEMA,
    _handle_code_security,
    code_security_scan_tool,
)
from .tools.blame import (  # noqa: E402, F401
    CODE_GIT_BLAME_SCHEMA,
    _handle_code_git_blame,
    code_git_blame_tool,
)
from .tools.testgen import (  # noqa: E402, F401
    CODE_GENERATE_TESTS_SCHEMA,
    _handle_code_generate_tests,
    code_generate_tests_tool,
)

# ---------------------------------------------------------------------------
# Re-exports from tools/ submodules — these functions were extracted
# from this monolith into dedicated modules for maintainability.
# The original definitions remain here as local names so that existing
# imports (from within this package and from tests) continue to work.
# ---------------------------------------------------------------------------
from .tools.cache import (  # noqa: F401, I001
    _set_dir_cache, _find_project_root, _cache_key_for_path, _project_cache_path,
    persist_symbol_cache, load_symbol_cache, _set_cache, get_symbol_cache_stats,
    clear_symbol_cache, _invalidate_cache,
    _LANG_LOCK, _LANG_CACHE, _PARSER_CACHE, _LANG_READY,
    _SYMBOL_CACHE, _DIR_SYMBOL_CACHE, _MAX_DIR_CACHE, _PERSIST_DIR, _PERSIST_VERSION,
)
from .tools.language import (  # noqa: F401
    _EXT_TO_LANG, _NODE_KIND_MAP, _init_languages, _get_language, _get_parser,
    detect_language, _classify_node,
)
from .tools.workspace import (  # noqa: F401
    _detect_lang_for_summary, _find_lang_folders, _count_extensions, _scan_workspace,
    _detect_monorepo_markers, code_workspace_summary_tool, _handle_code_workspace_summary,
    CODE_WORKSPACE_SUMMARY_SCHEMA,
)
from .tools.type_hierarchy import (  # noqa: F401
    _ast_type_hierarchy_supertypes, _ast_type_hierarchy_subtypes,
)
from .tools.metrics import (  # noqa: F401
    code_metrics_tool, _handle_code_metrics, CODE_METRICS_SCHEMA,
)
from .tools.search_by_error import (  # noqa: F401
    code_search_by_error_tool, _handle_code_search_by_error, CODE_SEARCH_BY_ERROR_SCHEMA,
)
from .tools.graph_analysis import (  # noqa: F401
    code_hot_paths_tool, _handle_code_hot_paths, CODE_HOT_PATHS_SCHEMA,
    code_cycle_detector_tool, _handle_code_cycle_detector, CODE_CYCLE_DETECTOR_SCHEMA,
    code_dependency_graph_tool, _handle_code_dependency_graph, CODE_DEPENDENCY_GRAPH_SCHEMA,
)
from .tools.test_coverage import (  # noqa: F401
    _tests_find_references, _tests_find_symbol_name, _calc_test_score,
    _tests_filter_and_score, _tests_calc_coverage, code_tests_for_symbol_tool,
    _handle_code_tests_for_symbol, CODE_TESTS_FOR_SYMBOL_SCHEMA,
)
from .tools.ast_edit import (  # noqa: F401
    _find_symbol_in_ast, _ast_search_references,
    code_replace_body_tool, _handle_code_replace_body, CODE_REPLACE_BODY_SCHEMA,
    code_safe_delete_tool, _handle_code_safe_delete, CODE_SAFE_DELETE_SCHEMA,
    code_insert_before_tool, _handle_code_insert_before, CODE_INSERT_BEFORE_SCHEMA,
    code_insert_after_tool, _handle_code_insert_after, CODE_INSERT_AFTER_SCHEMA,
    code_move_tool, _handle_code_move, CODE_MOVE_SCHEMA,
)
from .tools.export import (  # noqa: F401
    code_export_tool, _handle_code_export, CODE_EXPORT_SCHEMA,
    code_docstring_generate_tool, _handle_code_docstring_generate, CODE_DOCSTRING_GENERATE_SCHEMA,
    code_dependency_risk_tool, _handle_code_dependency_risk, CODE_DEPENDENCY_RISK_SCHEMA,
)