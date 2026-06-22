"""tools/pattern.py — Ast-grep Refactoring Utilities.

Extracted from code_tools.py for maintainability.
Provides ast-grep matching, rewrite interpolation, and change application.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ast-grep language mapping
# ---------------------------------------------------------------------------
_AST_GREP_LANG_MAP = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "rust": "rust",
    "go": "go",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
}

# Reusable regex for extracting ast-grep meta variable names from a pattern
_AST_GREP_VAR_RE = re.compile(r'\$(\$)?([A-Z_][A-Z0-9_]*)')


def _check_ast_grep_reqs() -> bool:
    """Always return True so the tool is visible, but fail gracefully."""
    return True


def _ast_grep_rewrite(src: str, rewrite_template: str, variables: dict) -> str:
    """Interpolate ast-grep meta variables into a rewrite template.

    ast-grep-py's commit_edits doesn't interpolate $VAR in replacement text,
    so we do it manually.
    """
    result = rewrite_template
    # Sort by key length descending to avoid partial replacements
    for var_name in sorted(variables, key=len, reverse=True):
        # $NAME and $$NAME are both used by ast-grep
        for prefix in ("$$", "$"):
            placeholder = f"{prefix}{var_name}"
            if placeholder in result:
                result = result.replace(placeholder, variables[var_name])
    return result


def _build_refactor_changes(matches, source_lines, pattern, rewrite, context_lines):
    """Convert ast-grep matches to change dicts."""
    var_names = set(_AST_GREP_VAR_RE.findall(pattern))
    changes = []
    for match in matches:
        rng = match.range()
        start_row, start_col = rng.start.line, rng.start.column
        end_row, end_col = rng.end.line, rng.end.column

        original = source_lines[start_row][start_col:]
        if end_row > start_row:
            original += "\n" + "\n".join(source_lines[start_row + 1:end_row])
        if end_row < len(source_lines):
            original += source_lines[end_row][:end_col]

        variables = {}
        for is_multi, var_name in var_names:
            try:
                var_node = match.get_match(var_name)
                if var_node is not None:
                    variables[var_name] = var_node.text()
            except Exception as exc:
                logger.debug("ast-grep: failed to extract variable %s: %s", var_name, exc)
                pass

        replacement = _ast_grep_rewrite("", rewrite, variables)
        ctx_start = max(0, start_row - context_lines)
        ctx_end = min(len(source_lines) - 1, end_row + context_lines)

        changes.append({
            "line": start_row + 1,
            "end_line": end_row + 1,
            "original": original[:300],
            "replacement": replacement[:300],
            "variables": variables,
            "context": {
                "start": ctx_start + 1, "end": ctx_end + 1,
                "before": "\n".join(source_lines[ctx_start:start_row]) if start_row > 0 else "",
                "after": "\n".join(source_lines[end_row + 1:ctx_end + 1]) if end_row < ctx_end else "",
            },
        })
    return changes


def _apply_refactor_changes(changes, matches, source_lines, target, dry_run):
    """Apply refactor changes. Returns bool or error dict."""
    if dry_run or not changes:
        return False
    try:
        lines_out = source_lines[:]
        for change, match in zip(reversed(changes), reversed(matches)):
            rng = match.range()
            sr, sc = rng.start.line, rng.start.column
            er, ec = rng.end.line, rng.end.column
            first = lines_out[sr][:sc] + change["replacement"]
            last = lines_out[er][ec:] if er < len(lines_out) else ""
            lines_out[sr:er + 1] = [first + last]
        target.write_text("\n".join(lines_out), encoding="utf-8")
        return True
    except Exception as e:
        return {"error": f"Failed to apply: {e}", "match_count": len(changes)}


__all__ = [
    "_check_ast_grep_reqs",
    "_ast_grep_rewrite",
    "_build_refactor_changes",
    "_apply_refactor_changes",
    "_AST_GREP_LANG_MAP",
    "_AST_GREP_VAR_RE",
]
