#!/usr/bin/env python3
"""tools/export.py — Export, Docstring, and Dependency Risk tools.

Extracted from code_tools.py for modularity.
Provides code_export_tool, code_docstring_generate_tool, code_dependency_risk_tool
with their schemas and handler functions.
"""

from __future__ import annotations

import re
from pathlib import Path

from .._fmt import fmt_err, fmt_json, fmt_ok
from .._import_graph import ImportGraph
from .._logging import setup_logger as _setup_code_intel_logger

# from ..code_tools import code_symbols_tool

logger = _setup_code_intel_logger(__name__)


# ---------------------------------------------------------------------------
# F1: code_export_tool — Export symbol index as JSON or Markdown
# ---------------------------------------------------------------------------


def code_export_tool(
    path: str = ".",
    fmt: str = "json",
    kind: str = "all",
) -> str:
    """Export symbol index from a project as JSON or Markdown.

    Uses extract_symbols() for AST-based symbol extraction, then formats
    the result as JSON, Markdown, or a compact summary.

    Args:
        path: Project or file path to export symbols from.
        fmt: Output format: "json", "markdown", or "summary" (default: json).
        kind: Filter by symbol kind: "all", "function", "class", "method" (default: all).

    Returns:
        Formatted symbol index output.

    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Get symbols directly from internal function (not via code_symbols_tool
    # which returns rich-formatted output that breaks json.loads).
    # Get symbols directly from internal functions — code_symbols_tool
    # returns rich-formatted output that breaks json.loads.
    symbols = []
    try:
        target_path = Path(path).expanduser().resolve()
        from ..tools.base import _EXT_TO_LANG, detect_language

        if target_path.is_file():
            lang_key = detect_language(str(target_path))
            if lang_key:
                from ..tools.symbols import _symbols_extract_single

                raw_syms, _ = _symbols_extract_single(
                    target_path,
                    lang_key,
                    pattern=None,
                    kind=None if kind == "all" else kind,
                    include_body=False,
                )
                for s in raw_syms:
                    s["file"] = str(target_path)
                symbols = raw_syms
        elif target_path.is_dir():
            from ..tools.symbols import _symbols_extract_single

            seen_files = set()
            for ext in _EXT_TO_LANG:
                for file_path in sorted(target_path.rglob(f"*{ext}")):
                    if not file_path.is_file() or file_path in seen_files:
                        continue
                    seen_files.add(file_path)
                    file_lang = detect_language(str(file_path))
                    if not file_lang:
                        continue
                    try:
                        raw_syms, _ = _symbols_extract_single(
                            file_path,
                            file_lang,
                            pattern=None,
                            kind=None if kind == "all" else kind,
                            include_body=False,
                        )
                        for s in raw_syms:
                            s["file"] = str(file_path)
                        symbols.extend(raw_syms)
                    except Exception:
                        continue
    except Exception:
        logger.debug("export: symbol collection failed")
        symbols = []

    if not symbols:
        return fmt_err("No symbols found")

    # Group by file
    by_file = {}
    for sym in symbols:
        fpath = sym.get("file", "unknown")
        by_file.setdefault(fpath, []).append(sym)

    if fmt == "markdown":
        md_lines = [
            "# Project Symbol Index",
            f"Total: {len(symbols)} symbols across {len(by_file)} files",
            "",
        ]
        for fpath in sorted(by_file):
            syms = by_file[fpath]
            funcs = [s for s in syms if s.get("kind") in ("function", "method")]
            classes = [s for s in syms if s.get("kind") == "class"]
            md_lines.append(f"## {fpath}")
            if classes:
                md_lines.append(f"({len(classes)} classes, {len(funcs)} functions)")
            elif funcs:
                md_lines.append(f"({len(funcs)} functions)")
            if funcs:
                md_lines.append("")
                md_lines.append("| Name | Line | Kind |")
                md_lines.append("|------|------|------|")
                for s in sorted(funcs, key=lambda x: x.get("line", 0)):
                    md_lines.append(f"| {s.get('name', '?')} | {s.get('line', 0)} | {s.get('kind', '')} |")
            if classes:
                for s in classes:
                    children = s.get("children", [])
                    n_methods = sum(1 for c in children if c.get("kind") == "method")
                    md_lines.append(f"- **{s.get('name', '?')}** (L{s.get('line', 0)}, {n_methods} methods)")
            md_lines.append("")
        return fmt_ok({"markdown": "\n".join(md_lines)}, title="Symbol Export (Markdown)")

    elif fmt == "summary":
        lang_counts = {}
        for fpath in by_file:
            ext = Path(fpath).suffix
            lang_counts[ext] = lang_counts.get(ext, 0) + 1
        summary = {
            "total_symbols": len(symbols),
            "total_files": len(by_file),
            "files_by_extension": lang_counts,
            "top_files": sorted(by_file.keys(), key=lambda f: len(by_file[f]), reverse=True)[:10],
        }
        return fmt_ok(summary, title="Symbol Index Summary")

    # Default: JSON format
    result = {
        "project": str(target),
        "total_symbols": len(symbols),
        "total_files": len(by_file),
        "symbols_by_file": by_file,
    }
    return fmt_json(result)


CODE_EXPORT_SCHEMA = {
    "name": "code_export",
    "description": "Export symbol index as JSON or Markdown for documentation.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project or file path"},
            "fmt": {
                "type": "string",
                "enum": ["json", "markdown", "summary"],
                "description": "Output format (default: json)",
            },
            "kind": {
                "type": "string",
                "enum": ["all", "function", "class", "method"],
                "description": "Filter by symbol kind (default: all)",
            },
        },
        "required": ["path"],
    },
}


def _handle_code_export(args, **kw):
    return code_export_tool(
        path=args.get("path", "."),
        fmt=args.get("fmt", "json"),
        kind=args.get("kind", "all"),
    )


# ---------------------------------------------------------------------------
# D2: code_docstring_generate_tool — Generate docstring template from AST
# ---------------------------------------------------------------------------


def code_docstring_generate_tool(
    path: str,
    line: int,
    style: str = "google",
) -> str:
    """Generate a docstring template from a function's AST signature.

    Reads the function signature via AST, extracts parameters and return
    type annotations, and produces a structured docstring template.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Lazy import detect_language for language-aware docstring generation
    from ..code_tools import detect_language  # noqa: F401

    # Read the file and extract the function definition
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
    except Exception as e:
        return fmt_err(f"Cannot read file: {e}")

    # Find the function definition at or near the given line
    func_lines = []
    func_line = -1
    start_idx = max(0, line - 3)
    for i in range(start_idx, len(lines)):
        stripped = lines[i].strip()
        if any(
            stripped.startswith(kw)
            for kw in [
                "def ",
                "async def ",
                "func ",
                "func(",
                "function ",
                "function(",
                "fn ",
                "fn(",
                "pub fn ",
                "pub fn(",
            ]
        ):
            func_line = i + 1
            # Collect function lines (def + body until blank line or next def/class)
            depth = 0
            for j in range(i, len(lines)):
                func_lines.append(lines[j])
                # Count indentation depth
                if j == i:
                    continue
                s = lines[j].strip()
                if not s and depth == 0:
                    break
                if s.startswith("def ") or s.startswith("class ") or s.startswith("async def "):
                    break
                if s.startswith("fn ") or s.startswith("pub fn "):
                    break
            break

    if not func_lines:
        return fmt_err("No function definition found at or near the given line")

    func_text = "\n".join(func_lines)

    # Parse parameters using regex
    param_pattern = r"def\s+\w+\s*\((.*?)\)(?:\s*->\s*([^:]+))?\s*:"
    match = re.search(param_pattern, func_text, re.DOTALL)
    if not match:
        return fmt_err("Could not parse function signature")

    params_str = match.group(1)
    return_type = match.group(2).strip() if match.group(2) else "None"

    # Parse individual parameters
    params = []
    if params_str.strip():
        for p in params_str.split(","):
            p = p.strip()
            if p == "self" or p == "cls" or p == "self," or not p:
                continue
            # Split on ':' to get name and type
            if ":" in p:
                name, ptype = p.split(":", 1)
                params.append({"name": name.strip(), "type": ptype.strip().split("=")[0].strip()})
            elif "=" in p:
                name = p.split("=")[0].strip()
                params.append({"name": name, "type": "Any"})
            else:
                params.append({"name": p.strip(), "type": "Any"})

    # Extract function name
    name_match = re.match(r"(?:async\s+)?def\s+(\w+)", func_text)
    func_name = name_match.group(1) if name_match else "unknown"

    style = style.lower()
    if style == "numpy":
        doc_lines = [
            f'"""{func_name}',
            "",
            "    Parameters",
            "    ----------",
        ]
        for p in params:
            doc_lines.append(f"    {p['name']} : {p['type']}")
            doc_lines.append(f"        Description of {p['name']}.")
        doc_lines.extend(
            [
                "",
                "    Returns",
                "    -------",
                f"    {return_type}",
                "        Description of return value.",
                '"""',
            ]
        )
    elif style == "sphinx":
        doc_lines = [
            f'"""{func_name}.',
            "",
            "    :param params: ...",
        ]
        for p in params:
            doc_lines.append(f"    :param {p['name']}: Description.")
            doc_lines.append(f"    :type {p['name']}: {p['type']}")
        doc_lines.extend(
            [
                "",
                "    :returns: Description.",
                f"    :rtype: {return_type}",
                '"""',
            ]
        )
    else:  # google (default)
        doc_lines = [
            f'"""{func_name}.',
            "",
        ]
        if params:
            doc_lines.append("    Args:")
            for p in params:
                doc_lines.append(f"        {p['name']} ({p['type']}): Description.")
            doc_lines.append("")
        doc_lines.extend(
            [
                "    Returns:",
                f"        {return_type}: Description.",
                '"""',
            ]
        )

    docstring = "\n".join(doc_lines)

    return fmt_ok(
        {
            "path": str(target),
            "function": func_name,
            "line": func_line,
            "parameters": params,
            "return_type": return_type,
            "style": style,
            "docstring": docstring,
        }
    )


