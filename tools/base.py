#!/usr/bin/env python3
"""
tools/base.py — Gemeinsame Infrastruktur für code_intel tools.

Stellt bereit:
- Language-Registry (tree-sitter Parser/Language)
- Symbol-Cache (OrderedDict mit Persistenz)
- Basis-Utilities: _find_project_root, detect_language, _classify_node,
  _classify_symbol_kind, _detect_if_method, _extract_candidate, _setup_query
- Konstanten: _EXT_TO_LANG, _NODE_KIND_MAP, _SYMBOL_QUERIES
"""

import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Optional

from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# Language registry — maps file extensions → tree-sitter Language objects
# Lazy-loaded on first use to avoid slow imports at module level.
# ---------------------------------------------------------------------------

_LANG_LOCK = threading.Lock()
_LANG_CACHE: Dict[str, object] = {}   # ext → Language
_PARSER_CACHE: Dict[str, object] = {} # lang_key → Parser
_LANG_READY = False
_SYMBOL_CACHE: OrderedDict = OrderedDict()

# ---------------------------------------------------------------------------
# Persistent symbol index — saves/loads AST cache to disk
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
        env_root = os.environ.get("HERMES_PROJECT_ROOT", "")
        if env_root and Path(env_root).is_dir():
            return str(Path(env_root).resolve())
        start = Path.cwd()

    for p in [start] + list(start.parents):
        for marker in ("pnpm-workspace.yaml", "nx.json", "lerna.json"):
            if (p / marker).exists():
                return str(p)
        if p.parent == p:
            break
    for p in [start] + list(start.parents):
        for marker in (".git", "pyproject.toml", "Cargo.toml", "go.mod"):
            if (p / marker).exists():
                return str(p)
        if p.parent == p:
            break
    return str(start)


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
    safe_entries = {}
    for k, v in _SYMBOL_CACHE.items():
        key = str(k) if not isinstance(k, str) else k
        try:
            json.dumps({key: v})
            safe_entries[key] = v
        except (TypeError, ValueError) as e:
            logger.debug("_persist_cache: skipping non-serializable entry: %s", e)
            continue
    data = {
        "version": _PERSIST_VERSION,
        "project_root": project_root,
        "entries": safe_entries,
    }
    try:
        with open(path, "w") as f:
            json.dump(data, f)
        logger.debug("Persisted %d symbol cache entries to %s", len(safe_entries), path)
        return len(safe_entries)
    except Exception as e:
        logger.warning("Failed to persist symbol cache: %s", e)
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
        loaded = 0
        for k, v in data.get("entries", {}).items():
            if k not in _SYMBOL_CACHE:
                _SYMBOL_CACHE[k] = v
                loaded += 1
        logger.info("Loaded %d symbol cache entries from %s", loaded, path)
        return loaded
    except Exception as e:
        logger.warning("Failed to load symbol cache: %s", e)
        return 0


def _set_cache(key, value):
    _SYMBOL_CACHE[key] = value
    if len(_SYMBOL_CACHE) > 2000:
        _SYMBOL_CACHE.popitem(last=False)


def get_symbol_cache_stats() -> dict:
    return {"entries": len(_SYMBOL_CACHE)}


def clear_symbol_cache() -> None:
    _SYMBOL_CACHE.clear()


def _invalidate_cache(file_path: str) -> None:
    """Remove all cached entries for a specific file path."""
    prefix = str(Path(file_path).resolve()) + "|"
    stale_keys = [k for k in _SYMBOL_CACHE if k.startswith(prefix)]
    for k in stale_keys:
        try:
            del _SYMBOL_CACHE[k]
        except KeyError as e:
            logger.debug("_invalidate_cache: key not found: %s", e)
            pass
    if stale_keys:
        logger.debug("Invalidated %d cache entries for %s", len(stale_keys), file_path)


# ── Extension → language key mapping ──────────────────────────────────────
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
from .symbol_queries import _SYMBOL_QUERIES  # noqa: E402, F401

# ── Language / parser helpers ─────────────────────────────────────────────

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
    if query_capture_name == "name":
        pass
    kind = _NODE_KIND_MAP.get(node.type)
    if kind:
        return kind
    return "symbol"


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


__all__ = [
    '_find_project_root', '_get_language', '_get_parser', 'detect_language',
    '_classify_node', '_classify_symbol_kind', '_detect_if_method',
    '_extract_candidate', '_setup_query', '_init_languages',
    '_SYMBOL_CACHE', '_set_cache',
    '_EXT_TO_LANG', '_NODE_KIND_MAP', '_SYMBOL_QUERIES',
    '_invalidate_cache', 'get_symbol_cache_stats', 'clear_symbol_cache',
    'persist_symbol_cache', 'load_symbol_cache',
]
