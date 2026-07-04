#!/usr/bin/env python3
"""tools/metrics.py — Aggregate project metrics tool.

Extracted from code_tools.py.
Provides code_metrics_tool, CODE_METRICS_SCHEMA, and _handle_code_metrics.
Uses lazy imports from code_tools for language registry helpers.
"""

from __future__ import annotations

from pathlib import Path

from .._fmt import fmt_err, fmt_json
from .._logging import setup_logger as _setup_code_intel_logger
from .complexity import (
    _COMPLEXITY_NODE_TYPES,
    _FUNCTION_QUERIES,
    _count_early_returns,
    _count_nodes,
)

logger = _setup_code_intel_logger(__name__)


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


# ── Shared constants ──────────────────────────────────────────────
_COMMENT_PREFIXES = {
    "python": ("#",),
    "typescript": ("//",),
    "tsx": ("//",),
    "javascript": ("//",),
    "go": ("//",),
    "rust": ("//",),
    "java": ("//",),
}

_EXCLUDE_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", "target"}


def _count_file_lines(source_text: str, lang_key: str) -> tuple:
    """Count code, blank, and comment lines in a source file."""
    lines = source_text.splitlines()
    file_total = len(lines)
    file_code = 0
    file_blank = 0
    file_comment = 0
    in_block_comment = False
    comment_prefixes = _COMMENT_PREFIXES.get(lang_key, ())

    for line in lines:
        stripped = line.strip()
        if not stripped:
            file_blank += 1
            continue
        if in_block_comment:
            file_comment += 1
            if "*/" in stripped:
                in_block_comment = False
            continue
        if "/*" in stripped and "*/" not in stripped:
            file_comment += 1
            in_block_comment = True
            continue
        if stripped.startswith("/*") and stripped.endswith("*/"):
            file_comment += 1
            continue
        if comment_prefixes and any(stripped.startswith(p) for p in comment_prefixes):
            file_comment += 1
            continue
        if lang_key == "python" and (stripped.startswith('"""') or stripped.startswith("'''")):
            file_comment += 1
            if stripped.count('"""') < 2 and stripped.count("'''") < 2:
                in_block_comment = True
            continue
        file_code += 1

    return file_total, file_code, file_blank, file_comment


def _compute_file_complexities(source_bytes: bytes, lang_key: str, entry_path: str) -> list:
    """Calculate cyclomatic complexity for all functions in a file."""
    from tree_sitter import Query, QueryCursor

    from ..code_tools import _get_language, _get_parser

    if lang_key not in _COMPLEXITY_NODE_TYPES:
        return []
    ntypes = _COMPLEXITY_NODE_TYPES[lang_key]
    parser = _get_parser(lang_key)
    lang_obj = _get_language(lang_key)
    if parser is None or lang_obj is None:
        return []
    tree = parser.parse(source_bytes)
    if tree is None:
        return []
    fq = _FUNCTION_QUERIES.get(lang_key)
    if not fq:
        return []
    try:
        func_query = Query(lang_obj, fq)
    except Exception:
        logger.debug("metrics: empty metrics result")
        return []

    results = []
    for _pi, cd in QueryCursor(func_query).matches(tree.root_node):
        name = ""
        for nn in cd.get("name", []):
            try:
                name = source_bytes[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
            except Exception:
                name = "?"
            break
        for dn in cd.get("def", []):
            branches = _count_nodes(dn, ntypes.get("branches", []))
            loops = _count_nodes(dn, ntypes.get("loops", []))
            exceptions = _count_nodes(dn, ntypes.get("exceptions", []))
            early_returns = _count_early_returns(dn, dn, ntypes.get("return_type", "return_statement"))
            total = 1 + branches + loops + exceptions + early_returns
            results.append({
                "function": name,
                "file": entry_path,
                "line": dn.start_point[0] + 1,
                "total": total,
            })
            break
    return results


def _format_metrics_result(target, total_files, files_by_language, total_lines,
                           code_lines, blank_lines, comment_lines, all_complexities):
    """Build the final metrics result dict."""
    comment_ratio = round(comment_lines / code_lines, 4) if code_lines > 0 else 0.0
    avg_complexity = round(sum(c["total"] for c in all_complexities) / len(all_complexities), 2) if all_complexities else 0.0
    all_complexities.sort(key=lambda c: c["total"], reverse=True)
    top_complexity = all_complexities[:5]
    return {
        "path": str(target),
        "total_files": total_files,
        "files_by_language": dict(sorted(files_by_language.items(), key=lambda x: -x[1])),
        "total_lines": total_lines,
        "code_lines": code_lines,
        "blank_lines": blank_lines,
        "comment_lines": comment_lines,
        "comment_ratio": comment_ratio,
        "avg_complexity": avg_complexity,
        "functions_analyzed": len(all_complexities),
        "top_complexity": top_complexity,
    }


def code_metrics_tool(path: str = ".", directory: bool = True, depth: int = 5) -> str:
    """Aggregate project metrics: LOC, files per language, comment ratio, average complexity."""
    from ..code_tools import _EXT_TO_LANG

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")
    if not target.is_dir():
        return fmt_err(f"Not a directory: {path}")

    total_files = 0
    files_by_language: dict = {}
    total_lines = 0
    code_lines = 0
    blank_lines = 0
    comment_lines = 0
    all_complexities = []

    # Walk directory tree with depth limit
    stack = [(target, 0)]
    while stack:
        current_dir, current_depth = stack.pop()
        if current_depth > depth:
            continue
        try:
            entries = sorted(current_dir.iterdir(), key=lambda e: e.name)
        except Exception as e:
            logger.debug("code_metrics_tool: iterating dir: %s", e)
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if entry.name not in _EXCLUDE_DIRS:
                    stack.append((entry, current_depth + 1))
            elif entry.is_file():
                ext = entry.suffix.lower()
                lang_key = _EXT_TO_LANG.get(ext)
                if lang_key is None:
                    continue
                total_files += 1
                files_by_language[lang_key] = files_by_language.get(lang_key, 0) + 1
                try:
                    source_bytes = entry.read_bytes()
                    source_text = source_bytes.decode("utf-8", errors="replace")
                except Exception as e:
                    logger.debug("code_metrics_tool: reading file: %s", e)
                    continue
                ft, fc, fb, fcm = _count_file_lines(source_text, lang_key)
                total_lines += ft
                code_lines += fc
                blank_lines += fb
                comment_lines += fcm
                all_complexities.extend(
                    _compute_file_complexities(source_bytes, lang_key, str(entry))
                )

    if total_files == 0:
        return fmt_err("No source files found in directory")

    result = _format_metrics_result(
        target, total_files, files_by_language, total_lines,
        code_lines, blank_lines, comment_lines, all_complexities,
    )
    return fmt_json(result)


# ---------------------------------------------------------------------------
# Schema + Handler
# ---------------------------------------------------------------------------

CODE_METRICS_SCHEMA = {
    "name": "code_metrics",
    "description": "Aggregate project metrics: LOC, files per language, comment ratio.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path (default: current dir)"},
            "depth": {"type": "integer", "description": "Max scan depth (default: 5)"},
        },
        "required": [],
    },
}


def _handle_code_metrics(args, **kw):
    return code_metrics_tool(
        path=args.get("path", "."),
        depth=args.get("depth", 5),
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "CODE_METRICS_SCHEMA",
    "code_metrics_tool",
    "_handle_code_metrics",
]
