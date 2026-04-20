#!/usr/bin/env python3
"""
Code Intelligence Tools Module

AST-aware code analysis tools using tree-sitter and ast-grep.
Provides structural symbol extraction, pattern search, and safe refactoring.

Token-efficient alternative to reading entire files for code navigation.
"""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import OrderedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language registry — maps file extensions → tree-sitter Language objects
# Lazy-loaded on first use to avoid slow imports at module level.
# ---------------------------------------------------------------------------

_LANG_LOCK = threading.Lock()
_LANG_CACHE: Dict[str, object] = {}  # ext → Language
_PARSER_CACHE: Dict[str, object] = {}  # lang_key → Parser
_LANG_READY = False
_SYMBOL_CACHE = OrderedDict()


# Extension → language key mapping

def _set_cache(key, value):
    _SYMBOL_CACHE[key] = value
    if len(_SYMBOL_CACHE) > 2000:
        _SYMBOL_CACHE.popitem(last=False)

def get_symbol_cache_stats() -> dict:
    return {"entries": len(_SYMBOL_CACHE)}

def clear_symbol_cache() -> None:
    _SYMBOL_CACHE.clear()

_EXT_TO_LANG = {
    ".py": "python",
    ".pyi": "python",
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
_AST_GRASP_LANGS = {
    "python", "javascript", "typescript", "tsx", "rust", "go", "java", "c", "cpp",
}

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


def _init_languages():
    """Load all language grammars. Thread-safe, runs once."""
    global _LANG_READY, _LANG_CACHE
    with _LANG_LOCK:
        if _LANG_READY:
            return

        try:
            import tree_sitter_python as tspython
            import tree_sitter_javascript as tsjs
            import tree_sitter_typescript as tsts
            import tree_sitter_rust as tsrust
            import tree_sitter_go as tsgo
            import tree_sitter_java as tsjava
            from tree_sitter import Language
        except ImportError as e:
            logger.warning("Code intelligence deps not installed: %s", e)
            return

        langs = {
            "python": Language(tspython.language()),
            "javascript": Language(tsjs.language()),
            "typescript": Language(tsts.language_typescript()),
            "tsx": Language(tsts.language_tsx()),
            "rust": Language(tsrust.language()),
            "go": Language(tsgo.language()),
            "java": Language(tsjava.language()),
        }

        _LANG_CACHE.update(langs)
        _LANG_READY = True


def _get_language(lang_key: str):
    """Get a tree-sitter Language by key, lazy-loading if needed."""
    if not _LANG_READY:
        _init_languages()
    return _LANG_CACHE.get(lang_key)


def _get_parser(lang_key: str):
    """Get or create a cached tree-sitter Parser for a language."""
    if not _LANG_READY:
        _init_languages()

    if lang_key not in _PARSER_CACHE:
        lang = _LANG_CACHE.get(lang_key)
        if lang is None:
            return None
        from tree_sitter import Parser
        parser = Parser(lang)
        _PARSER_CACHE[lang_key] = parser

    return _PARSER_CACHE[lang_key]


def detect_language(path: str, explicit_lang: Optional[str] = None) -> Optional[str]:
    """Detect language from file extension or explicit override."""
    if explicit_lang:
        return explicit_lang.lower()

    ext = Path(path).suffix.lower()
    return _EXT_TO_LANG.get(ext)


def _classify_node(node, query_capture_name: str) -> str:
    """Classify a tree-sitter node into a symbol kind."""
    # Check the capture name first
    if query_capture_name == "name":
        # Classify by parent or sibling context
        pass

    # Check node type directly
    kind = _NODE_KIND_MAP.get(node.type)
    if kind:
        return kind

    return "symbol"


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

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
    from tree_sitter import Query, QueryCursor

    parser = _get_parser(lang_key)
    lang = _get_language(lang_key)
    if parser is None or lang is None:
        return []

    tree = parser.parse(source)
    query_text = _SYMBOL_QUERIES.get(lang_key)
    if not query_text:
        # Fallback: generic query for common definitions
        query_text = """
            (function_definition name: (identifier) @name) @def
            (class_definition name: (identifier) @name) @def
            (function_declaration name: (identifier) @name) @def
            (class_declaration name: (type_identifier) @name) @def
        """

    try:
        query = Query(lang, query_text)
    except Exception as e:
        logger.debug("Query compile error for %s: %s", lang_key, e)
        return []

    # tree-sitter 0.25+: use QueryCursor.matches() which returns
    # (pattern_index, {capture_name: [Node, ...], ...}) tuples
    qc = QueryCursor(query)
    seen: set = set()
    symbols: List[dict] = []

    source_lines = source.split(b"\n")

    for _pattern_idx, captures_dict in qc.matches(tree.root_node):
        # Each match should have at minimum a "name" capture and a "def" capture.
        # We use "def" for the full definition extent and "name" for the identifier.
        name_nodes = captures_dict.get("name", [])
        def_nodes = (
            captures_dict.get("def")
            or captures_dict.get("constant")
            or captures_dict.get("field")
            or captures_dict.get("arrow")
        )

        if not name_nodes:
            continue

        # Take the first name node (some patterns have multiple via alternation)
        name_node = name_nodes[0]
        name_text = name_node.text.decode("utf-8", errors="replace")

        # Determine the definition node extent
        if def_nodes:
            def_node = def_nodes[0]
        else:
            # Fallback: use the parent of the name node
            def_node = name_node.parent
            if def_node is None:
                continue

        # Dedup by (name, start_row)
        key = (name_text, def_node.start_point[0])
        if key in seen:
            continue
        seen.add(key)

        # Determine symbol kind from node type
        kind = _NODE_KIND_MAP.get(def_node.type, "symbol")

        # For decorated_definition: look at the inner definition's type
        if def_node.type == "decorated_definition" and kind == "symbol":
            for child in def_node.children:
                inner_kind = _NODE_KIND_MAP.get(child.type)
                if inner_kind:
                    kind = inner_kind
                    break

        # For Go type_spec: detect struct/interface/trait by looking at the type child
        if def_node.type == "type_spec" and kind == "symbol":
            for child in def_node.children:
                child_kind = _NODE_KIND_MAP.get(child.type)
                if child_kind in ("struct", "interface"):
                    kind = child_kind
                    break

        # Detect methods (function inside class/impl/struct body)
        # Walk up through wrappers like decorated_definition
        _cur = def_node.parent
        _depth = 0
        while _cur and _depth < 4:
            _par = _cur.parent
            if _cur.type == "block" and _par and _par.type == "class_definition":
                if kind == "function":
                    kind = "method"
                break
            elif _cur.type in ("class_body", "declaration_list"):
                if _par and _par.type in (
                    "class_declaration", "class_definition",
                    "impl_item", "struct_item",
                ):
                    if kind == "function":
                        kind = "method"
                break
            elif _cur.type in ("decorated_definition", "abstract_method_declaration"):
                # Skip wrapper — keep walking up
                _cur = _par
                _depth += 1
                continue
            break

        # Apply kind filter
        if kind_filter and kind_filter != "all":
            if kind != kind_filter:
                continue

        # Apply pattern filter (fuzzy substring match)
        if pattern_filter:
            if pattern_filter.lower() not in name_text.lower():
                continue

        start_line = def_node.start_point[0] + 1  # 1-indexed
        end_line = def_node.end_point[0] + 1
        sig_start = def_node.start_point[0]
        sig_end = min(def_node.end_point[0], sig_start + 2)  # first 3 lines max
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

        symbols.append(sym)

    # Sort by line number
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
        return json.dumps({
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

    return json.dumps({
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
) -> str:

    try:
        import tree_sitter
    except ImportError:
        return json.dumps({
            "error": "Code intelligence dependencies are not installed. Please run: uv pip install 'hermes-agent[code-intel]'"
        })
    """Extract symbols from source files using tree-sitter AST parsing."""
    target = Path(path).expanduser().resolve()

    if not target.exists():
        return json.dumps({
            "error": f"Path not found: {path}",
        })

    if target.is_dir():
        # Skip language detection for directories — scan all supported files
        lang_key = None
    else:
        lang_key = detect_language(str(target), language)
        if lang_key is None:
            return json.dumps({
                "error": (
                    f"Unsupported language for '{path}'. "
                    f"Supported extensions: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
                ),
            })

    if target.is_file():
        mtime = target.stat().st_mtime
        cache_key = (str(target), mtime, lang_key, pattern, kind, include_body)
        
        if cache_key in _SYMBOL_CACHE:
            symbols = _SYMBOL_CACHE[cache_key]
            # Fast-path total lines reading since we don't need the source
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

        return _format_symbols_output(str(target), symbols, total_lines, lang_key)

    # Directory: scan all supported files
    results = []
    all_symbols = []
    for ext in _EXT_TO_LANG:
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
                
            cache_key = (str(file_path), mtime, file_lang, pattern, kind, False)
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
            if syms:
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

    if not results:
        return json.dumps({
            "path": str(target),
            "message": "No symbols found in directory scan.",
            "supported_extensions": sorted(set(_EXT_TO_LANG.values())),
        })

    # Build formatted output
    lines = []
    lines.append(f"Directory: {target} ({len(results)} files with symbols)")
    for r in results:
        lines.append(f"\n{r['path']} ({r['total_lines']} lines, {r['language']})")
        for sym in r["symbols"]:
            sig = sym["signature"]
            if len(sig) > 100:
                sig = sig[:97] + "..."
            lines.append(f"  L{sym['line']:>4d}  [{sym['kind']}] {sym['name']}  {sig}")

    return json.dumps({
        "path": str(target),
        "file_count": len(results),
        "total_symbols": len(all_symbols),
        "results": results,
        "formatted": "\n".join(lines),
    })


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

from tools.registry import registry


def _handle_code_symbols(args, **kw):
    return code_symbols_tool(
        path=args.get("path", ""),
        pattern=args.get("pattern"),
        kind=args.get("kind"),
        include_body=args.get("include_body", False),
        language=args.get("language"),
    )


registry.register(
    name="code_symbols",
    toolset="code_intel",
    schema=CODE_SYMBOLS_SCHEMA,
    handler=_handle_code_symbols,
    check_fn=_check_code_intel_reqs,
    emoji="🔍",
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
) -> str:

    try:
        import tree_sitter
    except ImportError:
        return json.dumps({
            "error": "Code intelligence dependencies are not installed. Please run: uv pip install 'hermes-agent[code-intel]'"
        })
    """AST-aware structural code search using tree-sitter Query API.

    Supports three modes:
    1. Raw tree-sitter query (via 'query' param)
    2. Named preset like 'function_calls', 'imports', 'try_catch', etc.
    3. Simple text pattern filter on captured nodes (via 'pattern' param)

    Accepts both files and directories (recursive scan of supported files).
    """
    target = Path(path).expanduser().resolve()

    if not target.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    if target.is_file():
        return _code_search_single_file(target, query, preset, pattern, language, max_results)

    # Directory: scan all supported files recursively
    return _code_search_directory(target, query, preset, pattern, language, max_results)


def _code_search_single_file(
    target: Path,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
) -> str:
    """Run code_search on a single file."""
    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return json.dumps({
            "error": (
                f"Unsupported language for '{target}'. "
                f"Supported: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            ),
        })

    query_str = _resolve_query(query, preset, pattern, lang_key, str(target))
    if isinstance(query_str, str) and query_str.startswith("{"):
        return query_str  # error JSON

    parser = _get_parser(lang_key)
    lang = _get_language(lang_key)
    if parser is None or lang is None:
        return json.dumps({"error": f"No tree-sitter grammar for {lang_key}"})

    source = target.read_bytes()
    tree = parser.parse(source)

    try:
        from tree_sitter import Query, QueryCursor
        ts_query = Query(lang, query_str)
    except Exception as e:
        return json.dumps({"error": f"Invalid tree-sitter query: {e}"})

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
    return json.dumps({
        "path": str(target),
        "language": lang_key,
        "query": query_str[:200],
        "match_count": len(results),
        "truncated": truncated,
        "results": results,
    })


def _code_search_directory(
    target: Path,
    query: Optional[str] = None,
    preset: Optional[str] = None,
    pattern: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
) -> str:
    """Run code_search across all supported files in a directory."""
    results = []
    files_scanned = 0
    files_with_matches = 0
    remaining = max_results

    for ext in _EXT_TO_LANG:
        for file_path in sorted(target.rglob(f"*{ext}")):
            if not file_path.is_file():
                continue
            file_lang = detect_language(str(file_path), language)
            if file_lang is None:
                continue

            # Resolve query for this file's language
            query_str = _resolve_query(query, preset, pattern, file_lang, str(file_path))
            if isinstance(query_str, str) and query_str.startswith("{"):
                continue  # skip files with unsupported language/preset

            parser = _get_parser(file_lang)
            lang = _get_language(file_lang)
            if parser is None or lang is None:
                continue

            try:
                source = file_path.read_bytes()
            except (OSError, PermissionError):
                continue

            files_scanned += 1
            tree = parser.parse(source)

            try:
                from tree_sitter import Query, QueryCursor
                ts_query = Query(lang, query_str)
            except Exception:
                continue

            qc = QueryCursor(ts_query)
            seen_spans = set()
            file_results = []

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
                        if remaining <= 0:
                            break
                    if remaining <= 0:
                        break
                if remaining <= 0:
                    break

            if file_results:
                files_with_matches += 1
                results.extend(file_results)

            if remaining <= 0:
                break

    truncated = remaining <= 0 and results
    return json.dumps({
        "path": str(target),
        "files_scanned": files_scanned,
        "files_with_matches": files_with_matches,
        "match_count": len(results),
        "truncated": truncated,
        "results": results,
    })


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
            return json.dumps({
                "error": f"Unknown preset '{preset}' for {lang_key} ({file_path}). "
                         f"Available: {', '.join(available)}",
            })
        return query_str
    elif pattern:
        return "(_) @node"
    else:
        return json.dumps({
            "error": "Provide 'query', 'preset', or 'pattern'. "
                     "Presets: function_calls, string_literals, imports, "
                     "decorator_calls, try_catch, return_stmts, assignments.",
        })


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
            "path": {"type": "string", "description": "File or directory path to search"},
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


registry.register(
    name="code_search",
    toolset="code_intel",
    schema=CODE_SEARCH_SCHEMA,
    handler=_handle_code_search,
    check_fn=_check_code_intel_reqs,
    emoji="🔎",
)


# ---------------------------------------------------------------------------
# code_refactor — ast-grep structural search & replace (dry-run default)
# ---------------------------------------------------------------------------

def _check_ast_grep_reqs() -> bool:
    """Always return True so the tool is visible, but fail gracefully."""
    return True


def _ast_grep_rewrite(src: str, rewrite_template: str, variables: dict) -> str:
    """Interpolate ast-grep meta variables into a rewrite template.

    ast-grep-py's commit_edits doesn't interpolate $VAR in replacement text,
    so we do it manually.
    """
    result = rewrite_template
    # Sort by key length descending to avoid partial replacements
    for var_name in sorted(variables, key=len, reverse=True):
        # $NAME and $$NAME are both used by ast-grep
        for prefix in ("$$", "$"):
            placeholder = f"{prefix}{var_name}"
            if placeholder in result:
                result = result.replace(placeholder, variables[var_name])
    return result


# Map language key to ast-grep language name
_AST_GREP_LANG_MAP = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "rust": "rust",
    "go": "go",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
}

# Reusable regex for extracting ast-grep meta variable names from a pattern
_AST_GREP_VAR_RE = re.compile(r'\$(\$)?([A-Z_][A-Z0-9_]*)')


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

    # Collect matches with context, compute rewrites
    var_names = set(_AST_GREP_VAR_RE.findall(pattern))
    changes = []

    for match in matches:
        rng = match.range()
        start_row = rng.start.line
        start_col = rng.start.column
        end_row = rng.end.line
        end_col = rng.end.column

        original = source_lines[start_row][start_col:]
        if end_row > start_row:
            original += "\n" + "\n".join(source_lines[start_row + 1 : end_row])
        if end_row < len(source_lines):
            original += source_lines[end_row][:end_col]

        # Extract meta variables
        variables = {}
        for is_multi, var_name in var_names:
            try:
                var_node = match.get_match(var_name)
                if var_node is not None:
                    variables[var_name] = var_node.text()
            except Exception:
                pass

        # Compute replacement text
        replacement = _ast_grep_rewrite("", rewrite, variables)

        # Context lines
        ctx_start = max(0, start_row - context_lines)
        ctx_end = min(len(source_lines) - 1, end_row + context_lines)

        change = {
            "line": start_row + 1,
            "end_line": end_row + 1,
            "original": original[:300],
            "replacement": replacement[:300],
            "variables": variables,
            "context": {
                "start": ctx_start + 1,
                "end": ctx_end + 1,
                "before": "\n".join(source_lines[ctx_start:start_row]) if start_row > 0 else "",
                "after": "\n".join(source_lines[end_row + 1 : ctx_end + 1]) if end_row < ctx_end else "",
            },
        }
        changes.append(change)

    # Apply changes if not dry-run
    applied = False
    if not dry_run:
        try:
            lines_out = source_lines[:]
            # Apply from bottom to top to preserve offsets
            for change, match in zip(reversed(changes), matches):
                rng = match.range()
                sr, sc = rng.start.line, rng.start.column
                er, ec = rng.end.line, rng.end.column
                new_first = lines_out[sr][:sc] + change["replacement"]
                new_last_part = lines_out[er][ec:] if er < len(lines_out) else ""
                lines_out[sr:er + 1] = [new_first + new_last_part]
            target.write_text("\n".join(lines_out), encoding="utf-8")
            applied = True
        except Exception as e:
            return {
                "path": str(target),
                "language": lang_key,
                "error": f"Failed to apply changes: {e}",
                "match_count": len(changes),
                "changes": changes,
            }

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

    return json.dumps({
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
        return json.dumps({"error": f"Path not found: {path}"})

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
        return json.dumps({
            "error": (
                f"Unsupported language for '{path}'. "
                f"Supported: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            ),
        })

    result = _code_refactor_single_file(target, pattern, rewrite, lang_key, dry_run, context_lines)
    return json.dumps(result)


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
            "path": {"type": "string", "description": "File path or directory to refactor"},
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


registry.register(
    name="code_refactor",
    toolset="code_intel",
    schema=CODE_REFACTOR_SCHEMA,
    handler=_handle_code_refactor,
    check_fn=_check_ast_grep_reqs,
    emoji="🔧",
)


# ---------------------------------------------------------------------------
# LSP-based tools — code_definition & code_references (cross-file resolution)
# ---------------------------------------------------------------------------

from .lsp_bridge import register_lsp_tools

register_lsp_tools()
