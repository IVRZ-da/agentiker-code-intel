# 🧠 agentiker-code-intel-plugin v0.27.10

> **Fork** von [`rewasa/hermes-code-intel-plugin`](https://github.com/rewasa/hermes-code-intel-plugin) — customized for [agentiker.de](https://agentiker.de) / [ivory.green](https://ivory.green)
>
> AST-aware code intelligence for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — tree-sitter + ast-grep + LSP

Add **semantic code understanding** to Hermes without forking the core repo. This plugin gives the agent **21 tools** (8 AST + 13 LSP) that understand your code's *structure*, not just its text — making it dramatically more token-efficient and accurate when navigating, searching, and refactoring codebases.

> **Hybrid Architecture** — same approach as Neovim (0.5+), Zed, Helix and modern Emacs: **tree-sitter** for fast syntactic understanding (symbols, structural search, refactor) + **LSP** for semantic features (definitions, references, diagnostics, hover, signatures, quick fixes, rename). The agent gets editor-grade code intelligence without leaving the terminal.

## ✨ Why?

Hermes ships with `search_files` (regex grep) and `read_file` (raw text). Those work, but they're **blind to syntax** — they match comments, strings, and formatting equally. This plugin adds:

- **Symbol extraction** — get all functions, classes, methods with signatures and line numbers (without reading the whole file)
- **Structural search** — find imports, decorators, function calls, try/catch blocks by *AST node type*, not regex
- **Safe refactoring** — rename patterns, wrap functions, add parameters across files. **Dry-run by default** — preview changes before applying
- **Go-to-definition** — LSP-powered jump to where a symbol is defined (falls back to AST if no LSP server)
- **Find all references** — LSP-powered cross-file usage search (falls back to AST)

The result: **10–50x fewer tokens** for code navigation tasks and far fewer false-positive matches.

## 🛠 Tools
<!-- AUTO-GENERATED -->

**Version:** 0.28.01
**Tests:** ?
**Tools (25):** code_symbols, code_search, code_refactor, code_definition, code_references, code_diagnostics, code_callers, code_callees, code_capsule, code_workspace_summary, code_impact, code_tests_for_symbol, code_query, code_rename, code_workspace_symbols, code_hover, code_type_definition, code_signatures, code_action, code_format, code_implementations, code_call_hierarchy, code_highlight, code_inlay_hints, code_document_symbols
**LSP Languages:** python, typescript, tsx, javascript, jsx, rust, go

### Recent Changelog

## [0.28.03] — 2026-06-17

### Added
- **code_call_hierarchy Tool**: Neues LSP-Tool (`textDocument/callHierarchy`)
  zum Finden der Call-Hierarchy eines Symbols. Unterstützt incoming/outgoing
  Calls mit konfigurierbarer transitiver Tiefe (max_depth=1-5), Begrenzung
  pro Level (max_callers_per_level=20) und formatierter Tree-Ausgabe.
  Nutzt existierende `incoming_calls()`/`outgoing_calls()` Bridge-Methoden.
  Registriert als 25. Tool (8 AST + 17 LSP).
- **Tests**: 9 neue code_call_hierarchy Tests

### Changed
- **Tests**: 1095 → 1104 (+9 code_call_hierarchy Tests)
- **LSP Capabilities**: `callHierarchy` im initialize-Request deklariert

## [0.28.02] — 2026-06-17

### Added
- **ImportGraph Foundation**: Neue Utility `_import_graph.py` für
  AST-basierten Import-Graphen über Python/TypeScript/Go/Rust.
  Methoden: `scan()`, `parse_imports()`, `parse_all()`, `find_cycles()`,
  `find_hot_paths()`, `analyze_blast_radius()`, `to_mermaid()`, `to_tree()`.
  Wiederverwendet von code_cycle_detector, code_dependency_graph,
  code_unused_finder, code_hot_paths, code_blast_radius, code_pr_impact.
- **Tests**: 35 neue ImportGraph-Tests

### Changed
- **Tests**: 1060 → 1095 (35 neue ImportGraph-Tests)

## [0.28.01] — 2026-06-17

### Added
- **code_document_symbols Tool**: Neues LSP-Tool (`textDocument/documentSymbol`) zum
  Abrufen ALLER Symbole einer Datei (Funktionen, Klassen, Variablen, Konstanten,
  Typ-Aliase) als hierarchischen Baum. Ergänzt das AST-basierte code_symbols mit
  LSP-Ebene-Informationen und korrekter Verschachtelung.
  Registriert als 24. Tool (8 AST + 16 LSP).
- **TSX: React-Komponenten-Erkennung**: PascalCase-Funktionen in `.tsx`-Dateien
  werden als `component` klassifiziert (statt `function`). `useXxx`-Funktionen
  als `hook`.
- **TSX: "use client"/"use server" Directives**: Werden als `directive`-Symbol
  in code_symbols erfasst (erkennbar an Zeile 1 der Datei).
- **TSX: `_SYMBOL_QUERIES["tsx"]` erweitert**: `enum_declaration`,
  `export default function/class` und Directive-Queries hinzugefügt.

<!-- END AUTO-GENERATED -->

## 📦 Installation

### Quick install (from ivory.green fork)

```bash
# Fork (agentiker.de / ivory.green)
hermes plugins install johannes/agentiker-code-intel-plugin

# Oder das Original (rewasa/upstream):
# hermes plugins install rewasa/hermes-code-intel-plugin
```

### Manual install (Fork)

```bash
# Clone our fork
git clone https://git.ivory.green/johannes/agentiker-code-intel-plugin.git ~/.hermes/plugins/code_intel

# Enable in your config
hermes plugins enable code_intel
```

### Upstream install

```bash
git clone https://github.com/rewasa/hermes-code-intel-plugin.git ~/.hermes/plugins/code_intel
```

Or add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - code_intel
```

### Dependencies

The plugin uses **tree-sitter** and **ast-grep** under the hood. Install them in your Hermes venv:

```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
pip install tree-sitter tree-sitter-languages ast-grep-py
```

> **LSP tools** (`code_definition`, `code_references`) work without additional setup — they fall back to AST analysis when no language server is available. For full LSP support, install your preferred language server:
>
> ```bash
> # Python (default — tried first)
> pip install pyright
>
> # TypeScript / JavaScript
> npm install -g typescript-language-server typescript
>
> # Rust
> rustup component add rust-analyzer
>
> # Go
> go install golang.org/x/tools/gopls@latest
> ```
>
> The plugin auto-discovers servers via PATH, monorepo `node_modules/.bin`, and `npx` fallback. No additional configuration needed.

### Monorepo Support

The plugin automatically detects monorepo roots by scanning for `pnpm-workspace.yaml`, `nx.json`, or `lerna.json`. When found:

- **Workspace folders** are parsed (e.g. `apps/*`, `packages/*`, `modules/*`) and sent to the LSP server during initialization
- This enables **cross-workspace type resolution** — e.g. resolving `@myorg/logger` imports across package boundaries
- Works out of the box with pnpm, Nx, and Lerna monorepos — no config needed

The workspace folder list is cached per project root and cleared on shutdown.

### TypeScript LSP Specifics

The TypeScript LSP integration has several smart behaviors for monorepo setups:

1. **tsconfig root detection** — Instead of using the monorepo root as `rootUri` (which confuses TSServer with 60+ workspace folders), the plugin finds the nearest `tsconfig.json` directory. This gives accurate cross-file resolution within a single app while keeping monorepo folders as `workspaceFolders`.

2. **typeDefinition fallback** — When `go-to-definition` on an import identifier returns the import binding itself (a TSServer quirk), the plugin automatically tries `textDocument/typeDefinition` to jump to the actual class/interface definition.

3. **Initialization retry** — TS language server sometimes returns empty results on the first request (still indexing). The plugin retries once after 500ms for TS/JS files.

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

## 📚 Bundled Skill (Auto-Registered)

The plugin ships a bundled skill `native-code-intelligence` that is **automatically registered** when the plugin is enabled. No manual setup needed.

After `hermes plugins enable code_intel`, the skill is available via:

```
skill_view("code_intel:native-code-intelligence")
```

### What the skill provides

- **Mandatory workflows** for writing new code, refactoring, and investigating unknown codebases
- **Tool-selection rules** — prevents agents from using `read_file`/`patch`/`search_files` when AST/LSP tools are better
- **Quality guardrails** — diagnostics gate after every write/refactor, signature checks for non-trivial APIs
- **IDE-feature coverage map** — verified parity with Neovim/Zed/Helix for all agent-relevant features
- **Verified pitfalls** — NestJS decorator false positives in AST diagnostics, `code_search` limitation with member expressions

### Accessing reference files

The skill ships with supporting files (roadmaps, templates). Access them via `file_path`:

```
skill_view("code_intel:native-code-intelligence", file_path="references/phase5-roadmap.md")
```

### Adding more skills

Add additional `.md` files in `skills/` and register them in `__init__.py`:

```python
ctx.register_skill(
    name="my-new-skill",
    path=Path(__file__).parent / "skills" / "my-new-skill.md",
    description="What this skill does.",
)
```

## 💬 Slash Command

Once enabled, you get a `/code-intel` command in CLI and gateway sessions:

```
/code-intel status   → Show AST symbol cache status
/code-intel clear    → Clear the AST symbol cache (free memory)
/code-intel help     → Show usage
```

## 🔧 How It Works

### Architecture

```
code_intel.py          ← tree-sitter / ast-grep tools (symbols, search, refactor, capsule, query, workspace_summary, impact, tests_for_symbol)
lsp_bridge.py          ← LSP tools (definition, references, callers, callees, diagnostics, hover, type_definition, signatures, action, rename, workspace_symbols)
__init__.py            ← plugin registration, steering hints, hooks
scripts/health_check.py ← Active runtime verification of all code_intel tools
```

> ⚠️ **Pitfall when adding new LSP tools:** they MUST be listed in BOTH `_HERMES_CORE_TOOLS` AND `TOOLSETS["code_intel"]` inside `__init__.py` — otherwise subagents won't see them.

### 🩺 Health Check Script

The plugin ships `scripts/health_check.py` — an active, zero-LLM runtime verification of all code_intel tools. It detects issues that passive log-grep can't (LSP deadlocks, tool registration drift, import failures).

**What it checks (10 assertions):**
- Tool registry registration (all tools present and callable)
- Tree-sitter symbol extraction on real TS/Python files
- AST-aware structural search (function_calls, imports, assignments)
- ast-grep refactoring (pattern → rewrite dry-run)
- LSP definition + references via subprocess isolation (bypasses LSPManager deadlocks)
- Tool schema validation (all required fields present)

**Run it manually:**
```bash
~/.hermes/hermes-agent/venv/bin/python3 \
  ~/.hermes/plugins/code_intel/scripts/health_check.py
```

**Set up as an agentless cron (recommended):** — runs hourly, silent when healthy:
```bash
hermes cronjob create \
  --name "code_intel_health" \
  --schedule "every 60m" \
  --script "scripts/health_check.py" \
  --no-agent
```

The script uses **subprocess isolation** for LSP tests — this avoids the deadlock risk of in-process LSPManager calls during health checks. Each LSP bridge is started and killed in a separate process with a hard 15-second timeout.

### LSP Bridge Performance

**Key optimizations (v1.5+):**

| Fix | Before | After | Impact |
|-----|--------|-------|--------|
| `stderr=subprocess.DEVNULL` | Pipe buffer (64KB) fills with plugin warnings → deadlock | Silenced | Cold starts never hang |
| `PYTHONWARNINGS=ignore` | pylsp writes ~200KB of deprecation/indexing warnings to stderr during init | Suppressed at source | 2× faster Python LSP init |
| `_LSP_INIT_TIMEOUT=15s` | 60s timeout on dead server → agent stalls for a full minute | 15s → fast retry | Agent doesn't appear frozen |
| `_LSP_REQUEST_TIMEOUT=15s` | 30s timeout on hung request (e.g. tsserver parsing unrelated giant file) | 15s | Quicker fallback to AST |

These fixes eliminated the "LSP cold start hang" that previously caused Hermes to appear frozen for 60+ seconds on first code_intel use in a session.

LSP bridges are keyed by `(language_id, workspace_root)` and pooled with LRU eviction:

- **Max 8 concurrent bridges** — supports multi-language monorepos (Python + TypeScript + Go, etc.)
- **Lazy creation** — bridges start on first use, not on plugin load
- **Auto-eviction** — oldest idle bridge is shut down when the pool is full
- **Server fallback chain** — e.g. `pyright-langserver` → `pylsp` for Python; first available server wins
- All bridges are cleaned up on session end via the `on_session_end` hook

### Monorepo Workspace Discovery

For monorepo projects, the plugin detects root markers (`pnpm-workspace.yaml`, `nx.json`, `lerna.json`) separately from generic markers (`.git`, `package.json`). This prevents false stops at nested `apps/*/package.json` files. Discovered workspace folders are parsed and sent to the LSP server during initialization for full cross-workspace intelligence.

### Symbol Caching

Parsed AST results are cached in memory (`OrderedDict`, max 2000 entries, LRU eviction). The cache is **automatically cleared at session end** via the `on_session_end` hook — no memory leaks during long-running gateway sessions.

### Toolset Injection

On startup, the plugin dynamically injects into:
- `toolsets._HERMES_CORE_TOOLS` (available on all platforms)
- `toolsets.TOOLSETS["hermes-acp"]` (ACP / VS Code / JetBrains)
- `toolsets.TOOLSETS["hermes-api-server"]` (API server mode)

### Subagent Toolset Refresh

Since `_SUBAGENT_TOOLSETS` and `DELEGATE_TASK_SCHEMA` are computed at import time (before this plugin loads), the plugin automatically **refreshes** them during registration:

1. **Rebuilds `_SUBAGENT_TOOLSETS`** from the current `TOOLSETS` registry — so `code_intel` appears in the available toolset list that `delegate_task` shows to agents
2. **Updates `DELEGATE_TASK_SCHEMA` descriptions** — the toolset parameter descriptions now include `code_intel`
3. **Appends `code_intel` to `DEFAULT_TOOLSETS`** — every subagent automatically gets code_intel tools without explicit configuration
4. **Injects steering into subagent context** — a concise reference of all code_intel tools and when to prefer them over `read_file`/`grep`/`patch`

This means **no manual config needed** — once the plugin is enabled, all subagents (including `delegate_task` spawns) automatically have code_intel tools and know how to use them.

## 🧪 Development

```bash
cd ~/.hermes/plugins/code_intel

# Run tests (uses Hermes venv for tree-sitter dependencies)
PYTHONPATH=~/.hermes/plugins ~/.hermes/hermes-agent/venv/bin/python3 \\
  -m pytest -q --tb=short
# 917 passed in ~23s

# Run a single test
PYTHONPATH=~/.hermes/plugins ~/.hermes/hermes-agent/venv/bin/python3 \\
  -m pytest tests/test_code_intel.py::test_extract_symbols_python -v
```

### Pre-Commit Hook

Das Plugin hat einen automatischen Pre-Commit-Hook, der vor jedem Commit Syntax-Check + Tests ausführt:

```bash
# Aktivieren (einmalig, nach Klonen):
git config core.hooksPath .githooks

# Zum Überspringen (bei schnellen Docs-Only-Commits):
git commit --no-verify
```

Der Hook ist installiert unter `.githooks/pre-commit` (Symlink auf `scripts/pre-commit-hook`).

### CHANGELOG

Jeder Release bekommt einen Eintrag in `CHANGELOG.md`:
- `[added]` für neue Features
- `[changed]` für Änderungen
- `[fixed]` für Bugfixes
- `[removed]` für Entfernungen

## 📋 Example: What the agent sees

### Symbol Extraction (code_symbols)

**Before** (reading a 500-line file to find a function):
```
→ read_file("src/service.py")  →  500 lines, ~8000 tokens
```

**After** (using code_symbols):
```
→ code_symbols("src/service.py")
← {"symbols": [
    {"name": "processOrder", "kind": "function", "line": 42,
     "signature": "def processOrder(order_id: str, priority: int = 0) -> dict"},
    {"name": "OrderService", "kind": "class", "line": 120,
     "signature": "class OrderService"},
    {"name": "validate", "kind": "method", "line": 145,
     "signature": "def validate(self, order: Order) -> bool"}
  ]}
  → ~200 tokens (40x savings)
```

### LSP Benchmarks (TypeScript, large pnpm monorepo)

Benchmarks from a pnpm monorepo (~60 workspace folders). Tests performed with `typescript-language-server` v5.1.3 on Apple Silicon.

| Tool | Scenario | Time | Output Tokens |
|------|----------|------|---------------|
| `code_definition` | Import binding → typeDefinition fallback | ~1.5s (first request) | ~272 |
| `code_definition` | Cached request | ~0.65s | ~290 |
| `code_definition` | External module symbol | ~0.65s | ~288 |
| `code_references` | Small class (~3 refs) | ~0.67s | ~1,362 |
| `code_references` | Medium class (~6 refs) | ~0.66s | ~2,610 |

Key observations:
- **First request penalty** (~1.5s) only for import identifiers that trigger the typeDefinition fallback
- **Cross-file references** resolve correctly across workspace package boundaries
- **Token efficiency**: definition results ~270-290 tokens, references scale with usage count
- **No LSP startup delay**: bridges are lazily created and pooled (max 8 concurrent)

## 🤝 Contributing

Contributions welcome! This is a community plugin — PRs for new languages, better LSP fallbacks, or caching improvements are appreciated.

1. Fork the repo
2. Create a feature branch
3. Add tests for your changes
4. Open a PR

## 📄 License

[MIT](LICENSE) — use it however you like.

## 🙏 Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — the plugin system this builds on
- [rewasa](https://github.com/rewasa) — original author of upstream `hermes-code-intel-plugin`
- [tree-sitter](https://tree-sitter.github.io/) — incremental parsing system
- [ast-grep](https://ast-grep.github.io/) — pattern-based code search and replacement
- [pyright](https://github.com/microsoft/pyright) — Python LSP server (fallback)
- [typescript-language-server](https://github.com/typescript-language-server/typescript-language-server) — TypeScript/JavaScript LSP server
- [tsserver](https://github.com/microsoft/TypeScript) — TypeScript language service (used by typescript-language-server)
