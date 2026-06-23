#!/usr/bin/env python3
"""Code Intelligence Tools Module.

AST-aware code analysis tools using tree-sitter and ast-grep.
Provides structural symbol extraction, pattern search, and safe refactoring.

Token-efficient alternative to reading entire files for code navigation.
"""

import os
import threading
from collections import OrderedDict
from pathlib import Path  # noqa: F401 — kept for test patches targeting code_intel.code_tools.Path
from typing import Dict

from ._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# Language registry — maps file extensions → tree-sitter Language objects
# Lazy-loaded on first use to avoid slow imports at module level.
# ---------------------------------------------------------------------------

_LANG_LOCK = threading.Lock()
_LANG_CACHE: Dict[str, object] = {}  # ext → Language
_PARSER_CACHE: Dict[str, object] = {}  # lang_key → Parser
_LANG_READY = False
_SYMBOL_CACHE = OrderedDict()

# Directory-level cache for _symbols_scan_directory.
# Key:   resolved_path|lang|pattern|kind|max_results
# Value: {"mtime": float, "files": {path: mtime}, "result": str}
_DIR_SYMBOL_CACHE = OrderedDict()

# ---------------------------------------------------------------------------
# Persistent symbol index (B5) — saves/loads AST cache to disk
# ---------------------------------------------------------------------------
_PERSIST_DIR = os.path.expanduser("~/.hermes/plugins/code_intel/.cache")
_PERSIST_VERSION = 2  # bump to invalidate stale caches

# ---------------------------------------------------------------------------
# Extension → language key mapping
# ---------------------------------------------------------------------------

_EXT_TO_LANG = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}

# Supported languages for ast-grep (subset — only those with grammars)

# ---------------------------------------------------------------------------
# tree-sitter symbol queries per language
# ---------------------------------------------------------------------------

