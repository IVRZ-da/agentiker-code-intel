# Tool Reference — Vollständige Tool-Dokumentation

## Navigation Tier (cheap, use first)

| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_symbols` | Extract function/class/method signatures from files/dirs | ~100-500 |
| `code_workspace_summary` | Monorepo overview: apps, packages, root markers | ~200-400 |
| `code_workspace_symbols` | LSP project-wide symbol search by name (sub-second) | ~100-400 |
| `code_query` | Smart query router — describe intent, get best tool | ~50 |

## Analysis Tier (medium cost, deeper insight)

| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_search` | AST-aware structural search (tree-sitter queries) | ~200-800 |
| `code_capsule` | One-shot compact symbol view: sig + definition + refs + imports | ~300-600 |
| `code_diagnostics` | LSP diagnostics (errors/warnings) per file or symbol | ~100-400 |
| `code_callers` | Find who calls a function/method (call graph up) | ~100-300 |
| `code_callees` | Find what a function/method calls (call graph down) | ~100-300 |
| `code_hover` | LSP hover — type signature + docstring at cursor (cheap signature lookup) | ~80-300 |
| `code_signatures` | LSP signature help — active parameter + overloads at call site | ~80-250 |

## Cross-File Tier (LSP-backed, higher cost but precise)

| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_definition` | Go-to-definition (LSP first, AST fallback) | ~200-600 |
| `code_references` | Find all references (LSP first, text fallback), `group_by_file` mode | ~300-2000 |
| `code_type_definition` | Jump to TYPE definition (vs value definition) | ~200-500 |

## Safety Tier (use before changes)

| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_impact` | Blast radius analysis: affected files, risk level, confidence | ~200-500 |
| `code_tests_for_symbol` | Find + prioritize tests covering a symbol | ~200-600 |
| `code_refactor` | AST-safe structural search & replace (dry-run default) | ~200-800 |
| `code_rename` | LSP semantic rename (scope-aware, dry-run default). Use over code_refactor when renaming a single symbol — respects scopes/shadowing. | ~200-600 |
| `code_action` | List/apply LSP code actions: organize imports, quick-fix diagnostics, extract method, source actions. Dry-run by default. | ~200-800 |

## Analysis Plugin Tools (wrapping code-intel)

| Tool | Purpose | 
|------|---------|
| `analysis_inspect(path, symbol, depth, persist)` | Multi-step code analysis (depth 1-5) |
| `analysis_architecture(path, format, depth)` | Full architecture analysis with dependency graph |
| `analysis_deadcode(path, kinds, persist)` | Dead code detection (imports, functions, errors) |
| `analysis_report(scope, findings, recommendations, persist)` | Structured report generation + Honcho persist |
| `analysis_diff(report_a, report_b, scope, format)` | Compare two analysis results |
| `analysis_trend(scope, intent, days)` | Trend analysis over time via Honcho history |
| `analysis_watch(path, frequency, depth, action, name)` | Set up recurring analysis cron jobs |
| `analysis_graph(report, type)` | Generate Mermaid diagrams from analysis results |

## Tool Details

### `code_symbols` — Symbol Extraction
Token-efficient navigation: extract function/class/method signatures with line ranges without reading entire files.
- Supports: Python, TypeScript, JavaScript, Rust, Go, Java, C/C++
- Filters: by kind (function/class/method/interface/type/variable), fuzzy name pattern
- Optional `include_body` for method bodies
- Output: `L196  get_tool_definitions(enabled, disabled) -> List[Dict]`

### `code_search` — AST-Aware Structural Search
Search by code structure, not text. Uses tree-sitter query language.
- **Supports both files and directories** — directory mode recursively scans supported extensions
- High-level shortcuts: function calls, class definitions, import patterns, decorators
- Returns file:line:col with context; directory results include `file` path per result
- `file_glob` filter for language targeting
- `max_results` respected across files (stops early when limit hit)

### `code_refactor` — AST-Safe Code Transformation
Structural search & replace via ast-grep. Guaranteed syntactically valid output.
- ast-grep metavariable syntax: `console.log($A)` → `logger.info($A)`
- `dry_run` mode (default: true) — shows diff preview before writing
- **Multi-file support with `path` as directory** — recursive scan across all supported languages
- `file_glob` param to filter files in directory mode (e.g. `*.service.ts`, `*_test.py`)
- Safety: validates output is syntactically valid before writing

