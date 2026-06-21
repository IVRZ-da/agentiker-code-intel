"""tools/git.py — Git-based code analysis tools for code_intel plugin.

Tools in this module:
    - code_todo_finder: Scan project for TODO/FIXME/HACK/XXX
    - code_merge_conflict_finder: Find unresolved merge conflict markers
    - code_git_log_symbol: Git log + blame for a symbol
    - code_git_diff_file: Uncommitted git diff
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .._fmt import fmt_ok, fmt_err


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


# ---------------------------------------------------------------------------
# code_todo_finder
# ---------------------------------------------------------------------------

_TODO_PATTERNS = [
    r"TODO",
    r"FIXME",
    r"HACK",
    r"XXX",
    r"WORKAROUND",
    r"KNOWN.?BUG",
    r"TEMP",
    r"BUG",
    r"REVIEW",
    r"OPTIMIZE",
]

_EXTENSIONS = [
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
    ".yaml", ".yml", ".json", ".toml",
    ".md", ".txt",
    ".css", ".scss",
]


def code_todo_finder_tool(
    path: str,
    include_patterns: Optional[List[str]] = None,
    include_extensions: Optional[List[str]] = None,
) -> str:
    """Scan a project for TODO/FIXME/HACK/XXX comments.

    Uses git grep (if available) or falls back to recursive grep.
    """
    git_root = _find_git_root(path)
    if not git_root:
        return fmt_err(f"Not inside a git repository: {path}")

    patterns = include_patterns or _TODO_PATTERNS
    exts = include_extensions or _EXTENSIONS

    try:
        # Build git grep command: search for any of the patterns
        grep_args = ["grep", "-n", "--no-color", "-i"]
        for p in patterns:
            grep_args.extend(["-e", p])
        grep_args.append("--")

        # Add file extensions
        for ext in exts:
            grep_args.append(f"*{ext}")

        result = subprocess.run(
            ["git"] + grep_args,
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return fmt_err(f"Search failed: {e}")

    if result.returncode != 0 or not result.stdout.strip():
        return fmt_ok({
            "path": git_root,
            "total": 0,
            "message": "No TODO/FIXME/HACK comments found",
        })

    # Parse results into structured format
    lines = result.stdout.strip().split("\n")
    findings = []
    for line in lines[:200]:  # cap at 200 results
        parts = line.split(":", 2)
        if len(parts) == 3:
            filepath = parts[0]
            line_num = parts[1]
            text = parts[2].strip()
            findings.append({
                "file": filepath,
                "line": int(line_num),
                "text": text,
            })

    # Group by file
    files: Dict[str, list] = {}
    for f in findings:
        files.setdefault(f["file"], []).append({
            "line": f["line"],
            "text": f["text"],
        })

    return fmt_ok({
        "path": git_root,
        "total": len(findings),
        "files": len(files),
        "findings": findings[:200],
        "grouped_by_file": {k: v[:10] for k, v in files.items()},
    })


CODE_TODO_FINDER_SCHEMA = {
    "name": "code_todo_finder",
    "description": (
        "Scan a project for TODO, FIXME, HACK, XXX, and WORKAROUND comments "
        "using git grep. Results are grouped by file with line numbers. "
        "Useful for technical debt assessment and code review preparation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to a file or directory inside the git repository.",
            },
            "include_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Custom regex patterns to search for (default: TODO, FIXME, HACK, XXX, WORKAROUND, KNOWN BUG).",
            },
            "include_extensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "File extensions to include (default: .py, .ts, .js, .go, .rs, etc.).",
            },
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# code_merge_conflict_finder
# ---------------------------------------------------------------------------


def code_merge_conflict_finder_tool(path: str) -> str:
    """Find unresolved merge conflict markers (<<<<<<<, =======, >>>>>>>).

    Uses git grep for speed.
    """
    git_root = _find_git_root(path)
    if not git_root:
        return fmt_err(f"Not inside a git repository: {path}")

    try:
        result = subprocess.run(
            ["git", "grep", "-n", "--no-color",
             "-e", "^<<<<<<<", "-e", "^=======$", "-e", "^>>>>>>>",
             "--"],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return fmt_err(f"Git grep failed: {e}")

    if result.returncode != 0 or not result.stdout.strip():
        return fmt_ok({
            "path": git_root,
            "total": 0,
            "message": "No merge conflict markers found",
        })

    lines = result.stdout.strip().split("\n")
    markers = []
    for line in lines[:100]:
        parts = line.split(":", 2)
        if len(parts) >= 3:
            markers.append({
                "file": parts[0],
                "line": int(parts[1]),
                "marker": parts[2].strip(),
            })

    files_affected = len(set(m["file"] for m in markers))

    return fmt_err({
        "path": git_root,
        "total": len(markers),
        "files_affected": files_affected,
        "markers": markers,
        "message": f"Found {len(markers)} merge conflict markers in {files_affected} files",
    })


CODE_MERGE_CONFLICT_FINDER_SCHEMA = {
    "name": "code_merge_conflict_finder",
    "description": (
        "Find unresolved merge conflict markers (<<<<<<<, =======, >>>>>>>) "
        "in a git repository using git grep. Returns file:line for each marker. "
        "Use this BEFORE committing to ensure no conflict markers remain."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to a file or directory inside the git repository.",
            },
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# code_git_log_symbol
# ---------------------------------------------------------------------------


def _find_symbol_line(path: str, line: int) -> int:
    """Find the definition line of a symbol at or near the given line.

    Uses a simple heuristic: look backward from the given line to find
    a function/class definition keyword.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return line
    content = target.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")
    # Search backward from the given line for a definition
    search_start = min(line - 1, len(lines) - 1)
    for i in range(search_start, -1, -1):
        stripped = lines[i].strip()
        if any(stripped.startswith(kw) for kw in
               ["def ", "class ", "async def ", "func ", "func(",
                "function ", "function(", "pub fn ", "pub fn(",
                "fn ", "fn(", "public ", "private ", "protected "]):
            return i + 1  # 1-based
    return line