_SYMBOL_QUERIES = {
    "python": """
        ; Functions (sync and async) — catches top-level AND bare methods
        ; (method detection happens in extract_symbols via parent chain)
        (function_definition
            name: (identifier) @name
        ) @def

        ; Classes
        (class_definition
            name: (identifier) @name
        ) @def

        ; Module-level assignments that look like constants (UPPER_CASE)
        (assignment
            left: (identifier) @name
        ) @constant

        ; Decorated functions/classes (including decorated methods inside classes)
        (decorated_definition
            definition: (function_definition
                "async"? @keyword
                name: (identifier) @name
            ) @def
        )

        (decorated_definition
            definition: (class_definition
                name: (identifier) @name
            ) @def
        )
    """,
    "typescript": """
        ; Functions (sync and async)
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Arrow functions assigned to variables (const/let)
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Arrow functions assigned to variables (var)
        (variable_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Classes
        (class_declaration
            name: (type_identifier) @name
        ) @def

        ; Interfaces
        (interface_declaration
            name: (type_identifier) @name
        ) @def

        ; Type aliases
        (type_alias_declaration
            name: (type_identifier) @name
        ) @def

        ; Enums
        (enum_declaration
            name: (identifier) @name
        ) @def

        ; Export statements wrapping the above
        (export_statement
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            (class_declaration
                name: (type_identifier) @name
            ) @def
        )

        (export_statement
            (interface_declaration
                name: (type_identifier) @name
            ) @def
        )

        ; Class methods (including decorated — decorator is a sibling, not parent)
        (method_definition
            name: (property_identifier) @name
        ) @def
    """,
    "tsx": """
        ; Same as typescript plus component detection
        ; Functions (sync and async)
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Arrow functions (const/let)
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Arrow functions (var)
        (variable_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        (class_declaration
            name: (type_identifier) @name
        ) @def

        (interface_declaration
            name: (type_identifier) @name
        ) @def

        (type_alias_declaration
            name: (type_identifier) @name
        ) @def

        (enum_declaration
            name: (identifier) @name
        ) @def

        ; "use client" / "use server" directives
        (expression_statement
            (string
                (string_fragment) @name
            )
        ) @directive

        ; Export default function/class (has "default" keyword child)
        (export_statement
            "default"
            .
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            "default"
            .
            (class_declaration
                name: (type_identifier) @name
            ) @def
        )

        ; Named exports
        (export_statement
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            (class_declaration
                name: (type_identifier) @name
            ) @def
        )

        (export_statement
            (interface_declaration
                name: (type_identifier) @name
            ) @def
        )

        ; Class methods (including decorated)
        (method_definition
            name: (property_identifier) @name
        ) @def
    """,
    "javascript": """
        ; Functions (sync and async — async is a keyword child, handled automatically)
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Arrow functions (const/let)
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Arrow functions (var)
        (variable_declaration
            (variable_declarator
                name: (identifier) @name
                value: (arrow_function) @arrow
            )
        )

        ; Classes
        (class_declaration
            name: (identifier) @name
        ) @def

        ; Class methods (including decorated)
        (method_definition
            name: (property_identifier) @name
        ) @def

        ; Export statements
        (export_statement
            (function_declaration
                name: (identifier) @name
            ) @def
        )

        (export_statement
            (class_declaration
                name: (identifier) @name
            ) @def
        )
    """,
    "rust": """
        ; Functions (matches both sync and async — async is a function_modifiers child)
        (function_item
            name: (identifier) @name
        ) @def

        ; Structs
        (struct_item
            name: (type_identifier) @name
        ) @def

        ; Enums
        (enum_item
            name: (type_identifier) @name
        ) @def

        ; Traits
        (trait_item
            name: (type_identifier) @name
        ) @def

        ; impl blocks — methods
        (impl_item
            body: (declaration_list
                (function_item
                    name: (identifier) @name
                ) @def
            )
        )

        ; impl blocks for traits
        (impl_item
            trait: (type_identifier) @trait_name
            type: (type_identifier) @impl_for
            body: (declaration_list
                (function_item
                    name: (identifier) @name
                ) @def
            )
        )

        ; Constants
        (const_item
            name: (identifier) @name
        ) @constant

        ; Type aliases
        (type_item
            name: (type_identifier) @name
        ) @def

        ; Mods
        (mod_item
            name: (identifier) @name
        ) @def
    """,
    "go": """
        ; Functions
        (function_declaration
            name: (identifier) @name
        ) @def

        ; Methods (receiver functions)
        (method_declaration
            name: (field_identifier) @name
        ) @def

        ; Structs
        (type_declaration
            (type_spec
                name: (type_identifier) @name
                type: (struct_type)
            )
        ) @def

        ; Interfaces
        (type_declaration
            (type_spec
                name: (type_identifier) @name
                type: (interface_type)
            )
        ) @def

        ; Type aliases
        (type_declaration
            (type_spec
                name: (type_identifier) @name
            )
        ) @def

        ; Variables
        (var_declaration
            (var_spec
                name: (identifier) @name
            )
        ) @constant
    """,
    "java": """
        ; Classes
        (class_declaration
            name: (identifier) @name
        ) @def

        ; Interfaces
        (interface_declaration
            name: (identifier) @name
        ) @def

        ; Enums
        (enum_declaration
            name: (identifier) @name
        ) @def

        ; Methods
        (class_declaration
            body: (class_body
                (method_declaration
                    name: (identifier) @name
                ) @def
            )
        )

        ; Fields
        (class_declaration
            body: (class_body
                (field_declaration
                    (variable_declarator
                        name: (identifier) @name
                    )
                ) @field
            )
        )
    """,
}

# Node types that indicate specific symbol kinds
_NODE_KIND_MAP = {
    "function_definition": "function",
    "function_declaration": "function",
    "function_item": "function",
    "arrow_function": "function",
    "class_definition": "class",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "enum_item": "enum",
    "struct_item": "struct",
    "struct_type": "struct",
    "interface_type": "interface",
    "trait_item": "trait",
    "type_item": "type",
    "type_alias": "type",
    "type_spec": "type",
    "method_definition": "method",
    "method_declaration": "method",
    "impl_item": "impl",
    "mod_item": "module",
    "assignment": "variable",
    "variable_declaration": "variable",
    "const_item": "constant",
    "constant_item": "constant",
    "var_declaration": "variable",
    "var_spec": "variable",
    "field_declaration": "field",
}
# ---------------------------------------------------------------------------

# Language → AST query for class extends/implements detection
_TYPE_HIERARCHY_FALLBACK_LANGS = {"python", "typescript", "tsx", "javascript"}

_PYTHON_CLASS_EXTENDS = """
(class_definition
    name: (identifier) @class_name
    (argument_list
        (identifier) @extends_name
    )
) @class_def
"""

_TS_CLASS_EXTENDS = """
; class Foo extends Bar { }
(class_declaration
    name: (type_identifier) @class_name
    (class_heritage
        (identifier) @extends_name
    )
) @class_def

; interface Foo extends Bar { }
(interface_declaration
    name: (type_identifier) @class_name
    (class_heritage
        (identifier) @extends_name
    )
) @class_def
"""

# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Schemas — kept here for __init__.py compatibility
# (Functions extracted to tools/*.py)
# ---------------------------------------------------------------------------
CODE_SYMBOLS_SCHEMA = {
    "name": "code_symbols",
    "description": (
        "AST-powered symbol extraction — get a structured index of functions, classes, "
        "methods, interfaces, types, enums, structs, traits from any source file. "
        "Use this INSTEAD of read_file when you need to understand what a file contains "
        "(what functions exist, what classes define which methods, where things are). "
        "Returns line numbers, signatures, and symbol kinds. Pass a directory to index "
        "all files at once. Supports Python, TypeScript, TSX, JavaScript, Rust, Go, Java."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory path to extract symbols from"},
            "pattern": {"type": "string", "description": "Fuzzy symbol name filter (optional, substring match)"},
            "kind": {
                "type": "string",
                "enum": ["all", "function", "class", "method", "interface", "type", "enum", "struct", "trait", "constant", "variable", "module"],
                "description": "Filter by symbol kind (default: all)",
            },
            "include_body": {"type": "boolean", "description": "Include function/method body text (default: false, only for single file)"},
            "language": {"type": "string", "description": "Override language auto-detection (e.g. 'python', 'typescript')"},
            "max_results": {"type": "integer", "description": "Maximum results to return (default: 200, use 0 for unlimited)"},
        },
        "required": ["path"],
    },
}

CODE_SEARCH_SCHEMA = {
    "name": "code_search",
    "description": (
        "AST-aware structural code search — find function calls, imports, decorators, "
        "try/catch blocks, return statements, assignments by their semantic structure, "
        "not just text. Use this INSTEAD of search_files (grep) when searching for code "
        "patterns inside source files — it understands syntax and won't match comments "
        "or strings by accident. Accepts files and directories (recursive scan). "
        "Use named presets: function_calls, imports, decorator_calls, try_catch, "
        "return_stmts, string_literals, assignments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "query": {"type": "string", "description": "Raw tree-sitter query string (e.g. '(call function: (identifier) @func) @call')"},
            "preset": {"type": "string", "description": "Named preset: function_calls, string_literals, imports, decorator_calls, try_catch, return_stmts, assignments"},
            "pattern": {"type": "string", "description": "Simple text pattern to filter captured nodes (substring match)"},
            "language": {"type": "string", "description": "Override language auto-detection"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default: 50)"},
        },
        "required": ["path"],
    },
}

CODE_REFACTOR_SCHEMA = {
    "name": "code_refactor",
    "description": (
        "AST-aware structural search and replace — matches code by syntax tree structure, "
        "not raw text. Use this INSTEAD of patch when doing bulk refactoring across files or directories "
        "(rename patterns, wrap functions, add parameters, change decorators, etc.). "
        "Supports meta variables: $NAME for single nodes, $$BODY for multi-node captures. "
        "DRY-RUN by default — set dry_run=false to apply. "
        "Supports both files and directories (recursive scan across all supported languages). "
        "Supports Python, TypeScript, TSX, JavaScript, Rust, Go, Java, C, C++."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file or directory path"},
            "pattern": {"type": "string", "description": "ast-grep pattern (e.g. 'console.log($ARG)', 'def $NAME($$$ARGS): $$$BODY')"},
            "rewrite": {"type": "string", "description": "Replacement template with meta variables (e.g. 'console.info($ARG)')"},
            "language": {"type": "string", "description": "Override language auto-detection (single file only)"},
            "dry_run": {"type": "boolean", "description": "Preview changes without writing (default: true)"},
            "context_lines": {"type": "integer", "description": "Lines of context around each match (default: 1)"},
            "file_glob": {"type": "string", "description": "Filter files by glob pattern in directory mode (e.g. '*.service.ts', '*_test.py')"},
        },
        "required": ["path", "pattern", "rewrite"],
    },
}

CODE_EXPLAIN_SCHEMA = {
    "name": "code_explain",
    "description": (
        "Get a structured explanation of a symbol at a given location. "
        "Combines signature, docstring, cyclomatic complexity, caller count, "
        "and key references into a single structured output."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path containing the symbol"},
            "line": {"type": "integer", "description": "1-based line number where the symbol appears"},
            "character": {
                "type": "integer",
                "description": "1-based column (optional, for disambiguation)",
            },
            "language": {
                "type": "string",
                "description": "Language override. Auto-detected from extension.",
            },
        },
        "required": ["path", "line"],
    },
}

