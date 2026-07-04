# 🧠 agentiker-code-intel — Hermes Plugin

> **AST-aware code intelligence for Hermes Agent** — tree-sitter + ast-grep + LSP
> 70 tools that understand your code's *structure*, not just its text. 10–50× fewer tokens for code navigation.

> **Forked** from [`rewasa/hermes-code-intel-plugin`](https://github.com/rewasa/hermes-code-intel-plugin) — extended for [agentiker.de](https://agentiker.de) (70 Tools, 3125 Tests)



## 📋 Table of Contents

- [✨ Why?](#-why)
- [🚀 Quick Start](#-quick-start)
- [🛠 Tools](#-tools)
- [📦 Installation](#-installation)
- [🌐 Supported Languages](#-supported-languages)
- [🏗 Architecture](#-architecture)
- [🔧 How It Works](#-how-it-works)
- [🧪 Development](#-development)
- [🤝 Contributing](#-contributing)
- [🙏 Credits](#-credits)

---

## ✨ Why?

Hermes ships with `search_files` (regex grep) and `read_file` (raw text). Those work, but they're **blind to syntax** — they match comments, strings, and formatting equally. This plugin adds:

| Feature | What it does |
|---------|-------------|
| **Symbol extraction** | Get all functions, classes, methods with signatures and line numbers without reading the whole file |
| **Structural search** | Find imports, decorators, function calls, try/catch blocks by *AST node type*, not regex |
| **Safe refactoring** | Rename patterns, wrap functions, add parameters across files — **dry-run by default** |
| **Go-to-definition** | LSP-powered jump to symbol definition (falls back to AST) |
| **Find references** | LSP-powered cross-file usage search (falls back to AST) |
| **Blast radius** | What breaks if you change this symbol? Transitive callers + test coverage |
| **PR impact** | Git diff + call graph + test coverage + reviewer suggestions |
| **Hot paths** | Rank files by transitive import count via ImportGraph |
| **Complexity** | Per-function cyclomatic complexity with rank A-E |
| **Cycle detection** | Find circular imports via Tarjan's SCC algorithm |
| **Knowledge graph** | SQLite-persistent import graph — callers, callees, health metrics |

The result: **editor-grade code intelligence** in the terminal — same approach as Neovim 0.5+, Zed, and Helix.

---

## 🚀 Quick Start

### Installation
```bash
cd ~/.hermes/plugins
git clone https://github.com/IVRZ-da/agentiker-code-intel.git
cd agentiker-code-intel
pip install -e .
```

### Usage
```python
# Symbole einer Datei abrufen (ohne die ganze Datei zu lesen)
code_symbols(path="src/service.py")

# Strukturelle Suche — finde alle Imports
code_search(path="src/", preset="imports")

# Go-to-Definition
code_definition(path="src/service.py", line=42)

# Alle Referenzen eines Symbols finden
code_references(path="src/service.py", line=42)

# Code-Risiko-Analyse
code_dependency_risk(path="src/")
code_blast_radius(path="src/service.py", line=42)

# Impact-Analyse vor Refactoring
code_impact(path="src/service.py", line=42)
```

### Cross-Plugin: Feed Scout Bug-Hunt from code-intel
```python
# code-intel finds dead code → scout scans automatically
analysis_deadcode(path="src/")
bug_hunt_scan(session_id="...", patterns=["analysis"])
```

---

## 🛠 Tools

<!-- README_AUTO -->

[![Version](https://img.shields.io/badge/version-0.6.16-blue.svg)]() [![Tests](https://img.shields.io/badge/tests-3140%20tests-green.svg)]() [![License](https://img.shields.io/badge/license-MIT-green.svg)]() [![Languages](https://img.shields.io/badge/languages-9-orange.svg)]()

**Version:** 0.6.16

**Tests:** 3140 tests

**Tools (70):**

**Profiles:**

| Profile | Tools | Description |
|---------|-------|-------------|
| `all` | 70 | All 70 tools (default) |
| `core` | 22 | AST core tools: symbols, search, definition, references |
| `search` | 15 | Code search and analysis: search_by_error, duplicates, hot_paths |
| `edit` | 10 | AST-based code editing: replace_body, safe_delete, insert |
| `lsp` | 25 | LSP integration: definition, references, diagnostics, completion |

**Supported Languages:** c, cpp, go, java, javascript, python, rust, tsx, typescript

| Tool | Description |
|------|-------------|
| `code_action` | LSP code actions: auto-fixes, refactoring suggestions, quick-fixes |
| `code_batch_refactor` | ast-grep bulk refactoring across multiple files (dry-run) |
| `code_blast_radius` | What breaks if you change this symbol? Callers + tests |
| `code_call_hierarchy` | LSP call hierarchy: incoming and outgoing calls |
| `code_callees` | Find all callees of a symbol (transitive) |
| `code_callers` | Find all callers of a symbol (transitive) |
| `code_capsule` | Compact symbol overview: signature, doc, references, imports |
| `code_code_lens` | LSP code lens: run/debug/test links above functions |
| `code_completion` | LSP completion: auto-completion at cursor position |
| `code_complexity` | Cyclomatic complexity per function with rank A-E |
| `code_cycle_detector` | Find circular import chains with Tarjan's SCC algorithm |
| `code_definition` | LSP-powered go-to-definition with AST fallback |
| `code_dependency_graph` | Visual dependency graph as Mermaid diagram |
| `code_dependency_risk` | Rate dependency risks (score 0-10) |
| `code_diagnostics` | LSP diagnostics: errors, warnings, hints for a file |
| `code_diagram_symbol` | Generate ASCII/Mermaid diagrams for functions and classes |
| `code_diff_analysis` | Compare two git refs: complexity delta + blast radius |
| `code_docstring_generate` | Generate docstring template from AST signature |
| `code_document_links` | LSP document links: clickable links in docs and comments |
| `code_document_symbols` | LSP document symbols: all symbols in the current file |
| `code_duplicates` | Find duplicate or similar code blocks via AST comparison |
| `code_explain` | Structured symbol explanation with complexity + callers |
| `code_export` | Export symbol index as JSON/Markdown for documentation |
| `code_folding_range` | LSP folding ranges: code fold regions for a file |
| `code_format` | LSP formatting: auto-format a file using language server |
| `code_generate_tests` | Generate test scaffold from a function signature |
| `code_git_blame` | Per-line git blame for a file |
| `code_git_diff_file` | Show uncommitted git diff for a file |
| `code_git_log_symbol` | Git log for a specific symbol (author, date, message) |
| `code_graph_query` | Query the Knowledge Graph: callers, callees, hot paths, cycles |
| `code_highlight` | LSP document highlight: all occurrences of a symbol in a file |
| `code_hot_paths` | Rank files by transitive import frequency |
| `code_hover` | LSP hover: type information and docstring at cursor |
| `code_impact` | Impact analysis before refactoring — blast radius + test coverage |
| `code_implementations` | LSP implementations: find all implementations of an interface |
| `code_index` | Build a persistent Knowledge Graph (SQLite) for a project |
| `code_inlay_hints` | LSP inlay hints: type hints inline (parameters, variables) |
| `code_inline_values` | LSP inline values: value display for variables at debug time |
| `code_insert_after` | Insert code AFTER a symbol definition (AST-based) |
| `code_insert_before` | Insert code BEFORE a symbol definition (AST-based) |
| `code_linked_editing` | LSP linked editing: coupled editing (JSX tags, CSS classes) |
| `code_merge_conflict_finder` | Find merge conflict markers (<<<<<<<, =======) |
| `code_metrics` | Aggregated project metrics: LOC, files, comment ratio |
| `code_migration` | YAML-based bulk migrations across a project |
| `code_move` | Move a symbol between files via AST extraction |
| `code_overview` | Compact symbol overview of a file as tree view |
| `code_pr_impact` | PR impact analysis: diff + call graph + test coverage |
| `code_prepare_rename` | LSP prepare rename: check if a symbol can be renamed |
| `code_query` | Smart query router — auto-selects the best tool for your intent |
| `code_refactor` | Structural search-and-replace with ast-grep (dry-run by default) |
| `code_references` | LSP-powered cross-file reference search with AST fallback |
| `code_rename` | LSP-powered symbol rename across the entire project |
| `code_replace_body` | Replace the entire definition of a symbol (AST-based) |
| `code_review_assistant` | Automated code review between git refs (diff + security) |
| `code_safe_delete` | Delete a symbol ONLY if it has no external references |
| `code_search` | AST-based structural search — find imports, decorators, try/catch blocks |
| `code_search_by_error` | Find all places that handle a specific error type |
| `code_security_scan` | Security scan: hardcoded secrets, SQL injection, path traversal |
| `code_selection_range` | LSP selection ranges: hierarchical selection regions |
| `code_semantic_tokens` | LSP semantic tokens: colored syntax highlighting via LSP |
| `code_signatures` | LSP signature help: parameter info for function calls |
| `code_symbols` | Extract functions, classes, and methods via AST — no read_file needed |
| `code_tests_for_symbol` | Find tests that cover a specific symbol |
| `code_timeline` | Track symbol evolution across git history |
| `code_todo_finder` | Find TODO/FIXME/HACK/KNOWN-BUG comments in codebase |
| `code_type_definition` | LSP type definition: jump to type definition of a symbol |
| `code_type_hierarchy` | LSP type hierarchy: subtypes and supertypes of a type |
| `code_unused_finder` | Find unused imports and unused functions |
| `code_workspace_summary` | Compact monorepo overview: apps, packages, dependencies |
| `code_workspace_symbols` | LSP workspace symbol search across the entire project |

### Recent Changelog

## [0.6.16] — 2026-07-04

### ⚡ Performance / Token-Optimierung

- **Tool-Descriptions gekürzt** — 19 längste SCHEMA descriptions von durchschnittlich 77 auf 60 Zeichen reduziert. Spart ~470 Zeichen (~150 Tokens) pro Session. Betrifft: code_query, code_document_links, code_document_symbols, code_metrics, code_call_hierarchy, code_semantic_tokens, code_safe_delete, code_complexity, code_replace_body, code_type_hierarchy, code_code_lens, code_pr_impact, code_highlight, code_folding_range, code_insert_before, code_blast_radius, code_insert_after, code_inline_values, code_move.

<!-- END README_AUTO -->

## ⚠️ Limitations

| Limitation | Description |
|-----------|-------------|
| **LSP Dependency** | Tools like `code_rename`, `code_hover`, `code_diagnostics`, `code_signatures` require an LSP server. Without a server they fall back to AST/text analysis. |
| **C/C++/Java** | No LSP support (tree-sitter + ast-grep only for basic operations) |
| **Cold Start** | First LSP tool call ~1.5s (server process needs to initialize). Subsequent calls are faster (~0.65s for cached LSP) |
| **Batch Refactoring** | `code_batch_refactor` creates `.bak` backups — check disk space for 500+ files |
| **Subagent Compatibility** | See Subagent Integration section below |

---

### Subagent Integration

All code_intel tools are automatically available in delegated subagents (`delegate_task`):

```python
# Subagent hat automatisch Zugriff auf alle 70 code_intel Tools
delegate_task(goal="Refactoriere die User-Service Klasse",
              context="...",
              toolsets=["agentiker_code_intel", "terminal", "file"])
```

**Steering hint:** Subagents are instructed to use `code_symbols` instead of `read_file` for code understanding — saves 10-50× tokens.

---

## 📦 Installation

### 1. Plugin aktivieren

Enable in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - code_intel
```

Requires Hermes restart (`/new` or daemon restart).

### 2. Dependencies installieren

```bash
# Ins Hermes-Venv installieren (vom Plugin-Ordner aus)
cd ~/.hermes/plugins/agentiker-code-intel
~/.hermes/hermes-agent/venv/bin/pip install -e .

# Option B — Via Install-Script
./scripts/install-deps.sh

# Option C — Manuell (nur Dependencies)
python3 -m pip install tree-sitter tree-sitter-languages ast-grep-py rich PyYAML
```

**Dependencies:** `tree-sitter>=0.24.0`, `ast-grep-py>=0.37.0`, `rich>=13.0`, `PyYAML>=6.0`

### 3. LSP Server (optional, for full semantic features)

LSP tools (`code_definition`, `code_references`) work without a server — they fall back to AST analysis. For full support:

```bash
# Python
pip install pyright

# TypeScript / JavaScript
npm install -g typescript-language-server typescript

# Rust
rustup component add rust-analyzer

# Go
go install golang.org/x/tools/gopls@latest
```

Das Plugin erkennt Server automatisch via PATH, `node_modules/.bin` und `npx`-Fallback.

### 4. Monorepo Support

Automatic detection of `pnpm-workspace.yaml`, `nx.json`, `lerna.json` — workspace folders are sent to the LSP server for cross-workspace type resolution. No configuration needed.

---

## 🌐 Supported Languages

| Language | Extensions | Tree-sitter | ast-grep | LSP |
|----------|-----------|:-----------:|:--------:|:---:|
| Python | `.py`, `.pyi` | ✅ | ✅ | ✅ (pyright/pylsp) |
| JavaScript | `.js`, `.jsx` | ✅ | ✅ | ✅ |
| TypeScript | `.ts` | ✅ | ✅ | ✅ (tsls) |
| TSX | `.tsx` | ✅ | ✅ | ✅ (tsls) |
| Rust | `.rs` | ✅ | ✅ | ✅ (rust-analyzer) |
| Go | `.go` | ✅ | ✅ | ✅ (gopls) |
| Java | `.java` | ✅ | ✅ | — |
| C | `.c`, `.h` | ✅ | — | — |
| C++ | `.cpp` | ✅ | — | — |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    code_intel Plugin                         │
├──────────────────────────┬──────────────────────────────────┤
│   tree-sitter / AST      │   LSP Bridge                     │
│   (schnell, offline)     │   (semantisch, online)            │
│                          │                                  │
│  ┌────────────────────┐ │ ┌──────────────────────────────┐  │
│  │ code_symbols       │ │ │ code_definition              │  │
│  │ code_search        │ │ │ code_references              │  │
│  │ code_refactor      │ │ │ code_diagnostics             │  │
│  │ code_capsule       │ │ │ code_hover                   │  │
│  │ code_complexity    │ │ │ code_call_hierarchy          │  │
│  │ code_duplicates    │ │ │ code_type_hierarchy          │  │
│  │ code_unused_finder │ │ │ code_rename                  │  │
│  │ ...                │ │ │ ...                          │  │
│  └────────────────────┘ │ └──────────────────────────────┘  │
├──────────────────────────┴──────────────────────────────────┤
│                    Knowledge Graph                           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ code_index → SQLite-persistenter ImportGraph          │   │
│  │ code_graph_query: callers, callees, hot_paths,       │   │
│  │   cycles, health, summary                            │   │
│  └──────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                    Subagent Integration                      │
│  • Automatische Injektion in _SUBAGENT_TOOLSETS             │
│  • Jeder delegate_task-Spawn hat code_intel Tools           │
│  • Steering-Hint: "Nutze code_symbols statt read_file"      │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔧 How It Works

### LSP Bridge Performance

| Fix | Before | After | Impact |
|-----|--------|-------|--------|
| `stderr=subprocess.DEVNULL` | Pipe buffer (64KB) fills with warnings → Deadlock | Silenced | Cold starts never hang |
| `PYTHONWARNINGS=ignore` | pylsp schreibt ~200KB Deprecation-Warnings | Suppressed | 2× faster Python LSP init |
| `_LSP_INIT_TIMEOUT=15s` | 60s auf dead server → Agent blockt | 15s fast retry | Agent reagiert sofort |
| `_LSP_REQUEST_TIMEOUT=15s` | 30s auf hung request (tsserver parsing giant file) | 15s | Quicker fallback to AST |

LSP bridges are keyed by `(language_id, workspace_root)` with LRU pool (max 8 concurrent). Fallback chain: first available server wins.

### Symbol Caching

AST results are cached in memory (`OrderedDict`, max 2000 entries, LRU). Auto-cleared at session end.

### 🩺 Health Check Script

Das Plugin shiped `scripts/health_check.py` — 10 Assertions: Tool-Registry, tree-sitter extraktion, AST search, ast-grep refactoring, LSP definition + references, Schema-Validierung.

```bash
# Manuell
~/.hermes/hermes-agent/venv/bin/python3 \
  ~/.hermes/plugins/code_intel/scripts/health_check.py

# Als cron (empfohlen) — stumm bei Gesundheit:
hermes cronjob create \
  --name "code_intel_health" \
  --schedule "every 60m" \
  --script "scripts/health_check.py" \
  --no-agent
```

### Bundled Skill

Das Plugin registriert automatisch den Skill `code_intel:native-code-intelligence`:

```
skill_view("code_intel:native-code-intelligence")
```

Contains: Mandatory Workflows, Tool Selection Rules, Quality Guardrails, IDE Feature Coverage Map.

### Slash Command

Nach Aktivierung: `/code-intel status`, `/code-intel clear`, `/code-intel help`

### LSP Benchmarks (TypeScript, pnpm monorepo, 60 workspaces)

| Tool | Scenario | Time | Output Tokens |
|------|----------|------|---------------|
| `code_definition` | Import binding → typeDefinition fallback | ~1.5s (first) | ~272 |
| `code_definition` | Cached request | ~0.65s | ~290 |
| `code_references` | Small class (~3 refs) | ~0.67s | ~1,362 |
| `code_references` | Medium class (~6 refs) | ~0.66s | ~2,610 |

---

## 🧪 Development

```bash
cd ~/.hermes/plugins/agentiker-code-intel

# Run tests
PYTHONPATH=~/.hermes/plugins ~/.hermes/hermes-agent/venv/bin/python3 \
  -m pytest -q --tb=short

# Einzelner Test
PYTHONPATH=~/.hermes/plugins ~/.hermes/hermes-agent/venv/bin/python3 \
  -m pytest tests/test_code_intel.py::test_extract_symbols_python -v
```

**Pre-Commit Hook:** Automatischer Syntax-Check + Tests vor jedem Commit.

```bash
git config core.hooksPath .githooks
```

### CHANGELOG

Jeder Release bekommt einen Eintrag in `CHANGELOG.md`:
- `[added]` für neue Features
- `[changed]` für Änderungen
- `[fixed]` für Bugfixes
- `[removed]` für Entfernungen

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch
3. Add tests for your changes
4. Run `PYTHONPATH=~/.hermes/plugins python3 -m pytest -q` — all tests green
5. Open a PR

---

## 📄 License

[MIT](LICENSE) — use it however you like.

---

## 🙏 Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — the plugin system this builds on
- [rewasa](https://github.com/rewasa) — original author of upstream `hermes-code-intel-plugin`
- [tree-sitter](https://tree-sitter.github.io/) — incremental parsing system
- [ast-grep](https://ast-grep.github.io/) — pattern-based code search and replacement
- [pyright](https://github.com/microsoft/pyright) — Python LSP server (fallback)
- [typescript-language-server](https://github.com/typescript-language-server/typescript-language-server) — TypeScript/JavaScript LSP server
