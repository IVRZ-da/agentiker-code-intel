#!/usr/bin/env python3
"""
Code Intelligence Tools Module

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

# ---------------------------------------------------------------------------
# Persistent symbol index (B5) — saves/loads AST cache to disk
# ---------------------------------------------------------------------------
_PERSIST_DIR = os.path.expanduser("~/.hermes/plugins/code_intel/.cache")
_PERSIST_VERSION = 2  # bump to invalidate stale caches

def _find_project_root(filepath: str = "") -> str:
    """Find the project root (monorepo or standalone) from a file path or CWD.

    Walks up from the given file (or CWD) looking for monorepo markers first,
    then generic project markers like .git, pyproject.toml, etc.

    If no filepath is given, tries HERMES_PROJECT_ROOT env var before CWD
    so that the Agent process (running from its own dir) still resolves
    the correct user project root.
    """
    if filepath:
        start = Path(filepath).resolve().parent
    else:
        # Prefer explicit env var (set by hermes config or launcher)
        env_root = os.environ.get("HERMES_PROJECT_ROOT", "")
        if env_root and Path(env_root).is_dir():
            return str(Path(env_root).resolve())
        # Walk CWD but also try common project directories
        start = Path.cwd()

    # Monorepo markers take priority
    for p in [start] + list(start.parents):
        for marker in ("pnpm-workspace.yaml", "nx.json", "lerna.json"):
            if (p / marker).exists():
                return str(p)
        # Stop at filesystem root
        if p.parent == p:
            break
    # Fallback: generic project root
    for p in [start] + list(start.parents):
        for marker in (".git", "pyproject.toml", "Cargo.toml", "go.mod"):
            if (p / marker).exists():
                return str(p)
        if p.parent == p:
            break
    return str(start)


def _cache_key_for_path(file_path: str) -> str:
    """Generate a cache key for a file path, relative to project root.

    Falls back to absolute path if the file is outside the project root
    (e.g. on a different filesystem or symlink).
    """
    root = _find_project_root(file_path)
    try:
        return str(Path(file_path).relative_to(Path(root)))
    except ValueError:
        return str(Path(file_path).resolve())


def _project_cache_path(project_root: str = "") -> str:
    """Return the per-project cache file path based on project root hash."""
    import hashlib
    root = project_root or _find_project_root()
    h = hashlib.sha256(root.encode()).hexdigest()[:12]
    return os.path.join(_PERSIST_DIR, f"symidx_{h}.json")

def persist_symbol_cache() -> int:
    """Save current symbol cache to disk. Returns number of entries saved."""
    if not _SYMBOL_CACHE:
        return 0
    os.makedirs(_PERSIST_DIR, exist_ok=True)
    path = _project_cache_path()
    project_root = _find_project_root()
    # Ensure all keys are JSON-serializable strings — skip non-string keys (e.g. tuples)
    safe_entries = {}
    for k, v in _SYMBOL_CACHE.items():
        key = str(k) if not isinstance(k, str) else k
        try:
            # Quick check: can we serialize this entry?
            json.dumps({key: v})
            safe_entries[key] = v
        except (TypeError, ValueError):
            continue
    data = {
        "version": _PERSIST_VERSION,
        "project_root": project_root,
        "entries": safe_entries
    }
    try:
        with open(path, "w") as f:
            json.dump(data, f)
        logger.debug(f"Persisted {len(safe_entries)} symbol cache entries to {path}")
        return len(safe_entries)
    except Exception as e:
        logger.warning(f"Failed to persist symbol cache: {e}")
        return 0

def load_symbol_cache() -> int:
    """Load symbol cache from disk. Returns number of entries loaded."""
    path = _project_cache_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("version") != _PERSIST_VERSION:
            logger.info("Symbol cache version mismatch, skipping load")
            return 0
        # Validate project root matches (allow any root if not stored)
        # We no longer require CWD to match — project root is more stable
        loaded = 0
        for k, v in data.get("entries", {}).items():
            if k not in _SYMBOL_CACHE:
                _SYMBOL_CACHE[k] = v
                loaded += 1
        logger.info(f"Loaded {loaded} symbol cache entries from {path}")
        return loaded
    except Exception as e:
        logger.warning(f"Failed to load symbol cache: {e}")
        return 0


# Extension → language key mapping

def _set_cache(key, value):
    _SYMBOL_CACHE[key] = value
    if len(_SYMBOL_CACHE) > 2000:
        _SYMBOL_CACHE.popitem(last=False)

def get_symbol_cache_stats() -> dict:
    return {"entries": len(_SYMBOL_CACHE)}

def clear_symbol_cache() -> None:
    _SYMBOL_CACHE.clear()


def _invalidate_cache(file_path: str) -> None:
    """Remove all cached entries for a specific file path.

    Used by code_replace_body and code_safe_delete to ensure stale
    cached symbol data doesn't persist after edits.
    """
    prefix = str(Path(file_path).resolve()) + "|"
    stale_keys = [k for k in _SYMBOL_CACHE if k.startswith(prefix)]
    for k in stale_keys:
        try:
            del _SYMBOL_CACHE[k]
        except KeyError:
            pass
    if stale_keys:
        logger.debug("Invalidated %d cache entries for %s", len(stale_keys), file_path)


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


def _init_languages():
    """Load all language grammars. Thread-safe, runs once."""
    global _LANG_READY, _LANG_CACHE
    with _LANG_LOCK:
        if _LANG_READY:
            return

        try:
            import tree_sitter_go as tsgo
            import tree_sitter_java as tsjava
            import tree_sitter_javascript as tsjs
            import tree_sitter_python as tspython
            import tree_sitter_rust as tsrust
            import tree_sitter_typescript as tsts
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
# AST Type Hierarchy (Fallback für Python/TS ohne LSP-Support)
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


def _ast_type_hierarchy_supertypes(path: str, line: int) -> Optional[list]:
    """AST-basierte Supertypes (Eltern-Klassen/Interfaces).

    Funktioniert für Python und TypeScript/JS als Fallback
    wenn der LSP-Server TypeHierarchy nicht unterstützt.
    """
    from pathlib import Path as _Path
    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return None

    from .code_tools import _get_language, _get_parser, detect_language
    lang_key = detect_language(str(target))
    if not lang_key or lang_key not in _TYPE_HIERARCHY_FALLBACK_LANGS:
        return None

    if lang_key == "python":
        query_source = _PYTHON_CLASS_EXTENDS
    else:
        query_source = _TS_CLASS_EXTENDS

    try:
        from tree_sitter import Query, QueryCursor
        lang_obj = _get_language(lang_key)
        if lang_obj is None:
            return None
        query = Query(lang_obj, query_source)
    except Exception:
        return None

    parser = _get_parser(lang_key)
    if parser is None:
        return None

    try:
        with open(str(target), "rb") as f:
            source = f.read()
    except (OSError, IOError):
        return None

    tree = parser.parse(source)
    if tree is None:
        return None

    # Finde die Klasse an der angegebenen Line
    target_class_name = None
    qc = QueryCursor(query)
    for _pi, cd in qc.matches(tree.root_node):
        for _n in cd.get("class_def", []):
            start_line = _n.start_point[0] if hasattr(_n, "start_point") else 0
            if start_line == line - 1:  # 0-based vs 1-based
                for name_node in cd.get("class_name", []):
                    target_class_name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                break
        if target_class_name:
            break

    if not target_class_name:
        return None

    # Suche nach Eltern-Klassen
    result = []
    qc2 = QueryCursor(query)
    for _pi, cd in qc2.matches(tree.root_node):
        for n in cd.get("extends_name", []):
            try:
                name = source[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
            except (UnicodeDecodeError, IndexError):
                continue
            # Nur wenn diese extends-Klasse unser target_class_name ist,
            # und die definierende Klasse existiert
            for class_node in cd.get("class_name", []):
                try:
                    cn = source[class_node.start_byte:class_node.end_byte].decode("utf-8", errors="replace")
                except (UnicodeDecodeError, IndexError):
                    continue
                if name != target_class_name and cn == target_class_name:
                    for def_node in cd.get("class_def", []):
                        start = def_node.start_point[0] if hasattr(def_node, "start_point") else 0
                        result.append({
                            "name": name,
                            "kind": "class" if "class" in str(def_node.type) else "interface",
                            "line": start + 1,
                            "file": str(target),
                        })

    return result if result else None


def _ast_type_hierarchy_subtypes(path: str, line: int) -> Optional[list]:
    """AST-basierte Subtypes (Kind-Klassen/Interfaces).

    Findet alle Klassen die VON der Klasse an position line erben.
    """
    from pathlib import Path as _Path
    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return None

    from .code_tools import _get_language, _get_parser, detect_language
    lang_key = detect_language(str(target))
    if not lang_key or lang_key not in _TYPE_HIERARCHY_FALLBACK_LANGS:
        return None

    if lang_key == "python":
        query_source = _PYTHON_CLASS_EXTENDS
    else:
        query_source = _TS_CLASS_EXTENDS

    try:
        from tree_sitter import Query, QueryCursor
        lang_obj = _get_language(lang_key)
        if lang_obj is None:
            return None
        query = Query(lang_obj, query_source)
    except Exception:
        return None

    parser = _get_parser(lang_key)
    if parser is None:
        return None

    try:
        with open(str(target), "rb") as f:
            source = f.read()
    except (OSError, IOError):
        return None

    tree = parser.parse(source)
    if tree is None:
        return None

    # Finde den Klassennamen an der angegebenen Line
    target_class_name = None
    qc = QueryCursor(query)
    for _pi, cd in qc.matches(tree.root_node):
        for n in cd.get("class_def", []):
            start_line = n.start_point[0] if hasattr(n, "start_point") else 0
            if start_line == line - 1:
                for name_node in cd.get("class_name", []):
                    target_class_name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                break
        if target_class_name:
            break

    if not target_class_name:
        return None

    # Scanne ALLE Dateien im Projekt + target_dir nach Klassen die target_class_name extenden
    result = []
    scan_dir = target.parent
    for ext in [".py", ".ts", ".tsx", ".js"]:
        for f in scan_dir.glob(f"**/*{ext}"):
            if any(p in str(f) for p in ["node_modules", ".venv", "__pycache__", ".git"]):
                continue
            try:
                with open(f, "rb") as sf:
                    scan_source = sf.read()
            except (OSError, IOError):
                continue
            scan_tree = parser.parse(scan_source)
            if scan_tree is None:
                continue
            qc3 = QueryCursor(query)
            for _pi2, cd2 in qc3.matches(scan_tree.root_node):
                for n in cd2.get("extends_name", []):
                    try:
                        name = scan_source[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
                    except (UnicodeDecodeError, IndexError):
                        continue
                    if name == target_class_name:
                        for def_node in cd2.get("class_def", []):
                            start = def_node.start_point[0] if hasattr(def_node, "start_point") else 0
                            cn = "?"
                            for cn_node in cd2.get("class_name", []):
                                try:
                                    cn = scan_source[cn_node.start_byte:cn_node.end_byte].decode("utf-8", errors="replace")
                                except (UnicodeDecodeError, IndexError):
                                    cn = "?"
                            result.append({
                                "name": cn,
                                "kind": "class" if "class" in str(def_node.type) else "interface",
                                "line": start + 1,
                                "file": str(f),
                            })

    return result if result else None


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
    results = []
    all_symbols = []
    count = 0
    done = False
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

    return fmt_ok({
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



def _build_refactor_changes(matches, source_lines, pattern, rewrite, context_lines):
    """Convert ast-grep matches to change dicts."""
    var_names = set(_AST_GREP_VAR_RE.findall(pattern))
    changes = []
    for match in matches:
        rng = match.range()
        start_row, start_col = rng.start.line, rng.start.column
        end_row, end_col = rng.end.line, rng.end.column

        original = source_lines[start_row][start_col:]
        if end_row > start_row:
            original += "\n" + "\n".join(source_lines[start_row + 1:end_row])
        if end_row < len(source_lines):
            original += source_lines[end_row][:end_col]

        variables = {}
        for is_multi, var_name in var_names:
            try:
                var_node = match.get_match(var_name)
                if var_node is not None:
                    variables[var_name] = var_node.text()
            except Exception:
                pass

        replacement = _ast_grep_rewrite("", rewrite, variables)
        ctx_start = max(0, start_row - context_lines)
        ctx_end = min(len(source_lines) - 1, end_row + context_lines)

        changes.append({
            "line": start_row + 1,
            "end_line": end_row + 1,
            "original": original[:300],
            "replacement": replacement[:300],
            "variables": variables,
            "context": {
                "start": ctx_start + 1, "end": ctx_end + 1,
                "before": "\n".join(source_lines[ctx_start:start_row]) if start_row > 0 else "",
                "after": "\n".join(source_lines[end_row + 1:ctx_end + 1]) if end_row < ctx_end else "",
            },
        })
    return changes


def _apply_refactor_changes(changes, matches, source_lines, target, dry_run):
    """Apply refactor changes. Returns bool or error dict."""
    if dry_run or not changes:
        return False
    try:
        lines_out = source_lines[:]
        for change, match in zip(reversed(changes), reversed(matches)):
            rng = match.range()
            sr, sc = rng.start.line, rng.start.column
            er, ec = rng.end.line, rng.end.column
            first = lines_out[sr][:sc] + change["replacement"]
            last = lines_out[er][ec:] if er < len(lines_out) else ""
            lines_out[sr:er + 1] = [first + last]
        target.write_text("\n".join(lines_out), encoding="utf-8")
        return True
    except Exception as e:
        return {"error": f"Failed to apply: {e}", "match_count": len(changes)}


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
        from .lsp.bridge import get_lsp_manager
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


def _capsule_get_references(target: str, line: int, matched: Optional[dict], lang: Optional[str]) -> dict:
    """Rufe LSP References ab und gruppiere Top-5 (direkt via Bridge, kein fmt_ok)."""
    try:
        from .lsp.bridge import get_lsp_manager
        manager = get_lsp_manager()
        bridge = manager.get_bridge(lang, target) if lang else None
        if bridge is None:
            return {"total": 0, "top": [], "files": 0}
        char = (matched.get("start_column", 0) or 0) - 1 if matched else 0
        refs = bridge.find_references(target, line - 1, char, include_declaration=False)
    except Exception:
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
                if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                    comment_lines.insert(0, stripped.lstrip("#/* "))
                elif stripped == "" or stripped.startswith("@") or stripped.startswith("["):
                    continue
                else:
                    break
            return " | ".join(comment_lines[:3])
    except Exception:
        pass
    return ""


def _capsule_find_tests(target: str, line: int, matched: Optional[dict], lang: Optional[str]) -> list:
    """Finde Test-Dateien die dieses Symbol referenzieren (direkt via Bridge)."""
    try:
        from .lsp.bridge import get_lsp_manager
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

    lang = language or detect_language(str(target))

    # 1. Symbol metadata via direkte Extraktion (kein json.loads auf fmt_ok-Output)
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
            "path": {"type": "string", "description": "Absolute file path containing the symbol"},
            "line": {"type": "integer", "description": "1-based line number where the symbol appears"},
            "language": {"type": "string", "description": "Language override. Auto-detected from extension."},
            "include_tests": {"type": "boolean", "description": "Include test files referencing this symbol (default: False)"},
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
             ".jsx": "typescript", ".rs": "rust", ".go": "go", ".java": "java"}


def _detect_lang_for_summary(child, ext_lang):
    """Walk up to 2 levels deep looking for code files; return dominant language."""
    ext_counts = {}
    for d in _find_lang_folders(child):
        _count_extensions(d, ext_lang, ext_counts)
        if ext_counts:
            break
    if ext_counts:
        return ext_lang[max(ext_counts, key=ext_counts.get)]


def _find_lang_folders(child):
    """Find candidate directories for language detection."""
    candidates = [child / s for s in ("app", "src", "lib", "source")]
    candidates = [d for d in candidates if d.is_dir()]
    return candidates if candidates else [child]


def _count_extensions(d, ext_lang, ext_counts):
    """Walk up to 2 levels counting file extensions."""
    try:
        stack = [(d, 0)]
        seen = 0
        while stack and seen < 200:
            cur, depth = stack.pop()
            try:
                for f in cur.iterdir():
                    seen += 1
                    if seen > 200:
                        break
                    if f.is_file() and f.suffix in ext_lang:
                        ext_counts[f.suffix] = ext_counts.get(f.suffix, 0) + 1
                    elif f.is_dir() and depth < 1 and f.name not in ("node_modules", ".git", "dist", "build", ".next", ".turbo"):
                        stack.append((f, depth + 1))
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return None


def _scan_workspace(base_dir, max_d, parent_kind=None, detect_lang=None, ext_lang=None):
    """Scan workspace directories for apps and packages, up to *max_d* levels deep.

    *parent_kind*: 'app' | 'package' | None. Forces classification when scanning apps/ or packages/.
    *detect_lang*: callable for language detection (defaults to _detect_lang_for_summary).
    """
    detect_lang = detect_lang or _detect_lang_for_summary
    ext_lang = ext_lang or _EXT_LANG
    import json as _json
    apps, packages = [], []
    if max_d <= 0:
        return apps, packages
    try:
        children = sorted(base_dir.iterdir())
    except PermissionError:
        return apps, packages
    for child in children:
        if not child.is_dir() or child.name in ("node_modules", ".git", ".hg"):
            continue
        nm = child.name.lower()
        pkg_json = child / "package.json"
        if pkg_json.exists():
            try:
                data = _json.loads(pkg_json.read_text("utf-8", errors="replace"))
                name = data.get("name", child.name)
                lang = detect_lang(child, ext_lang)
                if parent_kind == "app":
                    apps.append({"name": name, "path": str(child), "language": lang})
                elif parent_kind == "package":
                    packages.append({"name": name, "path": str(child), "language": lang})
                elif data.get("private"):
                    apps.append({"name": name, "path": str(child), "language": lang})
                else:
                    packages.append({"name": name, "path": str(child), "language": lang})
            except (OSError, json.JSONDecodeError):
                pass
        if nm == "apps":
            sa, sp = _scan_workspace(child, max_d - 1, parent_kind="app", detect_lang=detect_lang, ext_lang=ext_lang)
            apps.extend(sa)
            packages.extend(sp)
        elif nm == "packages":
            sa, sp = _scan_workspace(child, max_d - 1, parent_kind="package", detect_lang=detect_lang, ext_lang=ext_lang)
            apps.extend(sa)
            packages.extend(sp)
    return apps, packages



CODE_WORKSPACE_SUMMARY_SCHEMA = {
    "name": "code_workspace_summary",
    "description": (
        "Returns a compact overview of a monorepo: apps, packages, root markers, "
        "top-level dependencies, and entry points. Use to understand project structure."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "depth": {"type": "integer", "description": "How deep to scan for apps/packages (default: 2)"},
        },
        "required": ["path"],
    },
}




def _detect_monorepo_markers(target, _json_module):
    """Detect monorepo root markers in a directory. Returns (markers, marker_type)."""
    markers = []
    marker_type = None
    mono = ["pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json", "rush.json"]
    for m in mono:
        if (target / m).exists():
            marker_type = m
            markers.append(m)
    if (target / ".git").exists():
        markers.append(".git")
    pkg = target / "package.json"
    if pkg.exists():
        try:
            data = _json_module.loads(pkg.read_text("utf-8", errors="replace"))
            if data.get("workspaces"):
                markers.append("package.json#workspaces")
                if not marker_type:
                    marker_type = "npm-workspaces"
        except Exception:
            pass
    if (target / "tsconfig.json").exists():
        markers.append("tsconfig.json")
        if not marker_type:
            marker_type = "tsconfig.json"
    return markers, marker_type


def code_workspace_summary_tool(path: str, depth: int = 2) -> str:
    """Return a compact monorepo/project overview: apps, packages, root markers, entry points."""
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    root_markers, marker_type = _detect_monorepo_markers(target, _json)
    pkg = target / "package.json"
    if not root_markers:
        root_markers.append("project_root")

    apps_list, packages_list = _scan_workspace(target, max_d=depth, detect_lang=_detect_lang_for_summary, ext_lang=_EXT_LANG)

    top_deps = {}
    if pkg.exists():
        try:
            data = _json.loads(pkg.read_text("utf-8", errors="replace"))
            top_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for k in list(top_deps.keys())[:30]:
                if top_deps[k] == "*":
                    top_deps[k] = str(data.get("peerDependencies", {}).get(k, "latest"))
        except Exception:
            pass

    return fmt_ok({
        "root": str(target),
        "type": marker_type or "project",
        "apps": apps_list[:30],
        "packages": packages_list[:30],
        "root_markers": root_markers,
        "top_level_dependencies": dict(list(top_deps.items())[:20]),
    })


def _handle_code_workspace_summary(args, **kw):
    return code_workspace_summary_tool(
        path=args.get("path", ""),
        depth=args.get("depth", 2),
    )


# ---------------------------------------------------------------------------
# B1c: code_metrics — Aggregate project metrics (LOC, files per language, etc.)
# ---------------------------------------------------------------------------

def code_metrics_tool(path: str = ".", directory: bool = True, depth: int = 5) -> str:
    """Aggregate project metrics: LOC, files per language, comment ratio, average complexity."""

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")
    if not target.is_dir():
        return fmt_err(f"Not a directory: {path}")

    EXCLUDE_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", "target"}

    # Language -> comment prefixes
    _COMMENT_PREFIXES = {
        "python": ("#",),
        "typescript": ("//",),
        "tsx": ("//",),
        "javascript": ("//",),
        "go": ("//",),
        "rust": ("//",),
        "java": ("//",),
    }

    total_files = 0
    files_by_language: dict = {}
    total_lines = 0
    code_lines = 0
    blank_lines = 0
    comment_lines = 0
    all_complexities = []
    top_complexity = []

    # Walk directory tree with depth limit
    stack = [(target, 0)]
    while stack:
        current_dir, current_depth = stack.pop()
        if current_depth > depth:
            continue
        try:
            entries = sorted(current_dir.iterdir(), key=lambda e: e.name)
        except Exception:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if entry.name not in EXCLUDE_DIRS:
                    stack.append((entry, current_depth + 1))
            elif entry.is_file():
                ext = entry.suffix.lower()
                lang_key = _EXT_TO_LANG.get(ext)
                if lang_key is None:
                    continue

                total_files += 1
                files_by_language[lang_key] = files_by_language.get(lang_key, 0) + 1

                try:
                    source_bytes = entry.read_bytes()
                    source_text = source_bytes.decode("utf-8", errors="replace")
                except Exception:
                    continue

                lines = source_text.splitlines()
                file_total = len(lines)
                file_code = 0
                file_blank = 0
                file_comment = 0
                in_block_comment = False

                comment_prefixes = _COMMENT_PREFIXES.get(lang_key, ())

                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        file_blank += 1
                        continue

                    # Block comment handling (/* ... */)
                    if in_block_comment:
                        file_comment += 1
                        if "*/" in stripped:
                            in_block_comment = False
                        continue
                    if "/*" in stripped and "*/" not in stripped:
                        file_comment += 1
                        in_block_comment = True
                        continue
                    if stripped.startswith("/*") and stripped.endswith("*/"):
                        file_comment += 1
                        continue

                    # Single-line comment detection
                    if comment_prefixes and any(stripped.startswith(p) for p in comment_prefixes):
                        file_comment += 1
                        continue

                    # Python triple-quoted strings as comments (docstrings)
                    if lang_key == "python" and (stripped.startswith('"""') or stripped.startswith("'''")):
                        file_comment += 1
                        if stripped.count('"""') < 2 and stripped.count("'''") < 2:
                            in_block_comment = True
                        continue

                    file_code += 1

                total_lines += file_total
                code_lines += file_code
                blank_lines += file_blank
                comment_lines += file_comment

                # Calculate complexity for this file
                if lang_key in _COMPLEXITY_NODE_TYPES:
                    ntypes = _COMPLEXITY_NODE_TYPES[lang_key]
                    parser = _get_parser(lang_key)
                    lang_obj = _get_language(lang_key)
                    if parser is not None and lang_obj is not None:
                        tree = parser.parse(source_bytes)
                        if tree is not None:
                            from tree_sitter import Query, QueryCursor
                            fq = _FUNCTION_QUERIES.get(lang_key)
                            if fq:
                                try:
                                    func_query = Query(lang_obj, fq)
                                except Exception:
                                    func_query = None
                                if func_query:
                                    for _pi, cd in QueryCursor(func_query).matches(tree.root_node):
                                        name = ""
                                        for nn in cd.get("name", []):
                                            try:
                                                name = source_bytes[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
                                            except Exception:
                                                name = "?"
                                            break
                                        for dn in cd.get("def", []):
                                            branches = _count_nodes(dn, ntypes.get("branches", []))
                                            loops = _count_nodes(dn, ntypes.get("loops", []))
                                            exceptions = _count_nodes(dn, ntypes.get("exceptions", []))
                                            early_returns = _count_early_returns(dn, dn, ntypes.get("return_type", "return_statement"))
                                            total = 1 + branches + loops + exceptions + early_returns
                                            all_complexities.append({
                                                "function": name,
                                                "file": str(entry),
                                                "line": dn.start_point[0] + 1,
                                                "total": total,
                                            })
                                            break

    if total_files == 0:
        return fmt_err("No source files found in directory")

    comment_ratio = round(comment_lines / code_lines, 4) if code_lines > 0 else 0.0
    avg_complexity = round(sum(c["total"] for c in all_complexities) / len(all_complexities), 2) if all_complexities else 0.0

    # Top 5 most complex functions
    all_complexities.sort(key=lambda c: c["total"], reverse=True)
    top_complexity = all_complexities[:5]

    result = {
        "path": str(target),
        "total_files": total_files,
        "files_by_language": dict(sorted(files_by_language.items(), key=lambda x: -x[1])),
        "total_lines": total_lines,
        "code_lines": code_lines,
        "blank_lines": blank_lines,
        "comment_lines": comment_lines,
        "comment_ratio": comment_ratio,
        "avg_complexity": avg_complexity,
        "functions_analyzed": len(all_complexities),
        "top_complexity": top_complexity,
    }

    return fmt_json(result)


CODE_METRICS_SCHEMA = {
    "name": "code_metrics",
    "description": "Aggregate project metrics: LOC, files per language, comment ratio, average complexity.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path (default: current dir)"},
            "depth": {"type": "integer", "description": "Max scan depth (default: 5)"},
        },
        "required": [],
    },
}


