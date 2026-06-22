#!/usr/bin/env python3
"""tools/cache.py — Cache infrastructure for code_intel tools.

Extracted from code_tools.py for modularity.
Provides directory-level caches, persistent symbol index management,
and cache invalidation routines used by the AST code-intel tools.

Module-level globals:
    _LANG_LOCK, _LANG_CACHE, _PARSER_CACHE, _LANG_READY
    _SYMBOL_CACHE, _DIR_SYMBOL_CACHE, _MAX_DIR_CACHE
    _PERSIST_DIR, _PERSIST_VERSION

Functions:
    _set_dir_cache, _find_project_root, _cache_key_for_path,
    _project_cache_path, persist_symbol_cache, load_symbol_cache,
    _set_cache, get_symbol_cache_stats, clear_symbol_cache,
    _invalidate_cache
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict

from .._logging import setup_logger as _setup_code_intel_logger

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
_MAX_DIR_CACHE = 10


def _set_dir_cache(key: str, value: dict) -> None:
    """Store entry in directory symbol cache with LRU eviction."""
    _DIR_SYMBOL_CACHE[key] = value
    if len(_DIR_SYMBOL_CACHE) > _MAX_DIR_CACHE:
        _DIR_SYMBOL_CACHE.popitem(last=False)


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
        # Validate project root matches (allow any root if not stored)
        # We no longer require CWD to match — project root is more stable
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
    """Store an entry in the symbol cache with LRU eviction (max 2000)."""
    _SYMBOL_CACHE[key] = value
    if len(_SYMBOL_CACHE) > 2000:
        _SYMBOL_CACHE.popitem(last=False)


def get_symbol_cache_stats() -> dict:
    """Return current symbol cache statistics."""
    return {"entries": len(_SYMBOL_CACHE)}


def clear_symbol_cache() -> None:
    """Remove all entries from the in-memory symbol cache."""
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
