---
name: native-code-intelligence
description: Native tree-sitter + ast-grep code intelligence tools for Hermes agent. Replaces deprecated LSP MCP with in-process AST parsing. 39 tools (21 AST + 18 LSP), 1291+ tests.
---

# Native Code Intelligence Tools (agentiker-code-intel-plugin v0.29.00)

> 39 Tools (21 AST + 18 LSP) — Python, TS/JS/TSX/JSX, Rust, Go, Java, C/C++

## Context

Replaced deprecated `lsp-mcp-server` with native code intelligence.
No external servers (no MCP). Tree-sitter + ast-grep-py directly embedded in Hermes.
LSP support via pyright/pylsp (Python), typescript-language-server (TS/JS),
rust-analyzer (Rust), gopls (Go) with automatic AST fallback.

**Stack:** `tree-sitter` (>=0.24.0) + `tree-sitter-languages` + `ast-grep-py` (>=0.37.0)

## 🚨 MANDATORY WORKFLOWS (use BEFORE read_file/patch)

### Workflow 0: Staleness Check (FIRST CALL of any code task)

Before relying on newer LSP tools, verify the gateway isn't holding stale plugin code.
Cheapest probe: call `code_workspace_summary` on the target monorepo and check
that known app directories appear in `apps[]` (NOT `packages[]`). If they're
misclassified, the gateway predates 2026-04-23 and newer LSP tools may also be
missing — tell the user, continue with the 13 stable tools.

### Workflow A: Writing NEW Code in an Existing File
1. `code_workspace_summary` (first time in repo) → understand monorepo layout
2. `code_symbols` on target file → find insertion point, see existing conventions
3. `code_search` (preset=`imports` or `function_calls`) → check existing utilities to reuse
4. `code_capsule` on any function/class you'll interact with → signature + usage
5. Write code via `patch` or `write_file`
6. `code_diagnostics` on the modified file → catch errors immediately
7. `code_search` on related test files → run/extend tests

### Workflow B: Refactoring Existing Code
1. `code_capsule` on target symbol → signature, refs, usage
2. `code_impact` → blast radius (files affected, risk level)
3. `code_tests_for_symbol` → which tests cover this?
4. `code_callers` + `code_callees` → both directions of call graph
5. Choose transform: `code_rename` (single symbol) or `code_refactor` (pattern-based) with `dry_run=true`
6. Apply with `dry_run=false`
7. `code_diagnostics` post-change → verify nothing broke
8. Run tests from step 3
9. `code_references(group_by_file=true)` → confirm 0 stale refs

### Workflow C: Investigating Unknown Code
1. `code_symbols` on file/dir → map of what exists
2. `code_capsule` on interesting symbol → one-shot context (replaces 4 calls)
3. `code_definition` only if capsule's definition field is null (cross-file unresolved)

### Workflow D: Quality Gate Before Commit
1. `code_diagnostics` on every changed file → 0 errors
2. `code_impact` on changed symbols → if `risk_level=high`, ensure tests exist

### Workflow E: Code Quality Guardrails (MANDATORY for refactor + new code)

**🔒 Diagnostics-Gate (run after EVERY write/patch/refactor):**
```
code_diagnostics(path=<changed_file>) → if errors > 0: revert or fix immediately.
```

**For NEW code:** code_search → code_capsule → code_signatures → code_hover → code_symbols → Diagnostics-Gate → code_action (organizeImports) → code_search (verify discoverable).

**For REFACTORS:** code_impact FIRST → code_callers → choose transform (code_rename/code_refactor/code_action) → dry_run preview → apply → Diagnostics-Gate on ALL changed files → code_action(organizeImports) → run tests → code_references (0 stale refs).

**For NAVIGATION:** `code_workspace_symbols(query, kind)` INSTEAD of `search_files`.

### Tool-Selection Rules (HARD)

| If you're about to do... | STOP. Use this instead. |
|--------------------------|-------------------------|
| `read_file` on a file >300 lines just to find a function | `code_symbols` (~90% token savings) |
| `read_file` to understand a class | `code_capsule` (sig + refs + imports in one call) |
| `search_files` regex for `function foo(`, `class Bar`, etc. | `code_search` preset (no false positives in comments/strings) |
| `search_files` to find usages of a symbol | `code_references(group_by_file=true)` |
| `patch` with `replace_all=true` for a rename | `code_refactor` (AST-safe, syntax-validated) |
| Multi-file rename via `delegate_task` + grep | `code_refactor` with directory path + `file_glob` |
| Manual blast-radius assessment | `code_impact` (one call) |
| "Does test X cover this?" | `code_tests_for_symbol` |

### Hard NO list
- ❌ `patch(replace_all=true)` for renames → use `code_rename` or `code_refactor`
- ❌ `read_file` → `write_file` for reorganization → use `code_refactor`
- ❌ Skipping `code_diagnostics` — 0-error gate or revert
- ❌ Adding new util when `code_search` shows an equivalent exists

## Analysis Plugin Integration (8 wrapping tools)

See `skill_view("analysis-plugin")` for the 8 analysis tools that wrap code-intel:
`analysis_inspect`, `analysis_architecture`, `analysis_deadcode`, `analysis_report`,
`analysis_diff`, `analysis_trend`, `analysis_watch`, `analysis_graph`.

## References

- **Tool Reference (39 tools detailed):** `references/tool-reference.md`
- **Verification & Pitfalls:** `references/verification.md`
- **Phase 5 Roadmap:** `references/phase5-roadmap.md`