CODE_DOCSTRING_GENERATE_SCHEMA = {
    "name": "code_docstring_generate",
    "description": (
        "Generate a docstring template from a function's AST signature. "
        "Reads the function definition, extracts parameters and return type, "
        "and produces a structured docstring in Google, NumPy, or Sphinx style. "
        "Use this to quickly scaffold documentation for a function."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path."},
            "line": {"type": "integer", "description": "1-based line number inside the function."},
            "style": {
                "type": "string",
                "enum": ["google", "numpy", "sphinx"],
                "description": "Docstring style (default: google).",
                "default": "google",
            },
        },
        "required": ["path", "line"],
    },
}


def _handle_code_docstring_generate(args, **kw):
    return code_docstring_generate_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        style=args.get("style", "google"),
    )


# ---------------------------------------------------------------------------
# F3: code_dependency_risk_tool — Dependency health analysis
# ---------------------------------------------------------------------------


def code_dependency_risk_tool(path: str) -> str:
    """Analyze code dependency health and produce a risk score (0-10).

    Factors: cyclic dependencies, import depth, hot paths, unused imports.
    Uses ImportGraph for project-level analysis.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    if target.is_file():
        project_root = target.parent
    else:
        project_root = target

    # Scan the project
    graph = ImportGraph(str(project_root))
    try:
        graph.scan(depth=3)
        graph.parse_all()
    except Exception as e:
        return fmt_err(f"Import scan failed: {e}")

    risk_factors = []
    risk_score = 0

    # 1. Cyclic dependencies
    cycles = graph.find_cycles()
    if cycles:
        n_cycles = len(cycles)
        risk_factors.append(
            {
                "factor": "cyclic_dependencies",
                "count": n_cycles,
                "severity": "high" if n_cycles > 5 else "medium" if n_cycles > 2 else "low",
                "details": [list(c) for c in cycles[:5]],
            }
        )
        risk_score += min(3, n_cycles * 0.5)

    # 2. Hot paths (most-imported files)
    hot_paths = graph.find_hot_paths(top_n=5)
    max_hot_path = hot_paths[0]["caller_count"] if hot_paths else 0
    if max_hot_path > 20:
        risk_factors.append(
            {
                "factor": "hot_paths",
                "count": max_hot_path,
                "severity": "medium",
                "details": [h["file"] for h in hot_paths[:3]],
            }
        )
        risk_score += 1.5

    # 3. Total import edges (complexity indicator)
    g = graph.graph
    edge_count = len(g)
    if edge_count > 200:
        risk_factors.append(
            {
                "factor": "import_complexity",
                "count": edge_count,
                "severity": "medium",
                "details": [f"{edge_count} import relationships"],
            }
        )
        risk_score += min(2, edge_count / 200)

    # 4. File count vs import density
    file_count = len(list(graph.files))
    if file_count > 0:
        density = edge_count / file_count
        if density > 3:
            risk_factors.append(
                {
                    "factor": "import_density",
                    "count": round(density, 2),
                    "severity": "low",
                    "details": [f"{density:.1f} imports per file"],
                }
            )
            risk_score += min(1, density * 0.2)

    # Cap at 10
    risk_score = min(10, round(risk_score, 1))

    return fmt_ok(
        {
            "path": str(project_root),
            "files_scanned": file_count,
            "import_edges": edge_count,
            "risk_score": risk_score,
            "risk_level": "low" if risk_score < 3 else "medium" if risk_score < 6 else "high",
            "factors": risk_factors,
        }
    )


CODE_DEPENDENCY_RISK_SCHEMA = {
    "name": "code_dependency_risk",
    "description": (
        "Analyze code dependency health and produce a risk score (0-10). "
        "Factors considered: cyclic dependencies, hot import paths, import complexity/density. "
        "Returns structured breakdown with risk level (low/medium/high). "
        "Useful for technical debt assessment before major refactors."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory path to analyze.",
            },
        },
        "required": ["path"],
    },
}


def _handle_code_dependency_risk(args, **kw):
    return code_dependency_risk_tool(
        path=args.get("path", "."),
    )
