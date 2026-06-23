"""tools/impact.py — Blast radius, PR impact, and impact analysis tools.

Extracted from code_tools.py to reduce module size.
Follows the same pattern as tools/unused.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .._fmt import fmt_err, fmt_json, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger
from .language import _get_language, _get_parser, detect_language  # noqa: E402
from .test_coverage import code_tests_for_symbol_tool  # noqa: E402

logger = _setup_code_intel_logger(__name__)


# ---------------------------------------------------------------------------
# B2: code_impact — Impact analysis for symbol or file changes
# ---------------------------------------------------------------------------

CODE_IMPACT_SCHEMA = {
    "name": "code_impact",
    "description": (
        "Impact analysis before refactors or API changes. For a symbol or file, shows "
        "affected files, reference counts, test coverage, and confidence level. "
        "Use BEFORE making changes to understand blast radius."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "line": {"type": "integer", "description": "1-based line number of the symbol to analyze"},
            "language": {"type": "string", "description": "Language override"},
        },
        "required": ["path"],
    },
}


def _impact_file_level(target, language, base_r, _json):
    """Analyze imports for file-level impact analysis."""
    try:
        lang = language or detect_language(str(target))
        from ..code_tools import code_search_tool  # lazy: avoid circular import
        search_json = code_search_tool(str(target), preset="imports", language=lang)
        search_data = _json.loads(search_json) if isinstance(search_json, str) else search_json
        if isinstance(search_data, dict):
            import_count = len(search_data.get("results", search_data.get("matches", [])))
        elif isinstance(search_data, list):
            import_count = len(search_data)
        else:
            import_count = 0
        base_r["reference_count"] = import_count
        base_r["reference_type"] = "file-level"
        return fmt_json(base_r)
    except Exception as exc:
        return fmt_err(f"Unable to analyze imports: {exc}")


def code_impact_tool(path: str, line: int = 0, language: Optional[str] = None) -> str:
    """Impact analysis for a symbol or file. Returns affected files, reference counts, test coverage."""
    import json as _json
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    base_r = {
        "path": str(target),
        "files_affected": [],
        "test_files": [],
        "reference_count": 0,
        "direct_refs": 0,
        "indirect_refs": 0,
        "risk_level": "low",
        "confidence": "low",
    }

    # File-level: count imports via tree-sitter
    if line == 0:
        return _impact_file_level(target, language, base_r, _json)

    # Symbol-level: use lsp_bridge for cross-file resolution
    try:
        from ..lsp_bridge import code_references_tool
    except ImportError:
        return fmt_err("lsp_bridge not available")

    lang = language or detect_language(str(target))
    try:
        refs_json = code_references_tool(
            str(target), line,
            language=lang,
            include_declaration=False,
            group_by_file=True,
        )
        refs_data = _json.loads(refs_json)
        by_file = refs_data.get("by_file", {}) if isinstance(refs_data, dict) else {}
    except Exception:
        return fmt_err("Failed to resolve references")

    direct_refs = 0
    test_files = []
    files_affected = []
    for fpath, locations in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
        cnt = len(locations)
        direct_refs += cnt
        is_test = "test" in fpath.lower() or "spec" in fpath.lower()
        files_affected.append({"path": fpath, "reference_count": cnt, "test": is_test})
        if is_test:
            test_files.append(fpath)

    b = {**base_r, "direct_refs": direct_refs, "reference_count": direct_refs,
         "files_affected": files_affected[:20], "test_files": test_files[:10]}
    b["confidence"] = "high" if direct_refs > 10 else ("medium" if direct_refs > 3 else "low")
    b["risk_level"] = "high" if direct_refs > 30 else ("medium" if direct_refs > 10 else "low")
    return fmt_json(b)


def _handle_code_impact(args, **kw):
    return code_impact_tool(
        path=args.get("path", ""),
        line=args.get("line", 0),
        language=args.get("language"),
    )


# ---------------------------------------------------------------------------
# B4: code_blast_radius — Blast Radius Analysis
# ---------------------------------------------------------------------------

def code_blast_radius_tool(
    path: str,
    line: int,
    character: int = 0,
    depth: int = 3,
    language: str = "",
    test_coverage: bool = True,
) -> str:
    """Analyze blast radius of a symbol — what breaks if you change it.

    Combines LSP callHierarchy (direct callers) + ImportGraph (transitive)
    + test coverage analysis to provide a complete impact report.

    Args:
        path: Absolute file path.
        line: 1-based line number.
        character: 1-based column (auto-detected if omitted).
        depth: Maximum transitive depth (default: 3, max: 5).
        language: Language override.

    Returns:
        Formatted impact report.

    """
    import json as _json
    from pathlib import Path as _Path

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language
    if not lang:
        lang = detect_language(str(target))
    if not lang:
        return fmt_err("Could not detect language")

    depth = min(depth, 5)

    # Step 1: Direct callers via LSP callHierarchy
    from ..lsp_bridge import (
        LSPBridge,
        _auto_detect_identifier_column,
        _detect_language_for_lsp,
        get_lsp_manager,
    )

    col = character
    if not col:
        col = _auto_detect_identifier_column(str(target), line - 1) or 1

    lsp_lang = language or _detect_language_for_lsp(str(target))
    direct_callers = []
    manager = get_lsp_manager()
    bridge = manager.get_bridge(lsp_lang, str(target)) if lsp_lang else None
    if bridge and bridge.ensure_initialized():
        try:
            items = bridge.incoming_calls(str(target), line - 1, col - 1)
            if items:
                for item in items:
                    file_path = LSPBridge._uri_to_path(item.get("uri", ""))
                    direct_callers.append({
                        "file": file_path,
                        "line": (item.get("range", {}) or {}).get("start", {}).get("line", 0) + 1,
                        "name": item.get("name", "?"),
                    })
        except Exception:
            logger.debug("code_blast_radius: LSP callHierarchy direct callers failed")

    # Step 2: Transitive callers via ImportGraph
    transitive = {}
    try:
        from .._import_graph import ImportGraph
        g = ImportGraph(str(target.parent))
        g.scan(depth=5)
        tr = g.analyze_blast_radius(str(target), depth=depth)
        if tr["total"] > 0:
            transitive = tr
    except Exception:
        logger.debug("code_blast_radius: ImportGraph transitive analysis failed")

    # Step 3: Tests via code_tests_for_symbol_tool
    tests_found = []
    if test_coverage:
        try:
            tests_raw = code_tests_for_symbol_tool(
                path=str(target), line=line, language=lang
            )
            if tests_raw:
                try:
                    tests_data = _json.loads(tests_raw)
                    if "tests" in tests_data:
                        tests_found = tests_data["tests"]
                except Exception:
                    logger.debug("code_blast_radius: tests_data parse failed")
        except Exception:
            logger.debug("code_blast_radius: code_tests_for_symbol_tool failed")

    # Step 4: Impact classification
    nc = len(direct_callers)
    tc = transitive.get("total", 0)
    if nc > 10 or tc > 20:
        impact = "HIGH"
    elif nc > 0 or tc > 0:
        impact = "MEDIUM"
    else:
        impact = "LOW"

    result = {
        "symbol": target.name,
        "path": str(target),
        "line": line,
        "impact": impact,
        "depth": depth,
        "direct_callers": {
            "count": len(direct_callers),
            "items": direct_callers[:50],
        },
        "transitive_callers": {
            "count": tc,
            "levels": {str(k): v for k, v in transitive.get("levels", {}).items()},
        },
        "test_coverage": {
            "count": len(tests_found),
            "items": tests_found[:20],
        },
        "recommendation": "",
    }

    if impact == "HIGH":
        result["recommendation"] = "High impact — review all callers before making changes."
    elif tc > 0 and not tests_found:
        result["recommendation"] = "Untested transitive callers — add tests first."
    elif not direct_callers:
        result["recommendation"] = "Low impact — appears unused or private."

    return fmt_json(result)


CODE_BLAST_RADIUS_SCHEMA = {
    "name": "code_blast_radius",
    "description": "Analyze blast radius of a symbol — what breaks if you change it. "
                   "Combines LSP callHierarchy (direct callers), ImportGraph (transitive), "
                   "and test coverage analysis.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "line": {"type": "integer", "description": "1-based line number"},
            "character": {"type": "integer", "description": "1-based column (auto-detected if omitted)"},
            "depth": {"type": "integer", "description": "Max transitive depth (default: 3, max: 5)"},
            "language": {"type": "string", "description": "Language override"},
            "test_coverage": {"type": "boolean", "description": "Include test coverage analysis (default: true)"},
        },
        "required": ["path", "line"],
    },
}


def _handle_code_blast_radius(args, **kw):
    return code_blast_radius_tool(
        path=args.get("path", ""),
        line=args.get("line", 1),
        character=args.get("character", 0),
        depth=args.get("depth", 3),
        language=args.get("language", ""),
        test_coverage=args.get("test_coverage", True),
    )


# ---------------------------------------------------------------------------
# C1: code_pr_impact — PR Impact Analysis (git diff + ImportGraph)
# ---------------------------------------------------------------------------


def code_pr_impact_tool(
    base_branch: str = "main",
    auto_detect: bool = True,
    path: str = ".",
    max_files: int = 10,
) -> str:
    """Analyze the impact of a PR by combining git diff with ImportGraph.

    Shows changed functions, blast radius, test coverage, reviewers,
    and a suggested commit message.

    Args:
        base_branch: Git base branch to diff against (default: "main").
        auto_detect: Auto-detect base branch via git (main/develop/release) before falling back to base_branch. (default: True).
        path: Project root path (default: current dir).
        max_files: Max files to analyze in large diffs (default: 10).

    Returns:
        Formatted impact report.

    """
    import subprocess as _sp
    from pathlib import Path as _Path

    # --- auto-detect base branch ---
    if auto_detect:
        try:
            result = _sp.run(
                ['git', 'branch', '-r'], capture_output=True, text=True,
                cwd=str(_Path(path).expanduser().resolve()), timeout=5,
            )
            if result.returncode == 0:
                for candidate in ['origin/main', 'origin/develop', 'origin/release', 'origin/master']:
                    if candidate in result.stdout:
                        base_branch = candidate.replace('origin/', '')
                        break
        except Exception:
            logger.debug("code_pr_impact: auto-detect base branch failed, using default")
    # --- end auto-detect ---

    root = _Path(path).expanduser().resolve()
    if not root.exists():
        return fmt_err(f"Path not found: {path}")

    # Step 1: git diff
    try:
        diff_result = _sp.run(
            ["git", "diff", base_branch, "--diff-filter=AM", "--", "*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.go", "*.rs"],
            capture_output=True, text=True, cwd=str(root), timeout=30,
        )
        if diff_result.returncode != 0:
            return fmt_err(f"git diff failed: {diff_result.stderr.strip() or 'unknown error'}")
        diff_output = diff_result.stdout
    except FileNotFoundError:
        return fmt_err("Not a git repository or git not installed")
    except _sp.TimeoutExpired:
        return fmt_err("git diff timed out")

    if not diff_output.strip():
        return fmt_ok({"message": f"No changes detected against {base_branch}", "changes": []})

    # Step 2: Parse changed files
    changed_files: set = set()
    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            file_path = line[6:].strip()
            if file_path and not file_path.startswith("/dev"):
                changed_files.add(file_path)

    if not changed_files:
        return fmt_ok({"message": "No source files changed", "changes": []})

    changed_list = sorted(changed_files)[:max_files]
    total_changed = len(changed_files)

    # Step 3: Analyze each changed file
    from .._import_graph import ImportGraph

    g = ImportGraph(str(root))
    g.scan(depth=5)

    changed_functions = []
    total_blast = {"direct": 0, "transitive": 0}

    for cf in changed_list:
        abs_path = str((root / cf).resolve())
        if not _Path(abs_path).exists():
            continue

        functions_in_file = _find_functions_in_file(abs_path)
        for func in functions_in_file:
            func["file"] = cf
            try:
                tr = g.analyze_blast_radius(abs_path, depth=2)
                func["transitive_callers"] = tr.get("total", 0)
                total_blast["transitive"] += tr.get("total", 0)
            except Exception:
                logger.debug("code_pr_impact: blast radius analysis failed for %s", cf)
                func["transitive_callers"] = 0
            changed_functions.append(func)

        try:
            g.parse_all()
        except Exception:
            logger.debug("code_pr_impact: g.parse_all() failed")

    total_blast["direct"] = len(changed_functions)

    # Step 4: Test coverage gaps
    test_gaps = []
    for func in changed_functions:
        has_test = False
        for tf in root.rglob("*test*"):
            if tf.suffix in (".py", ".ts", ".tsx"):
                try:
                    content = tf.read_text()
                    if func.get("name") and func["name"] in content:
                        has_test = True
                        break
                except Exception:
                    logger.debug("code_pr_impact: test file read failed for %s", tf)
                    continue
        if not has_test:
            test_gaps.append(func)

    # Step 5: Suggested reviewers via git blame
    reviewers: dict = {}
    for cf in changed_list[:5]:
        try:
            blame = _sp.run(
                ["git", "blame", "--line-porcelain", cf],
                capture_output=True, text=True, cwd=str(root), timeout=10,
            )
            if blame.returncode == 0:
                for line in blame.stdout.splitlines():
                    if line.startswith("author "):
                        author = line[7:].strip()
                        reviewers[author] = reviewers.get(author, 0) + 1
        except Exception as e:
            logger.debug("code_impact: git blame failed: %s", e)
            continue

    suggested_reviewers = sorted(reviewers.items(), key=lambda x: x[1], reverse=True)[:5]

    # Step 6: Build report
    total_added = sum(1 for line in diff_output.splitlines() if line.startswith("+") and not line.startswith("+++"))
    total_removed = sum(1 for line in diff_output.splitlines() if line.startswith("-") and not line.startswith("---"))

    result = {
        "base_branch": base_branch,
        "files_changed": total_changed,
        "files_analyzed": len(changed_list),
        "lines_added": total_added,
        "lines_removed": total_removed,
        "changed_functions": changed_functions[:50],
        "blast_radius": {
            "direct_callers": total_blast["direct"],
            "transitive_callers": total_blast["transitive"],
        },
        "test_gaps": len(test_gaps),
        "untested_functions": [{"name": f.get("name"), "file": f.get("file"), "line": f.get("line")} for f in test_gaps[:10]],
        "suggested_reviewers": [{"name": name, "lines": count} for name, count in suggested_reviewers],
    }

    if total_changed > max_files:
        result["warning"] = f"Large diff ({total_changed} files) — showing top {max_files}"

    return fmt_json(result)


def _find_functions_in_file(file_path: str) -> list:
    """Find all function names in a source file via tree-sitter."""
    from tree_sitter import Query, QueryCursor

    lang_key = detect_language(file_path)
    if not lang_key:
        return []

    fn_queries = {
        "python": "(function_definition name: (identifier) @name) @def",
        "typescript": "(function_declaration name: (identifier) @name) @def\n(method_definition name: (property_identifier) @name) @def",
        "tsx": "(function_declaration name: (identifier) @name) @def\n(method_definition name: (property_identifier) @name) @def",
        "go": "(function_declaration name: (identifier) @name) @def\n(method_declaration name: (field_identifier) @name) @def",
        "rust": "(function_item name: (identifier) @name) @def",
    }

    qs = fn_queries.get(lang_key)
    if not qs:
        return []

    lang_obj = _get_language(lang_key)
    parser = _get_parser(lang_key)
    if not parser or not lang_obj:
        return []

    try:
        q = Query(lang_obj, qs)
    except Exception:
        return []

    try:
        with open(file_path, "rb") as f:
            src = f.read()
    except OSError:
        return []

    tree = parser.parse(src)
    if not tree:
        return []

    functions = []
    qc = QueryCursor(q)
    for _pi, cd in qc.matches(tree.root_node):
        name = ""
        for nn in cd.get("name", []):
            try:
                name = src[nn.start_byte:nn.end_byte].decode("utf-8", errors="replace")
            except Exception:
                name = "?"
            break
        for dn in cd.get("def", []):
            functions.append({
                "name": name,
                "line": dn.start_point[0] + 1,
            })
    return functions


CODE_PR_IMPACT_SCHEMA = {
    "name": "code_pr_impact",
    "description": "Analyze the impact of a PR by combining git diff with ImportGraph. "
                   "Shows changed functions, blast radius, test coverage gaps, "
                   "suggested reviewers, and a commit hint.",
    "parameters": {
        "type": "object",
        "properties": {
            "base_branch": {"type": "string", "description": "Git base branch (default: main)"},
            "auto_detect": {"type": "boolean", "description": "Auto-detect base branch via git (main/develop/release). Falls True, wird base_branch ignoriert. (default: True)"},
            "path": {"type": "string", "description": "Project root path (default: current dir)"},
            "max_files": {"type": "integer", "description": "Max files in large diffs (default: 10)"},
        },
        "required": [],
    },
}


def _handle_code_pr_impact(args, **kw):
    return code_pr_impact_tool(
        base_branch=args.get("base_branch", "main"),
        auto_detect=args.get("auto_detect", True),
        path=args.get("path", "."),
        max_files=args.get("max_files", 10),
    )