def _handle_code_metrics(args, **kw):
    return code_metrics_tool(
        path=args.get("path", "."),
        depth=args.get("depth", 5),
    )


# ---------------------------------------------------------------------------
# B2: code_impact — Impact analysis for symbol or file changes
# ---------------------------------------------------------------------------

CODE_IMPACT_SCHEMA = {
    "name": "code_impact",
    "description": (
        "Impact analysis before refactors or API changes. For a symbol or file, shows "
        "affected files, reference counts, test coverage, and confidence level. "
        "Use BEFORE making changes to understand blast radius."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number of the symbol to analyze"},
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path"],
    },
}


def _impact_file_level(target, language, base_r, _json):
    """Analyze imports for file-level impact analysis."""
    try:
        lang = language or detect_language(str(target))
        search_json = code_search_tool(str(target), preset="imports", language=lang)
        search_data = json.loads(search_json) if isinstance(search_json, str) else search_json
        if isinstance(search_data, dict):
            import_count = len(search_data.get("results", search_data.get("matches", [])))
        elif isinstance(search_data, list):
            import_count = len(search_data)
        else:
            import_count = 0
        base_r["reference_count"] = import_count
        base_r["reference_type"] = "file-level"
        return fmt_json(base_r)
    except Exception as exc:
        return fmt_err(f"Unable to analyze imports: {exc}")


def code_impact_tool(path: str, line: int = 0, language: Optional[str] = None) -> str:
    """Impact analysis for a symbol or file. Returns affected files, reference counts, test coverage."""
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    base_r = {
        "path": str(target),
        "files_affected": [],
        "test_files": [],
        "reference_count": 0,
        "direct_refs": 0,
        "indirect_refs": 0,
        "risk_level": "low",
        "confidence": "low",
    }

    # File-level: count imports via tree-sitter
    if line == 0:
        return _impact_file_level(target, language, base_r, _json)

    # Symbol-level: use lsp_bridge for cross-file resolution
    try:
        from .lsp_bridge import code_references_tool
    except ImportError:
        return fmt_err("lsp_bridge not available")

    lang = language or detect_language(str(target))
    try:
        refs_json = code_references_tool(
            str(target), line,
            language=lang,
            include_declaration=False,
            group_by_file=True,
        )
        refs_data = _json.loads(refs_json)
        by_file = refs_data.get("by_file", {}) if isinstance(refs_data, dict) else {}
    except Exception:
        return fmt_err("Failed to resolve references")

    direct_refs = 0
    test_files = []
    files_affected = []
    for fpath, locations in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
        cnt = len(locations)
        direct_refs += cnt
        is_test = "test" in fpath.lower() or "spec" in fpath.lower()
        files_affected.append({"path": fpath, "reference_count": cnt, "test": is_test})
        if is_test:
            test_files.append(fpath)

    b = {**base_r, "direct_refs": direct_refs, "reference_count": direct_refs,
         "files_affected": files_affected[:20], "test_files": test_files[:10]}
    b["confidence"] = "high" if direct_refs > 10 else ("medium" if direct_refs > 3 else "low")
    b["risk_level"] = "high" if direct_refs > 30 else ("medium" if direct_refs > 10 else "low")
    return fmt_json(b)


def _handle_code_impact(args, **kw):
    return code_impact_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# B1b: code_complexity — Cyclomatic Complexity Analysis
# ---------------------------------------------------------------------------

# Language -> AST node types for complexity counting
_COMPLEXITY_NODE_TYPES: dict = {
    "python": {
        "branches": ["if_statement", "elif_clause"],
        "loops": ["for_statement", "while_statement"],
        "exceptions": ["except_clause", "finally_clause"],
        "return_type": "return_statement",
    },
    "typescript": {
        "branches": ["if_statement", "switch_case", "ternary_expression"],
        "loops": ["for_statement", "for_in_statement", "while_statement", "do_statement"],
        "exceptions": ["catch_clause", "finally_clause"],
        "return_type": "return_statement",
    },
    "tsx": {
        "branches": ["if_statement", "switch_case", "ternary_expression"],
        "loops": ["for_statement", "for_in_statement", "while_statement", "do_statement"],
        "exceptions": ["catch_clause", "finally_clause"],
        "return_type": "return_statement",
    },
    "go": {
        "branches": ["if_statement", "switch_statement", "select_statement"],
        "loops": ["for_statement", "range_clause"],
        "exceptions": [],
        "return_type": "return_statement",
    },
    "rust": {
        "branches": ["if_expression", "match_expression", "match_arm"],
        "loops": ["for_expression", "loop_expression", "while_expression"],
        "exceptions": [],
        "return_type": "return_expression",
    },
}

# Function-finding queries per language
_FUNCTION_QUERIES: dict = {
    "python": """
(function_definition
    name: (identifier) @name
) @def
""",
    "typescript": """
(function_declaration
    name: (identifier) @name
) @def
(method_definition
    name: (property_identifier) @name
) @def
""",
    "tsx": """
(function_declaration
    name: (identifier) @name
) @def
(method_definition
    name: (property_identifier) @name
) @def
""",
    "go": """
(function_declaration
    name: (identifier) @name
) @def
(method_declaration
    name: (field_identifier) @name
) @def
""",
    "rust": """
(function_item
    name: (identifier) @name
) @def
""",
}


