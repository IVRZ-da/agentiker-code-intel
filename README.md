# 🧠 agentiker-code-intel — Hermes Plugin

> **AST-aware code intelligence for Hermes Agent** — tree-sitter + ast-grep + LSP
> 70 tools that understand your code's *structure*, not just its text. 10–50× fewer tokens for code navigation.

[![Version](https://img.shields.io/badge/version-0.6.2-blue.svg)]()
[![Tests](https://img.shields.io/badge/tests-1315-green.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()
[![Languages](https://img.shields.io/badge/languages-9-orange.svg)]()

> **Fork** von [`rewasa/hermes-code-intel-plugin`](https://github.com/rewasa/hermes-code-intel-plugin) — customized for [agentiker.de](https://agentiker.de)

---

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

---

## 🛠 Tools

<!-- README_AUTO -->

[![Version](https://img.shields.io/badge/version-0.6.10-blue.svg)]() [![Tests](https://img.shields.io/badge/tests-2424%20tests-green.svg)]() [![License](https://img.shields.io/badge/license-MIT-green.svg)]() [![Languages](https://img.shields.io/badge/languages-9-orange.svg)]()

**Version:** 0.6.10

**Tests:** 2424 tests

**Tools (70):**

**Profiles:**

| Profile | Tools | Description |
|---------|-------|-------------|
| `all` | 70 |  |
| `core` | 22 |  |
| `search` | 15 |  |
| `edit` | 10 |  |
| `lsp` | 25 |  |

**Supported Languages:** c, cpp, go, java, javascript, python, rust, tsx, typescript

| Tool | Description |
|------|-------------|
| `code_action` | — |
| `code_batch_refactor` | — |
| `code_blast_radius` | — |
| `code_call_hierarchy` | — |
| `code_callees` | — |
| `code_callers` | — |
| `code_capsule` | — |
| `code_code_lens` | — |
| `code_completion` | — |
| `code_complexity` | — |
| `code_cycle_detector` | — |
| `code_definition` | Cached request |
| `code_dependency_graph` | — |
| `code_dependency_risk` | — |
| `code_diagnostics` | — |
| `code_diagram_symbol` | — |
| `code_diff_analysis` | — |
| `code_docstring_generate` | — |
| `code_document_links` | — |
| `code_document_symbols` | — |
| `code_duplicates` | — |
| `code_explain` | — |
| `code_export` | — |
| `code_folding_range` | — |
| `code_format` | — |
| `code_generate_tests` | — |
| `code_git_blame` | — |
| `code_git_diff_file` | — |
| `code_git_log_symbol` | — |
| `code_graph_query` | — |
| `code_highlight` | — |
| `code_hot_paths` | — |
| `code_hover` | — |
| `code_impact` | — |
| `code_implementations` | — |
| `code_index` | — |
| `code_inlay_hints` | — |
| `code_inline_values` | — |
| `code_insert_after` | — |
| `code_insert_before` | — |
| `code_linked_editing` | — |
| `code_merge_conflict_finder` | — |
| `code_metrics` | — |
| `code_migration` | — |
| `code_move` | — |
| `code_overview` | — |
| `code_pr_impact` | — |
| `code_prepare_rename` | — |
| `code_query` | — |
| `code_refactor` | — |
| `code_references` | Medium class (~6 refs) |
| `code_rename` | — |
| `code_replace_body` | — |
| `code_review_assistant` | — |
| `code_safe_delete` | — |
| `code_search` | — |
| `code_search_by_error` | — |
| `code_security_scan` | — |
| `code_selection_range` | — |
| `code_semantic_tokens` | — |
| `code_signatures` | — |
| `code_symbols` | — |
| `code_tests_for_symbol` | — |
| `code_timeline` | — |
| `code_todo_finder` | — |
| `code_type_definition` | — |
| `code_type_hierarchy` | — |
| `code_unused_finder` | — |
| `code_workspace_summary` | — |
| `code_workspace_symbols` | — |

### Recent Changelog

## [0.6.10] — 2026-06-25

### 🐛 Bug-Hunt Fixes (3 Silent Catches)

- **P2: hooks.py** — 2× `except Exception: pass` in pre_llm_call Hook durch `logger.debug()` ersetzt
- **P3: tools/diagram.py** — `except Exception: pass` in Column-Autodetektion durch `logger.debug()` ersetzt

### 🧪 Coverage Campaign (+176 Tests, Gesamt ~73%)

| Modul | Vorher | Nachher |
|-------|--------|---------|
| tools/security.py | 17% | **99%** (83 neue Tests) |
| tools/symbols.py | 36% | **88%** (65 neue Tests) |
| tools/impact.py | 53% | **84%** |
| tools/ast_edit.py | 67% | **70%** |
| tools/type_hierarchy.py | 8% | **34%** |
| tools/testgen.py | 8% | **61%** |

### 🔧 Complexity-Refactoring (3 Hotspots)


## [0.6.9] — 2026-06-25

### Coverage-Offensive

- **Coverage-Jagd**: Mehrere Module auf >90% gebracht
- **Config**: .coveragerc + fail_under=69 für Pre-Commit Coverage-Gate

## [0.6.8] — 2026-06-25

### Coverage-Measurement eingerichtet

- **Coverage-Infrastruktur**: .coveragerc mit source/omit/show_missing/fail_under
- **Pre-Commit Check #13**: Coverage-Gate (non-blocking, >=60%)

<!-- END README_AUTO -->

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

### 3. LSP-Server (optional, für volle semantische Features)

LSP-Tools (`code_definition`, `code_references`) funktionieren ohne Server — sie fallen auf AST-Analyse zurück. Für volle Unterstützung:

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

Automatische Erkennung von `pnpm-workspace.yaml`, `nx.json`, `lerna.json` — Workspace-Folder werden an den LSP-Server übermittelt für Cross-Workspace-Type-Resolution. Keine Konfiguration nötig.

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
| `stderr=subprocess.DEVNULL` | Pipe buffer (64KB) füllt sich mit Warnings → Deadlock | Silenced | Cold starts never hang |
| `PYTHONWARNINGS=ignore` | pylsp schreibt ~200KB Deprecation-Warnings | Suppressed | 2× faster Python LSP init |
| `_LSP_INIT_TIMEOUT=15s` | 60s auf dead server → Agent blockt | 15s fast retry | Agent reagiert sofort |
| `_LSP_REQUEST_TIMEOUT=15s` | 30s auf hung request (tsserver parsing giant file) | 15s | Quicker fallback to AST |

LSP bridges sind keyed by `(language_id, workspace_root)` mit LRU-Pool (max 8 concurrent). Fallback-Chain: erste verfügbare Server gewinnt.

### Symbol Caching

AST-Results werden in memory gecached (`OrderedDict`, max 2000 Einträge, LRU). Automatischer Clear bei Session-Ende.

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

Enthält: Mandatory Workflows, Tool-Selection-Rules, Quality Guardrails, IDE-Feature-Coverage-Map.

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

# Tests ausführen
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
4. Run `PYTHONPATH=~/.hermes/plugins python3 -m pytest -q` — alle Tests grün
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
