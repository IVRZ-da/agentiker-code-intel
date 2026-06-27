#!/usr/bin/env python3
"""lsp/discovery.py — Workspace root discovery for LSP integration.

Extracted from lsp/bridge.py to reduce its size (2178→~1900 lines).

Provides workspace root detection, TypeScript tsconfig resolution,
and monorepo workspace folder discovery.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

from .._logging import setup_logger as _setup_lsp_bridge_logger

logger = _setup_lsp_bridge_logger(__name__)

# ---------------------------------------------------------------------------
# Sub-project markers
# ---------------------------------------------------------------------------

_SUB_PROJECT_MARKERS: Dict[str, str] = {
    "next.config.ts": "nextjs",
    "next.config.mjs": "nextjs",
    "next.config.js": "nextjs",
    "medusa-config.ts": "medusa",
    "medusa-config.js": "medusa",
    "pyproject.toml": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "Gemfile": "ruby",
    "composer.json": "php",
    "mix.exs": "elixir",
}

_USER_MARKERS_PATH = os.path.expanduser("~/.hermes/code_intel_markers.json")
if os.path.exists(_USER_MARKERS_PATH):
    try:
        with open(_USER_MARKERS_PATH) as _f:
            _user_markers = json.load(_f)
        if isinstance(_user_markers, dict):
            _SUB_PROJECT_MARKERS.update(
                {k: v for k, v in _user_markers.items()
                 if isinstance(k, str) and isinstance(v, str)}
            )
    except (json.JSONDecodeError, OSError) as _e:
        logging.getLogger("code_intel.lsp_bridge").warning(
            "Failed to load %s: %s", _USER_MARKERS_PATH, _e
        )

# Workspace root cache (TTL 300s, max 100 entries)
_WORKSPACE_ROOT_CACHE: Dict[str, tuple[str, float]] = {}
_WORKSPACE_ROOT_CACHE_TTL = 300.0
_WORKSPACE_ROOT_CACHE_MAX = 100


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _set_workspace_cache(file_path: str, result: str) -> None:
    """Cache a workspace root result, respecting TTL and max size."""
    now = time.monotonic()
    if len(_WORKSPACE_ROOT_CACHE) >= _WORKSPACE_ROOT_CACHE_MAX:
        oldest = min(_WORKSPACE_ROOT_CACHE.items(),
                     key=lambda kv: kv[1][1])
        del _WORKSPACE_ROOT_CACHE[oldest[0]]
    _WORKSPACE_ROOT_CACHE[file_path] = (result, now)


def _find_workspace_root(file_path: str) -> str:
    """Best-effort workspace root discovery for *file_path*.

    Three-pass strategy:
    1. Look for sub-project markers (next.config.ts, medusa-config.ts,
       pyproject.toml, go.mod, Cargo.toml, etc.)
    2. Look for generic project markers, but SKIP monorepo roots
       (package.json with workspaces field) so we keep walking up
    3. Fallback: parent directory of the file

    Results are cached for ``_WORKSPACE_ROOT_CACHE_TTL`` seconds.
    """
    now = time.monotonic()
    cached = _WORKSPACE_ROOT_CACHE.get(file_path)
    if cached and now - cached[1] < _WORKSPACE_ROOT_CACHE_TTL:
        return cached[0]

    p = Path(file_path).resolve().parent
    candidates = [p] + list(p.parents)
    max_depth = 40

    for parent in candidates[:max_depth]:
        for marker in _SUB_PROJECT_MARKERS:
            if (parent / marker).exists():
                result = str(parent)
                _set_workspace_cache(file_path, result)
                return result
        if (parent / "tsconfig.json").exists() and (parent / "package.json").exists():
            result = str(parent)
            _set_workspace_cache(file_path, result)
            return result

    mono_markers = ("pnpm-workspace.yaml", "nx.json", "lerna.json")
    generic_markers = (
        ".git", ".hg",
        "pyproject.toml", "setup.py", "setup.cfg",
        "package.json",
        "Cargo.toml", "go.mod",
        "pom.xml", "build.gradle", "Makefile",
    )
    mono_root: Optional[str] = None
    generic_root: Optional[str] = None
    for parent in candidates[:max_depth]:
        for m in mono_markers:
            if (parent / m).exists():
                if mono_root is None:
                    mono_root = str(parent)
        if generic_root is None:
            for m in generic_markers:
                if (parent / m).exists():
                    if m == "package.json" and _is_monorepo_root(parent):
                        continue
                    generic_root = str(parent)
                    break
        if mono_root and generic_root:
            break
    result = mono_root or generic_root or str(Path(file_path).resolve().parent)
    _set_workspace_cache(file_path, result)
    return result


def _is_monorepo_root(p: Path) -> bool:
    """Check if *p* is a monorepo root (package.json with workspaces)."""
    pkg_file = p / "package.json"
    if not pkg_file.exists():
        return False
    try:
        data = json.loads(pkg_file.read_text())
        return "workspaces" in data
    except (json.JSONDecodeError, OSError):
        return False


def _find_tsconfig_root(file_path: str) -> Optional[str]:
    """Find the most appropriate tsconfig.json for *file_path*.

    Prefers a project-level tsconfig (same dir or parent with references)
    over a monorepo-root tsconfig. Returns None if none found.
    """
    p = Path(file_path).resolve()
    tsconfig_dirs = []
    mono_root = None
    for parent in [p] + list(p.parents):
        ts = parent / "tsconfig.json"
        if ts.exists():
            tsconfig_dirs.append(parent)
        if (parent / "pnpm-workspace.yaml").exists() or (
            parent / "nx.json"
        ).exists() or (
            parent / "lerna.json"
        ).exists():
            if mono_root is None:
                mono_root = parent
        if parent.parent == parent:  # Reached filesystem root
            break

    if not tsconfig_dirs:
        return None

    # If the first tsconfig is at the file's dir (or a parent with project),
    # prefer that over the monorepo root.
    for ts_dir in tsconfig_dirs:
        if mono_root and ts_dir == mono_root:
            continue
        root_tsconfig = ts_dir / "tsconfig.json"
        if root_tsconfig.exists():
            try:
                data = json.loads(root_tsconfig.read_text())
                if data.get("references"):
                    return str(ts_dir)
            except (json.JSONDecodeError, OSError):
                pass
    return str(tsconfig_dirs[0])


def _find_workspace_folders(root_path: str, workspace_cfg: Optional[dict] = None) -> list:
    """Discover workspace folders from monorepo configs.

    Checks pnpm-workspace.yaml, nx.json, lerna.json, and
    package.json workspaces field.
    """
    root = Path(root_path)
    if workspace_cfg:
        patterns = workspace_cfg.get("workspace_folders", [])
        if patterns:
            return _expand_workspace_patterns(root, patterns)

    # Original behavior: check nx.json/lerna.json first
    folders = _find_nx_or_lerna_folders(root)
    if folders:
        return folders

    # Then check pnpm-workspace.yaml
    yaml_path = root / "pnpm-workspace.yaml"
    if yaml_path.exists():
        patterns = _parse_pnpm_workspace(root)
        if patterns:
            return _expand_workspace_patterns(root, patterns)

    return []


def _find_nx_or_lerna_folders(root: Path) -> list:
    """Extract workspace folders from nx.json or lerna.json.

    Original bridge.py behavior: if nx.json or lerna.json exists,
    return all found standard workspace directories (apps, packages, modules, libs).
    """
    for marker in ("nx.json", "lerna.json"):
        if (root / marker).exists():
            folders = []
            for d in ("apps", "packages", "modules", "libs"):
                if (root / d).is_dir():
                    folders.append(str(root / d))
            return folders
    return []


def _parse_pnpm_workspace(root: Path) -> list:
    """Parse pnpm-workspace.yaml and return package patterns."""
    yaml_path = root / "pnpm-workspace.yaml"
    if not yaml_path.exists():
        return []
    try:
        import yaml as _yaml
        text = yaml_path.read_text()
        cfg = _yaml.safe_load(text)
        if isinstance(cfg, dict):
            patterns = cfg.get("packages", [])
            return patterns if isinstance(patterns, list) else []
    except Exception:
        logger.debug("discovery: workspace pattern resolution failed")
        return []
    return []


def _expand_workspace_patterns(root: Path, patterns: list) -> list:
    """Expand glob-like workspace patterns to actual directories."""
    folders = []
    for pattern in patterns:
        # Convert simple glob patterns like "apps/*" or "packages/*"
        if "*" in pattern:
            base_part = pattern.split("*")[0]
            search_dir = root / base_part
            if search_dir.exists():
                for item in sorted(search_dir.iterdir()):
                    if item.is_dir() and not item.name.startswith("."):
                        folders.append(str(item))
        else:
            full = root / pattern
            if full.exists():
                folders.append(str(full))
    return folders


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "_SUB_PROJECT_MARKERS",
    "_WORKSPACE_ROOT_CACHE",
    "_WORKSPACE_ROOT_CACHE_TTL",
    "_WORKSPACE_ROOT_CACHE_MAX",
    "_set_workspace_cache",
    "_find_workspace_root",
    "_is_monorepo_root",
    "_find_tsconfig_root",
    "_find_workspace_folders",
    "_find_nx_or_lerna_folders",
    "_parse_pnpm_workspace",
    "_expand_workspace_patterns",
]
