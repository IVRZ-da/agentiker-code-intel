# Phase 5: Code-Intelligence Layer Enhancement

**Goal:** Evolve from a "collection of AST/LSP tools" to a "compact Code-Intelligence Layer" — single-call insights instead of multi-step exploration.

**Challenge Methodology:** Before implementing new tools, evaluate each proposal against three criteria: (1) Does it reduce token consumption? (2) Does it eliminate multi-step workflows? (3) Is the fallback strategy clear and testable? Tasks that fail on these grounds were rejected below.

## Phase 5a — Implementation Tasks (COMPLETED ✅)

| Task | File | What | Status |
|------|------|------|--------|
| **code_capsule** | `code_intel.py` | One-shot symbol view: signature, short description, definition, top references, imports, optional tests. | ✅ Done |
| **code_callers** | `lsp_bridge.py` | AST-based caller recognition + LSP callHierarchy fallback for cross-file. | ✅ Done |
| **code_diagnostics** | `lsp_bridge.py` | Unified view of LSP diagnostics (Errors/Warnings). | ✅ Done |
| **code_callees** | `lsp_bridge.py` | AST-based callee recognition (what does this function call?). | ✅ Done |
| **code_impact** | `code_intel.py` | Blast radius analysis: affected files, risk level, confidence, test files. | ✅ Done (promoted from rejected B3) |
| **code_tests_for_symbol** | `code_intel.py` | Find, prioritize, and condense tests for a given symbol. | ✅ Done |
| **code_workspace_summary** | `code_intel.py` | Monorepo overview: apps, packages, root markers, entry points. | ✅ Done (promoted from rejected B2) |
| **code_query** | `code_intel.py` | Smart query router — describe intent, get best tool. | ✅ Done (promoted from rejected C1) |
| **code_references summary_mode** | `lsp_bridge.py` | `group_by_file` + compact reference output. | ✅ Done |
| **Extended tool-steering hints** | `__init__.py` | Steering hints for all code_intel tools. | ✅ Done |
| **Expanded /code-intel status** | `__init__.py` | LSP server health, active bridges, workspace roots, cache stats. | ✅ Done (Bug fix 2026-04-22: `mgr.bridges` → `mgr._bridges`) |

## Phase 5b — Rejected Tasks (with rationale)

| Task | Status | Reasoning |
|------|--------|-----------|
| ~~B1 Persistent Project Index~~ | **Rejected** | Session-based LRU-Caching (2000 entries, mtime-based) is sufficient. Persistent Disk-Index = SQLite/JSON with invalidation logic — massive effort, limited gain. |
| ~~C2 pre_llm_call Context Builder~~ | **Rejected** | Fires on ALL questions. Automatic code injection without request is overwhelming. |
| ~~C3 Safe-refactor Workflow~~ | **Rejected** | **Meta-Workflow / Skill**, not a plugin tool. Uses existing tools. Better implemented as skill. |
| ~~C4 Symbol Graph Export~~ | **Rejected** | Graph output is for human readers, not LLMs. Only useful once a visualization tool exists. |

## Phase 6 — Editor-Informed Gap Analysis & Roadmap

Based on comparison with Neovim 0.5+, Zed, Helix, Emacs (tree-sitter + lsp-mode), and GitHub semantic search:

### 6a — Shipped ✅ (validated by ≥2 editors)

| Tool | LSP Method | What | Editors that have it | Status |
|------|-----------|------|---------------------|--------|
| **code_rename** | `textDocument/rename` | Semantic rename across files | Neovim, Zed, Emacs, Helix | ✅ Shipped 04-22 |
| **code_action** | `textDocument/codeAction` | Quick fixes, organize imports, extract method | Neovim, Zed, Emacs | ✅ Shipped 04-23 |
| **code_workspace_symbols** | `workspace/symbol` | Project-wide symbol search | Neovim, Zed, Emacs, Helix | ✅ Shipped 04-22 |
| **code_signatures** | `textDocument/signatureHelp` | Parameter info for function calls | Neovim, Emacs, Zed | ✅ Shipped 04-23 |
| **code_hover** | `textDocument/hover` | Type signature + docstring at cursor | All editors | ✅ Shipped 04-23 |
| **code_type_definition** | `textDocument/typeDefinition` | Jump to type declaration (not value) | Neovim, Zed, Emacs, Helix | ✅ Shipped 04-23 |

### 6b — Not Planned (Validated as unnecessary for agents)

| Feature | Why Skip |
|---------|----------|
| **textobjects** (select function/class as unit) | `code_symbols` returns line ranges — sufficient for an agent |
| **Inlay hints** | Visual feature, useless for agents |
| **Completions** (textDocument/completion) | Agent generates code, doesn't type it; completions are latency overhead |
| **Indexed cross-repo search** | Out of scope — agent works per-repo |
| **Code lens** | Visual UI feature |

