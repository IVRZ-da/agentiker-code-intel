# 🧠 agentiker-code-intel — Hermes Plugin

> **AST-aware code intelligence for Hermes Agent** — tree-sitter + ast-grep + LSP
> 70 tools that understand your code's *structure*, not just its text. 10–50× fewer tokens for code navigation.

> **Inspiriert** von [`rewasa/hermes-code-intel-plugin`](https://github.com/rewasa/hermes-code-intel-plugin) — stark erweitert für [agentiker.de](https://agentiker.de) (70 Tools, 3125 Tests)



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

### Cross-Plugin: Scout Bug-Hunt gefüttert von code-intel
```python
# code-intel findet Dead Code → scout scannt automatisch
analysis_deadcode(path="src/")
bug_hunt_scan(session_id="...", patterns=["analysis"])
```

---

## 🛠 Tools

<!-- README_AUTO -->

[![Version](https://img.shields.io/badge/version-0.6.15-blue.svg)]() [![Tests](https://img.shields.io/badge/tests-3125%20tests-green.svg)]() [![License](https://img.shields.io/badge/license-MIT-green.svg)]() [![Languages](https://img.shields.io/badge/languages-9-orange.svg)]()

**Version:** 0.6.15

**Tests:** 3125 tests

**Tools (70):**

**Profiles:**

| Profile | Tools | Description |
|---------|-------|-------------|
| `all` | 70 | Sämtliche 70 Tools (Standard) |
| `core` | 22 | AST-Basis-Tools: symbols, search, definition, references |
| `search` | 15 | Code-Suche und Analyse: search_by_error, duplicates, hot_paths |
| `edit` | 10 | AST-basierte Code-Editierung: replace_body, safe_delete, insert |
| `lsp` | 25 | LSP-Integration: definition, references, diagnostics, completion |

**Supported Languages:** c, cpp, go, java, javascript, python, rust, tsx, typescript

| Tool | Description |
|------|-------------|
| `code_action` | LSP-Code-Actions: Auto-Fixes, Refactoring-Vorschläge, Quick-Fixes |
| `code_batch_refactor` | ast-grep Bulk-Refactoring über mehrere Dateien (dry-run) |
| `code_blast_radius` | Was bricht wenn du dieses Symbol änderst? (Callers + Tests) |
| `code_call_hierarchy` | LSP-Call-Hierarchy: incoming + outgoing Calls |
| `code_callees` | Alle Aufrufe eines Symbols finden (transitiv) |
| `code_callers` | Alle Aufrufer eines Symbols finden (transitiv) |
| `code_capsule` | Kompakte Symbol-Übersicht: Signatur, Doc, Referenzen, Imports |
| `code_code_lens` | LSP-Code-Lens: Run/Debug/Test-Links über Funktionen |
| `code_completion` | LSP-Completion: Autovervollständigung am Cursor |
| `code_complexity` | Zyklomatische Komplexität pro Funktion mit Rank A-E |
| `code_cycle_detector` | Finde zirkuläre Import-Ketten mit Tarjans SCC Algorithmus |
| `code_definition` | LSP-powered Go-to-Definition mit AST-Fallback |
| `code_dependency_graph` | Visueller Dependency-Graph als Mermaid-Diagramm |
| `code_dependency_risk` | Bewerte Abhängigkeitsrisiken (Score 0-10) |
| `code_diagnostics` | LSP-Diagnostik: Fehler, Warnungen, Hinweise für eine Datei |
| `code_diagram_symbol` | ASCII/Mermaid-Diagramm für Funktionen/Klassen generieren |
| `code_diff_analysis` | Vergleiche zwei Git-Refs: Complexity-Delta + Blast Radius |
| `code_docstring_generate` | Generiere Docstring-Template aus AST-Signatur |
| `code_document_links` | LSP-Document-Links: klickbare Links in Dokumentation/Kommentaren |
| `code_document_symbols` | LSP-Dokument-Symbole: alle Symbole in der aktuellen Datei |
| `code_duplicates` | Finde duplizierte/ähnliche Code-Blöcke via AST-Vergleich |
| `code_explain` | Strukturierte Erklärung eines Symbols mit Complexity + Callern |
| `code_export` | Exportiere Symbol-Index als JSON/Markdown für Doku |
| `code_folding_range` | LSP-Folding-Ranges: Code-Faltungsbereiche einer Datei |
| `code_format` | LSP-Formatierung: automatische Code-Formatierung einer Datei |
| `code_generate_tests` | Generiere Test-Gerüst aus einer Funktions-Signatur |
| `code_git_blame` | Per-Line Git-Blame für eine Datei |
| `code_git_diff_file` | Zeige uncommitted Git-Diff für eine Datei |
| `code_git_log_symbol` | Git-Log für ein bestimmtes Symbol (Autor, Datum, Message) |
| `code_graph_query` | Query den Knowledge Graph: Callers, Callees, Hot Paths, Cycles |
| `code_highlight` | LSP-Document-Highlight: alle Vorkommen eines Symbols in der Datei |
| `code_hot_paths` | Ranke Dateien nach transitiver Import-Häufigkeit |
| `code_hover` | LSP-Hover: Typ-Information und Docstring unter dem Cursor |
| `code_impact` | Impact-Analyse vor Refactoring — Blast Radius + Testabdeckung |
| `code_implementations` | LSP-Implementierungen: finde alle Implementierungen eines Interfaces |
| `code_index` | Baue persistierten Knowledge Graph (SQLite) für ein Projekt |
| `code_inlay_hints` | LSP-Inlay-Hints: Typ-Hinweise im Code (Parameter, Variablen) |
| `code_inline_values` | LSP-Inline-Values: Wertanzeige bei Variablen zur Debug-Zeit |
| `code_insert_after` | Füge Code NACH einer Symbol-Definition ein (AST-basiert) |
| `code_insert_before` | Füge Code VOR einer Symbol-Definition ein (AST-basiert) |
| `code_linked_editing` | LSP-Linked-Editing: gekoppeltes Editieren (JSX-Tags, CSS-Klassen) |
| `code_merge_conflict_finder` | Finde Merge-Konflikt-Markierungen (<<<<<<<, =======) |
| `code_metrics` | Aggregierte Projekt-Metriken: LOC, Dateien, Comment-Ratio |
| `code_migration` | YAML-basierte Bulk-Migrationen über ein Projekt |
| `code_move` | Verschiebe ein Symbol zwischen Dateien via AST-Extraktion |
| `code_overview` | Kompakte Symbol-Übersicht einer Datei als Tree-View |
| `code_pr_impact` | PR-Impact-Analyse: Diff + Call-Graph + Testabdeckung |
| `code_prepare_rename` | LSP-Prepare-Rename: prüft ob ein Symbol umbenannt werden kann |
| `code_query` | Smart Query Router — wählt automatisch das beste Tool aus |
| `code_refactor` | Strukturelle Search-and-Replace mit ast-grep (dry-run by default) |
| `code_references` | LSP-powered Cross-File-Referenz-Suche mit AST-Fallback |
| `code_rename` | LSP-powered Symbol-Rename über das gesamte Projekt |
| `code_replace_body` | Ersetze die komplette Definition eines Symbols (AST-basiert) |
| `code_review_assistant` | Automated Code-Review zwischen Git-Refs (Diff + Security) |
| `code_safe_delete` | Lösche ein Symbol NUR wenn es keine externen Referenzen hat |
| `code_search` | AST-basierte Code-Suche — finde Imports, Dekorateure, try/catch |
| `code_search_by_error` | Finde alle Stellen die einen bestimmten Error-Typ behandeln |
| `code_security_scan` | Security-Scan: hardcodierte Secrets, SQL-Injection, Path-Traversal |
| `code_selection_range` | LSP-Selection-Ranges: hierarchische Selektionsbereiche |
| `code_semantic_tokens` | LSP-Semantik-Tokens: farbliche Syntax-Hervorhebung via LSP |
| `code_signatures` | LSP-Signature-Help: Parameter-Info für Funktionsaufrufe |
| `code_symbols` | Strukturierte Symbol-Extraktion via AST — Funktionen, Klassen, Methoden |
| `code_tests_for_symbol` | Tests finden, die ein Symbol abdecken |
| `code_timeline` | Evolution eines Symbols über die Git-History |
| `code_todo_finder` | Finde TODO/FIXME/HACK/KNOWN-BUG Kommentare im Codebase |
| `code_type_definition` | LSP-Type-Definition: springe zur Typ-Definition eines Symbols |
| `code_type_hierarchy` | LSP-Type-Hierarchy: Subtypes + Supertypes eines Typs |
| `code_unused_finder` | Finde ungenutzte Imports und ungenutzte Funktionen |
| `code_workspace_summary` | Kompakte Monorepo-Übersicht: Apps, Packages, Dependencies |
| `code_workspace_symbols` | LSP-Workspace-Symbol-Suche über das gesamte Projekt |

### Recent Changelog

## [0.6.15] — 2026-06-29

### 📝 README-Content ausgebaut

- **Tool-Descriptions hinzugefügt** — 70 Tools mit Kurzbeschreibung in der Tool-Tabelle (vorher nur "—") (scripts/generate_readme.py)
- **Limitations-Sektion** — Neu: LSP-Abhängigkeiten, Sprachen-Support, Cold Start, Batch-Grenzen
- **Subagent-Integration dokumentiert** — Steering-Hinweis für `delegate_task` mit code_intel Tools
- **`code_complexity` Description ergänzt** — Fehlte im Generator-Dict (scripts/generate_readme.py)

<!-- END README_AUTO -->

## ⚠️ Limitations

| Bereich | Details |
|---------|---------|
| **LSP-Abhängigkeit** | `code_rename`, `code_hover`, `code_diagnostics`, `code_signatures` u.a. benötigen einen LSP-Server. Ohne Server fallen Tools auf AST/Text-Fallback zurück. |
| **C/C++/Java** | Kein LSP-Support (nur tree-sitter + ast-grep für Grundfunktionen) |
| **Rust/Go** | LSP erkannt aber Server (`rust-analyzer`, `gopls`) muss separat installiert sein |
| **Grosse Projekte** | `code_index` + `code_graph_query` bei 100k+ Dateien: initiale Indexierung kann 30-60s dauern |
| **LSP Cold Start** | Erster `code_definition`/`code_references` Call pro Sprache dauert ~1.5s (LSP-Initialisierung) |
| **Batch-Refactoring** | `code_batch_refactor` erstellt `.bak` Backups — bei 500+ Dateien Speicherbedarf prüfen |
| **Subagent-Kompatibilität** | Siehe Subagent-Integration unten |

---

## 🤖 Subagent-Integration

Alle code_intel Tools sind automatisch in delegierten Subagenten (`delegate_task`) verfügbar:

```python
# Subagent hat automatisch Zugriff auf alle 70 code_intel Tools
delegate_task(goal="Refactoriere die User-Service Klasse",
              context="...",
              toolsets=["agentiker_code_intel", "terminal", "file"])
```

**Steering-Hinweis:** Subagenten werden angewiesen, `code_symbols` statt `read_file` für Code-Verständnis zu nutzen — das spart 10-50× Tokens.

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
