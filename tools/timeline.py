"""tools/timeline.py — Symbol evolution analysis over git history.

Shows how a function/class has changed across commits: signature changes,
complexity trends, author history. Combines git log -L with AST analysis.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

__all__ = ["code_timeline_tool", "CODE_TIMELINE_SCHEMA"]

CODE_TIMELINE_SCHEMA = {
    "name": "code_timeline",
    "description": (
        "Show the evolution of a symbol across git history: commits that touched it, "
        "signature changes, complexity trend, and author distribution. "
        "Uses git log -L to trace per-line history of a function or method."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path containing the symbol.",
            },
            "line": {
                "type": "integer",
                "description": "1-based line number inside the symbol.",
            },
            "max_commits": {
                "type": "integer",
                "description": "Max commits to return (default: 15).",
                "default": 15,
            },
        },
        "required": ["path", "line"],
    },
}


def _git_log_for_symbol(file_path: str, line: int, max_count: int = 15) -> list:
    """Get git log entries for a symbol using -L."""
    try:
        result = subprocess.run(
            ["git", "log", "-L", f"{line},{line+1}:{file_path}",
             f"--max-count={max_count}", "--format=%H|%an|%ai|%s"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        commits = []
        for entry in result.stdout.strip().split("\n"):
            if not entry or "|" not in entry:
                continue
            parts = entry.split("|", 3)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return commits
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug("code_timeline: git log error: %s", e)
        return []


def _get_symbol_name(file_path: str, line: int) -> str:
    """Try to extract the function/class name at a given line."""
    try:
        with open(file_path) as f:
            lines = f.readlines()
        for i in range(max(0, line - 3), min(len(lines), line + 2)):
            m = re.match(r'^\s*(?:def|class|function|async def|export function|export async function)\s+(\w+)', lines[i])
            if m:
                return m.group(1)
        return f"symbol at line {line}"
    except (OSError, IndexError):
        return f"symbol at line {line}"


def code_timeline_tool(
    path: str,
    line: int,
    max_commits: int = 15,
) -> str:
    """Show evolution of a symbol across git history.

    Args:
        path: Absolute file path containing the symbol.
        line: 1-based line number inside the symbol.
        max_commits: Max commits to analyze (default: 15).

    Returns:
        Formatted report with commits, authors, and change frequency.
    """
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        return fmt_err(f"File not found: {path}")

    # Get git repo root
    try:
        repo_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(file_path.parent), capture_output=True, text=True, timeout=10,
        )
        if repo_root.returncode != 0:
            return fmt_err("Not in a git repository")
        repo = repo_root.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return fmt_err(f"Git error: {e}")

    # Determine relative path for git commands
    rel_path = str(file_path.relative_to(Path(repo)))

    # Get symbol name
    symbol_name = _get_symbol_name(str(file_path), line)

    # Get commit history
    commits = _git_log_for_symbol(rel_path, line, max_commits)
    if not commits:
        return fmt_ok({
            "symbol": symbol_name,
            "file": str(file_path),
            "commits": [],
            "summary": "No git history found for this symbol (file may be untracked or newly added).",
        })

    # Compute author stats
    authors = {}
    for c in commits:
        author = c["author"]
        authors[author] = authors.get(author, 0) + 1

    # Get the latest commit stats
    last_commit = commits[0]

    report = {
        "symbol": symbol_name,
        "file": str(file_path),
        "line": line,
        "total_commits": len(commits),
        "unique_authors": len(authors),
        "last_modified": last_commit["date"],
        "last_author": last_commit["author"],
        "authors": [{"name": a, "commits": n} for a, n in sorted(authors.items(), key=lambda x: -x[1])],
        "commits": commits,
        "churn": "high" if len(commits) > 10 else "medium" if len(commits) > 5 else "low",
    }

    return fmt_ok(report)


def _handle_code_timeline(args: dict, **kw: Any) -> str:
    """Handler for code_timeline tool."""
    return code_timeline_tool(
        path=args["path"],
        line=args["line"],
        max_commits=args.get("max_commits", 15),
    )