### `code_capsule` — One-Shot Symbol Summary
Replaces calling code_symbols → code_definition → code_references → read_file.
- Returns: signature, short doc, definition location, top references, imports, optional tests
- Use when you need a quick understanding of a symbol without multiple tool calls

### `code_definition` — Go-to-Definition
Navigate to the original declaration/definition of a symbol using LSP.
- Requires file path + line where the symbol appears
- Uses pyright/pylsp for Python, typescript-language-server for TS/JS (cross-file resolution)
- Falls back to AST-based search if LSP is unavailable

### `code_references` — Find All References
Find ALL project-wide usages/references of a symbol using LSP.
- Shows every file and line where a function, class, variable, or type is used
- `group_by_file=True` to save tokens on large codebases

### `code_diagnostics` — LSP Errors & Warnings
Fetch LSP diagnostics (errors, warnings, info) for a source file.
- Falls back to a lightweight AST lint heuristic if no LSP server is active

### `code_hover` — Type & Doc Preview
Get type signature, parameter info, and docstring for a symbol via LSP hover.
- Use BEFORE calling/editing a function to confirm its exact signature

### `code_signatures` — Parameter Hints
Get parameter / signature hints for a function call site via LSP signatureHelp.
- Use BEFORE writing or editing a call to an unfamiliar function
- Cursor MUST be inside the call's parentheses

### `code_implementations` — Find Implementations
Find implementations of a symbol via LSP textDocument/implementation.
- Useful for finding where interfaces are implemented, abstract methods overridden

### `code_call_hierarchy` — Call Tree
Find call hierarchy for a symbol — incoming calls (who calls this) and outgoing calls.
- Returns a formatted tree with configurable depth

### `code_type_hierarchy` — Type Tree
Find type hierarchy — supertypes (parent types) and subtypes (child types).
- Uses LSP typeHierarchy when available, falls back to AST-based analysis

### `code_highlight` — File-Local Occurrences
Find ALL occurrences of a symbol in the current file (file-local).
- Faster than code_references when you only need file-local matches

### `code_inlay_hints` — Type Hints Inline
Get inferred type hints (inlay hints) for a code range.

### `code_document_symbols` — LSP Symbols
Get ALL symbols in a file via LSP textDocument/documentSymbol.
- Supplements the AST-based code_symbols with LSP-level information

### `code_complexity` — Cyclomatic Complexity
Calculate cyclomatic complexity for a function.

### `code_search_by_error` — Error Search
Find all places that handle specific error types.

### `code_hot_paths` — Hot Import Paths
Find the most-imported files (hot paths) in a project.

### `code_cycle_detector` — Circular Imports
Find circular import chains in a project using Tarjan's SCC algorithm.

### `code_dependency_graph` — Import Graph
Generate a visual dependency graph (Mermaid flowchart or ASCII tree).

### `code_unused_finder` — Dead Code
Find unused imports and unused functions in a project.

### `code_blast_radius` — Blast Radius
Analyze blast radius of a symbol — what breaks if you change it.

### `code_pr_impact` — PR Impact
Analyze the impact of a PR by combining git diff with ImportGraph.

### `code_query` — Smart Query Router
Describe what you want to find and it auto-selects the best tool.

### `code_replace_body` — Replace Symbol Body
Replace the full definition of a symbol using AST-accurate boundaries.

### `code_safe_delete` — Safe Symbol Deletion
Delete a symbol ONLY if it has no external references.

### `code_insert_before` / `code_insert_after` — Code Insertion
Insert code before or after a symbol's definition using AST boundaries.

### `code_overview` — Compact File Overview
Get a compact overview of all symbols in a source file or directory (tree view).

### `code_format` — File Formatting
Format a file using the LSP server's textDocument/formatting.

## Hooks (automatic, zero manual invocation)

| Hook | What it does |
|------|-------------|
| `pre_llm_call` | Auto-injects symbol context for file paths mentioned in user messages |
| `on_session_end` | Persists symbol cache to disk, then clears memory |