def code_git_log_symbol_tool(
    path: str,
    line: int,
    max_count: int = 10,
) -> str:
    """Show git commit history for a symbol.

    Uses 'git log -L' to trace changes to the function/class at the
    given line, plus 'git blame' for the last modifier.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    git_root = _find_git_root(str(target))
    if not git_root:
        return fmt_err(f"Not inside a git repository: {path}")

    # Find the actual definition line
    def_line = _find_symbol_line(str(target), line)

    try:
        # Git log for this function (using -L)
        log_result = subprocess.run(
            ["git", "log", "-n", str(max_count), "--format=%H|%an|%ai|%s",
             "-L", f"{def_line},{def_line}:{target.name}",
             "--", str(target)],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Git blame for the line
        blame_result = subprocess.run(
            ["git", "blame", "-L", f"{def_line},{def_line}",
             "--porcelain", str(target)],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return fmt_err(f"Git operation failed: {e}")

    commits = []
    if log_result.returncode == 0 and log_result.stdout.strip():
        for entry in log_result.stdout.strip().split("\n"):
            parts = entry.split("|", 3)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })

    # Parse blame output for author info
    blame_author = ""
    blame_date = ""
    if blame_result.returncode == 0:
        for bline in blame_result.stdout.split("\n"):
            if bline.startswith("author "):
                blame_author = bline[7:].strip()
            elif bline.startswith("author-time "):
                import datetime
                ts = int(bline[12:].strip())
                blame_date = datetime.datetime.fromtimestamp(ts).isoformat()

    return fmt_ok({
        "path": str(target),
        "symbol_line": def_line,
        "total_commits": len(commits),
        "commits": commits,
        "last_modified_by": blame_author,
        "last_modified_at": blame_date,
    })


CODE_GIT_LOG_SYMBOL_SCHEMA = {
    "name": "code_git_log_symbol",
    "description": (
        "Show git commit history and blame info for a function/class symbol. "
        "Uses 'git log -L' to trace changes to the symbol and 'git blame' "
        "for the last modifier. Returns commit history with author, date, message. "
        "Use for understanding why a symbol was changed and by whom."
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
            "max_count": {
                "type": "integer",
                "description": "Maximum number of commits to return (default: 10).",
                "default": 10,
            },
        },
        "required": ["path", "line"],
    },
}


# ---------------------------------------------------------------------------
# code_git_diff_file
# ---------------------------------------------------------------------------


def code_git_diff_file_tool(
    path: str,
    staged: bool = False,
    context_lines: int = 3,
) -> str:
    """Show uncommitted git diff for a file or entire project.

    Uses 'git diff' to show working tree changes.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    git_root = _find_git_root(str(target))
    if not git_root:
        return fmt_err(f"Not inside a git repository: {path}")

    try:
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--cached")
        cmd.extend(["-U", str(context_lines)])
        # If path is a file, limit diff to that file
        if target.is_file():
            cmd.extend(["--", str(target)])

        result = subprocess.run(
            cmd, cwd=git_root, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return fmt_err(f"Git diff failed: {e}")

    diff_text = result.stdout.strip()

    if not diff_text:
        return fmt_ok({
            "path": str(target),
            "staged": staged,
            "has_changes": False,
            "message": "No uncommitted changes",
        })

    # Count changed files and lines
    files_changed = set()
    added = 0
    removed = 0
    for line in diff_text.split("\n"):
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            files_changed.add(line[6:])
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1

    return fmt_ok({
        "path": str(target),
        "staged": staged,
        "has_changes": True,
        "files_changed": len(files_changed),
        "lines_added": added,
        "lines_removed": removed,
        "diff": diff_text[:5000],  # cap diff output
    })


CODE_GIT_DIFF_FILE_SCHEMA = {
    "name": "code_git_diff_file",
    "description": (
        "Show uncommitted git diff for a file or the entire project. "
        "Returns a summary (files changed, lines added/removed) and "
        "the diff text. Use staged=true for staged changes (git diff --cached)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory path to check for changes.",
            },
            "staged": {
                "type": "boolean",
                "description": "Show staged changes instead of working tree (default: false).",
                "default": False,
            },
            "context_lines": {
                "type": "integer",
                "description": "Lines of context around each change (default: 3).",
                "default": 3,
            },
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# Handler functions for registry dispatch
# ---------------------------------------------------------------------------


def _handle_code_todo_finder(args, **kw):
    return code_todo_finder_tool(
        path=args.get("path", ""),
        include_patterns=args.get("include_patterns"),
        include_extensions=args.get("include_extensions"),
    )


def _handle_code_merge_conflict_finder(args, **kw):
    return code_merge_conflict_finder_tool(
        path=args.get("path", ""),
    )


def _handle_code_git_log_symbol(args, **kw):
    return code_git_log_symbol_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        max_count=args.get("max_count", 10),
    )


def _handle_code_git_diff_file(args, **kw):
    return code_git_diff_file_tool(
        path=args.get("path", ""),
        staged=args.get("staged", False),
        context_lines=args.get("context_lines", 3),
    )


__all__ = [
    "code_todo_finder_tool", "code_merge_conflict_finder_tool",
    "code_git_log_symbol_tool", "code_git_diff_file_tool",
    "CODE_TODO_FINDER_SCHEMA", "CODE_MERGE_CONFLICT_FINDER_SCHEMA",
    "CODE_GIT_LOG_SYMBOL_SCHEMA", "CODE_GIT_DIFF_FILE_SCHEMA",
    "_handle_code_todo_finder", "_handle_code_merge_conflict_finder",
    "_handle_code_git_log_symbol", "_handle_code_git_diff_file",
]
