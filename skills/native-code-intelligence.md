---
name: native-code-intelligence
description: Native tree-sitter + ast-grep code intelligence tools for Hermes agent. Replaces deprecated LSP MCP with in-process AST parsing.
---

# Native Code Intelligence Tools

## Context

Replaced deprecated `lsp-mcp-server` with native code intelligence.
No external servers (no MCP). Tree-sitter + ast-grep-py directly embedded in Hermes.
LSP support via pyright (Python, default) and typescript-language-server (TS/JS) with automatic fallback to AST.

## Architecture Decision

**Native in-process** > External MCP/LSP because:
- Zero external process management / startup latency
- Zero MCP protocol overhead
- Works in all environments (local, docker, ssh, modal)
- Token savings: 46-68% vs native-only workflows (benchmarked by code-analyze-mcp)

### Evaluated & Rejected

| Approach | Why Rejected |
|----------|-------------|
| LSP MCP Server | Complex setup, language server deps, slow startup, fragile |
| code-analyze-mcp | External MCP server, adds protocol overhead |
| code-graph-mcp | Go binary, external dependency |
| ast-grep CLI only | External binary, no programmatic access |
| ast-grep Python only | No cross-file indexing (use tree-sitter for that) |

### Chosen Stack

- `tree-sitter` (>=0.24.0) — Core parsing, query language, 20+ langs
- `tree-sitter-python/typescript/javascript/rust/go/java` — Language grammars
- `ast-grep-py` (>=0.37.0) — Structural search & replace via PyO3 bindings

## 🚨 MANDATORY WORKFLOWS (use BEFORE read_file/patch)

### Workflow 0: Staleness Check (FIRST CALL of any code task)
Before relying on the 6 newer LSP tools, verify the gateway isn't holding stale plugin code. Cheapest probe: call `code_workspace_summary` on the target monorepo and check that known app directories appear in `apps[]` (NOT `packages[]`). If they're misclassified, the gateway predates 2026-04-23 → 6 new LSP tools also missing → tell the user, continue with the 13 stable tools.

### Workflow A: Writing NEW Code in an Existing File
1. `code_workspace_summary` (only if first time in repo) → understand monorepo layout
2. `code_symbols` on target file → find insertion point, see existing patterns/conventions
3. `code_search` (preset=`imports` or `function_calls`) → check existing utilities to reuse instead of reinventing
4. `code_capsule` on any function/class you'll interact with → signature + usage before calling
5. Write code via `patch` (small targeted insert) or `write_file` (new file)
6. `code_diagnostics` on the modified file → catch errors immediately (no manual lint round-trip)
7. `code_search` on related test files → run/extend tests