CODE_DUPLICATES_SCHEMA = {
    "name": "code_duplicates",
    "description": "Find duplicate/similar code blocks via AST comparison.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path"},
            "min_lines": {"type": "integer", "description": "Minimum lines for a duplicate block (default: 5)"},
            "top_n": {"type": "integer", "description": "Number of top results (default: 20)"},
            "max_files": {"type": "integer", "description": "Maximum number of files to scan (default: 200)"},
            "similarity_threshold": {"type": "number", "description": "Similarity ratio threshold for near-duplicate detection (default: 0.8)"},
            "timeout": {"type": "integer", "description": "Maximum seconds for the search (default: 60)"},
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# Re-Exports for backward compatibility
# (Functions live in tools/*.py, re-exported via code_tools.py)
# ---------------------------------------------------------------------------
from .tools.ast_edit import (  # noqa: E402, F401
    CODE_INSERT_AFTER_SCHEMA,
    CODE_INSERT_BEFORE_SCHEMA,
    CODE_MOVE_SCHEMA,
    CODE_REPLACE_BODY_SCHEMA,
    CODE_SAFE_DELETE_SCHEMA,
    _ast_search_references,
    _find_symbol_in_ast,
    _handle_code_insert_after,
    _handle_code_insert_before,
    _handle_code_move,
    _handle_code_replace_body,
    _handle_code_safe_delete,
    code_insert_after_tool,
    code_insert_before_tool,
    code_move_tool,
    code_replace_body_tool,
    code_safe_delete_tool,
)
from .tools.batch import (  # noqa: E402, F401
    CODE_BATCH_REFACTOR_SCHEMA,
    _handle_code_batch_refactor,
    code_batch_refactor_tool,
)
from .tools.blame import (  # noqa: E402, F401
    CODE_GIT_BLAME_SCHEMA,
    _handle_code_git_blame,
    code_git_blame_tool,
)