def _count_nodes(node, types: list) -> int:
    """Count descendants with matching node types."""
    count = 0
    if node.type in types:
        count += 1
    for child in node.named_children:
        count += _count_nodes(child, types)
    return count


def _count_early_returns(node, body_node, return_type: str) -> int:
    """Count returns that are NOT the last statement in the function body."""
    count = 0
    if node.type == return_type:
        try:
            children = list(body_node.named_children)
            if node is not children[-1]:
                count += 1
        except Exception:
            logger.debug("_count_early_returns: AST parse error for node type %s", node.type)
            count += 1
    for child in node.named_children:
        count += _count_early_returns(child, body_node, return_type)
    return count


def code_complexity_tool(
    path: str,
    function: str = "",
    line: int = 0,
    language: str = "",
    directory: bool = False,
) -> str:
    """Calculate cyclomatic complexity for a function.

    Analyzes branches, loops, exceptions, and early returns.
    Reports total complexity with breakdown and rank (A-E).

    When directory=True, scans all source files recursively and returns
    a sorted project-level hotspot report (functions with highest complexity first).
    """
    from pathlib import Path as _Path

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # ── Directory mode: scan all source files ──────────────────────
    if directory:
        if not target.is_dir():
            return fmt_err(f"Not a directory: {path}")

        _EXT_MAP = {
            ".py": "python", ".ts": "typescript", ".tsx": "tsx",
            ".js": "typescript", ".jsx": "tsx",
            ".go": "go", ".rs": "rust",
        }

        all_results = []
        for ext, lang_key in _EXT_MAP.items():
            ntypes = _COMPLEXITY_NODE_TYPES.get(lang_key)
            if not ntypes:
                continue
            parser = _get_parser(lang_key)
            lang_obj = _get_language(lang_key)
            if parser is None or lang_obj is None:
                continue

            for fpath in sorted(target.rglob(f"*{ext}")):
                # Skip node_modules, .git, __pycache__, build dirs
                parts = fpath.parts
                if any(p in parts for p in ("node_modules", ".git", "__pycache__", "build", "dist", ".venv")):
                    continue
                try:
                    source_bytes = fpath.read_bytes()
                except OSError:
                    continue

                tree = parser.parse(source_bytes)
                if tree is None:
                    continue

                from tree_sitter import Query, QueryCursor
                fq = _FUNCTION_QUERIES.get(lang_key)
                if not fq:
                    continue
                try:
                    func_query = Query(lang_obj, fq)
                except Exception:
                    continue

                for _pi, cd in QueryCursor(func_query).matches(tree.root_node):
                    name = ""
                    for nn in cd.get("name", []):
                        try:
                            name = source_bytes[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
                        except Exception:
                            name = "?"
                        break
                    for dn in cd.get("def", []):
                        branches = _count_nodes(dn, ntypes.get("branches", []))
                        loops = _count_nodes(dn, ntypes.get("loops", []))
                        exceptions = _count_nodes(dn, ntypes.get("exceptions", []))
                        early_returns = _count_early_returns(dn, dn, ntypes.get("return_type", "return_statement"))
                        total = 1 + branches + loops + exceptions + early_returns
                        rank = "A" if total <= 10 else "B" if total <= 20 else "C" if total <= 30 else "D" if total <= 40 else "E"
                        all_results.append({
                            "function": name,
                            "file": str(fpath),
                            "line": dn.start_point[0] + 1,
                            "total": total,
                            "rank": rank,
                        })
                        break

        if not all_results:
            return fmt_err("No functions found in directory")

        all_results.sort(key=lambda r: r["total"], reverse=True)
        top = all_results[:50]
        summary = {
            "mode": "directory",
            "path": str(target),
            "total_functions": len(all_results),
            "total_files": len(set(r["file"] for r in all_results)),
            "hotspots": top,
        }
        return fmt_json(summary)

    # ── Single-file mode (original logic) ──────────────────────────
    lang_key = language or detect_language(str(target))
    if not lang_key:
        return fmt_err("Could not detect language")
    if lang_key not in _COMPLEXITY_NODE_TYPES:
        return fmt_err(f"Unsupported language: {lang_key}")

    ntypes = _COMPLEXITY_NODE_TYPES[lang_key]

    parser = _get_parser(lang_key)
    lang_obj = _get_language(lang_key)
    if parser is None or lang_obj is None:
        return fmt_err(f"Parser init failed for {lang_key}")

    try:
        with open(str(target), "rb") as f:
            source_bytes = f.read()
    except OSError as exc:
        return fmt_err(f"Cannot read: {exc}")

    tree = parser.parse(source_bytes)
    if tree is None:
        return fmt_err("Parse failed")

    from tree_sitter import Query, QueryCursor

    fq = _FUNCTION_QUERIES.get(lang_key)
    try:
        func_query = Query(lang_obj, fq)
    except Exception as exc:
        return fmt_err(f"Query failed: {exc}")

    functions = []
    qc = QueryCursor(func_query)
    for _pi, cd in qc.matches(tree.root_node):
        name = ""
        for nn in cd.get("name", []):
            try:
                name = source_bytes[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
            except Exception:
                name = "?"
            break
        for dn in cd.get("def", []):
            functions.append({
                "name": name,
                "node": dn,
                "line": dn.start_point[0] + 1,
                "end_line": dn.end_point[0] + 1,
            })

    if not functions:
        return fmt_err("No functions found")

    selected = functions[0]
    if function:
        for f in functions:
            if f["name"] == function:
                selected = f
                break
    elif line:
        for f in functions:
            if f["line"] <= line <= f["end_line"]:
                selected = f
                break

    fn_node = selected["node"]

    branches = _count_nodes(fn_node, ntypes.get("branches", []))
    loops = _count_nodes(fn_node, ntypes.get("loops", []))
    exceptions = _count_nodes(fn_node, ntypes.get("exceptions", []))
    early_returns = _count_early_returns(fn_node, fn_node, ntypes.get("return_type", "return_statement"))

    total = 1 + branches + loops + exceptions + early_returns

    if total <= 10:
        rank = "A"
    elif total <= 20:
        rank = "B"
    elif total <= 30:
        rank = "C"
    elif total <= 40:
        rank = "D"
    else:
        rank = "E"

    result = {
        "function": selected["name"],
        "path": str(target),
        "line": selected["line"],
        "total": total,
        "rank": rank,
        "breakdown": {
            "base": 1,
            "branches": branches,
            "loops": loops,
            "exceptions": exceptions,
            "early_returns": early_returns,
        },
        "recommendation": "",
    }
    if total > 20:
        result["recommendation"] = "Consider extracting sub-functions to reduce complexity."
    if total > 30:
        result["recommendation"] = "High complexity - refactoring strongly recommended."

    return fmt_json(result)


# Schema + Handler + Registration
CODE_COMPLEXITY_SCHEMA = {
    "name": "code_complexity",
    "description": "Calculate cyclomatic complexity for a function or scan directory for hotspots. "
                   "Analyzes branches, loops, exceptions, and early returns. "
                   "Reports total complexity with breakdown and rank (A-E). "
                   "Set directory=True for project-level hotspot analysis.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "function": {"type": "string", "description": "Function name to analyze (optional, analyzes first if omitted)"},
            "line": {"type": "integer", "description": "1-based line number (optional, finds function at this line)"},
            "language": {"type": "string", "description": "Language override"},
            "directory": {"type": "boolean", "description": "Scan directory recursively for complexity hotspots (default: false)"},
        },
        "required": ["path"],
    },
}


def _handle_code_complexity(args, **kw):
    return code_complexity_tool(
        path=args.get("path", ""),
        function=args.get("function", ""),
        line=args.get("line", 0),
        language=args.get("language", ""),
        directory=args.get("directory", False),
    )


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

_ERROR_SUPPORTED_LANGS = set(_ERROR_QUERIES.keys())


def code_search_by_error_tool(
    path: str,
    error: str,
    language: str = "",
) -> str:
    """Find all places that handle specific error types.

    Searches for:
    - raise/throw sites
    - catch/except sites
    - custom error class definitions

    Args:
        path: File or directory to search.
        error: Error type name (e.g. "ValidationError", "ValueError").
        language: Language filter (optional).

    Returns:
        Formatted result with matches grouped by category.
    """
    from pathlib import Path as _Path

    search_path = _Path(path).expanduser().resolve()
    if not search_path.exists():
        return fmt_err(f"Path not found: {path}")

    from tree_sitter import Query, QueryCursor

    # Collect files to search
    files_to_search = []
    if search_path.is_file():
        files_to_search.append(search_path)
    else:
        for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"]:
            for f in search_path.rglob(f"*{ext}"):
                rel = f.relative_to(search_path)
                parts = rel.parts
                if any(p in _ERROR_EXCLUDE_DIRS for p in parts):
                    continue
                files_to_search.append(f)

    if not files_to_search:
        return fmt_err("No source files found")

    # Search each file
    raise_sites: list = []
    catch_sites: list = []
    custom_sites: list = []

    for f in files_to_search:
        lang_key = language or detect_language(str(f))
        if not lang_key or lang_key not in _ERROR_SUPPORTED_LANGS:
            continue

        query_source = _ERROR_QUERIES.get(lang_key)
        if not query_source:
            continue

        lang_obj = _get_language(lang_key)
        parser = _get_parser(lang_key)
        if parser is None or lang_obj is None:
            continue

        try:
            q = Query(lang_obj, query_source)
        except Exception:
            continue

        try:
            with open(str(f), "rb") as sf:
                source_bytes = sf.read()
        except OSError:
            continue

        tree = parser.parse(source_bytes)
        if tree is None:
            continue

        qc2 = QueryCursor(q)
        for _pi, cd in qc2.matches(tree.root_node):
            errors_found = set()
            for n in cd.get("error_name", []):
                try:
                    name = source_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
                except Exception:
                    continue
                errors_found.add(name)


            if error not in errors_found:
                continue

            line = 0
            for dn in cd.get("raise_site", cd.get("throw_site", cd.get("return_site", cd.get("catch_site", cd.get("custom_site", []))))):
                line = dn.start_point[0] + 1
                break

            for _rn in cd.get("raise_site", []):
                raise_sites.append({"file": str(f), "line": line})
            for _tn in cd.get("throw_site", []):
                raise_sites.append({"file": str(f), "line": line})
            for _rn2 in cd.get("return_site", []):
                raise_sites.append({"file": str(f), "line": line})
            for _cn in cd.get("catch_site", []):
                catch_sites.append({"file": str(f), "line": line})
            for _cs in cd.get("custom_site", []):
                custom_sites.append({"file": str(f), "line": line})

    result = {
        "error": error,
        "results": {
            "raise/throw": sorted(raise_sites, key=lambda x: x["file"]),
            "catch/except": sorted(catch_sites, key=lambda x: x["file"]),
            "custom_classes": sorted(custom_sites, key=lambda x: x["file"]),
        },
        "total": len(raise_sites) + len(catch_sites) + len(custom_sites),
    }

    return fmt_json(result)


# Schema + Handler + Registration
CODE_SEARCH_BY_ERROR_SCHEMA = {
    "name": "code_search_by_error",
    "description": "Find all places that handle specific error types. "
                   "Searches for raise/throw sites, catch/except blocks, "
                   "and custom error class definitions. Supports Python, TypeScript, Go, Rust.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory to search"},
            "error": {"type": "string", "description": "Error type name (e.g. 'ValidationError', 'ValueError')"},
            "language": {"type": "string", "description": "Language filter (optional)"},
        },
        "required": ["path", "error"],
    },
}


def _handle_code_search_by_error(args, **kw):
    return code_search_by_error_tool(
        path=args.get("path", ""),
        error=args.get("error", ""),
        language=args.get("language", ""),
    )


# ---------------------------------------------------------------------------
# B3a: code_hot_paths — Hot Path Detection via ImportGraph
# ---------------------------------------------------------------------------

def code_hot_paths_tool(
    path: str,
    top_n: int = 10,
    depth: int = 5,
) -> str:
    """Find the most-imported files (hot paths) in a project.

    Uses ImportGraph to scan the project and rank files by
    transitive caller count.

    Args:
        path: Project root directory to scan.
        top_n: Number of top results (default: 10).
        depth: Scan depth for subdirectories (default: 5).

    Returns:
        JSON with ranked hot paths.
    """
    from pathlib import Path as _Path

    root = _Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return fmt_err(f"Directory not found: {path}")

    try:
        from ._import_graph import ImportGraph
    except ImportError:
        return fmt_err("ImportGraph not available")

    g = ImportGraph(str(root))
    g.scan(depth=depth)
    if not g.files:
        return fmt_err("No source files found")

    g.parse_all()
    hot = g.find_hot_paths(top_n=top_n)

    result = {
        "project": str(root),
        "total_files": len(g.files),
        "total_edges": sum(len(v) for v in g.graph.values()),
        "top_n": top_n,
        "hot_paths": hot,
    }
    return fmt_json(result)


CODE_HOT_PATHS_SCHEMA = {
    "name": "code_hot_paths",
    "description": "Find the most-imported files (hot paths) in a project. "
                   "Uses ImportGraph to rank files by transitive caller count.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root directory"},
            "top_n": {"type": "integer", "description": "Number of top results (default: 10)"},
            "depth": {"type": "integer", "description": "Scan depth (default: 5)"},
        },
        "required": ["path"],
    },
}


def _handle_code_hot_paths(args, **kw):
    return code_hot_paths_tool(
        path=args.get("path", ""),
        top_n=args.get("top_n", 10),
        depth=args.get("depth", 5),
    )


def code_cycle_detector_tool(
    path: str,
    max_cycles: int = 20,
    depth: int = 5,
) -> str:
    """Find circular import chains in a project using ImportGraph.

    Uses Tarjan's strongly-connected-components algorithm on the
    project's import graph to detect cycles. A cycle of length >1
    means file A imports B and B imports A (directly or transitively).

    Args:
        path: Project root directory to scan.
        max_cycles: Max cycles to report (default: 20, 0 = unlimited).
        depth: Scan depth for subdirectories (default: 5).

    Returns:
        JSON with list of cycles, each showing the files in the cycle.
    """
    from pathlib import Path as _Path

    root = _Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return fmt_err(f"Directory not found: {path}")

    try:
        from ._import_graph import ImportGraph
    except ImportError:
        return fmt_err("ImportGraph not available")

    g = ImportGraph(str(root))
    g.scan(depth=depth)
    if not g.files:
        return fmt_err("No source files found")

    g.parse_all()
    cycles = g.find_cycles()

    # Filter: cycles of length > 1 (trivial self-imports are not interesting)
    real_cycles = [c for c in cycles if len(c) > 1]

    if max_cycles and max_cycles > 0:
        real_cycles = real_cycles[:max_cycles]

    # Build detailed output: for each cycle, trace the import edges
    detailed = []
    for cycle in real_cycles:
        edges = []
        n = len(cycle)
        for i in range(n):
            a = cycle[i]
            b = cycle[(i + 1) % n]
            callees = g.graph.get(a, set())
            if b in callees:
                edges.append(f"{a} \u2192 {b}")
        detailed.append({
            "cycle": cycle,
            "length": n,
            "edges": edges,
        })

    result = {
        "project": str(root),
        "total_files": len(g.files),
        "total_edges": sum(len(v) for v in g.graph.values()),
        "cycles_found": len(real_cycles),
        "cycles": detailed,
    }
    return fmt_json(result)


CODE_CYCLE_DETECTOR_SCHEMA = {
    "name": "code_cycle_detector",
    "description": "Find circular import chains in a project. "
                   "Uses ImportGraph with Tarjan's SCC algorithm.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root directory"},
            "max_cycles": {"type": "integer", "description": "Max cycles to report (default: 20, 0 = unlimited)"},
            "depth": {"type": "integer", "description": "Scan depth (default: 5)"},
        },
        "required": ["path"],
    },
}


