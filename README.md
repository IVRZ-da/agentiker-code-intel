# 🧠 hermes-code-intel-plugin

> AST-aware code intelligence for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — tree-sitter + ast-grep + LSP

Add **semantic code understanding** to Hermes without forking the core repo. This plugin gives the agent 5 tools that understand your code's *structure*, not just its text — making it dramatically more token-efficient and accurate when navigating, searching, and refactoring codebases.

## ✨ Why?

Hermes ships with `search_files` (regex grep) and `read_file` (raw text). Those work, but they're **blind to syntax** — they match comments, strings, and formatting equally. This plugin adds:

- **Symbol extraction** — get all functions, classes, methods with signatures and line numbers (without reading the whole file)
- **Structural search** — find imports, decorators, function calls, try/catch blocks by *AST node type*, not regex
- **Safe refactoring** — rename patterns, wrap functions, add parameters across files. **Dry-run by default** — preview changes before applying
- **Go-to-definition** — LSP-powered jump to where a symbol is defined (falls back to AST if no LSP server)
- **Find all references** — LSP-powered cross-file usage search (falls back to AST)

The result: **10–50x fewer tokens** for code navigation tasks and far fewer false-positive matches.

## 🛠 Tools

| Tool | What it does | Replaces |
|------|-------------|----------|
| `code_symbols` | Extract functions, classes, methods, interfaces, enums, structs from any file. Returns signatures + line numbers. | Reading entire files just to see "what's in here?" |
| `code_search` | Tree-sitter query-based structural search. Find function calls, imports, decorators, return statements, assignments by their *semantic* meaning. | `search_files` / grep for code patterns |
| `code_refactor` | ast-grep structural search-and-replace. Matches by AST structure, not raw text. Supports meta-variables (`$NAME`, `$$BODY`). | `patch` / sed for structural changes |
| `code_definition` | LSP go-to-definition. Falls back to tree-sitter AST analysis if no language server. | Manual `grep` for symbol definitions |
| `code_references` | LSP find-all-references. Falls back to tree-sitter AST analysis if no language server. | Manual `grep` for symbol usages |

### Steering Hints

The plugin automatically injects hints into the built-in tool descriptions, so the agent **naturally prefers** the AST tools:

- `read_file` → *"prefer code_symbols to understand what a file contains"*
- `search_files` → *"prefer code_search for structural code patterns"*
- `patch` → *"prefer code_refactor for AST-aware structural replacement"*

No prompt changes needed — it just works.

## 📦 Installation

### Quick install (from GitHub)

```bash
hermes plugins install rewasa/hermes-code-intel-plugin
hermes plugins enable code_intel
```

### Manual install

```bash
# Clone into your plugins directory
git clone https://github.com/rewasa/hermes-code-intel-plugin.git ~/.hermes/plugins/code_intel

# Enable in your config
hermes plugins enable code_intel
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
- This enables **cross-workspace type resolution** — e.g. resolving `@agentselly/logger` imports across package boundaries
- Works out of the box with pnpm, Nx, and Lerna monorepos — no config needed

The workspace folder list is cached per project root and cleared on shutdown.

## 🌐 Supported Languages

| Language | Extensions | Tree-sitter | ast-grep | LSP |
|----------|-----------|:-----------:|:--------:|:---:|
| Python | `.py`, `.pyi` | ✅ | ✅ | ✅ (pyright) |
| JavaScript | `.js`, `.jsx` | ✅ | ✅ | ✅ |
| TypeScript | `.ts` | ✅ | ✅ | ✅ (tsls) |
| TSX | `.tsx` | ✅ | ✅ | ✅ (tsls) |
| Rust | `.rs` | ✅ | ✅ | ✅ (rust-analyzer) |
| Go | `.go` | ✅ | ✅ | ✅ (gopls) |
| Java | `.java` | ✅ | ✅ | ✅ (jdtls) |
| C | `.c`, `.h` | ✅ | — | ✅ (clangd) |
| C++ | `.cpp` | ✅ | — | ✅ (clangd) |

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
code_intel.py          ← tree-sitter tools (code_symbols, code_search, code_refactor)
lsp_bridge.py          ← LSP tools (code_definition, code_references)
__init__.py            ← plugin registration, steering hints, hooks
```

### LSP Bridge Pooling

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

## 🧪 Development

```bash
cd ~/.hermes/plugins/code_intel

# Run tests (uses Hermes venv for tree-sitter dependencies)
PYTHONPATH=~/.hermes/plugins ~/.hermes/hermes-agent/venv/bin/python3 \
  -m pytest tests/test_code_intel.py -v

# Run a single test
PYTHONPATH=~/.hermes/plugins ~/.hermes/hermes-agent/venv/bin/python3 \
  -m pytest tests/test_code_intel.py::test_extract_symbols_python -v
```

## 📋 Example: What the agent sees

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

## 🤝 Contributing

Contributions welcome! This is a community plugin — PRs for new languages, better LSP fallbacks, or caching improvements are appreciated.

1. Fork the repo
2. Create a feature branch
3. Add tests for your changes
4. Open a PR

## 📄 License

[MIT](LICENSE) — use it however you like.

## 🙏 Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research — the plugin system this builds on
- [tree-sitter](https://tree-sitter.github.io/) — incremental parsing system
- [ast-grep](https://ast-grep.github.io/) — pattern-based code search and replacement
- [pyright](https://github.com/microsoft/pyright) — Python LSP server (fallback)
- [typescript-language-server](https://github.com/typescript-language-server/typescript-language-server) — TypeScript/JavaScript LSP server
- [tsserver](https://github.com/microsoft/TypeScript) — TypeScript language service (used by typescript-language-server)
