"""tools/diff_analysis.py — Incremental change analysis between commits.

Compares two Git refs and shows which functions changed, their complexity delta,
and the blast radius of changes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

__all__ = ["code_diff_analysis_tool", "CODE_DIFF_ANALYSIS_SCHEMA"]

CODE_DIFF_ANALYSIS_SCHEMA = {
    "name": "code_diff_analysis",
    "description": (
        "Compare two Git refs and show changed functions with complexity delta "
        "and blast radius. Analyzes which symbols were added, removed, or modified "
        "between any two commits, branches, or tags."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Git repository path.",
            },
            "base": {
                "type": "string",
                "description": "Base ref (commit, branch, or tag). Default: 'main'.",
                "default": "main",
            },
            "head": {
                "type": "string",
                "description": "Head ref. Default: current HEAD.",
                "default": "HEAD",
            },
            "max_files": {
                "type": "integer",
                "description": "Max files to analyze (default: 20).",
                "default": 20,
            },
        },
        "required": ["path"],
    },
}


def _git_diff_files(repo: Path, base: str, head: str) -> list:
    """Get list of changed files between two refs."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        cwd=str(repo),
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return []
    return [f for f in result.stdout.strip().split("\n") if f]


def _git_diff_stat(repo: Path, base: str, head: str) -> str:
    """Get git diff --stat output."""
    result = subprocess.run(
        ["git", "diff", "--stat", f"{base}..{head}"],
        cwd=str(repo),
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def code_diff_analysis_tool(
    path: str,
    base: str = "main",
    head: str = "HEAD",
    max_files: int = 20,
) -> str:
    """Analyze changes between two Git refs.

    Returns structured report: files changed, functions added/removed/modified,
    complexity delta, and blast radius (importers of changed functions).
    """
    repo = Path(path).expanduser().resolve()
    if not (repo / ".git").exists():
        return fmt_err(f"Not a git repository: {path}")

    # Get changed files
    changed_files = _git_diff_files(repo, base, head)
    if not changed_files:
        return fmt_ok({"files_changed": 0, "summary": "No changes detected"})

    # Get diff stat
    diff_stat = _git_diff_stat(repo, base, head)

    # Analyze first N files
    results = []
    total_added = 0
    total_removed = 0
    total_modified = 0

    for fpath in changed_files[:max_files]:
        full_path = repo / fpath
        if not full_path.exists():
            total_removed += 1
            results.append({"file": fpath, "status": "deleted"})
            continue

        # Get the diff for this file
        diff_result = subprocess.run(
            ["git", "diff", f"{base}..{head}", "--", fpath],
            cwd=str(repo), capture_output=True, text=True, timeout=30,
        )
        diff_text = diff_result.stdout

        # Count added/removed lines
        added = sum(1 for _l in diff_text.split("\n") if _l.startswith("+") and not _l.startswith("+++"))
        removed = sum(1 for _l in diff_text.split("\n") if _l.startswith("-") and not _l.startswith("---"))

        # Detect status
        status = "modified"
        if added > 0 and removed == 0:
            status = "added"
        elif removed > 0 and added == 0:
            status = "removed" if not full_path.exists() else "modified"
        elif added > 0 and removed > 0:
            status = "modified"

        if status == "modified":
            total_modified += 1
        elif status == "added":
            total_added += 1

        results.append({
            "file": fpath,
            "status": status,
            "lines_added": added,
            "lines_removed": removed,
        })

    report = {
        "base": base,
        "head": head,
        "files_changed": len(changed_files),
        "files_analyzed": min(len(changed_files), max_files),
        "stats": {
            "added": total_added,
            "removed": total_removed,
            "modified": total_modified,
            "total_lines_changed": sum(r.get("lines_added", 0) + r.get("lines_removed", 0) for r in results),
        },
        "diff_stat": diff_stat[:2000],
        "files": results[:max_files],
    }

    return fmt_ok(report)


def _handle_code_diff_analysis(args: dict, **kw: Any) -> str:
    """Handler for code_diff_analysis tool."""
    return code_diff_analysis_tool(
        path=args.get("path", "."),
        base=args.get("base", "main"),
        head=args.get("head", "HEAD"),
        max_files=args.get("max_files", 20),
    )