def _handle_code_cycle_detector(args, **kw):
    return code_cycle_detector_tool(
        path=args.get("path", ""),
        max_cycles=args.get("max_cycles", 20),
        depth=args.get("depth", 5),
    )


def code_dependency_graph_tool(
    path: str,
    format: str = "mermaid",
    direction: str = "LR",
    module_level: bool = False,
    depth: int = 5,
) -> str:
    """Generate a visual dependency graph for a project using ImportGraph.

    Supports Mermaid flowchart format and ASCII tree view.

    Args:
        path: Project root directory to scan.
        format: Output format — "mermaid" (default) or "tree".
        direction: Mermaid graph direction — "LR" (left-right, default) or "TD" (top-down).
        module_level: When True, show module-level paths instead of full file paths.
        depth: Scan depth for subdirectories (default: 5).

    Returns:
        Mermaid code block or ASCII tree string.
    """
    from pathlib import Path as _Path

    root = _Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return fmt_err(f"Directory not found: {path}")

    try:
        from ._import_graph import ImportGraph
    except ImportError:
        return fmt_err("ImportGraph not available")

    g = ImportGraph(str(root))
    g.scan(depth=depth)
    if not g.files:
        return fmt_err("No source files found")

    g.parse_all()

    fmt = format.lower()
    if fmt == "mermaid":
        return g.to_mermaid(direction=direction, module_level=module_level)
    elif fmt == "tree":
        return g.to_tree()
    else:
        return fmt_err(f"Unknown format: {format}. Use 'mermaid' or 'tree'.")


CODE_DEPENDENCY_GRAPH_SCHEMA = {
    "name": "code_dependency_graph",
    "description": "Generate a visual dependency graph for a project. "
                   "Supports Mermaid flowchart and ASCII tree view.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root directory"},
            "format": {"type": "string", "description": "Output format: 'mermaid' (default) or 'tree'", "enum": ["mermaid", "tree"]},
            "direction": {"type": "string", "description": "Mermaid graph direction: 'LR' (left-right) or 'TD' (top-down)", "enum": ["LR", "TD"]},
            "module_level": {"type": "boolean", "description": "Show module-level paths instead of full file paths (default: false)"},
            "depth": {"type": "integer", "description": "Scan depth (default: 5)"},
        },
        "required": ["path"],
    },
}


def _handle_code_dependency_graph(args, **kw):
    return code_dependency_graph_tool(
        path=args.get("path", ""),
        format=args.get("format", "mermaid"),
        direction=args.get("direction", "LR"),
        module_level=args.get("module_level", False),
        depth=args.get("depth", 5),
    )


# ---------------------------------------------------------------------------
# B4: code_blast_radius — Blast Radius Analysis
# ---------------------------------------------------------------------------

def code_blast_radius_tool(
    path: str,
    line: int,
    character: int = 0,
    depth: int = 3,
    language: str = "",
    test_coverage: bool = True,
) -> str:
    """Analyze blast radius of a symbol — what breaks if you change it.

    Combines LSP callHierarchy (direct callers) + ImportGraph (transitive)
    + test coverage analysis to provide a complete impact report.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (auto-detected if omitted).
        depth: Maximum transitive depth (default: 3, max: 5).
        language: Language override.

    Returns:
        Formatted impact report.
    """
    import json as _json
    from pathlib import Path as _Path

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language
    if not lang:
        lang = detect_language(str(target))
    if not lang:
        return fmt_err("Could not detect language")

    depth = min(depth, 5)

    # Step 1: Direct callers via LSP callHierarchy
    from .lsp_bridge import (
        LSPBridge,
        _auto_detect_identifier_column,
        _detect_language_for_lsp,
        get_lsp_manager,
    )

    col = character
    if not col:
        col = _auto_detect_identifier_column(str(target), line - 1) or 1

    lsp_lang = language or _detect_language_for_lsp(str(target))
    direct_callers = []
    manager = get_lsp_manager()
    bridge = manager.get_bridge(lsp_lang, str(target)) if lsp_lang else None
    if bridge and bridge.ensure_initialized():
        try:
            items = bridge.incoming_calls(str(target), line - 1, col - 1)
            if items:
                for item in items:
                    file_path = LSPBridge._uri_to_path(item.get("uri", ""))
                    direct_callers.append({
                        "file": file_path,
                        "line": (item.get("range", {}) or {}).get("start", {}).get("line", 0) + 1,
                        "name": item.get("name", "?"),
                    })
        except Exception:
            pass

    # Step 2: Transitive callers via ImportGraph
    transitive = {}
    try:
        from ._import_graph import ImportGraph
        g = ImportGraph(str(target.parent))
        g.scan(depth=5)
        g.parse_all()
        tr = g.analyze_blast_radius(str(target), depth=depth)
        if tr["total"] > 0:
            transitive = tr
    except Exception:
        pass

    # Step 3: Tests via code_tests_for_symbol_tool
    tests_found = []
    if test_coverage:
        try:
            tests_raw = code_tests_for_symbol_tool(
                path=str(target), line=line, language=lang
            )
            if tests_raw:
                try:
                    tests_data = _json.loads(tests_raw)
                    if "tests" in tests_data:
                        tests_found = tests_data["tests"]
                except Exception:
                    pass
        except Exception:
            pass

    # Step 4: Impact classification
    nc = len(direct_callers)
    tc = transitive.get("total", 0)
    if nc > 10 or tc > 20:
        impact = "HIGH"
    elif nc > 0 or tc > 0:
        impact = "MEDIUM"
    else:
        impact = "LOW"

    result = {
        "symbol": target.name,
        "path": str(target),
        "line": line,
        "impact": impact,
        "depth": depth,
        "direct_callers": {
            "count": len(direct_callers),
            "items": direct_callers[:50],
        },
        "transitive_callers": {
            "count": tc,
            "levels": {str(k): v for k, v in transitive.get("levels", {}).items()},
        },
        "test_coverage": {
            "count": len(tests_found),
            "items": tests_found[:20],
        },
        "recommendation": "",
    }

    if impact == "HIGH":
        result["recommendation"] = "High impact — review all callers before making changes."
    elif tc > 0 and not tests_found:
        result["recommendation"] = "Untested transitive callers — add tests first."
    elif not direct_callers:
        result["recommendation"] = "Low impact — appears unused or private."

    return fmt_json(result)


CODE_BLAST_RADIUS_SCHEMA = {
    "name": "code_blast_radius",
    "description": "Analyze blast radius of a symbol — what breaks if you change it. "
                   "Combines LSP callHierarchy (direct callers), ImportGraph (transitive), "
                   "and test coverage analysis.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
            "depth": {"type": "integer", "description": "Max transitive depth (default: 3, max: 5)"},
            "language": {"type": "string", "description": "Language override"},
            "test_coverage": {"type": "boolean", "description": "Include test coverage analysis (default: true)"},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_blast_radius(args, **kw):
    return code_blast_radius_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        character=args.get("character", 0),
        depth=args.get("depth", 3),
        language=args.get("language", ""),
        test_coverage=args.get("test_coverage", True),
    )


# ---------------------------------------------------------------------------
# C1: code_pr_impact — PR Impact Analysis (git diff + ImportGraph)
# ---------------------------------------------------------------------------


def code_pr_impact_tool(
    base_branch: str = "main",
    auto_detect: bool = True,
    path: str = ".",
    max_files: int = 10,
) -> str:
    """Analyze the impact of a PR by combining git diff with ImportGraph.

    Shows changed functions, blast radius, test coverage, reviewers,
    and a suggested commit message.

    Args:
        base_branch: Git base branch to diff against (default: "main").
        auto_detect: Auto-detect base branch via git (main/develop/release) before falling back to base_branch. (default: True).
        path: Project root path (default: current dir).
        max_files: Max files to analyze in large diffs (default: 10).

    Returns:
        Formatted impact report.
    """

    import subprocess as _sp
    from pathlib import Path as _Path

    # --- auto-detect base branch ---
    if auto_detect:
        try:
            result = _sp.run(
                ['git', 'branch', '-r'], capture_output=True, text=True,
                cwd=str(_Path(path).expanduser().resolve()), timeout=5,
            )
            if result.returncode == 0:
                for candidate in ['origin/main', 'origin/develop', 'origin/release', 'origin/master']:
                    if candidate in result.stdout:
                        base_branch = candidate.replace('origin/', '')
                        break
        except Exception:
            pass  # fallback to base_branch default
    # --- end auto-detect ---

    root = _Path(path).expanduser().resolve()
    if not root.exists():
        return fmt_err(f"Path not found: {path}")

    # Step 1: git diff
    try:
        diff_result = _sp.run(
            ["git", "diff", base_branch, "--diff-filter=AM", "--", "*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.go", "*.rs"],
            capture_output=True, text=True, cwd=str(root), timeout=30,
        )
        if diff_result.returncode != 0:
            return fmt_err(f"git diff failed: {diff_result.stderr.strip() or 'unknown error'}")
        diff_output = diff_result.stdout
    except FileNotFoundError:
        return fmt_err("Not a git repository or git not installed")
    except _sp.TimeoutExpired:
        return fmt_err("git diff timed out")

    if not diff_output.strip():
        return fmt_ok({"message": f"No changes detected against {base_branch}", "changes": []})

    # Step 2: Parse changed files
    changed_files: set = set()
    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            file_path = line[6:].strip()
            if file_path and not file_path.startswith("/dev"):
                changed_files.add(file_path)

    if not changed_files:
        return fmt_ok({"message": "No source files changed", "changes": []})

    changed_list = sorted(changed_files)[:max_files]
    total_changed = len(changed_files)

    # Step 3: Analyze each changed file
    from ._import_graph import ImportGraph

    g = ImportGraph(str(root))
    g.scan(depth=5)

    changed_functions = []
    total_blast = {"direct": 0, "transitive": 0}

    for cf in changed_list:
        abs_path = str((root / cf).resolve())
        if not _Path(abs_path).exists():
            continue

        functions_in_file = _find_functions_in_file(abs_path)
        for func in functions_in_file:
            func["file"] = cf
            try:
                tr = g.analyze_blast_radius(abs_path, depth=2)
                func["transitive_callers"] = tr.get("total", 0)
                total_blast["transitive"] += tr.get("total", 0)
            except Exception:
                func["transitive_callers"] = 0
            changed_functions.append(func)

        try:
            g.parse_all()
        except Exception:
            pass

    total_blast["direct"] = len(changed_functions)

    # Step 4: Test coverage gaps
    test_gaps = []
    for func in changed_functions:
        has_test = False
        for tf in root.rglob("*test*"):
            if tf.suffix in (".py", ".ts", ".tsx"):
                try:
                    content = tf.read_text()
                    if func.get("name") and func["name"] in content:
                        has_test = True
                        break
                except Exception:
                    continue
        if not has_test:
            test_gaps.append(func)

    # Step 5: Suggested reviewers via git blame
    reviewers: dict = {}
    for cf in changed_list[:5]:
        try:
            blame = _sp.run(
                ["git", "blame", "--line-porcelain", cf],
                capture_output=True, text=True, cwd=str(root), timeout=10,
            )
            if blame.returncode == 0:
                for line in blame.stdout.splitlines():
                    if line.startswith("author "):
                        author = line[7:].strip()
                        reviewers[author] = reviewers.get(author, 0) + 1
        except Exception:
            continue

    suggested_reviewers = sorted(reviewers.items(), key=lambda x: x[1], reverse=True)[:5]

    # Step 6: Build report
    total_added = sum(1 for line in diff_output.splitlines() if line.startswith("+") and not line.startswith("+++"))
    total_removed = sum(1 for line in diff_output.splitlines() if line.startswith("-") and not line.startswith("---"))

    result = {
        "base_branch": base_branch,
        "files_changed": total_changed,
        "files_analyzed": len(changed_list),
        "lines_added": total_added,
        "lines_removed": total_removed,
        "changed_functions": changed_functions[:50],
        "blast_radius": {
            "direct_callers": total_blast["direct"],
            "transitive_callers": total_blast["transitive"],
        },
        "test_gaps": len(test_gaps),
        "untested_functions": [{"name": f.get("name"), "file": f.get("file"), "line": f.get("line")} for f in test_gaps[:10]],
        "suggested_reviewers": [{"name": name, "lines": count} for name, count in suggested_reviewers],
    }

    if total_changed > max_files:
        result["warning"] = f"Large diff ({total_changed} files) — showing top {max_files}"

    return fmt_json(result)


