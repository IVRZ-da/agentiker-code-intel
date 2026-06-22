"""Remove extracted functions from code_tools.py and add re-imports at the end."""
import ast
import pathlib

CODETOOLS = pathlib.Path("/home/jo/.hermes/plugins/code_intel/code_tools.py")
source = CODETOOLS.read_text()
lines = source.split("\n")

tree = ast.parse(source)

# All functions/assignments now living in tools/* submodules
EXTRACTED = {
    "_set_dir_cache", "_find_project_root", "_cache_key_for_path", "_project_cache_path",
    "persist_symbol_cache", "load_symbol_cache", "_set_cache", "get_symbol_cache_stats",
    "clear_symbol_cache", "_invalidate_cache",
    "_init_languages", "_get_language", "_get_parser", "detect_language", "_classify_node",
    "_detect_lang_for_summary", "_find_lang_folders", "_count_extensions", "_scan_workspace",
    "_detect_monorepo_markers", "code_workspace_summary_tool", "_handle_code_workspace_summary",
    "_ast_type_hierarchy_supertypes", "_ast_type_hierarchy_subtypes",
    "code_metrics_tool", "_handle_code_metrics",
    "code_search_by_error_tool", "_handle_code_search_by_error",
    "code_hot_paths_tool", "_handle_code_hot_paths", "code_cycle_detector_tool",
    "_handle_code_cycle_detector", "code_dependency_graph_tool", "_handle_code_dependency_graph",
    "_tests_find_references", "_tests_find_symbol_name", "_calc_test_score",
    "_tests_filter_and_score", "_tests_calc_coverage", "code_tests_for_symbol_tool",
    "_handle_code_tests_for_symbol",
    "_find_symbol_in_ast", "_ast_search_references", "code_replace_body_tool",
    "_handle_code_replace_body", "code_safe_delete_tool", "_handle_code_safe_delete",
    "code_insert_before_tool", "_handle_code_insert_before", "code_insert_after_tool",
    "_handle_code_insert_after", "code_move_tool", "_handle_code_move",
    "code_export_tool", "_handle_code_export", "code_docstring_generate_tool",
    "_handle_code_docstring_generate", "code_dependency_risk_tool", "_handle_code_dependency_risk",
    # Schemas
    "CODE_WORKSPACE_SUMMARY_SCHEMA", "CODE_METRICS_SCHEMA", "CODE_SEARCH_BY_ERROR_SCHEMA",
    "CODE_HOT_PATHS_SCHEMA", "CODE_CYCLE_DETECTOR_SCHEMA", "CODE_DEPENDENCY_GRAPH_SCHEMA",
    "CODE_TESTS_FOR_SYMBOL_SCHEMA", "CODE_REPLACE_BODY_SCHEMA", "CODE_SAFE_DELETE_SCHEMA",
    "CODE_INSERT_BEFORE_SCHEMA", "CODE_INSERT_AFTER_SCHEMA", "CODE_MOVE_SCHEMA",
    "CODE_EXPORT_SCHEMA", "CODE_DOCSTRING_GENERATE_SCHEMA", "CODE_DEPENDENCY_RISK_SCHEMA",
}

