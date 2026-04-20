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

> **LSP tools** (`code_definition`, `code_references`) work without additional setup — they fall back to AST analysis when no language server is available. For full LSP support, ensure `pyright` (Python), `typescript-language-server` (TS), or equivalent is installed and on your PATH.

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