def _find_functions_in_file(file_path: str) -> list:
    """Find all function names in a source file via tree-sitter."""
    from tree_sitter import Query, QueryCursor

    lang_key = detect_language(file_path)
    if not lang_key:
        return []

    fn_queries = {
        "python": "(function_definition name: (identifier) @name) @def",
        "typescript": "(function_declaration name: (identifier) @name) @def\n(method_definition name: (property_identifier) @name) @def",
        "tsx": "(function_declaration name: (identifier) @name) @def\n(method_definition name: (property_identifier) @name) @def",
        "go": "(function_declaration name: (identifier) @name) @def\n(method_declaration name: (field_identifier) @name) @def",
        "rust": "(function_item name: (identifier) @name) @def",
    }

    qs = fn_queries.get(lang_key)
    if not qs:
        return []

    lang_obj = _get_language(lang_key)
    parser = _get_parser(lang_key)
    if not parser or not lang_obj:
        return []

    try:
        q = Query(lang_obj, qs)
    except Exception:
        return []

    try:
        with open(file_path, "rb") as f:
            src = f.read()
    except OSError:
        return []

    tree = parser.parse(src)
    if not tree:
        return []

    functions = []
    qc = QueryCursor(q)
    for _pi, cd in qc.matches(tree.root_node):
        name = ""
        for nn in cd.get("name", []):
            try:
                name = src[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
            except Exception:
                name = "?"
            break
        for dn in cd.get("def", []):
            functions.append({
                "name": name,
                "line": dn.start_point[0] + 1,
            })
    return functions


CODE_PR_IMPACT_SCHEMA = {
    "name": "code_pr_impact",
    "description": "Analyze the impact of a PR by combining git diff with ImportGraph. "
                   "Shows changed functions, blast radius, test coverage gaps, "
                   "suggested reviewers, and a commit hint.",
    "parameters": {
        "type": "object",
        "properties": {
            "base_branch": {"type": "string", "description": "Git base branch (default: main)"},
            "auto_detect": {"type": "boolean", "description": "Auto-detect base branch via git (main/develop/release). Falls True, wird base_branch ignoriert. (default: True)"},
            "path": {"type": "string", "description": "Project root path (default: current dir)"},
            "max_files": {"type": "integer", "description": "Max files in large diffs (default: 10)"},
        },
        "required": [],
    },
}


def _handle_code_pr_impact(args, **kw):
    return code_pr_impact_tool(
        base_branch=args.get("base_branch", "main"),
        auto_detect=args.get("auto_detect", True),
        path=args.get("path", "."),
        max_files=args.get("max_files", 10),
    )


# ---------------------------------------------------------------------------
# C2: code_tests_for_symbol — Find tests covering a specific symbol
# ---------------------------------------------------------------------------

CODE_TESTS_FOR_SYMBOL_SCHEMA = {
    "name": "code_tests_for_symbol",
    "description": (
        "Find tests that cover a specific symbol. Returns prioritized test files with "
        "relevance scores. Use before making changes to ensure safe refactoring."
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
        from .lsp_bridge import code_references_tool
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
        from .lsp_bridge import code_references_tool  # noqa: F401
    except ImportError:
        return fmt_err("lsp_bridge not available")

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


# ---------------------------------------------------------------------------
# C1: Query Router — auto-selects best backend for a given intent
# ---------------------------------------------------------------------------

_QUERY_INTENT_MAP = {
    "find_usage": ("code_references", "search_files"),
    "find_usages": ("code_references", "search_files"),
    "references": ("code_references", "search_files"),
    "definition": ("code_definition", "code_symbols"),
    "go_to_def": ("code_definition", "code_symbols"),
    "where_defined": ("code_definition", "code_symbols"),
    "rename": ("code_rename", "code_refactor"),
    "semantic_rename": ("code_rename", "code_refactor"),
    "refactor": ("code_refactor", "patch"),
    "safe_edit": ("code_rename", "code_refactor"),
    "understand": ("code_capsule", "code_symbols"),
    "what_is": ("code_capsule", "code_symbols"),
    "overview": ("code_workspace_summary", "code_symbols"),
    "structure": ("code_symbols", "read_file"),
    "symbols": ("code_symbols", "read_file"),
    "functions": ("code_symbols", "read_file"),
    "classes": ("code_symbols", "read_file"),
    "tests": ("code_tests_for_symbol", "search_files"),
    "test_coverage": ("code_tests_for_symbol", "search_files"),
    "impact": ("code_impact", "code_references"),
    "blast_radius": ("code_impact", "code_references"),
    "diagnostics": ("code_diagnostics", "code_symbols"),
    "errors": ("code_diagnostics", "search_files"),
    "warnings": ("code_diagnostics", "search_files"),
    "callers": ("code_callers", "code_references"),
    "who_calls": ("code_callers", "code_references"),
    "callees": ("code_callees", "code_symbols"),
    "what_calls": ("code_callees", "code_symbols"),
    "search_pattern": ("code_search", "search_files"),
    "find_pattern": ("code_search", "search_files"),
    "structural": ("code_search", "search_files"),
    # -- New LSP tools --
    "hover": ("code_hover", "code_capsule"),
    "type_info": ("code_hover", "code_capsule"),
    "docstring": ("code_hover", "code_capsule"),
    "signature": ("code_signatures", "code_hover"),
    "params": ("code_signatures", "code_hover"),
    "arguments": ("code_signatures", "code_hover"),
    "type_definition": ("code_type_definition", "code_definition"),
    "type_of": ("code_type_definition", "code_definition"),
    "interface": ("code_type_definition", "code_definition"),
    "quick_fix": ("code_action", "code_refactor"),
    "organize_imports": ("code_action", "code_refactor"),
    "auto_fix": ("code_action", "code_refactor"),
    "code_action": ("code_action", "code_refactor"),
    "find_symbol": ("code_workspace_symbols", "code_search"),
    "workspace_search": ("code_workspace_symbols", "code_search"),
    "cmd_t": ("code_workspace_symbols", "code_search"),
    # -- Symbol-level editing tools (v0.28.11) --
    "replace_body": ("code_replace_body", "code_refactor"),
    "replace_function": ("code_replace_body", "code_refactor"),
    "replace_method": ("code_replace_body", "code_refactor"),
    "safe_delete": ("code_safe_delete", "patch"),
    "delete_symbol": ("code_safe_delete", "patch"),
    "insert_before": ("code_insert_before", "patch"),
    "insert_after": ("code_insert_after", "patch"),
    "insert_symbol_before": ("code_insert_before", "patch"),
    "insert_symbol_after": ("code_insert_after", "patch"),
    # -- code_overview (v0.28.11) --
    "file_overview": ("code_overview", "code_symbols"),
    "symbol_overview": ("code_overview", "code_symbols"),
    "file_summary": ("code_overview", "code_symbols"),
}

CODE_QUERY_SCHEMA = {
    "name": "code_query",
    "description": (
        "Smart query router for code intelligence. Describe what you want to find "
        "(e.g. 'find_usage', 'definition', 'rename', 'impact', 'tests', "
        "'replace_body', 'safe_delete', 'insert_before') and it auto-selects "
        "the best tool. Returns routing decision + recommended args. "
        "If you already know which tool to call, call it directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "What you want: find_usage, definition, rename, understand, overview, tests, impact, diagnostics, callers, callees, structure, search_pattern, replace_body, safe_delete, insert_before, insert_after, file_overview",
            },
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "Optional 1-based line number"},
            "language": {"type": "string", "description": "Optional language override"},
        },
        "required": ["intent"],
    },
}

def code_query_tool(intent: str, path: Optional[str] = None, line: int = 0, language: Optional[str] = None) -> str:
    """Route a code intelligence query to the best available tool."""
    intent_lower = intent.lower().strip().replace(" ", "_")
    matched = _QUERY_INTENT_MAP.get(intent_lower)
    if not matched:
        for key, val in _QUERY_INTENT_MAP.items():
            if key in intent_lower or intent_lower in key:
                matched = val
                break
    if not matched:
        return fmt_ok({
            "intent": intent,
            "routed_to": "search_files",
            "reason": f"No match for '{intent}'. Falling back.",
            "available_intents": sorted(set(_QUERY_INTENT_MAP.keys())),
        })
    primary, fallback = matched
    args = {}
    if path:
        args["path"] = path
    if line and line > 0:
        args["line"] = line
    if language:
        args["language"] = language
    if primary == "code_search":
        args.setdefault("preset", "function_calls")
    return fmt_ok({
        "intent": intent,
        "routed_to": primary,
        "fallback": fallback,
        "recommended_args": args,
    })

def _handle_code_query(args, **kw):
    return code_query_tool(
        intent=args.get("intent", ""),
        path=args.get("path"),
        line=int(args.get("line", 0)),
        language=args.get("language"),
    )

# ---------------------------------------------------------------------------
# Symbol-Level Editing Tools
# ---------------------------------------------------------------------------


def _find_symbol_in_ast(
    path: str,
    symbol_name: str,
    language: Optional[str] = None,
) -> Optional[dict]:
    """Find a symbol in a source file using tree-sitter AST.

    Returns a dict with byte-exact boundaries:

        {name, kind, start_byte, end_byte, start_line, end_line, body}

    Supports name_path syntax: ``"ClassName/method_name"``.
    Returns ``None`` if the symbol is not found.
    """
    from pathlib import Path as _Path

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return None

    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return None

    # Parse name_path
    name_parts = symbol_name.strip().split("/")
    leaf_name = name_parts[-1]
    parent_filter = name_parts[:-1]

    try:
        source = target.read_bytes()
    except (OSError, IOError) as e:
        logger.debug("Cannot read file %s: %s", target, e)
        return None

    from tree_sitter import QueryCursor as _QC

    setup = _setup_query(lang_key)
    if setup is None:
        return None
    parser, lang, query = setup

    tree = parser.parse(source)
    qc = _QC(query)

    for _pidx, caps in qc.matches(tree.root_node):
        name_nodes = caps.get("name", [])
        def_nodes = (
            caps.get("def")
            or caps.get("constant")
            or caps.get("field")
            or caps.get("arrow")
        )
        if not name_nodes or not def_nodes:
            continue

        name_node = name_nodes[0]
        def_node = def_nodes[0]

        try:
            name_text = name_node.text.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, IndexError, AttributeError):
            continue

        if name_text != leaf_name:
            continue

        # If parent_filter specified, check parent hierarchy
        if parent_filter:
            _cur = def_node.parent
            _depth = 0
            _matched_parents = []
            while _cur and _depth < 10:
                try:
                    pname_node = None
                    for child in _cur.children:
                        if child.type in (
                            "identifier", "type_identifier",
                            "property_identifier",
                        ):
                            pname_node = child
                            break
                    if pname_node:
                        pn = pname_node.text.decode("utf-8", errors="replace")
                        _matched_parents.insert(0, pn)
                except (UnicodeDecodeError, IndexError):
                    pass
                _cur = _cur.parent
                _depth += 1

            # Check if parents match the filter
            expected = list(parent_filter)  # e.g., ["ClassName"]
            match = True
            for i, exp in enumerate(expected):
                if i < len(_matched_parents):
                    if _matched_parents[-(i + 1)] != exp:
                        match = False
                        break
                else:
                    match = False
                    break
            if not match:
                continue

        # Found it — extract byte boundaries
        start_byte = def_node.start_byte
        end_byte = def_node.end_byte
        start_line = def_node.start_point[0] + 1
        end_line = def_node.end_point[0] + 1
        kind = _classify_symbol_kind(def_node)
        kind = _detect_if_method(def_node, kind)
        body = source[start_byte:end_byte].decode("utf-8", errors="replace")

        return {
            "name": name_text,
            "kind": kind,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "start_line": start_line,
            "end_line": end_line,
            "body": body,
        }

    return None


# ---------------------------------------------------------------------------
# C1: code_replace_body — Replace symbol body via AST
# ---------------------------------------------------------------------------

CODE_REPLACE_BODY_SCHEMA = {
    "name": "code_replace_body",
    "description": (
        "Replace the full definition of a symbol (function, method, class) in a "
        "source file using AST-accurate boundaries. Supports name_path syntax "
        "(e.g. 'MyClass/my_method'). dry_run=True (default) shows a diff without "
        "writing. include_decorators=True replaces decorators with the definition."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "new_body": {
                "type": "string",
                "description": (
                    "The new source code replacing the entire symbol definition "
                    "(signature + body)."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a diff without modifying the file."
                ),
                "default": True,
            },
            "include_decorators": {
                "type": "boolean",
                "description": (
                    "When True (default), the replacement includes decorators. "
                    "When False, only the definition body (after decorators) is replaced."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "new_body"],
    },
}


def code_replace_body_tool(
    path: str,
    symbol: str,
    new_body: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    include_decorators: bool = True,
) -> str:
    """Replace the full definition of a symbol using AST-accurate boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path (e.g. 'MyClass/my_method').
        new_body: Replacement source code.
        language: Language override.
        dry_run: When True, return diff without writing.
        include_decorators: When True, replace decorators too.

    Returns:
        JSON result with success/error message and optional diff.
    """

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")

    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    new_body_bytes = new_body.encode("utf-8")

    if not include_decorators:
        lang_key2 = detect_language(str(target), language)
        if lang_key2:
            # imports not needed — _get_parser/_get_language handles this
            _p2 = _get_parser(lang_key2)
            _l2 = _get_language(lang_key2)
            if _p2 and _l2:
                _tree2 = _p2.parse(source_bytes)
            # Walk from root to find the exact node at start_byte
            _node_at = _tree2.root_node.named_descendant_for_byte_range(
                start_byte, start_byte + 1
            )
            # If it's a decorated_definition, find the inner definition
            if _node_at and _node_at.type == "decorated_definition":
                for _child in _node_at.children:
                    if _child.type in (
                        "function_definition", "class_definition",
                        "function_declaration", "class_declaration",
                        "method_definition",
                    ):
                        start_byte = _child.start_byte
                        break

    old_text = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")

    if dry_run:
        import difflib as _dl
        _diff_lines = list(_dl.unified_diff(
            old_text.splitlines(keepends=True),
            new_body.splitlines(keepends=True),
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
            n=3,
        ))
        diff_text = "".join(_diff_lines)
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "line": symbol_info["start_line"],
            "diff": diff_text,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # --- Apply ---
    new_content = source_bytes[:start_byte] + new_body_bytes + source_bytes[end_byte:]

    # Create backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        # Restore backup
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    # Clean up backup on success
    try:
        backup_path.unlink()
    except OSError:
        pass

    # Invalidate symbol cache for this file
    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "line": symbol_info["start_line"],
        "end_line": symbol_info["end_line"],
        "message": f"Replaced {symbol_info['kind']} '{symbol_info['name']}' "
                   f"(lines {symbol_info['start_line']}-{symbol_info['end_line']}).",
    })


def _handle_code_replace_body(args, **kw):
    return code_replace_body_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        new_body=args.get("new_body", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        include_decorators=args.get("include_decorators", True),
    )


# ---------------------------------------------------------------------------
# C2: code_safe_delete — Delete symbol if unreferenced
# ---------------------------------------------------------------------------

CODE_SAFE_DELETE_SCHEMA = {
    "name": "code_safe_delete",
    "description": (
        "Delete a symbol (function, method, class) ONLY if it has no external "
        "references. Uses AST-based reference search across the project. "
        "Set force=True to delete even if referenced. "
        "dry_run=True (default) shows what would be deleted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path containing the symbol.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "force": {
                "type": "boolean",
                "description": (
                    "When True, delete the symbol even if it has external "
                    "references. Default: False (refuse if referenced)."
                ),
                "default": False,
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), shows what would be deleted without "
                    "modifying the file."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol"],
    },
}


def _ast_search_references(
    project_root: str,
    symbol_name: str,
    language: Optional[str] = None,
) -> List[dict]:
    """Search for references to a symbol across a project.

    Returns a list of {file, line, context} for each reference found.
    Uses grep -rn with code-file extensions.
    """
    import subprocess as _sp

    references = []
    root = Path(project_root)
    if not root.is_dir():
        root = root.parent
    if not root.exists():
        return references

    ext_list = [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h"]
    include_args = []
    for ext in ext_list:
        include_args.extend(["--include", f"*{ext}"])
    escaped = re.escape(symbol_name)

    try:
        cmd = ["grep", "-rn", "-C", "1"] + include_args + ["-e", escaped, str(root)]
        result = _sp.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        for line in result.stdout.splitlines():
            if not line.strip() or line.startswith("--"):
                continue
            parts = line.split(":", 2)
            if len(parts) >= 2:
                fpath = parts[0]
                try:
                    linenum = int(parts[1])
                except ValueError:
                    continue
                context = parts[2] if len(parts) > 2 else ""
                references.append({
                    "file": fpath,
                    "line": linenum,
                    "context": context.strip(),
                })
    except (_sp.TimeoutExpired, OSError) as e:
        logger.debug("Reference search failed for %s: %s", symbol_name, e)

    return references


def code_safe_delete_tool(
    path: str,
    symbol: str,
    language: Optional[str] = None,
    force: bool = False,
    dry_run: bool = True,
) -> str:
    """Delete a symbol ONLY if it has no external references.

    Uses AST-based reference search. Set force=True to bypass the check.

    Args:
        path: File containing the symbol.
        symbol: Symbol name or name_path.
        language: Language override.
        force: Delete even if referenced.
        dry_run: Preview without writing.

    Returns:
        JSON with result message and reference info.
    """

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    leaf_name = symbol.strip().split("/")[-1]

    # --- Reference check ---
    ext_refs = []
    if not force:
        refs = _ast_search_references(str(target.parent), leaf_name, language)
        definition_path = str(target)
        for ref in refs:
            # Skip self-references (the definition itself)
            if ref["file"] == definition_path and ref["line"] == symbol_info["start_line"]:
                continue
            ext_refs.append(ref)

    if ext_refs and not force:
        ref_summary = "\n".join(
            f"  {r['file']}:{r['line']}  {r['context'][:80]}"
            for r in ext_refs[:20]
        )
        if len(ext_refs) > 20:
            ref_summary += f"\n  ... and {len(ext_refs) - 20} more"
        return fmt_ok({
            "safe": False,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "references_found": len(ext_refs),
            "message": (
                f"Cannot delete '{leaf_name}': {len(ext_refs)} external "
                f"reference(s) found. Use force=True to override."
            ),
            "references": ref_summary,
        })

    # --- Dry-run ---
    if dry_run:
        return fmt_ok({
            "dry_run": True,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "line": symbol_info["start_line"],
            "end_line": symbol_info["end_line"],
            "body_preview": symbol_info["body"][:200],
            "external_references": len(ext_refs),
            "references_found": len(ext_refs) > 0,
            "message": f"Would delete {symbol_info['kind']} '{leaf_name}' "
                       f"(lines {symbol_info['start_line']}-{symbol_info['end_line']})."
                       f" Set dry_run=False to apply.",
        })

    # --- Apply: delete symbol range ---
    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    new_content = source_bytes[:start_byte] + source_bytes[end_byte:]

    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError:
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": leaf_name,
        "kind": symbol_info["kind"],
        "line": symbol_info["start_line"],
        "end_line": symbol_info["end_line"],
        "external_references": len(ext_refs),
        "message": f"Deleted {symbol_info['kind']} '{leaf_name}' "
                   f"(lines {symbol_info['start_line']}-{symbol_info['end_line']}).",
    })