### 6c — Full 19-Tool Audit (2026-07-11, Opus 4.7)

Every tool tested live against representative TypeScript controller/service files in a production monorepo:

| # | Tool | Test Target | Result |
|---|------|-------------|--------|
| 1 | `code_symbols` | ReportsController dir | ✅ 13 classes |
| 2 | `code_workspace_summary` | Monorepo root | ✅ 30 apps + 11 packages |
| 3 | `code_workspace_symbols` | "ReportsService" | ✅ 0 hits (correct) |
| 4 | `code_query` | "find_usage" | ✅ → code_references |
| 5 | `code_search` | imports HubSpotService | ✅ 6 AST hits |
| 6 | `code_capsule` | HubSpotService.ts:4 | ✅ sig + definition |
| 7 | `code_diagnostics` | ReportsController | ✅ 2 warnings (AST fallback) |
| 8 | `code_callers` | getExternalUserAgentHeader | ✅ 4 callers, 3 files |
| 9 | `code_callees` | ReportsController method | ✅ 42 callees |
| 10 | `code_hover` | ReportsController:8 | ✅ type signature |
| 11 | `code_signatures` | ReportsController:132 | ✅ correct hint |
| 12 | `code_definition` | HubSpotService.ts:4 | ✅ goes to def |
| 13 | `code_references` | ReportsController:8 | ✅ cross-file grouped |
| 14 | `code_type_definition` | DealsController:10 | ✅ jumps to interface |
| 15 | `code_impact` | ReportsController:125 | ✅ 2 files, low risk |
| 16 | `code_tests_for_symbol` | ReportsController | ✅ 0 tests (correct) |
| 17 | `code_refactor` | (dry_run default) | ✅ verified |
| 18 | `code_rename` | ExportQueryDto → V2 | ✅ 2 files, 2 edits |
| 19 | `code_action` | ReportsController:58 | ✅ no actions (correct) |

**All 19 tools verified functional. Zero failures.**

### 6d — Gap Analysis vs Editors (Final, 2026-07-11)

| Editor Feature | Hermes Equivalent | Gap? | Action |
|----------------|-------------------|------|--------|
| TS syntax highlighting / folding | `code_symbols` (line ranges) | None — agent doesn't render UI | — |
| TS textobjects (select body) | `code_symbols` + `read_file(offset,limit)` | None — range-based reads equivalent | — |
| LSP go-to-def / references / rename | `code_definition/code_references/code_rename` | None — full coverage | — |
| LSP diagnostics + quick-fix | `code_diagnostics/code_action` | None — full coverage | — |
| LSP hover / signature help | `code_hover/code_signatures` | None — full coverage | — |
| TS structural search | `code_search` (TS queries + presets) | None — full coverage | — |
| TS structural replace | `code_refactor` (ast-grep) | None — full coverage | — |
| Workspace symbol search (Cmd+T) | `code_workspace_symbols` | None — full coverage | — |
| Call hierarchy (in/out) | `code_callers/code_callees` | None — full coverage | — |
| Code formatting | — | **Minor** — no `textDocument/formatting` | Low — useful for cleanup pass, not blocking |
| Document highlights | — | **Minor** — `textDocument/documentHighlight` unused | Very low — `code_references` supersedes |
| Selection ranges (expand/shrink) | — | **Minor** — no TS range expansion | Low — `patch` handles most cases |
| Semantic tokens | — | Not applicable — visual-only | — |

**Result: Full IDE-parity for agent use-cases.** Only 3 minor gaps (formatting, doc highlights, selection ranges) remain, none blocking.

### 6e — Stability & Known Issues

| Issue | Status |
|-------|--------|
| `mgr.bridges` AttributeError | ✅ Fixed (`mgr._bridges`) |
| Symbol cache 0 on cold start | ✅ Documented as expected |
| `code_type_definition` KeyError `"path"` | ✅ Fixed (map `file` → `path`) |
| `code_diagnostics` AST-fallback false positives | ✅ Documented + mitigated (open_document first) |
| `code_workspace_summary` `language: null` | 🔲 Open (low priority) |

## Design Principles

1. **JSON-first Tool Outputs**: Every tool return should be small, consistent, and stable so Hermes can iterate reliably.
2. **Fallback Strategy Explicit**: LSP → AST → text search — or task-dependent reversed order; document and test.
3. **Dry-run as Default**: As `code_refactor` already does correctly; extend to any new editing tool.
4. **Token Budget as Feature**: Not just "works", but "delivers the smallest useful answer".
5. **Monorepo-first Thinking**: Workspace detection, nearest-tsconfig, pooled LSP bridges, cross-workspace resolution — new tools should build on these, not bypass them.
6. **Hybrid architecture validated**: All 5 major editors (Neovim, Zed, Helix, Emacs, GitHub) use Tree-sitter for syntax/structure + LSP for semantics. Hermes does the same. ✅