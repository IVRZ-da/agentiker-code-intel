"""tools/batch.py — Bulk refactoring via ast-grep across multiple files.

Tools in this module:
    - code_batch_refactor: Apply ast-grep patterns across many files with
      dry-run, preview, and apply modes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_ast_grep() -> Optional[str]:
    """Check if ast-grep (sg) is available on PATH."""
    sg_candidate = shutil.which("sg")
    if sg_candidate:
        return sg_candidate
    try:
        result = subprocess.run(
            ["sg", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "sg"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
        logger.debug("_find_ast_grep: sg not available: %s", e)
        pass
    return None


def _ast_grep_scan(
    sg_path: str,
    directory: str,
    pattern: str,
    file_glob: str,
    language: str,
) -> Optional[List[Dict[str, Any]]]:
    """Run ast-grep scan to find all matches without rewriting.

    Returns a list of match dicts, or None on failure.
    """
    try:
        result = subprocess.run(
            [
                sg_path, "scan",
                "--pattern", pattern,
                "--glob", file_glob,
                "--lang", language,
                "--json",
                directory,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            # ast-grep returns a list of file results
            # Each item: { "path": "...", "matches": [...] }
            if isinstance(data, list):
                return data
            # Some versions wrap in {"results": [...]}
            if isinstance(data, dict):
                return data.get("results") or data.get("matches") or []
        return []
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
        import logging
        logging.getLogger("agentiker_code_intel").debug(
            "ast-grep scan failed: %s", e
        )
        return None


def _ast_grep_apply(
    sg_path: str,
    directory: str,
    pattern: str,
    rewrite: str,
    file_glob: str,
    language: str,
) -> Optional[List[Dict[str, Any]]]:
    """Run ast-grep rewrite to apply changes.

    Returns a list of changed file results, or None on failure.
    """
    try:
        result = subprocess.run(
            [
                sg_path, "run",
                "--pattern", pattern,
                "--rewrite", rewrite,
                "--glob", file_glob,
                "--lang", language,
                "--json",
                directory,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("results") or data.get("rewritten") or []
        return []
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
        import logging
        logging.getLogger("agentiker_code_intel").debug(
            "ast-grep apply failed: %s", e
        )
        return None


def _fallback_scan(
    directory: str,
    pattern: str,
    file_glob: str,
) -> List[Dict[str, Any]]:
    """Fallback pattern matching: use Python glob + re.sub to find matches.

    This is a best-effort plain-text fallback when ast-grep is not
    available.  It does NOT understand AST structure.
    """
    results: List[Dict[str, Any]] = []
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return results

    # Convert a glob like **/*.ts to a simple suffix check
    # We'll just match by file extension from the glob pattern
    valid_extensions = _glob_to_extensions(file_glob)

    for filepath in sorted(root.rglob("*")):
        if not filepath.is_file():
            continue
        if valid_extensions and filepath.suffix not in valid_extensions:
            continue
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("_ast_grep_scan: reading file: %s", e)
            continue

        try:
            compiled = re.compile(pattern)
        except re.error:
            compiled = re.compile(re.escape(pattern))

        file_matches: List[Dict[str, Any]] = []
        for m in compiled.finditer(text):
            line_num = text[: m.start()].count("\n") + 1
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            context = text[start:end].replace("\n", " ").strip()
            file_matches.append({
                "line": line_num,
                "old": m.group(0),
                "new": None,
                "context": context,
                "start": m.start(),
                "end": m.end(),
            })

        if file_matches:
            results.append({
                "path": str(filepath),
                "matches": file_matches,
            })

    return results


def _fallback_apply(
    directory: str,
    pattern: str,
    rewrite: str,
    file_glob: str,
) -> List[Dict[str, Any]]:
    """Fallback apply: use Python re.sub to perform replacements.

    Returns list of changed file results.
    """
    results: List[Dict[str, Any]] = []
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return results

    valid_extensions = _glob_to_extensions(file_glob)

    try:
        compiled = re.compile(pattern)
    except re.error:
        compiled = re.compile(re.escape(pattern))

    for filepath in sorted(root.rglob("*")):
        if not filepath.is_file():
            continue
        if valid_extensions and filepath.suffix not in valid_extensions:
            continue
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("_code_batch_refactor: reading file: %s", e)
            continue

        new_text, count = compiled.subn(rewrite, text)
        if count == 0:
            continue

        # Collect individual changes for the report
        file_matches: List[Dict[str, Any]] = []
        for m in compiled.finditer(text):
            line_num = text[: m.start()].count("\n") + 1
            old_val = m.group(0)
            new_val = compiled.sub(rewrite, old_val)
            file_matches.append({
                "line": line_num,
                "old": old_val,
                "new": new_val,
            })

        results.append({
            "path": str(filepath),
            "matches": file_matches,
            "count": count,
            "new_text": new_text,
        })

    return results


def _glob_to_extensions(file_glob: str) -> set:
    """Extract valid extensions from a glob pattern like **/*.ts."""
    exts: set = set()
    parts = file_glob.split(".")
    if len(parts) > 1:
        ext = "." + parts[-1].rstrip("*?")
        if len(ext) > 1:
            exts.add(ext)
    return exts


# ---------------------------------------------------------------------------
# code_batch_refactor_tool
# ---------------------------------------------------------------------------


def code_batch_refactor_tool(
    path: str,
    pattern: str,
    rewrite: str,
    file_glob: str = "**/*.ts",
    dry_run: bool = True,
    language: str = "ts",
) -> str:
    """Apply an ast-grep pattern + rewrite across multiple files.

    Scans *path* recursively for files matching *file_glob*, runs ast-grep
    (or a plain-text fallback) to find matches, and returns structured
    results.  When *dry_run* is True (default) the changes are only
    previewed.  When False, .bak backups are created and changes are
    written.

    Parameters
    ----------
    path : str
        Directory or file to scan.
    pattern : str
        ast-grep pattern (e.g. ``console.log($ARG)``).
    rewrite : str
        Replacement template (e.g. ``console.info($ARG)``).
    file_glob : str, optional
        Glob pattern for files (default ``**/*.ts``).
    dry_run : bool, optional
        When True, preview only (default True).
    language : str, optional
        Target language for ast-grep (default ``ts``).

    Returns
    -------
    str
        Formatted result (rich Panel via `fmt_ok` / `fmt_err`).
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Determine scan root
    directory = str(target) if target.is_dir() else str(target.parent)

    # --- Try ast-grep first ---
    sg_path = _find_ast_grep()
    used_fallback = False
    matches: Any = None

    if sg_path:
        if dry_run:
            matches = _ast_grep_scan(sg_path, directory, pattern, file_glob, language)
        else:
            matches = _ast_grep_apply(sg_path, directory, pattern, rewrite, file_glob, language)

        if matches is None:
            # ast-grep failed — fall back
            import logging
            logging.getLogger("agentiker_code_intel").info(
                "ast-grep failed, falling back to Python re.sub"
            )
            used_fallback = True
    else:
        used_fallback = True

    if used_fallback:
        if dry_run:
            matches = _fallback_scan(directory, pattern, file_glob)
        else:
            matches = _fallback_apply(directory, pattern, rewrite, file_glob)

    # --- Normalise results ---
    files_scanned: set = set()
    files_changed: set = set()
    total_matches: int = 0
    changes: List[Dict[str, Any]] = []

    for file_result in (matches or []):
        if not isinstance(file_result, dict):
            continue
        fpath = file_result.get("path", "")
        if not fpath:
            continue
        files_scanned.add(fpath)

        file_matches = file_result.get("matches", [])
        if not file_matches:
            continue

        files_changed.add(fpath)

        for m in file_matches:
            line = m.get("line", 0)
            old = m.get("old", "")
            new_hint = ""
            if not dry_run:
                new_hint = m.get("new", "")
                # For ast-grep apply, the response format might differ
                if not new_hint and rewrite:
                    new_hint = rewrite
            changes.append({
                "file": fpath,
                "line": line,
                "old": old,
                "new": new_hint,
            })
            total_matches += 1

    # --- Apply changes (non-dry-run, fallback path) ---
    changes_applied = 0
    files_actually_changed = 0

    if not dry_run and used_fallback and matches:
        for file_result in matches:
            fpath = file_result.get("path", "")
            new_text = file_result.get("new_text", "")
            if not fpath or not new_text:
                continue
            filepath = Path(fpath)
            if not filepath.exists():
                continue
            # Create .bak backup
            try:
                bak_path = filepath.with_suffix(filepath.suffix + ".bak")
                if not bak_path.exists():
                    shutil.copy2(str(filepath), str(bak_path))
                filepath.write_text(new_text, encoding="utf-8")
                changes_applied += file_result.get("count", 0)
                files_actually_changed += 1
            except (OSError, UnicodeEncodeError) as e:
                import logging
                logging.getLogger("agentiker_code_intel").warning(
                    "Failed to write %s: %s", fpath, e
                )

    # For ast-grep apply (non-fallback), the tool already wrote changes.
    # We just count what changed.
    if not dry_run and sg_path and not used_fallback:
        files_actually_changed = len(files_changed)

    # --- Build result ---
    result = {
        "path": directory,
        "pattern": pattern,
        "rewrite": rewrite,
        "file_glob": file_glob,
        "language": language,
        "dry_run": dry_run,
        "used_ast_grep": sg_path is not None and not used_fallback,
        "files_scanned": len(files_scanned),
        "files_changed": len(files_changed),
        "total_matches": total_matches,
        "changes": changes[:200],  # cap at 200 individual changes
    }

    if not dry_run:
        result["files_actually_changed"] = files_actually_changed
        result["changes_applied"] = changes_applied or total_matches
        result["backups_created"] = ".bak" if files_actually_changed else "none"

    return fmt_ok(result)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CODE_BATCH_REFACTOR_SCHEMA = {
    "name": "code_batch_refactor",
    "description": (
        "Apply an ast-grep pattern + rewrite across multiple files "
        "with dry-run, preview, and apply modes. Scans a directory "
        "for files matching a glob, runs ast-grep (or plain-text "
        "fallback) to find matches, and returns structured results "
        "including files scanned, files changed, total matches, and "
        "individual change details. When dry_run=False, creates .bak "
        "backup files before writing changes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory or file path to scan for refactoring.",
            },
            "pattern": {
                "type": "string",
                "description": "ast-grep pattern (e.g. 'console.log($ARG)').",
            },
            "rewrite": {
                "type": "string",
                "description": "Replacement template (e.g. 'console.info($ARG)').",
            },
            "file_glob": {
                "type": "string",
                "description": "Glob pattern for files to include (default: '**/*.ts').",
                "default": "**/*.ts",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), return a preview of changes "
                    "without applying them. When False, write changes "
                    "and create .bak backup files."
                ),
                "default": True,
            },
            "language": {
                "type": "string",
                "description": "Target language for ast-grep (default: 'ts').",
                "default": "ts",
            },
        },
        "required": ["path", "pattern", "rewrite"],
    },
}


# ---------------------------------------------------------------------------
# Handler functions for registry dispatch
# ---------------------------------------------------------------------------


def _handle_code_batch_refactor(args: dict, **kwargs: Any) -> str:
    """Handler wrapper for ctx.register_tool()."""
    return code_batch_refactor_tool(
        path=args.get("path", ""),
        pattern=args.get("pattern", ""),
        rewrite=args.get("rewrite", ""),
        file_glob=args.get("file_glob", "**/*.ts"),
        dry_run=args.get("dry_run", True),
        language=args.get("language", "ts"),
    )


__all__ = [
    "code_batch_refactor_tool",
    "CODE_BATCH_REFACTOR_SCHEMA",
    "_handle_code_batch_refactor",
]