# ---------------------------------------------------------------------------
# Re-exports from tools/ submodules — these functions were extracted
# from this monolith into dedicated modules for maintainability.
# The original definitions remain here as local names so that existing
# imports (from within this package and from tests) continue to work.
# ---------------------------------------------------------------------------
from .tools.cache import (  # noqa: E402, F401, I001
    _DIR_SYMBOL_CACHE,
    _LANG_CACHE,
    _LANG_LOCK,
    _LANG_READY,
    _MAX_DIR_CACHE,
    _PARSER_CACHE,
    _PERSIST_DIR,
    _PERSIST_VERSION,
    _SYMBOL_CACHE,
    _cache_key_for_path,
    _find_project_root,
    _invalidate_cache,
    _project_cache_path,
    _set_cache,
    _set_dir_cache,
    clear_symbol_cache,
    get_symbol_cache_stats,
    load_symbol_cache,
    persist_symbol_cache,
)
from .tools.capsule import (  # noqa: E402, F401
    CODE_CAPSULE_SCHEMA,
    _handle_code_capsule,
    code_capsule_tool,
)
from .tools.complexity import (  # noqa: E402, F401
    _COMPLEXITY_NODE_TYPES,
    _FUNCTION_QUERIES,
    CODE_COMPLEXITY_SCHEMA,
    _count_early_returns,
    _count_nodes,
    _handle_code_complexity,
    code_complexity_tool,
)
from .tools.diagram import (  # noqa: E402, F401
    CODE_DIAGRAM_SYMBOL_SCHEMA,
    _handle_code_diagram_symbol,
    code_diagram_symbol_tool,
)
from .tools.duplicates_extractor import (  # noqa: E402, F401
    _EXT_LANG,
    _handle_code_duplicates,
    code_duplicates_tool,
)
from .tools.explain_extractor import (  # noqa: E402, F401
    _handle_code_explain,
    code_explain_tool,
)
from .tools.export import (  # noqa: E402, F401
    CODE_DEPENDENCY_RISK_SCHEMA,
    CODE_DOCSTRING_GENERATE_SCHEMA,
    CODE_EXPORT_SCHEMA,
    _handle_code_dependency_risk,
    _handle_code_docstring_generate,
    _handle_code_export,
    code_dependency_risk_tool,
    code_docstring_generate_tool,
    code_export_tool,
)
from .tools.graph_analysis import (  # noqa: E402, F401
    CODE_CYCLE_DETECTOR_SCHEMA,
    CODE_DEPENDENCY_GRAPH_SCHEMA,
    CODE_HOT_PATHS_SCHEMA,
    _handle_code_cycle_detector,
    _handle_code_dependency_graph,
    _handle_code_hot_paths,
    code_cycle_detector_tool,
    code_dependency_graph_tool,
    code_hot_paths_tool,
)
from .tools.impact import (  # noqa: E402, F401
    CODE_BLAST_RADIUS_SCHEMA,
    CODE_IMPACT_SCHEMA,
    CODE_PR_IMPACT_SCHEMA,
    _find_functions_in_file,
    _handle_code_blast_radius,
    _handle_code_impact,
    _handle_code_pr_impact,
    code_blast_radius_tool,
    code_impact_tool,
    code_pr_impact_tool,
)
from .tools.language import (  # noqa: E402, F401
    _EXT_TO_LANG,
    _NODE_KIND_MAP,
    _classify_node,
    _get_language,
    _get_parser,
    _init_languages,
    detect_language,
)
from .tools.metrics import (  # noqa: E402, F401
    CODE_METRICS_SCHEMA,
    _handle_code_metrics,
    code_metrics_tool,
)
from .tools.overview import (  # noqa: E402, F401
    CODE_OVERVIEW_SCHEMA,
    _handle_code_overview,
    code_overview_tool,
)
from .tools.pattern import (  # noqa: E402, F401
    _AST_GREP_LANG_MAP,
    _AST_GREP_VAR_RE,
    _apply_refactor_changes,
    _ast_grep_rewrite,
    _build_refactor_changes,
    _check_ast_grep_reqs,
)
from .tools.query import (  # noqa: E402, F401
    _QUERY_INTENT_MAP,
    CODE_QUERY_SCHEMA,
    _handle_code_query,
    code_query_tool,
)
from .tools.refactor_extractor import (  # noqa: E402, F401
    _code_refactor_directory,
    _code_refactor_single_file,
    _handle_code_refactor,
    code_refactor_tool,
)
from .tools.search_by_error import (  # noqa: E402, F401
    CODE_SEARCH_BY_ERROR_SCHEMA,
    _handle_code_search_by_error,
    code_search_by_error_tool,
)
from .tools.search_extractor import (  # noqa: E402, F401
    _CODE_SEARCH_PRESETS,
    _PRESET_ALIASES,
    _code_search_directory,
    _code_search_single_file,
    _handle_code_search,
    _process_match_captures,
    _resolve_preset,
    _resolve_query,
    _search_single_file,
    code_search_tool,
)
from .tools.security import (  # noqa: E402, F401
    CODE_SECURITY_SCHEMA,
    _handle_code_security,
    code_security_scan_tool,
)

# New re-exports (extracted 2026-06-23)
from .tools.symbols_extractor import (  # noqa: E402, F401
    _check_code_intel_reqs,
    _classify_symbol_kind,
    _detect_if_method,
    _extract_candidate,
    _format_symbols_output,
    _handle_code_symbols,
    _setup_query,
    _symbols_extract_single,
    _symbols_scan_directory,
    code_symbols_tool,
    extract_symbols,
)
from .tools.test_coverage import (  # noqa: E402, F401
    CODE_TESTS_FOR_SYMBOL_SCHEMA,
    _calc_test_score,
    _handle_code_tests_for_symbol,
    _tests_calc_coverage,
    _tests_filter_and_score,
    _tests_find_references,
    _tests_find_symbol_name,
    code_tests_for_symbol_tool,
)
from .tools.testgen import (  # noqa: E402, F401
    CODE_GENERATE_TESTS_SCHEMA,
    _handle_code_generate_tests,
    code_generate_tests_tool,
)
from .tools.type_hierarchy import (  # noqa: E402, F401
    _ast_type_hierarchy_subtypes,
    _ast_type_hierarchy_supertypes,
)
from .tools.unused import (  # noqa: E402, F401 — re-exported for __init__.py + tests
    CODE_UNUSED_FINDER_SCHEMA,
    _handle_code_unused_finder,
    code_unused_finder_tool,
)
from .tools.workspace import (  # noqa: E402, F401
    CODE_WORKSPACE_SUMMARY_SCHEMA,
    _count_extensions,
    _detect_lang_for_summary,
    _detect_monorepo_markers,
    _find_lang_folders,
    _handle_code_workspace_summary,
    _scan_workspace,
    code_workspace_summary_tool,
)
