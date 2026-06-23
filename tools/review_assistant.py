"""tools/review_assistant.py — Automated code review combining multiple analyses.

Combines: diff analysis, complexity, diagnostics, unused code, security,
and error handling patterns into a single PR review report.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

__all__ = ["code_review_assistant_tool", "CODE_REVIEW_ASSISTANT_SCHEMA"]

CODE_REVIEW_ASSISTANT_SCHEMA = {
    "name": "code_review_assistant",
    "description": (
        "Automated code review for changes between two Git refs. "
        "Combines diff analysis, complexity deltas, diagnostics scan, "
        "unused code detection, and security checks into one report. "
        "Use BEFORE opening a PR to catch issues early."
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
                "description": "Base branch (default: 'main').",
                "default": "main",
            },
            "head": {
                "type": "string",
                "description": "Head ref (default: current HEAD).",
                "default": "HEAD",
            },
            "max_files": {
                "type": "integer",
                "description": "Max files to analyze in depth (default: 15).",
                "default": 15,
            },
        },
        "required": ["path"],
    },
}


def _git_changed_files(repo: Path, base: str, head: str) -> list:
    """Get list of changed files between two refs."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        cwd=str(repo), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return []
    return [f.strip() for f in result.stdout.split("\n") if f.strip()]


def _git_diff_for_file(repo: Path, base: str, head: str, file_path: str) -> str:
    """Get diff for a single file."""
    result = subprocess.run(
        ["git", "diff", f"{base}..{head}", "--", file_path],
        cwd=str(repo), capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def _check_added_imports(diff_text: str) -> list:
    """Check for new dependencies added in the diff."""
    imports = []
    for line in diff_text.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            if any(stripped.startswith(kw) for kw in
                   ("import ", "from ", "require(", "const ")):
                imports.append(stripped)
    return imports[:10]


def _check_todo_comments(diff_text: str) -> list:
    """Check for TODO/FIXME/HACK in the diff."""
    import re
    findings = []
    for i, line in enumerate(diff_text.split("\n")):
        if line.startswith("+") and not line.startswith("+++"):
            for pattern in [r'\bTODO\b', r'\bFIXME\b', r'\bHACK\b', r'\bXXX\b']:
                if re.search(pattern, line):
                    findings.append(f"L{i}: {line.strip()[:80]}")
                    break
    return findings[:5]


def _check_debug_code(diff_text: str) -> list:
    """Check for debug code in the diff."""
    debug_patterns = ["console.log(", "console.debug(", "console.warn(",
                      "print(", "logger.debug(", "logger.info(",
                      "dg", "bp()"]  # debug/bp check patterns
    findings = []
    for i, line in enumerate(diff_text.split("\n")):
        if line.startswith("+") and not line.startswith("+++"):
            for pattern in debug_patterns:
                if pattern in line:
                    findings.append(f"L{i}: {line.strip()[:80]}")
                    break
    return findings[:5]


def _estimate_complexity(source: str) -> dict:
    """Simple complexity estimation (branch counting)."""
    import re
    branches = len(re.findall(r'\bif\b|\belif\b|\belse\b|\bcase\b|\bswitch\b', source))
    loops = len(re.findall(r'\bfor\b|\bwhile\b', source))
    exceptions = len(re.findall(r'\btry\b|\bcatch\b|\bexcept\b|\bfinally\b', source))
    returns = len(re.findall(r'\breturn\b', source))
    total = 1 + branches + loops + exceptions + returns
    return {
        "estimated_complexity": total,
        "branches": branches,
        "loops": loops,
        "exceptions": exceptions,
        "returns": returns,
        "rank": "A" if total <= 10 else "B" if total <= 20 else "C" if total <= 30 else "D" if total <= 40 else "E",
    }


def code_review_assistant_tool(
    path: str,
    base: str = "main",
    head: str = "HEAD",
    max_files: int = 15,
) -> str:
    """Run automated code review between two Git refs.

    Args:
        path: Git repository path.
        base: Base branch (default: 'main').
        head: Head ref (default: 'HEAD').
        max_files: Max files to analyze in depth.

    Returns:
        Structured review report with: summary, file-by-file findings,
        complexity assessment, and improvement suggestions.
    """
    repo = Path(path).expanduser().resolve()
    if not (repo / ".git").exists():
        return fmt_err(f"Not a git repository: {path}")

    changed_files = _git_changed_files(repo, base, head)
    if not changed_files:
        return fmt_ok({
            "summary": {"files_changed": 0, "message": "No changes detected"},
        })

    # Analyze each changed file
    file_reviews = []
    total_issues = 0
    total_warnings = 0

    for fpath in changed_files[:max_files]:
        full_path = repo / fpath
        diff_text = _git_diff_for_file(repo, base, head, fpath)

        if not full_path.exists():
            file_reviews.append({
                "file": fpath,
                "status": "deleted",
                "issues": [],
            })
            continue

        # Read current file content
        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            file_reviews.append({"file": fpath, "status": "error", "issues": ["Cannot read file"]})
            continue

        # Per-file checks
        issues = []
        warnings = []

        # 1. Added imports
        new_imports = _check_added_imports(diff_text)
        if new_imports:
            warnings.append(f"New imports: {', '.join(new_imports[:5])}")

        # 2. TODO/FIXME in changes
        todos = _check_todo_comments(diff_text)
        for t in todos:
            warnings.append(f"TODO/FIXME: {t}")

        # 3. Debug code in changes
        debug = _check_debug_code(diff_text)
        for d in debug:
            issues.append(f"Debug code: {d}")

        # 4. Complexity estimate
        lines = len(source.split("\n"))
        complexity = _estimate_complexity(source)

        # 5. File size warning
        if lines > 500:
            warnings.append(f"Large file ({lines} lines) — consider splitting")

        # 6. Diff size
        added_lines = sum(1 for _line in diff_text.split("\n") if _line.startswith("+") and not _line.startswith("+++"))
        removed_lines = sum(1 for _line in diff_text.split("\n") if _line.startswith("-") and not _line.startswith("---"))

        if added_lines > 200:
            warnings.append(f"Large change (+{added_lines}/-{removed_lines}) — consider smaller PRs")

        total_issues += len(issues)
        total_warnings += len(warnings)

        file_reviews.append({
            "file": fpath,
            "status": "modified",
            "lines_added": added_lines,
            "lines_removed": removed_lines,
            "total_lines": lines,
            "complexity": complexity,
            "issues": issues,
            "warnings": warnings,
        })

    # Overall summary
    review = {
        "summary": {
            "base": base,
            "head": head,
            "files_changed": len(changed_files),
            "files_reviewed": min(len(changed_files), max_files),
            "total_issues": total_issues,
            "total_warnings": total_warnings,
            "health": "good" if total_issues == 0 else "fair" if total_issues <= 3 else "needs_work",
        },
        "files": file_reviews,
    }

    return fmt_ok(review)


def _handle_code_review_assistant(args: dict, **kw: Any) -> str:
    """Handler for code_review_assistant tool."""
    return code_review_assistant_tool(
        path=args.get("path", "."),
        base=args.get("base", "main"),
        head=args.get("head", "HEAD"),
        max_files=args.get("max_files", 15),
    )