def _handle_code_safe_delete(args, **kw):
    return code_safe_delete_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        language=args.get("language"),
        force=args.get("force", False),
        dry_run=args.get("dry_run", True),
    )


# ---------------------------------------------------------------------------
# C3: code_insert_before — Insert code before a symbol
# ---------------------------------------------------------------------------

CODE_INSERT_BEFORE_SCHEMA = {
    "name": "code_insert_before",
    "description": (
        "Insert code before a symbol's definition in a source file. Uses "
        "AST-accurate boundaries to find the insertion point. Supports "
        "name_path syntax (e.g. 'MyClass/my_method'). "
        "dry_run=True (default) shows a preview without writing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "The source code to insert before the symbol definition."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a preview without modifying the file."
                ),
                "default": True,
            },
            "newline": {
                "type": "boolean",
                "description": (
                    "When True (default), adds a newline after the inserted code "
                    "to separate it from the symbol."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "code"],
    },
}


def code_insert_before_tool(
    path: str,
    symbol: str,
    code: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    newline: bool = True,
) -> str:
    """Insert code before a symbol's definition using AST boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path.
        code: Source code to insert.
        language: Language override.
        dry_run: Preview without writing.
        newline: Add newline after inserted code.

    Returns:
        JSON result.
    """

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    insert_at = symbol_info["start_byte"]
    code_bytes = code.encode("utf-8")
    if newline:
        code_bytes += b"\n"

    if dry_run:
        preview = source_bytes[:insert_at].decode("utf-8", errors="replace")
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "insert_before_line": symbol_info["start_line"],
            "insertion": code,
            "preview_context": preview[-200:] if len(preview) > 200 else preview,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # Backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    new_content = source_bytes[:insert_at] + code_bytes + source_bytes[insert_at:]

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError:
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "insert_before_line": symbol_info["start_line"],
        "message": f"Inserted code before {symbol_info['kind']} "
                   f"'{symbol_info['name']}' (line {symbol_info['start_line']}).",
    })


def _handle_code_insert_before(args, **kw):
    return code_insert_before_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        code=args.get("code", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        newline=args.get("newline", True),
    )


# ---------------------------------------------------------------------------
# C4: code_insert_after — Insert code after a symbol
# ---------------------------------------------------------------------------

CODE_INSERT_AFTER_SCHEMA = {
    "name": "code_insert_after",
    "description": (
        "Insert code after a symbol's definition in a source file. Uses "
        "AST-accurate boundaries to find the insertion point. Supports "
        "name_path syntax (e.g. 'MyClass/my_method'). "
        "dry_run=True (default) shows a preview without writing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "The source code to insert after the symbol definition."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a preview without modifying the file."
                ),
                "default": True,
            },
            "newline": {
                "type": "boolean",
                "description": (
                    "When True (default), adds a newline before the inserted code "
                    "to separate it from the symbol."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "code"],
    },
}


def code_insert_after_tool(
    path: str,
    symbol: str,
    code: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    newline: bool = True,
) -> str:
    """Insert code after a symbol's definition using AST boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path.
        code: Source code to insert.
        language: Language override.
        dry_run: Preview without writing.
        newline: Add newline before inserted code.

    Returns:
        JSON result.
    """

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    insert_at = symbol_info["end_byte"]
    code_bytes = code.encode("utf-8")
    if newline:
        code_bytes = b"\n" + code_bytes

    if dry_run:
        preview = source_bytes[insert_at:].decode("utf-8", errors="replace")
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "insert_after_line": symbol_info["end_line"],
            "insertion": code,
            "preview_context": preview[:200] if len(preview) > 200 else preview,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # Backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    new_content = source_bytes[:insert_at] + code_bytes + source_bytes[insert_at:]

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError:
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "insert_after_line": symbol_info["end_line"],
        "message": f"Inserted code after {symbol_info['kind']} "
                   f"'{symbol_info['name']}' (line {symbol_info['end_line']}).",
    })


def _handle_code_insert_after(args, **kw):
    return code_insert_after_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        code=args.get("code", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        newline=args.get("newline", True),
    )


# ---------------------------------------------------------------------------
# C5: code_overview — Compact file overview
# ---------------------------------------------------------------------------

CODE_OVERVIEW_SCHEMA = {
    "name": "code_overview",
    "description": (
        "Get a compact overview of all symbols in a source file or directory. "
        "Shows a tree view with symbol names, kinds, line numbers, and "
        "hierarchy. More token-efficient than code_symbols or "
        "code_document_symbols for quick orientation. "
        "Use depth=0 for top-level only, depth=1 (default) for methods/fields, "
        "depth=2 for deeper nesting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file or directory path.",
            },
            "depth": {
                "type": "integer",
                "description": (
                    "How deep to expand: 0 = top-level only, "
                    "1 (default) = include class members, "
                    "2 = include nested members."
                ),
                "default": 1,
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
        },
        "required": ["path"],
    },
}


def _build_overview_tree(
    source: bytes,
    lang_key: str,
    depth: int = 1,
) -> List[dict]:
    """Build a hierarchy of symbols from source code.

    Returns a list of dicts with nesting via 'children' key.
    Each entry: {name, kind, line, end_line, signature, children}
    """
    from tree_sitter import QueryCursor as _QC

    setup = _setup_query(lang_key)
    if setup is None:
        return []
    parser, lang, query = setup

    tree = parser.parse(source)
    qc = _QC(query)
    source_lines = source.split(b"\n")

    top_level: List[dict] = []

    for _pidx, caps in qc.matches(tree.root_node):
        name_nodes = caps.get("name", [])
        def_nodes = (
            caps.get("def")
            or caps.get("constant")
            or caps.get("field")
            or caps.get("arrow")
        )
        if not name_nodes or not def_nodes:
            continue

        name_node = name_nodes[0]
        def_node = def_nodes[0]

        try:
            name_text = name_node.text.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, IndexError, AttributeError):
            continue

        start_line = def_node.start_point[0] + 1
        end_line = def_node.end_point[0] + 1
        sig_start = def_node.start_point[0]
        sig_end = min(def_node.end_point[0], sig_start + 2)
        signature = (
            b"\n".join(source_lines[sig_start:sig_end])
            .decode("utf-8", errors="replace")
            .strip()
        )
        kind = _classify_symbol_kind(def_node)
        kind = _detect_if_method(def_node, kind)

        entry = {
            "name": name_text,
            "kind": kind,
            "line": start_line,
            "end_line": end_line,
            "signature": signature,
            "children": [],
        }

        # Determine nesting level based on parent chain
        _parent = def_node.parent
        _depth = 0
        _is_nested = False
        while _parent and _depth < 10:
            if _parent.type in (
                "class_definition", "class_declaration",
                "block", "body", "declaration_list",
                "impl_item", "interface_declaration",
                "module_body", "program",
            ):
                pass  # structural parents
            _parent = _parent.parent
            _depth += 1

        # Simple heuristic: if parents include a class/impl, it's nested
        _p = def_node.parent
        _found_class = False
        while _p and _p.type != "program" and _p.type != "module":
            if _p.type in (
                "class_definition", "class_declaration",
                "impl_item", "interface_declaration",
                "decorated_definition",
            ):
                _found_class = True
                break
            _p = _p.parent

        if _found_class and depth > 0:
            # Add to last top-level symbol's children
            if top_level:
                top_level[-1]["children"].append(entry)
            else:
                top_level.append(entry)
        elif not _found_class:
            top_level.append(entry)
        # else: depth=0 and inside a class — skip (no children at depth=0)

    return top_level


def _format_overview_tree(
    path: str,
    symbols: List[dict],
    lang_key: str,
    total_lines: int,
    depth: int = 1,
) -> str:
    """Format the symbol tree as a compact string."""
    lines = []

    # Header
    p = Path(path)
    lang_display = lang_key or "unknown"
    lines.append(f"📄 {p.name} ({lang_display}, {total_lines} lines)")

    if not symbols:
        lines.append("  (no symbols found)")
        return "\n".join(lines)

    for i, sym in enumerate(symbols):
        is_last = i == len(symbols) - 1
        prefix = "└── " if is_last else "├── "
        _line_info = f"line {sym['line']}" + (
            f"-{sym['end_line']}" if sym['end_line'] != sym['line'] else ""
        )
        icon = {
            "function": "ƒ", "method": "ƒ", "class": "⊞",
            "interface": "⊟", "struct": "⊡", "enum": "⊡",
            "type": "τ", "variable": "v", "constant": "c",
            "module": "⊟", "trait": "τ", "impl": "⊞",
        }.get(sym["kind"], "•")
        lines.append(f"{prefix}{icon} {sym['kind']} {sym['name']} ({_line_info})")

        # Children (methods, fields)
        if sym["children"] and depth > 0:
            for j, child in enumerate(sym["children"]):
                is_last_child = j == len(sym["children"]) - 1
                c_prefix = "    " + ("└── " if is_last_child else "├── ")
                _c_line = f"line {child['line']}" + (
                    f"-{child['end_line']}" if child['end_line'] != child['line'] else ""
                )
                c_icon = {
                    "function": "ƒ", "method": "ƒ",
                    "class": "⊞", "field": "•",
                    "variable": "v", "constant": "c",
                }.get(child["kind"], "•")
                lines.append(f"{c_prefix}{c_icon} {child['kind']} {child['name']} ({_c_line})")

    return "\n".join(lines)


def code_overview_tool(
    path: str,
    depth: int = 1,
    language: Optional[str] = None,
) -> str:
    """Get a compact overview of symbols in a file or directory.

    Args:
        path: Absolute file or directory path.
        depth: How deep to expand (0=top-level, 1=members, 2=nested).
        language: Language override.

    Returns:
        Formatted overview string.
    """

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_dir():
        # Scan directory
        results = []
        for ext, lang_key in sorted(_EXT_TO_LANG.items(), key=lambda x: x[0]):
            for f in sorted(target.rglob(f"*{ext}")):
                if not f.is_file():
                    continue
                overview = code_overview_tool(str(f), depth=depth, language=lang_key)
                results.append(overview)
        return "\n\n".join(results) if results else "No supported files found."

    # Single file
    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return fmt_ok({
            "error": (
                f"Unsupported language. "
                f"Supported: {', '.join(sorted(set(_EXT_TO_LANG.values())))}"
            ),
        })

    try:
        source = target.read_bytes()
        total_lines = source.count(b"\n") + 1
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    symbols = _build_overview_tree(source, lang_key, depth=depth)
    return _format_overview_tree(str(target), symbols, lang_key, total_lines, depth=depth)