# Find AST nodes to remove
# Collect FunctionDef / Assign nodes at module level
# We need to use ast.iter_child_nodes since these are direct children of the module
to_remove_line_ranges = []
for node in ast.iter_child_nodes(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name in EXTRACTED:
            to_remove_line_ranges.append((node.lineno, node.end_lineno))
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in EXTRACTED:
                to_remove_line_ranges.append((node.lineno, node.end_lineno))
                break
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id in EXTRACTED:
            to_remove_line_ranges.append((node.lineno, node.end_lineno))

# Remove overlapping ranges, sort by start, deduplicate
to_remove_line_ranges.sort()
merged = []
for start, end in to_remove_line_ranges:
    if merged and start <= merged[-1][1] + 1:
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    else:
        merged.append((start, end))

for start, end in merged:
    pass

# Remove lines from bottom up (to keep line numbers valid)
for start, end in reversed(merged):
    # Remove the range plus any blank lines immediately before
    strip_start = start - 1
    while strip_start > 1 and lines[strip_start - 1].strip() == "":
        strip_start -= 1
    del lines[strip_start - 1:end]

# Now add the re-imports at the end
REIMPORTS = """\
# ---------------------------------------------------------------------------
# Re-exports from tools/ submodules — these functions were extracted
# from this monolith into dedicated modules for maintainability.
# The original definitions remain here as local names so that existing
# imports (from within this package and from tests) continue to work.
# ---------------------------------------------------------------------------
from .tools.cache import (  # noqa: F401, I001
    _set_dir_cache, _find_project_root, _cache_key_for_path, _project_cache_path,
    persist_symbol_cache, load_symbol_cache, _set_cache, get_symbol_cache_stats,
    clear_symbol_cache, _invalidate_cache,
    _LANG_LOCK, _LANG_CACHE, _PARSER_CACHE, _LANG_READY,
    _SYMBOL_CACHE, _DIR_SYMBOL_CACHE, _MAX_DIR_CACHE, _PERSIST_DIR, _PERSIST_VERSION,
)
from .tools.language import (  # noqa: F401
    _EXT_TO_LANG, _NODE_KIND_MAP, _init_languages, _get_language, _get_parser,
    detect_language, _classify_node,
)
from .tools.workspace import (  # noqa: F401
    _detect_lang_for_summary, _find_lang_folders, _count_extensions, _scan_workspace,
    _detect_monorepo_markers, code_workspace_summary_tool, _handle_code_workspace_summary,
    CODE_WORKSPACE_SUMMARY_SCHEMA,
)
from .tools.type_hierarchy import (  # noqa: F401
    _ast_type_hierarchy_supertypes, _ast_type_hierarchy_subtypes,
)
from .tools.metrics import (  # noqa: F401
    code_metrics_tool, _handle_code_metrics, CODE_METRICS_SCHEMA,
)
from .tools.search_by_error import (  # noqa: F401
    code_search_by_error_tool, _handle_code_search_by_error, CODE_SEARCH_BY_ERROR_SCHEMA,
)
from .tools.graph_analysis import (  # noqa: F401
    code_hot_paths_tool, _handle_code_hot_paths, CODE_HOT_PATHS_SCHEMA,
    code_cycle_detector_tool, _handle_code_cycle_detector, CODE_CYCLE_DETECTOR_SCHEMA,
    code_dependency_graph_tool, _handle_code_dependency_graph, CODE_DEPENDENCY_GRAPH_SCHEMA,
)
from .tools.test_coverage import (  # noqa: F401
    _tests_find_references, _tests_find_symbol_name, _calc_test_score,
    _tests_filter_and_score, _tests_calc_coverage, code_tests_for_symbol_tool,
    _handle_code_tests_for_symbol, CODE_TESTS_FOR_SYMBOL_SCHEMA,
)
from .tools.ast_edit import (  # noqa: F401
    _find_symbol_in_ast, _ast_search_references,
    code_replace_body_tool, _handle_code_replace_body, CODE_REPLACE_BODY_SCHEMA,
    code_safe_delete_tool, _handle_code_safe_delete, CODE_SAFE_DELETE_SCHEMA,
    code_insert_before_tool, _handle_code_insert_before, CODE_INSERT_BEFORE_SCHEMA,
    code_insert_after_tool, _handle_code_insert_after, CODE_INSERT_AFTER_SCHEMA,
    code_move_tool, _handle_code_move, CODE_MOVE_SCHEMA,
)
from .tools.export import (  # noqa: F401
    code_export_tool, _handle_code_export, CODE_EXPORT_SCHEMA,
    code_docstring_generate_tool, _handle_code_docstring_generate, CODE_DOCSTRING_GENERATE_SCHEMA,
    code_dependency_risk_tool, _handle_code_dependency_risk, CODE_DEPENDENCY_RISK_SCHEMA,
)
"""

# Add re-exports at the end (before any trailing newlines)
while lines and lines[-1] == "":
    lines.pop()
lines.append("")
lines.append(REIMPORTS.strip())

# Write back
result = "\n".join(lines)
CODETOOLS.write_text(result)

new_lines = len(result.split("\n"))
