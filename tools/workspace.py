"""tools/workspace.py — Workspace summary functions extracted from code_tools.py.

Provides monorepo/project scanning, language detection, and workspace summary
capabilities, split from the monolithic code_tools.py for maintainability.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

# Extension-to-language mapping for workspace summary
_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
}


def _detect_lang_for_summary(child: Path, ext_lang: dict) -> Optional[str]:
    """Walk up to 2 levels deep looking for code files; return dominant language."""
    ext_counts: dict[str, int] = {}
    for d in _find_lang_folders(child):
        _count_extensions(d, ext_lang, ext_counts)
        if ext_counts:
            break
    if ext_counts:
        return ext_lang[max(ext_counts, key=lambda k: ext_counts[k])]
    return None


def _find_lang_folders(child: Path) -> list[Path]:
    """Find candidate directories for language detection."""
    candidates = [child / s for s in ("app", "src", "lib", "source")]
    candidates = [d for d in candidates if d.is_dir()]
    return candidates if candidates else [child]


def _count_extensions(d: Path, ext_lang: dict, ext_counts: dict) -> None:
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
                    elif f.is_dir() and depth < 1 and f.name not in (
                        "node_modules", ".git", "dist", "build", ".next", ".turbo"
                    ):
                        stack.append((f, depth + 1))
            except (OSError, PermissionError) as e:
                logger.debug("_detect_lang_for_summary: iterating dir entries: %s", e)
                continue
    except (OSError, PermissionError) as e:
        logger.debug("_detect_lang_for_summary: scanning child dir: %s", e)
        pass


def _scan_workspace(
    base_dir: Path,
    max_d: int,
    parent_kind: Optional[str] = None,
    detect_lang: Any = None,
    ext_lang: Any = None,
) -> tuple[list[dict], list[dict]]:
    """Scan workspace directories for apps and packages, up to *max_d* levels deep.

    *parent_kind*: 'app' | 'package' | None. Forces classification when
    scanning apps/ or packages/.
    *detect_lang*: callable for language detection (defaults to _detect_lang_for_summary).
    """
    detect_lang = detect_lang or _detect_lang_for_summary
    ext_lang = ext_lang or _EXT_LANG
    apps: list[dict] = []
    packages: list[dict] = []
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
                data = json.loads(pkg_json.read_text("utf-8", errors="replace"))
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
            except (OSError, json.JSONDecodeError) as e:
                logger.debug("_scan_workspace: reading package.json: %s", e)
                pass
        if nm == "apps":
            sa, sp = _scan_workspace(
                child, max_d - 1,
                parent_kind="app",
                detect_lang=detect_lang,
                ext_lang=ext_lang,
            )
            apps.extend(sa)
            packages.extend(sp)
        elif nm == "packages":
            sa, sp = _scan_workspace(
                child, max_d - 1,
                parent_kind="package",
                detect_lang=detect_lang,
                ext_lang=ext_lang,
            )
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


def _detect_monorepo_markers(target: Path, _json_module: Any) -> tuple[list[str], Optional[str]]:
    """Detect monorepo root markers in a directory. Returns (markers, marker_type)."""
    markers: list[str] = []
    marker_type: Optional[str] = None
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
        except Exception as e:
            logger.debug("_detect_monorepo_markers: reading package.json: %s", e)
            pass
    if (target / "tsconfig.json").exists():
        markers.append("tsconfig.json")
        if not marker_type:
            marker_type = "tsconfig.json"
    return markers, marker_type


def code_workspace_summary_tool(path: str, depth: int = 2) -> str:
    """Return a compact monorepo/project overview: apps, packages, root markers, entry points."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    root_markers, marker_type = _detect_monorepo_markers(target, json)
    pkg = target / "package.json"
    if not root_markers:
        root_markers.append("project_root")

    apps_list, packages_list = _scan_workspace(
        target, max_d=depth,
        detect_lang=_detect_lang_for_summary,
        ext_lang=_EXT_LANG,
    )

    top_deps: dict[str, str] = {}
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text("utf-8", errors="replace"))
            top_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for k in list(top_deps.keys())[:30]:
                if top_deps[k] == "*":
                    top_deps[k] = str(data.get("peerDependencies", {}).get(k, "latest"))
        except Exception as e:
            logger.debug("code_workspace_summary_tool: reading package.json: %s", e)
            pass

    return fmt_ok({
        "root": str(target),
        "type": marker_type or "project",
        "apps": apps_list[:30],
        "packages": packages_list[:30],
        "root_markers": root_markers,
        "top_level_dependencies": dict(list(top_deps.items())[:20]),
    })


def _handle_code_workspace_summary(args: dict, **kw: Any) -> str:
    return code_workspace_summary_tool(
        path=args.get("path", ""),
        depth=args.get("depth", 2),
    )