def _handle_code_overview(args, **kw):
    return code_overview_tool(
        path=args.get("path", ""),
        depth=int(args.get("depth", 1)),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# Unused Imports Detection
# ---------------------------------------------------------------------------


def _find_unused_imports_in_file(file_path: str) -> list:
    """Find unused imports in a single file using tree-sitter AST analysis.

    For each import statement, extracts the imported names and checks
    whether they appear anywhere in the file body (outside the import
    statement itself). Names with zero non-import references are
    reported as unused.

    Supports Python and TypeScript import syntax.

    Returns:
        List of dicts: [{"name": "...", "line": N, "statement": "..."}, ...]
    """
    from pathlib import Path

    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return []

    path.suffix.lower()
    lang_key = detect_language(file_path)
    if not lang_key:
        return []

    parser = _get_parser(lang_key)
    lang_obj = _get_language(lang_key)
    if parser is None or lang_obj is None:
        return []

    try:
        from tree_sitter import Query, QueryCursor
    except ImportError:
        return []

    # Language-specific import queries
    import_queries = {
        "python": """
            (import_statement
                name: (dotted_name) @import_name) @import_stmt
            (import_from_statement
                module_name: (dotted_name)? @_from_mod
                name: (dotted_name) @from_name) @import_stmt
        """,
        "typescript": """
            (import_statement
                source: (string) @_source
               ) @import_stmt
        """,
        "tsx": """
            (import_statement
                source: (string) @_source
                ) @import_stmt
        """,
        "javascript": """
            (import_statement
                source: (string) @_source
                ) @import_stmt
        """,
        "jsx": """
            (import_statement
                source: (string) @_source
                ) @import_stmt
        """,
    }

    query_source = import_queries.get(lang_key)
    if not query_source:
        return []

    try:
        query = Query(lang_obj, query_source)
    except Exception:
        return []

    try:
        with open(file_path, "rb") as f:
            source_bytes = f.read()
    except (OSError, IOError):
        return []

    if not source_bytes:
        return []

    tree = parser.parse(source_bytes)
    if not tree or not tree.root_node:
        return []

    source_text = source_bytes.decode("utf-8", errors="replace")

    # Collect import ranges and names
    import_ranges = []  # (start_byte, end_byte)
    imported_names = {}  # name -> [(line, statement_text)]

    qc = QueryCursor(query)
    for _pattern_idx, captures_dict in qc.matches(tree.root_node):
        # Get the import statement node range
        stmt_node = captures_dict.get("import_stmt", [None])[0]
        if stmt_node:
            import_ranges.append((stmt_node.start_byte, stmt_node.end_byte))

        # Python: import name (dotted_name in import_statement)
        for node in captures_dict.get("import_name", []):
            name = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            top_name = name.split(".")[0]
            stmt_text = source_bytes[stmt_node.start_byte:stmt_node.end_byte].decode("utf-8", errors="replace") if stmt_node else name
            if top_name not in imported_names:
                imported_names[top_name] = []
            line_num = source_text[:node.start_byte].count("\n") + 1
            imported_names[top_name].append({"line": line_num, "statement": stmt_text, "name": top_name})

        # Python: from_name (in import_from_statement)
        for node in captures_dict.get("from_name", []):
            name = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            top_name = name.split(".")[0]
            stmt_text = source_bytes[stmt_node.start_byte:stmt_node.end_byte].decode("utf-8", errors="replace") if stmt_node else name
            if top_name not in imported_names:
                imported_names[top_name] = []
            line_num = source_text[:node.start_byte].count("\n") + 1
            imported_names[top_name].append({"line": line_num, "statement": stmt_text, "name": top_name})

    if not imported_names:
        return []

    # For TS/JS: parse import statements by regex since the query captures the whole statement
    if lang_key in ("typescript", "tsx", "javascript", "jsx"):
        import re as _re
        for node in captures_dict.get("import_stmt", []):
            pass  # Already captured above
        # Re-scan: find all import ... from '...' and extract default/named imports
        ts_imports = _re.findall(
            r'(?:import\s+)(?:type\s+)?(?:\{?\s*(\w+))',
            source_text,
        )
        for name in ts_imports:
            if name not in imported_names and name not in ("from",):
                # Find the line
                idx = source_text.find(f"import {name}")
                if idx == -1:
                    idx = source_text.find(f"{{{name}")
                if idx >= 0:
                    line_num = source_text[:idx].count("\n") + 1
                else:
                    line_num = 0
                imported_names.setdefault(name, [])
                if not any(n["name"] == name for n in imported_names[name]):
                    imported_names[name].append({"line": line_num, "statement": f"import {name}", "name": name})

    # Build the "body" of the file = everything outside import statements
    # We'll search for each name in the source text with import ranges excluded
    unused = []
    for name, occurrences in imported_names.items():
        if not name or len(name) < 2:  # skip single-letter names like _
            continue

        # Check if the name is a built-in (we can't detect if imports are unused for types)
        if name in ("typing", "TYPE_CHECKING", "Any", "Optional", "List", "Dict", "Set", "Tuple"):
            continue

        # Count references to this name in the body (excluding import ranges)
        ref_count = 0
        for _ in _find_identifier_occurrences(name, source_text):
            ref_count += 1

        # Each import statement gives one "reference" (the import itself)
        # If ref_count == len(occurrences), all references are just the imports
        # If ref_count > len(occurrences), the name is used elsewhere
        num_imports = len(occurrences)
        if ref_count <= num_imports:
            # All references are just the import statements
            for occ in occurrences:
                unused.append({
                    "name": occ["name"],
                    "line": occ["line"],
                    "statement": occ["statement"],
                    "file": file_path,
                    "kind": "import",
                })

    return unused


def _find_identifier_occurrences(name: str, source_text: str) -> list:
    """Find non-import occurrences of an identifier in source text.

    Uses word-boundary matching to avoid false positives on substrings.

    Returns:
        List of line numbers where the identifier appears.
    """
    import re as _re
    results = []
    # Look for word-boundary-delimited occurrences
    pattern = _re.compile(r'\b' + _re.escape(name) + r'\b')
    for m in pattern.finditer(source_text):
        results.append(source_text[:m.start()].count("\n") + 1)
    return results


def _find_unused_imports(path: str, depth: int = 5) -> list:
    """Find unused imports across a project directory or single file.

    Args:
        path: File or directory path to scan.
        depth: Max scan depth for directories (default: 5).

    Returns:
        List of unused import dicts from _find_unused_imports_in_file.
    """
    from pathlib import Path as _Path

    root = _Path(path).expanduser().resolve()
    if not root.exists():
        return []

    if root.is_file():
        return _find_unused_imports_in_file(str(root))

    if not root.is_dir():
        return []

    results = []
    for ext in (".py", ".ts", ".tsx", ".js", ".jsx"):
        for f in sorted(root.rglob(f"*{ext}")):
            # Skip common excluded dirs
            rel = f.relative_to(root)
            parts = rel.parts
            if any(p in ("node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next") for p in parts):
                continue
            try:
                file_results = _find_unused_imports_in_file(str(f))
                results.extend(file_results)
            except Exception:
                continue

    return results


# ---------------------------------------------------------------------------
# Unused Functions Detection
# ---------------------------------------------------------------------------


def _find_unused_functions(path: str, depth: int = 5) -> list:
    """Find unused functions across a project.

    For each function definition found via tree-sitter, searches all
    project source files for references. Functions whose only reference
    is their own definition are reported as unused.

    Args:
        path: File or directory path to scan.
        depth: Max scan depth for directories (default: 5).

    Returns:
        List of dicts: [{"name": "...", "file": "...", "line": N, "kind": "function"}, ...]
    """
    from pathlib import Path as _Path

    root = _Path(path).expanduser().resolve()
    if not root.exists():
        return []

    # Collect all source files and parse them for function definitions
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

    # Step 1: Find all function definitions per file
    file_functions = {}  # file_path -> [(func_name, line)]
    all_texts = {}  # file_path -> source_text (for fast reference search)

    for f in source_files:
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
            all_texts[fpath] = source_text

            # Use tree-sitter to find function definitions
            from tree_sitter import Query, QueryCursor
            # A simple function definition query (works across most languages)
            func_query_text = _SYMBOL_QUERIES.get(lang_key, """
                (function_definition name: (identifier) @name) @def
                (function_declaration name: (identifier) @name) @def
                (method_definition name: (property_identifier) @name) @def
            """)
            try:
                query = Query(lang_obj, func_query_text)
            except Exception:
                # Try generic fallback
                try:
                    query = Query(lang_obj, """
                        (function_definition name: (identifier) @name) @def
                        (function_declaration name: (identifier) @name) @def
                    """)
                except Exception:
                    continue

            tree = parser.parse(source_bytes)
            if not tree or not tree.root_node:
                continue

            functions = []
            seen_names = set()
            qc = QueryCursor(query)
            for _pattern_idx, captures_dict in qc.matches(tree.root_node):
                for node in captures_dict.get("name", []):
                    name = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        line_num = source_text[:node.start_byte].count("\n") + 1
                        functions.append((name, line_num))
            if functions:
                file_functions[fpath] = functions
        except Exception:
            continue

    if not file_functions:
        return []

    # Step 2: Count project-wide references via AST (tree-sitter)
    # This avoids false positives from comments, strings, imports, and type annotations
    unused = []
    for fpath, funcs in file_functions.items():
        for func_name, def_line in funcs:
            # Skip single-letter, dunder methods, test functions
            if len(func_name) < 2 or func_name.startswith("__") or func_name.startswith("test_"):
                continue

            # Count project-wide references via tree-sitter AST
            total_refs = 0
            for search_path, search_text in all_texts.items():
                try:
                    lang_key = detect_language(search_path)
                    if not lang_key:
                        continue
                    parser = _get_parser(lang_key)
                    if parser is None:
                        continue

                    source_bytes = search_text.encode("utf-8")
                    tree = parser.parse(source_bytes)
                    if not tree or not tree.root_node:
                        continue

                    # Walk the AST tree counting identifier references,
                    # skipping comments, imports, and type annotations
                    def _walk(node, in_annotation=False, in_import=False):
                        nonlocal total_refs
                        node_type = node.type

                        # Skip comments entirely
                        if node_type in ("comment", "block_comment", "line_comment"):
                            return

                        # Track context from parent nodes
                        if node_type in (
                            "type_annotation",
                            "type_alias_declaration",
                            "type_definition",
                            "type_spec",
                            "type_parameter",
                            "type_parameters",
                            "generic_type",
                        ):
                            in_annotation = True

                        if node_type in (
                            "import_statement",
                            "import_from_statement",
                            "import_declaration",
                            "import_specifier",
                            "import_alias",
                            "require_statement",
                            "import",
                            "from_clause",
                        ):
                            in_import = True

                        # Check identifier and property_identifier nodes
                        if node_type in ("identifier", "property_identifier"):
                            if not in_import and not in_annotation:
                                try:
                                    text = source_bytes[node.start_byte:node.end_byte].decode("utf-8")
                                except Exception:
                                    text = ""
                                if text == func_name:
                                    # Skip the definition line itself (in the same file)
                                    if not (search_path == fpath and node.start_point[0] + 1 == def_line):
                                        total_refs += 1

                        # Recurse into named children
                        for child in node.named_children:
                            _walk(child, in_annotation, in_import)

                    _walk(tree.root_node)
                except Exception:
                    continue

            # A function is unused if it has no references outside its own definition
            if total_refs == 0:
                unused.append({
                    "name": func_name,
                    "file": fpath,
                    "line": def_line,
                    "kind": "function",
                    "total_references": total_refs,
                })

    return unused


def code_unused_finder_tool(
    path: str,
    kinds: list = None,
    depth: int = 5,
) -> str:
    """Find unused imports and unused functions in a project.

    Uses tree-sitter AST analysis to detect:
    - Unused imports: names that are imported but never referenced in the file body
    - Unused functions: functions defined but never called project-wide

    Args:
        path: File or directory path to scan.
        kinds: Types of unused code to find: ["imports"], ["functions"], or both.
               (default: ["imports"]).
        depth: Scan depth for directories (default: 5).

    Returns:
        JSON with grouped unused code findings.
    """

    if kinds is None:
        kinds = ["imports"]

    results = []

    if "imports" in kinds:
        results.extend(_find_unused_imports(path, depth=depth))

    if "functions" in kinds:
        results.extend(_find_unused_functions(path, depth=depth))

    # Group by file for a clean output
    by_file: dict = {}
    for r in results:
        fpath = r.get("file", "")
        if fpath not in by_file:
            by_file[fpath] = []
        by_file[fpath].append(r)

    # Sort for deterministic output
    sorted_files = sorted(by_file.keys())
    grouped = []
    for fpath in sorted_files:
        grouped.append({
            "file": fpath,
            "unused": by_file[fpath],
            "total": len(by_file[fpath]),
        })

    total = len(results)
    result = {
        "project": str(path),
        "total_unused": total,
        "files": grouped,
    }
    return fmt_json(result)


CODE_UNUSED_FINDER_SCHEMA = {
    "name": "code_unused_finder",
    "description": "Find unused imports and unused functions in a project. "
                   "Uses tree-sitter AST analysis to detect dead code.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory path to scan"},
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": ["imports", "functions"]},
                "description": "Types of unused code to find (default: ['imports'])",
            },
            "depth": {"type": "integer", "description": "Scan depth for directories (default: 5)"},
        },
        "required": ["path"],
    },
}


def _handle_code_unused_finder(args, **kw):
    return code_unused_finder_tool(
        path=args.get("path", ""),
        kinds=args.get("kinds", ["imports"]),
        depth=args.get("depth", 5),
    )


# ---------------------------------------------------------------------------
# C12: code_move_tool — Move a symbol between files via AST extraction
# ---------------------------------------------------------------------------


def code_move_tool(
    source: str,
    symbol: str,
    target: str,
    language: str = "",
    dry_run: bool = True,
) -> str:
    """Move a symbol between files. AST-based extraction + insertion.

    Phase 1: Functions only, no import-reference updating.

    Args:
        source: Source file path containing the symbol to move.
        symbol: Symbol name or name_path (e.g. 'MyClass/my_method').
        target: Target file path where the symbol should be inserted.
        language: Language override (auto-detected from extension).
        dry_run: When True, return diff without writing (default: True).

    Returns:
        JSON result with success/error message and optional diff.
    """

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        return fmt_err(f"Source file not found: {source}")
    if not source_path.is_file():
        return fmt_err(f"Not a file: {source}")

    target_path = Path(target).expanduser().resolve()
    if not target_path.exists():
        return fmt_err(f"Target file not found: {target}")
    if not target_path.is_file():
        return fmt_err(f"Not a file: {target}")

    # Resolve language — prefer explicit override, else auto-detect from source
    lang_key = language if language else detect_language(str(source_path))
    if lang_key is None:
        return fmt_err(
            f"Cannot detect language for source file: {source}. "
            "Set 'language' explicitly."
        )

    # ------------------------------------------------------------------
    # 1. Find the symbol in the source file via AST
    # ------------------------------------------------------------------
    symbol_info = _find_symbol_in_ast(str(source_path), symbol, lang_key)
    if symbol_info is None:
        return fmt_err(
            f"Symbol '{symbol}' not found in {source}"
        )

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    leaf_name = symbol.strip().split("/")[-1]

    # Read source file content
    try:
        source_bytes = source_path.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read source file: {e}")

    # Extract the symbol's source code
    symbol_code = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # 2. Compute insertion point in target file (before last line)
    # ------------------------------------------------------------------
    try:
        target_bytes = target_path.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read target file: {e}")

    target_text = target_bytes.decode("utf-8", errors="replace")
    # Strip trailing whitespace, append the new symbol + newline
    target_stripped = target_text.rstrip()
    insert_pos = len(target_stripped.encode("utf-8"))
    # Insert with a blank line separator
    insertion_code = "\n\n" + symbol_code + "\n"

    # ------------------------------------------------------------------
    # 3. Compute the new file contents
    # ------------------------------------------------------------------
    # Target: content before insertion point + insertion + trailing content
    new_target_content = (
        target_bytes[:insert_pos]
        + insertion_code.encode("utf-8")
        + target_bytes[insert_pos:].lstrip(b"\n")
    )

    # Source: remove the symbol range
    new_source_content = source_bytes[:start_byte] + source_bytes[end_byte:]

    # ------------------------------------------------------------------
    # 4. Dry-run: return a unified diff for both files
    # ------------------------------------------------------------------
    if dry_run:
        import difflib as _dl

        # Source diff
        source_old = source_bytes.decode("utf-8", errors="replace")
        source_new = new_source_content.decode("utf-8", errors="replace")
        source_diff_lines = list(_dl.unified_diff(
            source_old.splitlines(keepends=True),
            source_new.splitlines(keepends=True),
            fromfile=f"a/{source_path.name}",
            tofile=f"b/{source_path.name}",
            n=3,
        ))

        # Target diff
        target_old = target_bytes.decode("utf-8", errors="replace")
        target_new = new_target_content.decode("utf-8", errors="replace")
        target_diff_lines = list(_dl.unified_diff(
            target_old.splitlines(keepends=True),
            target_new.splitlines(keepends=True),
            fromfile=f"a/{target_path.name}",
            tofile=f"b/{target_path.name}",
            n=3,
        ))

        diff_text = "".join(source_diff_lines) + "\n" + "".join(target_diff_lines)

        return fmt_ok({
            "dry_run": True,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "source_line": symbol_info["start_line"],
            "source": str(source_path),
            "target": str(target_path),
            "diff": diff_text,
            "message": (
                f"Dry-run mode. Would move {symbol_info['kind']} '{leaf_name}' "
                f"from {source_path.name}:{symbol_info['start_line']} "
                f"to {target_path.name}. "
                "Set dry_run=False to apply."
            ),
        })

    # ------------------------------------------------------------------
    # 5. Apply: write both files (with backup)
    # ------------------------------------------------------------------

    # --- Write source file (remove symbol) ---
    source_backup = source_path.with_suffix(source_path.suffix + ".bak")
    try:
        source_backup.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create source backup: {e}")

    try:
        source_path.write_bytes(new_source_content)
    except (OSError, IOError) as e:
        source_backup.write_bytes(source_bytes)
        return fmt_err(f"Cannot write source file: {e}")

    try:
        source_backup.unlink()
    except OSError:
        pass

    # --- Write target file (insert symbol) ---
    target_backup = target_path.with_suffix(target_path.suffix + ".bak")
    try:
        target_backup.write_bytes(target_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create target backup: {e}")

    try:
        target_path.write_bytes(new_target_content)
    except (OSError, IOError) as e:
        target_backup.write_bytes(target_bytes)
        return fmt_err(f"Cannot write target file: {e}")

    try:
        target_backup.unlink()
    except OSError:
        pass

    # Invalidate caches so subsequent AST ops see fresh content
    _invalidate_cache(str(source_path))
    _invalidate_cache(str(target_path))

    return fmt_ok({
        "success": True,
        "symbol": leaf_name,
        "kind": symbol_info["kind"],
        "source": str(source_path),
        "source_line": symbol_info["start_line"],
        "target": str(target_path),
        "message": (
            f"Moved {symbol_info['kind']} '{leaf_name}' "
            f"from {source_path.name}:{symbol_info['start_line']} "
            f"to {target_path.name}."
        ),
    })


CODE_MOVE_SCHEMA = {
    "name": "code_move",
    "description": "Move a symbol between files via AST extraction and insertion.",
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Source file path",
            },
            "symbol": {
                "type": "string",
                "description": "Symbol name to move",
            },
            "target": {
                "type": "string",
                "description": "Target file path",
            },
            "language": {
                "type": "string",
                "description": "Language override",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview changes without writing (default: true)",
            },
        },
        "required": ["source", "symbol", "target"],
    },
}


def _handle_code_move(args, **kw):
    return code_move_tool(
        source=args.get("source", ""),
        symbol=args.get("symbol", ""),
        target=args.get("target", ""),
        language=args.get("language", ""),
        dry_run=args.get("dry_run", True),
    )


# ---------------------------------------------------------------------------
# Duplicate Code Detection (C13) — AST-based duplicate/similar code finder
# ---------------------------------------------------------------------------


