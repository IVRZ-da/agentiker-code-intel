"""tools/blame.py — Per-line git blame tool for code_intel plugin.

Provides per-line git blame information for a file or specific lines.
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._fmt import fmt_err, fmt_ok  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_git(cmd: List[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    try:
        return subprocess.run(
            ["git"] + cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Git command timed out")
    except FileNotFoundError:
        raise RuntimeError("Git not found — is git installed?")


def _find_git_root(path: str) -> Optional[str]:
    """Find the git root directory for a path."""
    target = Path(path).expanduser().resolve()
    if target.is_file():
        target = target.parent
    try:
        result = _run_git(["rev-parse", "--show-toplevel"], str(target))
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (RuntimeError, OSError):
        return None


def _parse_blame_porcelain(porcelain: str) -> List[Dict[str, Any]]:
    """Parse git blame --porcelain output into structured line entries.

    Each entry contains: commit_hash, author, author_email, timestamp,
    line_number, and content.
    """
    lines: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    content_buffer: List[str] = []

    for raw_line in porcelain.split("\n"):
        if not raw_line:
            continue

        # Content lines always start with a tab character
        if raw_line.startswith("\t"):
            content_buffer.append(raw_line[1:])
            continue

        # Start of a new blame group — first word is a 40-char hex hash
        first_word = raw_line.split(" ", 1)[0]
        if (
            len(first_word) >= 40
            and all(c in "0123456789abcdef" for c in first_word[:40].lower())
        ):
            # Flush previous group before starting new one
            if current and content_buffer:
                for i, content in enumerate(content_buffer):
                    entry = dict(current)
                    entry["content"] = content
                    lines.append(entry)
            current = {}
            content_buffer = []

            parts = raw_line.split(" ")
            current = {
                "commit_hash": first_word,
                "orig_line_number": int(parts[1]) if len(parts) > 1 else 0,
                "line_number": int(parts[2]) if len(parts) > 2 else 0,
                "group_size": int(parts[3]) if len(parts) > 3 else 1,
            }
            continue

        # Metadata fields (key-value)
        if raw_line.startswith("author "):
            current["author"] = raw_line[7:].strip()
        elif raw_line.startswith("author-mail "):
            current["author_email"] = raw_line[12:].strip().strip("<>")
        elif raw_line.startswith("author-time "):
            try:
                ts = int(raw_line[12:].strip())
                current["timestamp"] = datetime.datetime.fromtimestamp(
                    ts, tz=datetime.timezone.utc
                ).isoformat()
            except (ValueError, OSError):
                current["timestamp"] = ""

    # Flush the last group
    if current and content_buffer:
        for i, content in enumerate(content_buffer):
            entry = dict(current)
            entry["content"] = content
            lines.append(entry)

    return lines


# ---------------------------------------------------------------------------
# code_git_blame_tool
# ---------------------------------------------------------------------------


def code_git_blame_tool(
    path: str,
    line: int = 0,
    limit: int = 50,
) -> str:
    """Get per-line git blame for a file.

    When *line=0*: returns blame for the entire file (paginated, max ``limit`` lines).
    When *line>0*: returns blame for that specific line + surrounding context.

    Returns structured results with commit_hash, author, author_email, timestamp,
    line_number, and content for each blamed line.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    git_root = _find_git_root(str(target))
    if not git_root:
        return fmt_err(f"Not inside a git repository: {path}")

    # Check if file is tracked by git
    try:
        tracked_check = _run_git(
            ["ls-files", "--error-unmatch", str(target)], git_root
        )
        if tracked_check.returncode != 0:
            return fmt_ok({
                "path": str(target),
                "message": f"File is not tracked by git: {path}. "
                           "Only tracked files have blame information.",
                "blamed_lines": 0,
            })
    except (RuntimeError, OSError) as e:
        return fmt_err(f"Failed to check git tracking: {e}")

    # Cap limit to maximum allowed
    limit = min(limit, 200)

    try:
        if line > 0:
            # Specific line: show that line with surrounding context
            # context = limit // 2 lines on each side
            context = max(1, limit // 2)
            start = max(1, line - context)
            end = line + context
            blame_result = _run_git(
                ["blame", "--porcelain", "-L", f"{start},{end}", "--", str(target)],
                git_root,
            )
        else:
            # Entire file
            blame_result = _run_git(
                ["blame", "--porcelain", "--", str(target)],
                git_root,
            )
    except (RuntimeError, OSError) as e:
        return fmt_err(f"Git blame failed: {e}")

    if blame_result.returncode != 0:
        stderr = blame_result.stderr.strip()
        return fmt_err(f"Git blame error: {stderr or 'Unknown error'}")

    if not blame_result.stdout.strip():
        return fmt_ok({
            "path": str(target),
            "message": "No blame information available (empty file or no commits)",
            "blamed_lines": 0,
        })

    # Parse porcelain output
    parsed = _parse_blame_porcelain(blame_result.stdout)

    # Paginate when returning entire file
    if line == 0 and len(parsed) > limit:
        parsed = parsed[:limit]

    # Build clean result entries
    result: List[Dict[str, Any]] = []
    for entry in parsed:
        commit_hash = entry.get("commit_hash", "")
        result.append({
            "commit_hash": (
                commit_hash[:8]
                if commit_hash != "0000000000000000000000000000000000000000"
                else "00000000"
            ),
            "author": entry.get("author", "Unknown"),
            "author_email": entry.get("author_email", ""),
            "timestamp": entry.get("timestamp", ""),
            "line_number": entry.get("line_number", 0),
            "content": entry.get("content", ""),
        })

    payload: Dict[str, Any] = {
        "path": str(target),
        "git_root": git_root,
        "line_requested": line,
        "limit": limit,
        "blamed_lines": len(result),
        "lines": result,
    }

    # When showing a specific line, include the requested line indicator
    if line > 0:
        payload["line_requested"] = line
        payload["context_start"] = max(1, line - max(1, limit // 2))
        payload["context_end"] = line + max(1, limit // 2)

    return fmt_ok(payload)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CODE_GIT_BLAME_SCHEMA = {
    "name": "code_git_blame",
    "description": (
        "Get per-line git blame information for a file. "
        "When line=0, returns blame for the entire file (paginated). "
        "When line>0, returns blame for that specific line with surrounding context. "
        "Returns structured data with commit_hash, author, author_email, timestamp, "
        "line_number, and content for each blamed line."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute file path to get blame information for."
                ),
            },
            "line": {
                "type": "integer",
                "description": (
                    "Line number to get blame for (1-based). "
                    "When 0 (default), returns blame for the entire file (paginated)."
                ),
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Maximum number of blamed lines to return. "
                    "When line=0, controls pagination of the full file. "
                    "When line>0, controls the number of context lines "
                    "around the requested line."
                ),
                "default": 50,
                "maximum": 200,
            },
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _handle_code_git_blame(args: dict, **kwargs: Any) -> str:
    """Handler for the code_git_blame tool.

    Extracts arguments from the ``args`` dict (called by the tool registry).
    """
    return code_git_blame_tool(
        path=args.get("path", ""),
        line=int(args.get("line", 0)),
        limit=int(args.get("limit", 50)),
    )


__all__ = [
    "code_git_blame_tool",
    "CODE_GIT_BLAME_SCHEMA",
    "_handle_code_git_blame",
]