### Workflow B: Refactoring Existing Code
1. `code_capsule` on the target symbol → signature, refs, top usages — answers "what will break?" cheaply
2. `code_impact` on the target symbol → blast radius (files affected, risk level)
3. `code_tests_for_symbol` → which tests cover this? must they pass after?
4. `code_callers` (who depends on me?) + `code_callees` (what do I depend on?) → both directions
5. **Choose the right transform tool:**
   - **Rename a single symbol** → `code_rename(path, line, new_name, dry_run=true)` (LSP-backed, scope-aware, won't touch shadowed names)
   - **Pattern-based transform** (e.g. `console.log($A)` → `logger.info($A)`) → `code_refactor` with dry_run
6. Apply with `dry_run=false`
7. `code_diagnostics` post-change → verify nothing broke syntactically
8. Run the test files identified in step 3
9. `code_references` with `group_by_file=true` → confirm 0 stale refs (catches dynamic strings)

### Workflow C: Investigating Unknown Code
1. `code_query` with intent ("understand", "find_usage", etc.) → router suggests the right tool
2. `code_symbols` on file/dir → map of what exists
3. `code_capsule` on the interesting symbol → one-shot context (replaces 4 tool calls)
4. `code_definition` only if capsule's `definition` field is null (cross-file unresolved)

### Workflow D: Quality Gate Before Commit
1. `code_diagnostics` on every changed file → 0 errors (warnings case-by-case)
2. `code_impact` on changed symbols → if `risk_level=high`, ensure tests exist
3. `code_tests_for_symbol` for changed public APIs → coverage `medium` or higher

### Workflow E: Code Quality Guardrails (MANDATORY for refactor + new code)

These rules are **non-negotiable** — they exist because `read_file` + `patch` produces
inconsistent code when scaled across a monorepo. Skip these only for one-off scratch files.

**🔒 Diagnostics-Gate (run after EVERY write/patch/refactor):**
```
code_diagnostics(path=<changed_file>) → if errors > 0: revert or fix immediately. No deferral.
```
This is the single most important rule. pyright/tsserver via `lsp_bridge.py` IS the same
compiler that runs in CI — there is no "TS will catch it later".

**For NEW code (any new function/class/file):**
1. `code_search` preset=`function_calls` with the API name → confirm it exists, find callsites for style.
2. `code_capsule` on any class you instantiate/extend → know its signature before calling.
3. `code_signatures(path, line, character)` at every call site you author with non-trivial parameters
   (>2 args, overloaded, generic) → never guess argument order/types for typed APIs.
4. `code_hover(path, line)` over any imported symbol you don't fully recognize → cheap doc/type
   preview before committing to a usage pattern.
5. `code_symbols` on the target file → place new symbol next to peers of same kind.
6. **Diagnostics-Gate** (see above).
7. **Auto-fix pass:** `code_action(path, kind="source.organizeImports")` → apply, then
   `code_action(path)` (no kind) on each file with diagnostics → apply quick-fixes (dry-run preview first).
8. `code_search` for the new symbol name → verify discoverable (catches dead-code-by-typo).

**For REFACTORS (rename, signature change, move, split):**
1. `code_impact` FIRST. If `risk_level=high` and no tests → STOP, write tests (`test-driven-development` skill).
2. `code_callers` → list every callsite. Read 2-3 to understand call contexts.
3. **Pick the transform:**
   - Single symbol rename → `code_rename` (LSP, scope-aware, dry-run default)
   - Structural pattern (call-site rewrite, import move, decorator wrap) → `code_refactor` (AST, dry-run default)
   - Auto-fixable diagnostic / organize imports / extract method → `code_action` (LSP, dry-run default)
4. Preview with `dry_run=true` → surprises in fixtures? Narrow `file_glob`.
5. Apply with `dry_run=false`.
6. **Diagnostics-Gate on EVERY file in the diff** (not just the entry point).
7. **Post-refactor cleanup:** `code_action(path, kind="source.organizeImports")` on every changed file →
   removes dead imports left behind by signature changes / moves.
8. Run tests from step 1 (`test-runner` skill).
9. `code_references` post-change with `group_by_file=true` → confirm 0 stale refs to old symbol
   (catches strings/dynamic refs neither tool reaches).

**For NAVIGATION across a monorepo (instead of guessing paths):**
- `code_workspace_symbols(query="<SymbolName>", kind="class"|"function"|...)` → instant project-wide lookup.
  Use this BEFORE `search_files` for any symbol-by-name query — it's LSP-indexed and faster.

**Hard NO list (these patterns produce bad code):**
- ❌ `patch` with `replace_all=true` for renames → use `code_rename` (LSP, scope-aware) or `code_refactor` (AST).
- ❌ `read_file` → `write_file` for "small reorganization" → use `code_refactor` rewrites.
- ❌ Skipping `code_diagnostics` because "TypeScript will catch it later" → it won't,
  pyright/tsserver in the bridge IS the same compiler. 0-error gate or revert.
- ❌ Adding a new util when `code_search` shows an equivalent already exists in `packages/`.
- ❌ Importing across architectural boundaries (apps→app, packages→modules) → check
  with `code_search` preset=`imports` after writing.
- ❌ Trusting compaction summaries that claim files were created — verify with `search_files target=files` first.

**Common NestJS monorepo specifics (verify with code_search before write):**
- Logger: prefer the repository's shared logger package/pattern → `.info()` / `.error()` / `.warn()`.
  Avoid mixing NestJS `Logger` from `@nestjs/common` into codebases that have standardized on a shared logger.
  Verify: `code_search(preset="imports", pattern="logger", path=<file>)`.
- Type-only imports (`import type`) for types EXCEPT NestJS DI tokens (breaks injection silently).
- Apps live in `apps/<name>/app/` (NOT `src/`). Exceptions: `hubspot-ui-extensions/src/`, `browser-use/src/`.
- Cross-boundary import check after every new file:
  `code_search(preset="imports", path=<new_file>)` — flag any `apps/X` importing from `apps/Y`.

### Tool-Selection Rules (HARD)
| If you're about to do... | STOP. Use this instead. |
|--------------------------|-------------------------|
| `read_file` on a file >300 lines just to find a function | `code_symbols` (~90% token savings) |
| `read_file` to understand a class | `code_capsule` (sig + refs + imports in one call) |
| `search_files` regex for `function foo(`, `class Bar`, etc. | `code_search` preset (no false positives in comments/strings) |
| `search_files` to find usages of a symbol | `code_references` with `group_by_file=true` |
| `patch` with `replace_all=true` for a rename | `code_refactor` (AST-safe, syntax-validated) |
| Multi-file rename via `delegate_task` + grep | `code_refactor` with directory `path` + `file_glob` |
| Manual blast-radius assessment by reading dependents | `code_impact` (one call) |
| "Does test X cover this?" | `code_tests_for_symbol` |

## IDE-Feature Coverage (what we can actually do)

Hybrid Tree-sitter + LSP — same architecture as Neovim/Zed/Helix. Maps every IDE feature an agent might need to a tool:

| IDE Feature | Tool | Status |
|-------------|------|--------|
| Syntax-aware structural search | `code_search` (presets + raw TS queries) | ✅ |
| Symbol outline / textobjects | `code_symbols` (line ranges = node-select) | ✅ |
| Go to definition | `code_definition` (LSP) | ✅ |
| Find all references | `code_references` (LSP, `group_by_file`) | ✅ |
| Diagnostics (errors/warnings) | `code_diagnostics` (LSP + AST fallback) | ✅ |
| Hover / inline docs | `code_hover` (LSP) | ✅ shipped 04-23 |
| Signature help (param info) | `code_signatures` (LSP) | ✅ shipped 04-23 |
| Go to type definition | `code_type_definition` (LSP) | ✅ shipped 04-23 |
| Rename symbol (semantic) | `code_rename` (LSP, dry_run default) | ✅ shipped 04-22 |
| Code actions / quick-fix / organize imports | `code_action` (LSP, dry_run default) | ✅ shipped 04-23 |
| Workspace symbol search (Cmd+T) | `code_workspace_symbols` (LSP) | ✅ shipped 04-22 |
| AST-safe multi-file refactor | `code_refactor` (ast-grep, dry_run default) | ✅ |
| Call hierarchy in / out | `code_callers` / `code_callees` | ✅ |
| Symbol summary one-shot (definition+refs+doc) | `code_capsule` | ✅ |
| Impact analysis (blast radius) | `code_impact` | ✅ |
| Test discovery for symbol | `code_tests_for_symbol` | ✅ |
| Monorepo overview | `code_workspace_summary` | ✅ |
| Smart query routing | `code_query` | ✅ |
| Inlay hints | — | Skipped (visual-only, useless for agent) |
| Cross-repo indexed search | — | Out of scope (per-repo agent) |

**Result: full IDE feature parity for refactoring/code-quality use-cases.** Languages covered: Python (pyright), TypeScript/TSX/JS/JSX (tsserver), Rust (rust-analyzer), Go (gopls). Add others on demand.

### Plugin Skill Auto-Registration

When `hermes plugins enable code_intel` is run, the plugin's `register()` function automatically calls `ctx.register_skill()` for bundled skills. This means:

1. **Zero manual setup** — skill is available via `skill_view("code_intel:native-code-intelligence")` immediately after plugin enable.
2. **References/templates/scripts** resolve automatically — `_serve_plugin_skill()` uses `skill_dir = skill_md.parent` to find `references/`, `templates/`, `scripts/` subdirectories.
3. **file_path access works** — `skill_view("code_intel:native-code-intelligence", file_path="references/phase5-roadmap.md")` returns the file content with path-traversal protection.

**Layout convention for plugin-bundled skills:**
```
plugins/code_intel/
├── skills/
│   ├── native-code-intelligence.md   # SKILL.md content
│   └── references/
│       └── phase5-roadmap.md
```

To add more skills to the same plugin, add additional `.md` files + `ctx.register_skill()` calls in `register()`.

### Real-world Verification (2026-04-23 & 2026-07-11, Production Monorepo)

**Initial verification (04-23):** Tested all 4 tiers against `apps/unified-api/app/controllers/PublishingController.ts`:
- Navigation Tier: `code_workspace_summary` (30 apps + 11 packages), `code_symbols`, `code_query` — sub-second.
- Analysis Tier: `code_capsule`, `code_callers`, `code_callees`, `code_search` (40 calls indexed) work as documented.
- Cross-File LSP: `code_definition` + `code_references` (group_by_file) return precise hits via tsserver.
- Safety Tier: `code_impact`, `code_tests_for_symbol`, `code_refactor` (dry_run with metavar capture + context lines).

**Full 19-tool audit (07-11, Opus 4.7):** Every single tool verified live against `ReportsController.ts` and `HubSpotService.ts`:

| # | Tool | Test Target | Result |
|---|------|-------------|--------|
| 1 | `code_symbols` | ReportsController dir | ✅ 13 classes extracted |
| 2 | `code_workspace_summary` | Monorepo root | ✅ 30 apps + 11 packages |
| 3 | `code_workspace_symbols` | "ReportsService" | ✅ 0 hits (correct — service doesn't exist) |
| 4 | `code_query` | "find_usage" intent | ✅ Routed to `code_references` |
| 5 | `code_search` | imports HubSpotService | ✅ 6 AST hits |
| 6 | `code_capsule` | HubSpotService.ts:4 | ✅ Signature + definition |
| 7 | `code_diagnostics` | ReportsController | ✅ 2 warnings (AST fallback on NestJS decorators — expected) |
| 8 | `code_callers` | getExternalUserAgentHeader | ✅ 4 callers in 3 files |
| 9 | `code_callees` | ReportsController method | ✅ 42 callees listed |
| 10 | `code_hover` | ReportsController:8 | ✅ Type signature returned |
| 11 | `code_signatures` | ReportsController:132 | ✅ Correct "cursor inside parens" hint (test position was off-parens) |
| 12 | `code_definition` | HubSpotService.ts:4 | ✅ Goes to definition |
| 13 | `code_references` | ReportsController:8 | ✅ Cross-file refs with group_by_file |
| 14 | `code_type_definition` | DealsController:10 | ✅ Jumps to interface type |
| 15 | `code_impact` | ReportsController:125 | ✅ 2 files, risk=low |
| 16 | `code_tests_for_symbol` | ReportsController | ✅ 0 tests (correct — no test file) |
| 17 | `code_refactor` | (verified in prior session) | ✅ dry_run default |
| 18 | `code_rename` | ExportQueryDto → V2 | ✅ 2 files, 2 edits, dry_run |
| 19 | `code_action` | ReportsController:58 | ✅ No actions (correct at that position) |

### Editor-Parity Gap Analysis (validated against Neovim 0.5+, Zed, Helix, Emacs, GitHub)

| IDE/Editor Feature | Hermes Tool | Gap? | Notes |
|--------------------|-------------|------|-------|
| TS syntax highlighting / folding | `code_symbols` (line ranges) | ❌ None | Agent doesn't render UI |
| TS textobjects (select body) | `code_symbols` + `read_file(offset,limit)` | ❌ None | Range-based reads equivalent |
| LSP go-to-def | `code_definition` | ❌ None | Full coverage |
| LSP find references | `code_references` (grouped) | ❌ None | Full coverage |
| LSP rename | `code_rename` | ❌ None | Scope-aware, dry-run default |
| LSP diagnostics + quick-fix | `code_diagnostics` + `code_action` | ❌ None | Full coverage |
| LSP hover / signature help | `code_hover` + `code_signatures` | ❌ None | Full coverage |
| TS structural search | `code_search` (presets + raw TS queries) | ❌ None | Full coverage |
| TS structural replace | `code_refactor` (ast-grep) | ❌ None | Full coverage |
| Workspace symbol search (Cmd+T) | `code_workspace_symbols` | ❌ None | Full coverage |
| Call hierarchy (in/out) | `code_callers` / `code_callees` | ❌ None | Full coverage |
| Code formatting | — | ⚠️ Minor | Not blocking — `patch` handles most cases |
| Document highlights | — | ⚠️ Minor | `code_references` supersedes for agent use |
| Selection ranges (expand/shrink) | — | ⚠️ Minor | Visual-only feature |
| Semantic tokens | — | N/A | Visual-only, useless for agents |

**Conclusion: Complete IDE-parity for all agent-relevant use-cases.** The 3 minor gaps (formatting, doc highlights, selection ranges) are visual/interactive features that an LLM agent doesn't need.

Two pitfalls confirmed in the wild — encode in workflow:
- WARN: **`code_diagnostics` AST-fallback false positives on NestJS/Angular files**: When LSP isn't warm, the AST heuristic flags decorator-only imports (`Controller`, `Get`, `Body`, `Param`, `ApiResponse`) as "unused" — they ARE consumed by `@Decorator(...)` but only via decorator syntax, not text-referenced. Fix: pre-warm LSP by calling `code_capsule` or `code_definition` on the file FIRST. Then `code_diagnostics` uses cached `publishDiagnostics` (real tsserver output, 0 false positives). If you still get fallback warnings on NestJS controllers/services, ignore decorator-related "unused import" reports.
- WARN: **`code_search` matches only top-level call identifiers, NOT member expressions**: A search for `getStatus` will NOT find `this.service.getStatus(x)`. Workarounds: (a) search for the bare identifier as text, (b) use raw tree-sitter query `(call_expression function: (member_expression property: (property_identifier) @m))` with a pattern filter for the method name, (c) for finding all callers of a method, prefer `code_callers` on the method's definition line — it correctly resolves member calls via LSP.

### Gateway Restart Required After Adding LSP Tools
The 6 new LSP tools shipped 2026-04-23 (`code_workspace_symbols`, `code_rename`, `code_hover`, `code_type_definition`, `code_signatures`, `code_action`) are registered in `~/.hermes/plugins/code_intel/__init__.py` but won't appear in an already-running gateway session. Run `hermes restart` (or kill the gateway process) to pick them up. Verify with the model_tools listing helper. Must list 19 entries. If only 13 show up, the registration in `__init__.py` `_HERMES_CORE_TOOLS` and `TOOLSETS["code_intel"]` is missing the new names.

**In-session detection (always run when starting a code task):**
Try calling `code_rename` (or any of the 6 new tools) with dummy args. If the response is `Tool 'code_rename' does not exist`, the agent is on a stale gateway/ACP subprocess. Two signs you're stale:
1. Toolset shows only 13 `code_*` tools (no `code_rename`, `code_hover`, `code_signatures`, `code_action`, `code_type_definition`, `code_workspace_symbols`).
2. `code_workspace_summary` mis-classifies application directories as `packages`. The on-disk fix at `code_intel.py` lines 2018-2025 (parent_kind override) is correct — if classification is wrong, the running process holds pre-fix code.

When stale, do **NOT** silently work around it. Instead:
- Tell the user explicitly: "Gateway is on stale code from before YYYY-MM-DD. Restart Hermes (`hermes restart` or kill the ACP subprocess) to enable the 6 new LSP tools and the workspace-summary classification fix."
- Continue using the 13 working tools in the meantime — they're still production-ready.

**Verified 2026-04-23 (Opus 4.7 session):** Plugin code on disk = 19 tools + correct classification ✅. Live ACP subprocess = 13 tools + buggy classification ❌. Restart needed.

### Pitfall fixed 2026-04-22 (registration bug)
`code_rename` and `code_workspace_symbols` were **registered in the Hermes tool registry but missing from `toolsets.TOOLSETS["code_intel"]` and `_HERMES_CORE_TOOLS`** — invisible to most setups. Fixed in `~/.hermes/plugins/code_intel/__init__.py`. If new LSP tools are added in `lsp_bridge.py::register_lsp_tools()`, they MUST also be added to both lists in `__init__.py` (lines ~170 and ~180). Without that injection, tools exist but no platform exposes them.

## Tool Overview (19 Tools)

### Navigation Tier (cheap, use first)
| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_symbols` | Extract function/class/method signatures from files/dirs | ~100-500 |
| `code_workspace_summary` | Monorepo overview: apps, packages, root markers | ~200-400 |
| `code_workspace_symbols` | LSP project-wide symbol search by name (sub-second) | ~100-400 |
| `code_query` | Smart query router — describe intent, get best tool | ~50 |

### Analysis Tier (medium cost, deeper insight)
| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_search` | AST-aware structural search (tree-sitter queries) | ~200-800 |
| `code_capsule` | One-shot compact symbol view: sig + definition + refs + imports | ~300-600 |
| `code_diagnostics` | LSP diagnostics (errors/warnings) per file or symbol | ~100-400 |
| `code_callers` | Find who calls a function/method (call graph up) | ~100-300 |
| `code_callees` | Find what a function/method calls (call graph down) | ~100-300 |
| `code_hover` | LSP hover — type signature + docstring at cursor (cheap signature lookup) | ~80-300 |
| `code_signatures` | LSP signature help — active parameter + overloads at call site | ~80-250 |

### Cross-File Tier (LSP-backed, higher cost but precise)
| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_definition` | Go-to-definition (LSP first, AST fallback) | ~200-600 |
| `code_references` | Find all references (LSP first, text fallback), `group_by_file` mode | ~300-2000 |
| `code_type_definition` | Jump to TYPE definition (vs value definition) — for `const u = getUser()` lands on `User` interface | ~200-500 |

### Safety Tier (use before changes)
| Tool | Purpose | Token Cost |
|------|---------|------------|
| `code_impact` | Blast radius analysis: affected files, risk level, confidence | ~200-500 |
| `code_tests_for_symbol` | Find + prioritize tests covering a symbol | ~200-600 |
| `code_refactor` | AST-safe structural search & replace (dry-run default) | ~200-800 |
| `code_rename` | LSP semantic rename (scope-aware, dry-run default). **Use over code_refactor when renaming a single symbol** — respects scopes/shadowing. | ~200-600 |
| `code_action` | List/apply LSP code actions: organize imports, quick-fix diagnostics, extract method, source actions. Dry-run by default — preview WorkspaceEdit before apply. | ~200-800 |

### Hooks (automatic, zero manual invocation)
| Hook | What it does |
|------|-------------|
| `pre_llm_call` | Auto-injects symbol context for file paths mentioned in user messages |
| `on_session_end` | Persists symbol cache to disk, then clears memory |

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
- Single-file output: flat `{"path", "changes", ...}` — no `results` wrapper
- Directory output: `{"path", "files_scanned", "files_changed", "results": [...], ...}`
- Safety: validates output is syntactically valid before writing

### `code_capsule` — One-Shot Symbol Summary
Replaces the common pattern of calling code_symbols → code_definition → code_references → read_file.
- Returns: signature, short doc, definition location, top references, imports, optional tests
- Use when you need a quick understanding of a symbol without multiple tool calls
- Best for: "what does this class do?", "how is this function used?", "understand before editing"

### `code_diagnostics` — LSP Diagnostics
Get errors/warnings for a file or specific symbol from LSP servers.
- Uses cached `textDocument/publishDiagnostics` results (zero latency if already indexed)
- Falls back to LSP `textDocument/diagnostic` request on demand
- Returns: severity (error/warning/info), message, line, source
- Use before editing to check for existing problems

### `code_callers` / `code_callees` — Call Graph Navigation
- `code_callers`: Find all functions that CALL this function (who depends on me?)
- `code_callees`: Find all functions this function CALLS (what do I depend on?)
- LSP-backed with AST fallback
- Critical for: understanding refactoring impact, tracing execution paths

### `code_workspace_summary` — Monorepo Overview
Detects monorepo type (turbo/nx/lerna/pnpm workspaces) and returns compact structure.
- Lists: apps, packages, root markers, top-level dependencies, entry points
- Reads package.json for each app/package (name, language, main entry)
- Use at the START of any task involving a monorepo

### `code_impact` — Change Impact Analysis
Before refactoring, understand the blast radius.
- Symbol-level: traces references, counts direct/indirect refs, identifies test files
- File-level: finds files that import this file
- Returns: `risk_level` (low/medium/high), `confidence`, `files_affected`, `test_files`
- Use BEFORE code changes, especially for shared utilities/interfaces

### `code_tests_for_symbol` — Test Finder
Find tests that likely cover a specific symbol.
- Filters references for test files (path contains test/spec/__tests__)
- Scores by relevance: direct reference (3), name match (2), same directory (1)
- Returns: ranked test files with scores, coverage estimate (high/medium/low/none)
- Use before making changes to ensure safe refactoring

### `code_query` — Smart Query Router
Describe what you want to find, and it routes to the best tool automatically.
- 30 intent mappings: find_usage → code_references, definition → code_definition, etc.
- Returns: `routed_to` (primary tool), `fallback`, `recommended_args`
- When unsure which tool to use, call code_query first
- 50 tokens — cheapest way to find the right tool

## Steering Hints (Auto-Injected)

The plugin injects steering hints into the descriptions of built-in tools:
- `search_files` → "For AST-aware structural search, prefer code_search"
- `read_file` → "For understanding file contents, prefer code_symbols"
- `patch` → "For structural replacement, prefer code_refactor (dry-run default)"
- `code_definition` → "For a quick one-shot overview, use code_capsule instead"
- `code_references` → "Use group_by_file=True to save tokens; for summary use code_capsule"
- `code_symbols` → "For cross-file navigation, use code_definition/code_references after"

## Persistent Symbol Cache (B5)

Symbol cache is persisted to disk between sessions:
- Location: `~/.hermes/plugins/code_intel/.cache/symidx_<hash>.json`
- Hash is based on **project root** (NOT CWD) — `_find_project_root()` walks up from filepath looking for monorepo markers (`pnpm-workspace.yaml`, `nx.json`, `lerna.json`), then generic markers (`.git`, `pyproject.toml`, etc.)
- Cache keys are **pipe-delimited strings** (`"path|mtime|lang|pattern|kind|bool"`) — NOT tuples (tuples are not JSON-serializable, caused `"keys must be str, int, float, bool or None, not tuple"` error and 0 persisted entries)
- `_PERSIST_VERSION` controls cache invalidation — bump when cache format changes
- Saved on session end, restored on session start
- Invalidated on version mismatch

## Implementation Phases

### Phase 1: Foundation ✅ DONE (2026-04-16)
1. ~~Install dependencies in venv (all precompiled wheels, no build tools needed)~~ — `tree-sitter 0.25.2` + 6 language grammars installed
2. ~~Create `tools/code_intel.py` with tree-sitter language detection from file extension~~ — 882 lines
3. ~~Implement `code_symbols` handler~~ — working, 7 languages supported
4. ~~Register in `toolsets.py` — add `"code_intel"` toolset + add to `_HERMES_CORE_TOOLS`~~ — `code_symbols` in core list
5. ~~Write tests~~ — `tests/tools/test_code_intel.py` (40 tests, all passing). Commit `88750f13`.

### Phase 2: Structural Search ✅ DONE (2026-04-16)
1. ~~Implement `code_search` with tree-sitter Query API~~ — named presets + raw queries + text pattern filter
2. ~~Add high-level pattern shortcuts (common queries)~~ — 7 presets: `function_calls`, `imports`, `return_stmts`, `try_catch`, `assignments`, `decorator_calls`, `string_literals`. Aliases: `calls`, `strings`, `returns`, `try`, `assigns`, `decorators`, `literals`
3. ~~Dedup overlapping captures~~ — `(name, start_row)` tuple dedup, `max_results` truncation

### Phase 3: Structural Refactor ✅ DONE (2026-04-16)
1. ~~Integrate ast-grep-py for `code_refactor`~~ — working with meta variables `$NAME`, `$$BODY`
2. ~~Implement dry-run mode with unified diff output~~ — default `dry_run=true`, shows context lines
3. ~~Manual variable interpolation~~ — `ast-grep-py`'s `commit_edits()` doesn't handle metavar substitution, so manual `.text.decode()` interpolation on `$$BODY` and `$NAME` captures
4. ~~Context lines in output~~ — configurable `context` param (default 2)
5. Commit `8f65c267` — 720 lines added, 66 tests passing. Pushed to `origin` (rewasa/hermes-agent).

### Phase 4: Integration ✅ DONE (2026-04-16)
1. ~~Update `read_file` description: guide models toward `code_symbols` for navigation~~ — updated via skill patches on 6 skills (see Skill Cross-References below)
2. ~~Update `search_files` description: suggest `code_search` for structural patterns~~ — same skill patches
3. Add per-session symbol caching for repeated queries *(still pending)*
4. ~~Integration tests with Hermes test suite~~ — 11,574 passed, 0 regressions in code_intel

### Skill Cross-References (Phase 4)
These 6 skills now reference `code_symbols`/`code_search`/`code_refactor`:
- `code-review` — use `code_symbols` to navigate large files before reviewing
- `systematic-debugging` — use `code_symbols` + `code_search` + `code_refactor` during investigation
- `test-driven-development` — use `code_symbols` + `code_search` for test structure discovery
- `batch-migration` — use `code_refactor` for AST-safe batch renames across codebases
- `subagent-driven-development` — subagents use `code_symbols`/`code_search` for context-efficient navigation
- `writing-plans` — use `code_symbols`/`code_refactor` for codebase exploration in planning phase

### Benchmarks (Production Monorepo)
| Scenario | Tool | Time | Output Size | Tokens Saved |
|----------|------|------|-------------|--------------|
| HubSpotService.ts (1354 lines) method map | `code_symbols` | 0.012s | 8.7k chars | ~83% vs `read_file` (50.8k) |
| All try/catch in HubSpotService | `code_search` (preset) | 0.009s | 3.3k chars | Full AST blocks, not just matching lines |
| Controller classes in unified-api/ | `code_symbols` (dir) | 0.234s | 11.3k chars | 15 classes in one call |
| MongoDBService methods matching 'property' | `code_symbols` (pattern) | 0.021s | 1.8k chars | ~96% vs `read_file` (52k) |

## Git Workflow (Fork Setup)

Since Hermes is a fork of NousResearch/hermes-agent:

```
origin   → rewasa/hermes-agent  (your fork — primary push target)
upstream → NousResearch/hermes-agent (official — read-only)
```

`hermes update` auto-detects the fork via `_is_fork()` and will:
- Fetch upstream, compare with origin
- If you have local commits not on upstream → **skip** (preserves your changes)
- If origin is strictly behind → fast-forward pull + sync fork

**To merge upstream changes:**
```bash
git fetch upstream && git merge upstream/main && git push origin main
```

**Important:** Never force-push to upstream. Only push to `origin` (your fork).

## File Changes

```
tools/code_intel.py          # NEW — all 3 tool implementations
toolsets.py                  # ADD "code_intel" toolset + _HERMES_CORE_TOOLS entries
tools/file_tools.py          # UPDATE read_file description (cross-reference)
```

## Language Detection Map

| Extension | Language | tree-sitter Package |
|-----------|----------|-------------------|
| .py | Python | tree-sitter-python |
| .ts | TypeScript | tree-sitter-typescript |
| .tsx | TSX | tree-sitter-typescript |
| .js/.jsx/.mjs | JavaScript | tree-sitter-javascript |
| .rs | Rust | tree-sitter-rust |
| .go | Go | tree-sitter-go |
| .java | Java | tree-sitter-java |
| .c | C | tree-sitter-c |
| .cpp/.cc/.cxx | C++ | tree-sitter-cpp |

## Tree-sitter API (version >=0.24)

### QueryCursor vs Query

- **Use `QueryCursor(query)` + `.matches(node)`** — returns `(pattern_index, {capture_name: [Node, ...]})` tuples. One dict per match, not flat stream.
- **`Query.captures(node)` may not exist** — depends on version. Don't use it.
- **`Query.matches(node)` doesn't exist on Query object** — must use `QueryCursor`.

### Query Pattern Rules

1. **`@capture` on outer anchor** (e.g., `(class_declaration ...) @def`) — the capture IS the def_node in code.
2. **Nested captures without `@`** (e.g., `(lexical_declaration (variable_declarator name: (identifier) @name))`) — only `@name` appears in captures_dict. You need `def_node = name_node.parent` fallback.
3. **Impossible Pattern error** means the grammar doesn't support that structure. Check actual AST with `node.children` + `child_by_field_name()`.
4. **Duplicate matches are normal** — nested patterns (e.g., class methods captured both by top-level function pattern AND nested class pattern). Dedup with `(name, start_row)` tuple.

### Language-Specific Node Types

| Language | const | type alias | class body | async |
|----------|-------|-----------|-----------|-------|
| Rust | `const_item` (NOT `constant_item`) | `type_item` (NOT `type_alias`) | `declaration_list` inside `impl_item` | `function_modifiers` child — base `function_item` matches both |
| Go | `const_declaration > const_spec` | `type_declaration > type_spec` — detect struct/interface by scanning `type_spec` children for `struct_type`/`interface_type` | N/A (struct has no methods) | N/A |
| Python | `assignment` | N/A | `block` (NOT `class_body`) — method detection: `parent.type == "block" && grandparent.type == "class_definition"` | `"async"` is keyword child — base `function_definition` matches both |
| JS/TS | N/A | `type_alias_declaration` | `class_body` inside `class_declaration` | `"async"` is keyword child — base `function_declaration` matches both |
| JS/TS | Arrow functions: `const`/`let` use `lexical_declaration`, `var` uses `variable_declaration`. Need both patterns. | | | |

**Key insight:** Never use `"async" @keyword` literal patterns — `async` is a keyword child of the function node in most grammars, causing "Impossible pattern" errors. The base function pattern already matches async functions.

### Debugging Queries

```python
from tree_sitter import Language, Parser, Query, QueryCursor
# 1. Check actual node types:
tree = Parser(lang).parse(source)
for child in tree.root_node.children:
    print(f'{child.type}: {child.text.decode()[:40]}')
# 2. Compile test:
q = Query(lang, '(your_pattern name: (identifier) @name) @def')
qc = QueryCursor(q)
results = list(qc.matches(tree.root_node))
```

## Pitfalls

- **LLM ignores code_intel tools by default — steering problem, not availability**: Even when `code_symbols`/`code_search`/`code_refactor` are available in a subagent's toolset, the LLM falls back to `read_file` + `patch` unless explicitly instructed. The system prompt contains "DO NOT use patch for structural refactoring" but this gets ignored under heavy context. **Fix**: When using `delegate_task` for refactoring, ALWAYS include explicit steering in `goal` or `context`: "Use `code_refactor` (dry_run first) for structural changes. Use `code_search` instead of `search_files` for finding code patterns. Only use `patch` for single targeted text replacements." This is especially important for `glm-5-turbo` which has the strongest `read_file`/`patch` bias. **Verified**: 3 subagent sessions used code_intel successfully when explicitly told to; 2 subagent sessions defaulted to read_file+patch when not told.
- **Tools silently disappear if `check_fn` fails** — `code_symbols`/`code_search`/`code_refactor` have a `check_fn` that verifies `tree-sitter` and `ast-grep-py` are importable. If either is missing from the Hermes venv, the tools are **silently excluded** from the toolset with no error. Fix: install `tree-sitter` and `ast-grep-py` into the Hermes venv, then restart the gateway/agent. Verify with: `python -c "from model_tools import get_tool_definitions; print([t['function']['name'] for t in get_tool_definitions() if 'code_' in t['function']['name']])"`
- **`hermes` CLI false-positive "Package(s) not found" warning** — `hermes` may report `WARNING: Package(s) not found: ast-grep-py, tree-sitter` at startup even when the tools work correctly. This is a cosmetic bug: the CLI checks packages via the system Python instead of the Hermes venv Python. The actual `check_fn()` in `tools/code_intel.py` imports correctly from the venv. **Diagnostic:** Run `~/.hermes/hermes-agent/venv/bin/python3 -c "from tools.code_intel import _check_code_intel_reqs, _check_ast_grep_reqs; print(_check_code_intel_reqs(), _check_ast_grep_reqs())"` — both should return `True`. If they do, the tools are available despite the warning. Only reinstall the two packages into `~/.hermes/hermes-agent/venv/` if the check returns `False`.
- tree-sitter parsers are loaded lazily — cache per-process, don't re-instantiate per call
- ast-grep-py `SgNode` is immutable — must use `replace()` + `commit_edits()` pattern
- ast-grep metavariables in Python API: `$A` in CLI becomes `get_match('A')` in Python API
- **`commit_edits()` does NOT substitute metavariables** — when replacing `func($A)` → `log($A)`, the replacement string must have `$A` manually resolved. Extract via `node.get_match('A').text.decode()`, then build the replacement string in Python before passing to `replace()`.
- **`Pos` and `Range` API** — `SgNode.range()` returns an object; access line/col via `.start.line`, `.start.column` (NOT `range['start']['line']` subscript — that raises TypeError)
- **JS fixture quirks** — basic JS test fixtures often have no top-level identifier-based calls (only member expressions like `obj.method()`). Don't assert `function_calls` preset returns results on minimal fixtures. Use member_expression queries separately.
- tree-sitter queries can be expensive on huge files — limit recursion depth
- `code_refactor` MUST default to `dry_run: true` — never write without preview
- **Go `type_spec` nodes** map to "symbol" by default — must inspect children for `struct_type`/`interface_type` to get correct kind
- **Python class methods** live in a `block` node (not `class_body`) — method detection must check `parent.type == "block"`
- **JS `lexical_declaration`** (const/let) is NOT `variable_declaration` (var) — arrow function queries need both
- Always validate queries compile against the actual grammar before shipping — tree-sitter raises `Impossible pattern` or `Invalid node type` at Query construction time, not at match time
- **`code_symbols_tool` response shape differs for file vs directory:**
  - Single file → `{"path": ..., "language": ..., "symbol_count": N, "symbols": [...]}` — access via `result["symbols"]` directly
  - Directory → `{"results": [...], "total_symbols": N}` — each entry in `results` has `{"path": ..., "symbols": [...]}`
  - Do NOT access `result["results"][0]["symbols"]` for single-file calls — `results` key doesn't exist in that case
- **Directory vs file path**: When a tool accepts both files and directories, call `detect_language()` ONLY after checking `is_dir()`. Calling it on a directory path returns `None` and triggers a premature error. Pattern: check `is_dir()` → skip detection → iterate `rglob(f"*{ext}")` → detect per file.
- **tree-sitter core vs grammar version mismatch**: `tree-sitter` (core) and grammar packages (e.g. `tree-sitter-python`) MUST be from the same major version. Mismatched versions cause `TypeError: Expected a path or pointer for the first argument` because old core (0.21.x) expects `int` pointers while new grammars (0.25.x) return `PyCapsule`. Fix: upgrade `tree-sitter` in the Hermes venv to match grammar versions. The code uses the 0.25.x API (`Language(ptr)` single-arg, `QueryCursor(query)` constructor).
- **`LSPManager.bridges` AttributeError (FIXED 2026-04-22)**: Fixed. All `mgr.bridges` → `mgr._bridges`.
- **Module-level register_lsp_tools() (FIXED 2026-04-22)**: Removed double-registration + import crash. `__init__.py` already calls `register_lsp_tools()`.
- **pytest PYTHONPATH (FIXED 2026-04-22)**: Added `pyproject.toml` with `pythonpath = [".."]` to resolve package import.
- **Test status (verified 2026-04-22)**: 75/75 tests pass in 0.15s. 5/5 LSP tools registered. All servers functional.
- **Symbol cache shows 0 on cold start**: Expected behavior — the in-memory `_SYMBOL_CACHE` starts empty and fills as files are parsed. It persists to disk on session end (`_on_session_end`) and restores on next start. "0 parsed AST files" on first use is normal, not a bug.
- **Decorated methods invisible to anchored queries**: tree-sitter places `decorator` as a sibling *between* `class_body` and `method_definition` (e.g. `class_body → decorator → method_definition`). An anchored query `(class_body (method_definition ...))` misses decorated methods. **Fix**: use top-level `method_definition` queries and classify via parent-chain walk-up instead of anchoring inside `class_body`. Same issue in Python: `block → decorated_definition → function_definition`.
- **Python `decorated_definition` parent chain**: When a `function_definition` is captured inside a `decorated_definition`, its parent is `decorated_definition`, not `block`. Method detection must walk up through wrapper nodes: `function_definition → decorated_definition → block → class_definition`. Without the walk-up, decorated class methods get classified as top-level functions.
- **`hermes-acp` and `hermes-api-server` have explicit tool lists**: These two toolsets do NOT reference `_HERMES_CORE_TOOLS` — they have hardcoded tool arrays. When adding new tools to `_HERMES_CORE_TOOLS`, you MUST also add them to `hermes-acp` and `hermes-api-server` in `toolsets.py`, or subagents via ACP/API server won't see them.
- **Subagent toolset inheritance requires TOOLSETS dictionary entry**: Tools registered via `registry.register(toolset="code_intel")` are available in the main session but NOT in `delegate_task` subagents unless there's also a corresponding entry in the `TOOLSETS` dict in `toolsets.py`. The subagent's `_SUBAGENT_TOOLSETS` is built by iterating `TOOLSETS.items()` — if the toolset name isn't a key there, subagents never see it. **Fix:** Add a dict entry: `"code_intel": {"description": "...", "tools": ["code_symbols", "code_search", "code_refactor"], "includes": []}`. Requires gateway restart (Python module caching). **Diagnostic:** `~/.hermes/hermes-agent/venv/bin/python3 -c "from tools.delegate_tool import _SUBAGENT_TOOLSETS; print('code_intel' in _SUBAGENT_TOOLSETS)"`
- **Hermes ToolRegistry API**: `registry.get("name")` does NOT exist. Use `registry.get_entry("name")` which returns a `ToolEntry` (attribute-based, NOT subscriptable). Access via `entry.handler`, `entry.toolset`, `entry.schema` — never `entry["handler"]`. Other useful methods: `registry.get_all_tool_names()`, `registry.get_toolset_for_tool("name")`.
- **`_find_project_root()` CWD Trap (important)**: When Hermes agent starts from its own install directory (not the user project), `_find_project_root()` finds the agent's `.git` marker instead of the user's monorepo root. This causes: (a) Symbol cache always shows 0 entries, (b) LSP Bridge gets wrong workspaceFolders, (c) All cross-file resolution fails. **Fix**: configure the project root override in the Hermes config so the function uses it BEFORE walking CWD.
- **Symbol Cache persist crash**: Non-string keys (tuples from tree-sitter dedup) crash `json.dump()`. **Fix** (applied): `persist_symbol_cache()` now does try/except `json.dumps()` pre-check per entry.
- **LSP Bridge Fallback**: For the code_definition and code_references tools to work via LSP, a language server must be installed. Otherwise tools degrade to AST-based search fallback.
  - **Python**: `pyright` (recommended, via pip) or `python-lsp-server`
  - **TypeScript/JavaScript**: `typescript-language-server` + `typescript` (global npm) — auto-discovers via PATH → monorepo `node_modules/.bin` → `npx` fallback
  - **Rust**: `rust-analyzer` (via rustup component)
  - **Go**: `gopls` (`golang.org/x/tools/gopls`)
  - Plugin location: `~/.hermes/plugins/code_intel/lsp_bridge.py` (published as `rewasa/hermes-code-intel-plugin`)
- **TS LSP quirks** (typescript-language-server):
  - Needs longer `didOpen` delay (500ms vs 50ms for Python) — TS indexes the project on first open
  - `TSSERVER_PATH` env var should point to the monorepo's `node_modules/typescript/lib/tsserver.js` for correct module resolution
  - `maxTsServerMemory: 8192` is set in initializationOptions to prevent OOM on large monorepos
  - First request after cold start can be slow (5-15s) while TS indexes — subsequent requests are fast (~0.65s)
  - **`rootUri` MUST point to tsconfig directory** — `_find_tsconfig_root()` walks up to find the nearest `tsconfig.json` and uses that as bridge `rootUri`. The monorepo root is only used for `workspaceFolders`. Without this fix, TSServer only finds references within the current file (1 ref in 1 file) instead of cross-file (3-6 refs in 2-3 files). The bridge key is `(language_id, tsconfig_root)` so each app gets its own bridge instance.
  - **typeDefinition fallback for imports** — When `goto_definition` on an import identifier returns the import binding itself (same file, same line), the bridge automatically tries `textDocument/typeDefinition` which jumps to the actual class/interface definition. E.g., `import { AppModule } from './AppModule'` → `AppModule.ts:101` (the class declaration).
  - **`_normalize_locations` returns raw LSP dicts** — Format is `{"uri": ..., "range": {"start": {"line": X, "character": Y}}}` — NOT flattened `{"line": X}`. Any code checking `loc.get("line")` gets `None`/empty string. Must use `loc["range"]["start"]["line"]`. The `_location_to_dict` helper flattens this for tool output, but intermediate checks (like the typeDefinition fallback) must use the raw format.
  - **Empty first-request retry** — If `goto_definition` or `find_references` returns empty/None, the bridge retries once after 500ms sleep. TSServer sometimes needs an extra cycle after `didOpen` for large monorepos.
  - **Performance**: ~0.65s per cached request, ~1.5s for first import-definition (typeDefinition retry). Output: 270-2600 tokens depending on reference count.
- **`_auto_detect_identifier_column` keyword skipping**: When `character` param is omitted, the tool scans the line for the first identifier. Without keyword skipping, it lands on `import`/`export`/`const` etc. instead of the actual symbol name. The fix skips 40+ keywords and string literals. If auto-detect still picks wrong, explicitly pass `character` param.
- **`_location_to_dict` returns `"file"` key, not `"path"`**: The helper `_location_to_dict()` returns `{"file": ..., "line": ..., ...}` — NOT `"path"`. All consumers that destructure locations must use `d["file"]`. `code_type_definition` had a KeyError because it used `d["path"]` — fixed 2026-04-23 by adding `d["path"] = d.get("path", d.get("file", ""))`. If you add new LSP tools that use `_location_to_dict`, always map `"file"` → `"path"` or use `d["file"]`.
- **`code_diagnostics` must open_document before checking cache** (fixed 2026-04-23): The `publishDiagnostics` cache is populated asynchronously when TSServer sends notifications after `didOpen`. If `code_diagnostics` checks the cache BEFORE opening the document, it always falls back to the AST heuristic (which flags decorator imports as "unused" on NestJS/Angular files). Fix: call `bridge.open_document()` + appropriate sleep (1.0s for TS, 0.1s for others) before checking `get_cached_diagnostics()`. This ensures TSServer has time to push real diagnostics.
- **`_location_to_dict` text field**: Shows the actual target line (not first non-empty context line). Uses `min(line_0based, context_size, len(context_lines) - 1)` to handle edge case for files starting near line 0.

### Debugging LSP Issues

The plugin has comprehensive DEBUG logging built in (`lsp_bridge.py`):
- Dedicated `StreamHandler` with `propagate=False` (won't double-log via Hermes root logger)
- Format: `HH:MM:SS [LEVEL] lsp_bridge: message`
- Logs the full chain: language detection → tsconfig root → bridge create/reuse → LSP request/response with timing → typeDefinition fallback trigger
- LSP server messages (`window/logMessage`) are logged with severity mapping (Error→ERROR, Warning→WARNING)
- `publishDiagnostics` errors logged as WARNING with file:line (capped at 5 errors, 3 warnings to avoid spam)

**To verify LSP is working:**
```bash
cd ~/.hermes/plugins/code_intel && ~/.hermes/hermes-agent/venv/bin/python3 << 'EOF'
import sys; sys.path.insert(0, ".")
from lsp_bridge import code_definition_tool
import json
print(json.loads(code_definition_tool(
    path="/path/to/file.ts", line=29
)))
EOF
```
Watch for: `get_bridge: creating new bridge`, `LSP server initialized`, `typeDefinition fallback`. If you see `no server config for language_id`, the language server binary is not on PATH.