def code_duplicates_tool(
    path: str = ".",
    min_lines: int = 5,
    top_n: int = 20,
) -> str:
    """Find duplicate/similar code blocks via AST comparison.

    Uses tree-sitter AST to find all function definitions, normalizes them
    (removing names, string literals, numbers), then detects duplicates via
    exact hash matching and string similarity with difflib.

    Args:
        path: Project root path (default: ".").
        min_lines: Minimum lines for a duplicate block (default: 5).
        top_n: Number of top duplicate groups to return (default: 20).

    Returns:
        JSON with grouped duplicate findings.
    """
    import difflib
    import hashlib

    root = Path(path).expanduser().resolve()
    if not root.exists():
        return fmt_json({"error": f"Path not found: {path}", "duplicates": [], "total": 0})

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

    # Collect all function definitions with their source text
    functions = []

    for f in source_files:
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

            from tree_sitter import Query, QueryCursor

            func_query_text = _SYMBOL_QUERIES.get(lang_key, """
                (function_definition name: (identifier) @name) @def
                (function_declaration name: (identifier) @name) @def
                (method_definition name: (property_identifier) @name) @def
            """)
            try:
                query = Query(lang_obj, func_query_text)
            except Exception:
                try:
                    query = Query(lang_obj, """
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
            if ratio >= 0.85:
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
        },
        "required": ["path"],
    },
}


def _handle_code_duplicates(args, **kw):
    return code_duplicates_tool(
        path=args.get("path", "."),
        min_lines=args.get("min_lines", 5),
        top_n=args.get("top_n", 20),
    )


# ---------------------------------------------------------------------------
# LSP-based tools — code_definition & code_references (cross-file resolution)
# ---------------------------------------------------------------------------

# LSP tools are registered via register_lsp_tools() called from __init__.py
# during plugin load — do NOT call register_lsp_tools() at module level
# to avoid duplicate registration and import errors outside package context.


def code_export_tool(
    path: str = ".",
    fmt: str = "json",
    kind: str = "all",
) -> str:
    """Export symbol index from a project as JSON or Markdown.

    Uses extract_symbols() for AST-based symbol extraction, then formats
    the result as JSON, Markdown, or a compact summary.

    Args:
        path: Project or file path to export symbols from.
        fmt: Output format: "json", "markdown", or "summary" (default: json).
        kind: Filter by symbol kind: "all", "function", "class", "method" (default: all).

    Returns:
        Formatted symbol index output.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Get symbols via code_symbols_tool
    sym_json = code_symbols_tool(path=path, kind=kind if kind != "all" else None, include_body=False)
    if not sym_json:
        return fmt_err("No symbols found")

    import json as _json
    try:
        symbols = _json.loads(sym_json) if isinstance(sym_json, str) else sym_json
        if isinstance(symbols, dict) and "symbols" in symbols:
            symbols = symbols["symbols"]
    except Exception:
        symbols = [] if not isinstance(sym_json, list) else sym_json

    if not symbols:
        return fmt_err("No symbols found")

    # Group by file
    by_file = {}
    for sym in symbols:
        fpath = sym.get("file", "unknown")
        by_file.setdefault(fpath, []).append(sym)

    if fmt == "markdown":
        md_lines = ["# Project Symbol Index",
                    f"Total: {len(symbols)} symbols across {len(by_file)} files", ""]
        for fpath in sorted(by_file):
            syms = by_file[fpath]
            funcs = [s for s in syms if s.get("kind") in ("function", "method")]
            classes = [s for s in syms if s.get("kind") == "class"]
            md_lines.append(f"## {fpath}")
            if classes:
                md_lines.append(f"({len(classes)} classes, {len(funcs)} functions)")
            elif funcs:
                md_lines.append(f"({len(funcs)} functions)")
            if funcs:
                md_lines.append("")
                md_lines.append("| Name | Line | Kind |")
                md_lines.append("|------|------|------|")
                for s in sorted(funcs, key=lambda x: x.get("line", 0)):
                    md_lines.append(f"| {s.get('name', '?')} | {s.get('line', 0)} | {s.get('kind', '')} |")
            if classes:
                for s in classes:
                    children = s.get("children", [])
                    n_methods = sum(1 for c in children if c.get("kind") == "method")
                    md_lines.append(f"- **{s.get('name', '?')}** (L{s.get('line', 0)}, {n_methods} methods)")
            md_lines.append("")
        return fmt_ok({"markdown": "\n".join(md_lines)}, title="Symbol Export (Markdown)")

    elif fmt == "summary":
        lang_counts = {}
        for fpath in by_file:
            ext = Path(fpath).suffix
            lang_counts[ext] = lang_counts.get(ext, 0) + 1
        summary = {
            "total_symbols": len(symbols),
            "total_files": len(by_file),
            "files_by_extension": lang_counts,
            "top_files": sorted(by_file.keys(), key=lambda f: len(by_file[f]), reverse=True)[:10],
        }
        return fmt_ok(summary, title="Symbol Index Summary")

    # Default: JSON format
    result = {
        "project": str(target),
        "total_symbols": len(symbols),
        "total_files": len(by_file),
        "symbols_by_file": by_file,
    }
    return fmt_json(result)


# ---------------------------------------------------------------------------
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
        from .lsp.bridge import _detect_language_for_lsp as _lsp_lang
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
                while col > 0 and (src_line[col - 1].isalnum() or src_line[col - 1] == '_'):
                    col -= 1
                character = col + 1
            else:
                character = 1
        except Exception:
            character = 1
    lsp_char = (character or 0) - 1  # Convert to 0-based

    logger.info("code_diagram_symbol_tool: %s:%d:%s lang=%s depth=%d",
                path, line, character or "auto", lang, depth)

    # Try LSP call hierarchy
    from .lsp.bridge import get_lsp_manager
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
            logger.info("code_diagram_symbol: LSP returned %s incoming, %s outgoing",
                        len(incoming) if incoming else 0,
                        len(outgoing) if outgoing else 0)
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
            while start > 0 and (line_text[start - 1].isalnum() or line_text[start - 1] == '_'):
                start -= 1
            end = col
            while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == '_'):
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
        diagram_lines.append(f"    {f_id}[\"{f_label}\"] --> {t_id}[\"{t_label}\"]")

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
            if ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".c", ".cpp"):
                import re as _re
                func_patterns = {
                    "python": _re.compile(r'^\s*def\s+(\w+)\s*\('),
                    "typescript": _re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\('),
                    "tsx": _re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\('),
                    "javascript": _re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\('),
                    "rust": _re.compile(r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\('),
                    "go": _re.compile(r'^\s*(?:func\s+)(\w+)\s*\('),
                    "java": _re.compile(r'^\s*(?:public|private|protected|static|\s)*\s+(\w+)\s*\('),
                    "c": _re.compile(r'^\s*\w+\s+(\w+)\s*\('),
                    "cpp": _re.compile(r'^\s*\w+\s+(\w+)\s*\('),
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
                                diagram_lines.append(f"    {fn_id}[\"{fn_name}\"] -.-> {sym_id}[\"{symbol_name}\"]")
                                if len([l for l in lines_seen if "-->" in l]) >= depth * 3:
                                    break
        except Exception as e:
            logger.debug("code_diagram_symbol: AST fallback failed: %s", e)

    # Ensure symbol node is included even if no edges
    if not any(sym_id in l for l in diagram_lines):
        diagram_lines.append(sym_node)

    # Add depth note
    diagram_lines.append(f"    %% depth={depth} | LSP={'yes' if lsp_server else 'no'}")

    diagram = "\n".join(diagram_lines)

    result = {"mermaid": diagram}
    if lsp_server:
        result["lsp_server"] = lsp_server
    result["depth"] = depth
    result["symbol"] = symbol_name
    result["path"] = str(target)

    return fmt_ok(result, title=f"Call Graph: {symbol_name}")


CODE_DIAGRAM_SYMBOL_SCHEMA = {
    "name": "code_diagram_symbol",
    "description": "Generate a Mermaid call graph diagram for a symbol. "
                   "Uses LSP call hierarchy (incoming_calls + outgoing_calls) to show "
                   "who calls a function and who it calls, formatted as a Mermaid flowchart. "
                   "Falls back to AST-based analysis if LSP is unavailable.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
            "depth": {"type": "integer", "description": "Max call chain depth (default: 2, max: 5)"},
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


CODE_EXPORT_SCHEMA = {
    "name": "code_export",
    "description": "Export symbol index as JSON or Markdown for documentation.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project or file path"},
            "fmt": {
                "type": "string",
                "enum": ["json", "markdown", "summary"],
                "description": "Output format (default: json)",
            },
            "kind": {
                "type": "string",
                "enum": ["all", "function", "class", "method"],
                "description": "Filter by symbol kind (default: all)",
            },
        },
        "required": ["path"],
    },
}


def _handle_code_export(args, **kw):
    return code_export_tool(
        path=args.get("path", "."),
        fmt=args.get("fmt", "json"),
        kind=args.get("kind", "all"),
    )


# ---------------------------------------------------------------------------
# code_docstring_generate_tool — Generate docstring template from AST
# ---------------------------------------------------------------------------


def code_docstring_generate_tool(
    path: str,
    line: int,
    style: str = "google",
) -> str:
    """Generate a docstring template from a function's AST signature.

    Reads the function signature via AST, extracts parameters and return
    type annotations, and produces a structured docstring template.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Read the file and extract the function definition
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
    except Exception as e:
        return fmt_err(f"Cannot read file: {e}")

    # Find the function definition at or near the given line
    func_lines = []
    func_line = -1
    start_idx = max(0, line - 3)
    for i in range(start_idx, len(lines)):
        stripped = lines[i].strip()
        if any(stripped.startswith(kw) for kw in
               ["def ", "async def ", "func ", "func(",
                "function ", "function(", "fn ", "fn(",
                "pub fn ", "pub fn("]):
            func_line = i + 1
            # Collect function lines (def + body until blank line or next def/class)
            depth = 0
            for j in range(i, len(lines)):
                func_lines.append(lines[j])
                # Count indentation depth
                if j == i:
                    continue
                s = lines[j].strip()
                if not s and depth == 0:
                    break
                if s.startswith("def ") or s.startswith("class ") or s.startswith("async def "):
                    break
                if s.startswith("fn ") or s.startswith("pub fn "):
                    break
            break

    if not func_lines:
        return fmt_err("No function definition found at or near the given line")

    func_text = "\n".join(func_lines)

    # Parse parameters using regex
    import re
    param_pattern = r"def\s+\w+\s*\((.*?)\)(?:\s*->\s*([^:]+))?\s*:"
    match = re.search(param_pattern, func_text, re.DOTALL)
    if not match:
        return fmt_err("Could not parse function signature")

    params_str = match.group(1)
    return_type = match.group(2).strip() if match.group(2) else "None"

    # Parse individual parameters
    params = []
    if params_str.strip():
        for p in params_str.split(","):
            p = p.strip()
            if p == "self" or p == "cls" or p == "self," or not p:
                continue
            # Split on ':' to get name and type
            if ":" in p:
                name, ptype = p.split(":", 1)
                params.append({"name": name.strip(), "type": ptype.strip().split("=")[0].strip()})
            elif "=" in p:
                name = p.split("=")[0].strip()
                params.append({"name": name, "type": "Any"})
            else:
                params.append({"name": p.strip(), "type": "Any"})

    # Extract function name
    name_match = re.match(r"(?:async\s+)?def\s+(\w+)", func_text)
    func_name = name_match.group(1) if name_match else "unknown"

    style = style.lower()
    if style == "numpy":
        doc_lines = [
            f'"""{func_name}',
            "",
            "    Parameters",
            "    ----------",
        ]
        for p in params:
            doc_lines.append(f"    {p['name']} : {p['type']}")
            doc_lines.append(f"        Description of {p['name']}.")
        doc_lines.extend([
            "",
            "    Returns",
            "    -------",
            f"    {return_type}",
            "        Description of return value.",
            '"""',
        ])
    elif style == "sphinx":
        doc_lines = [
            f'"""{func_name}.',
            "",
            "    :param params: ...",
        ]
        for p in params:
            doc_lines.append(f"    :param {p['name']}: Description.")
            doc_lines.append(f"    :type {p['name']}: {p['type']}")
        doc_lines.extend([
            "",
            "    :returns: Description.",
            f"    :rtype: {return_type}",
            '"""',
        ])
    else:  # google (default)
        doc_lines = [
            f'"""{func_name}.',
            "",
        ]
        if params:
            doc_lines.append("    Args:")
            for p in params:
                doc_lines.append(f"        {p['name']} ({p['type']}): Description.")
            doc_lines.append("")
        doc_lines.extend([
            "    Returns:",
            f"        {return_type}: Description.",
            '"""',
        ])

    docstring = "\n".join(doc_lines)

    return fmt_ok({
        "path": str(target),
        "function": func_name,
        "line": func_line,
        "parameters": params,
        "return_type": return_type,
        "style": style,
        "docstring": docstring,
    })


CODE_DOCSTRING_GENERATE_SCHEMA = {
    "name": "code_docstring_generate",
    "description": (
        "Generate a docstring template from a function's AST signature. "
        "Reads the function definition, extracts parameters and return type, "
        "and produces a structured docstring in Google, NumPy, or Sphinx style. "
        "Use this to quickly scaffold documentation for a function."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number inside the function."},
            "style": {
                "type": "string",
                "enum": ["google", "numpy", "sphinx"],
                "description": "Docstring style (default: google).",
                "default": "google",
            },
        },
        "required": ["path", "line"],
    },
}


def _handle_code_docstring_generate(args, **kw):
    return code_docstring_generate_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        style=args.get("style", "google"),
    )


# ---------------------------------------------------------------------------
# code_dependency_risk_tool — Dependency health analysis
# ---------------------------------------------------------------------------


def code_dependency_risk_tool(path: str) -> str:
    """Analyze code dependency health and produce a risk score (0-10).

    Factors: cyclic dependencies, import depth, hot paths, unused imports.
    Uses ImportGraph for project-level analysis.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_file():
        project_root = target.parent
    else:
        project_root = target

    try:
        from ._import_graph import ImportGraph
    except ImportError:
        return fmt_err("ImportGraph not available")

    # Scan the project
    graph = ImportGraph(str(project_root))
    try:
        graph.scan(depth=3)
        graph.parse_all()
    except Exception as e:
        return fmt_err(f"Import scan failed: {e}")

    risk_factors = []
    risk_score = 0

    # 1. Cyclic dependencies
    cycles = graph.find_cycles()
    if cycles:
        n_cycles = len(cycles)
        risk_factors.append({
            "factor": "cyclic_dependencies",
            "count": n_cycles,
            "severity": "high" if n_cycles > 5 else "medium" if n_cycles > 2 else "low",
            "details": [list(c) for c in cycles[:5]],
        })
        risk_score += min(3, n_cycles * 0.5)

    # 2. Hot paths (most-imported files)
    hot_paths = graph.find_hot_paths(top_n=5)
    max_hot_path = hot_paths[0]["caller_count"] if hot_paths else 0
    if max_hot_path > 20:
        risk_factors.append({
            "factor": "hot_paths",
            "count": max_hot_path,
            "severity": "medium",
            "details": [h["file"] for h in hot_paths[:3]],
        })
        risk_score += 1.5

    # 3. Total import edges (complexity indicator)
    g = graph.graph()
    edge_count = len(g)
    if edge_count > 200:
        risk_factors.append({
            "factor": "import_complexity",
            "count": edge_count,
            "severity": "medium",
            "details": [f"{edge_count} import relationships"],
        })
        risk_score += min(2, edge_count / 200)

    # 4. File count vs import density
    file_count = len(list(graph.files()))
    if file_count > 0:
        density = edge_count / file_count
        if density > 3:
            risk_factors.append({
                "factor": "import_density",
                "count": round(density, 2),
                "severity": "low",
                "details": [f"{density:.1f} imports per file"],
            })
            risk_score += min(1, density * 0.2)

    # Cap at 10
    risk_score = min(10, round(risk_score, 1))

    return fmt_ok({
        "path": str(project_root),
        "files_scanned": file_count,
        "import_edges": edge_count,
        "risk_score": risk_score,
        "risk_level": "low" if risk_score < 3 else "medium" if risk_score < 6 else "high",
        "factors": risk_factors,
    })


CODE_DEPENDENCY_RISK_SCHEMA = {
    "name": "code_dependency_risk",
    "description": (
        "Analyze code dependency health and produce a risk score (0-10). "
        "Factors considered: cyclic dependencies, hot import paths, import complexity/density. "
        "Returns structured breakdown with risk level (low/medium/high). "
        "Useful for technical debt assessment before major refactors."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory path to analyze.",
            },
        },
        "required": ["path"],
    },
}


def _handle_code_dependency_risk(args, **kw):
    return code_dependency_risk_tool(
        path=args.get("path", "."),
    )
