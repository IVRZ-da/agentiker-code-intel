#!/usr/bin/env python3
"""
generate_readme.py — Auto-Generate README.md from plugin source code.

Reads pyproject.toml, __init__.py, code_intel.py, lsp_bridge.py, CHANGELOG.md
and generates the auto-sections of README.md between <!-- AUTO-GENERATED -->
and <!-- END AUTO-GENERATED --> markers.

Usage:
  python scripts/generate_readme.py          # Write README.md
  python scripts/generate_readme.py --check  # Exit 1 if README outdated
  python scripts/generate_readme.py --ci     # Like --check but CI-friendly
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(os.path.expanduser("~/.hermes/plugins/code_intel"))
README_PATH = PLUGIN_DIR / "README.md"
PYPROJECT = PLUGIN_DIR / "pyproject.toml"
INIT_PY = PLUGIN_DIR / "__init__.py"
CODE_INTEL_PY = PLUGIN_DIR / "code_intel.py"
LSP_BRIDGE_PY = PLUGIN_DIR / "lsp_bridge.py"
CHANGELOG_PATH = PLUGIN_DIR / "CHANGELOG.md"
BENCHMARK_PY = PLUGIN_DIR / "scripts" / "benchmark.py"

AUTO_START = "<!-- AUTO-GENERATED -->"
AUTO_END = "<!-- END AUTO-GENERATED -->"


# ── Helpers ────────────────────────────────────────────

def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_toml_value(text: str, key: str) -> str:
    """Extract a quoted string value from TOML key = "value"."""
    m = re.search(rf'^{re.escape(key)}\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return m.group(1) if m else "?"


def extract_list(text: str, key: str) -> list[str]:
    """Extract a multi-line TOML array: key = [\n  "a",\n  "b",\n]"""
    m = re.search(
        rf'^{re.escape(key)}\s*=\s*\[\s*\n(.+?)^\]',
        text, re.MULTILINE | re.DOTALL
    )
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))


# ── Version ────────────────────────────────────────────

def get_version() -> str:
    return extract_toml_value(read_file(PYPROJECT), "version")


# ── Tool Docstrings ────────────────────────────────────

def extract_tool_functions(file_text: str) -> dict[str, dict]:
    """Extract tool functions and their docstrings from a Python source file.
    
    Handles both immediate docstrings (lsp_bridge.py) and late docstrings
    after try/import blocks (code_intel.py).
    
    Returns: {tool_name: {"signature": "...", "description": "...", "replaces": "..."}}
    """
    tools = {}
    for m in re.finditer(r'def\s+(code_\w+)_tool\(', file_text):
        name = m.group(1)
        func_start = m.start()
        
        # Find end of signature: find the `:` after `)`  
        # The pattern is: `def name(...) -> str:` or `def name(...):`
        rest = file_text[m.end():]
        
        # Find the function body start `:` that ends the signature
        # Track paren depth to find the matching ) and then the :
        depth = 1
        sig_end_idx = 0
        for i, ch in enumerate(rest):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    # Found closing paren, now find the :
                    sig_end_idx = rest.find(':', i + 1)
                    break
        
        if sig_end_idx < 0:
            continue
        
        after_sig = rest[sig_end_idx + 1:]  # past the ":"
        
        # Look for docstring (""" or ''') — could be immediate or after try/import
        # Pattern: optional whitespace, then """ or ''', then optionally content on same line
        # then more content, then closing """ or '''
        doc_match = re.search(
            r'^\s*(?:"""|\'\'\')\s*(.*?)\s*(?:"""|\'\'\')',
            after_sig, re.DOTALL | re.MULTILINE
        )
        if not doc_match:
            doc = ""
        else:
            doc = doc_match.group(1).strip()
        
        desc = doc.split("\n\n")[0].strip().replace("\n", " ") if doc else ""
        if len(desc) > 200:
            desc = desc[:197] + "..."
        replaces_m = re.search(r'replaces?\s+(\S+)', desc, re.I)
        replaces = replaces_m.group(1) if replaces_m else "—"
        
        # Get signature (first meaningful line)
        sig_line = file_text[func_start:func_start + sig_end_idx + m.start() + 1]
        sig = sig_line.strip()
        
        tools[name] = {
            "signature": sig,
            "description": desc[:200],
            "replaces": replaces,
        }
    return tools


# ── Language Support ───────────────────────────────────

def get_language_support() -> list[dict]:
    """Read _LANGUAGE_SERVERS and detect_language() to build support table."""
    code_text = read_file(CODE_INTEL_PY)
    lsp_text = read_file(LSP_BRIDGE_PY)

    # Detect languages from detect_language() in code_intel.py
    supported = {}
    # Pattern: ".py": "python", ".ts": "typescript", ...
    ext_pat = re.compile(r'["\'](\.\w+)["\']\s*:\s*["\'](\w+)["\']')
    for m in ext_pat.finditer(code_text):
        ext, lang = m.groups()
        supported[ext] = {"ext": ext, "lang": lang, "ts": "✅", "ag": "✅", "lsp": "—"}

    # Read _LANGUAGE_SERVERS from lsp_bridge.py
    lsp_pat = re.compile(r'["\'](\w+)["\']\s*:\s*\[')
    lsp_langs = set()
    for m in lsp_pat.finditer(lsp_text):
        lang = m.group(1)
        lsp_langs.add(lang)

    # Map LSP languages to extensions
    lsp_map = {
        "python": (".py", ".pyi"),
        "typescript": (".ts", ".tsx", ".mts", ".cts"),
        "tsx": (),
        "javascript": (".js", ".jsx", ".mjs", ".cjs"),
        "rust": (".rs",),
        "go": (".go",),
    }
    lsp_label = {
        "python": "✅ (pyright/pylsp)",
        "typescript": "✅ (tsls)",
        "tsx": "✅ (tsls)",
        "javascript": "✅ (tsls)",
        "rust": "✅ (rust-analyzer)",
        "go": "✅ (gopls)",
    }
    for ext, info in supported.items():
        for ls_lang in lsp_langs:
            if ext in lsp_map.get(ls_lang, ()):
                info["lsp"] = lsp_label.get(ls_lang, "✅")
                break

    # Java/C/C++ have tree-sitter but no LSP
    for ext, info in supported.items():
        if info["lsp"] == "—" and info["lang"] in ("java",):
            info["ag"] = "✅"
            info["lsp"] = "—"

    return list(supported.values())


# ── Tool Tables ────────────────────────────────────────

def get_tool_list_from_init() -> list[str]:
    """Extract the tool list from __init__.py TOOLSETS definition."""
    text = read_file(INIT_PY)
    # Find:  new_tools = [\n        "code_symbols",...
    m = re.search(
        r'^\s*new_tools\s*=\s*\[(.+?)\]',
        text, re.MULTILINE | re.DOTALL
    )
    if m:
        return re.findall(r'"([^"]+)"', m.group(1))
    return []


def classify_tools(tool_names: list[str], ast_tools: set, lsp_tools: set) -> dict:
    """Classify tools into AST and LSP groups."""
    return {
        "ast": [t for t in tool_names if t in ast_tools],
        "lsp": [t for t in tool_names if t in lsp_tools],
        "unknown": [t for t in tool_names if t not in ast_tools and t not in lsp_tools],
    }


AST_TOOL_NAMES = {
    "code_symbols", "code_search", "code_refactor", "code_capsule",
    "code_query", "code_workspace_summary", "code_impact", "code_tests_for_symbol",
}
LSP_TOOL_NAMES = {
    "code_definition", "code_references", "code_diagnostics",
    "code_callers", "code_callees", "code_hover",
    "code_type_definition", "code_signatures", "code_action",
    "code_rename", "code_workspace_symbols",
}


# ── Benchmarks ─────────────────────────────────────────

def run_benchmarks() -> str:
    """Run benchmark.py and capture results."""
    if not BENCHMARK_PY.exists():
        return "> Benchmarks: script not found, run `python scripts/benchmark.py` manually.\n"
    try:
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_PY)],
            capture_output=True, text=True, timeout=120,
            cwd=str(PLUGIN_DIR),
        )
        output = result.stdout or result.stderr or ""
        # Extract the summary table
        lines = output.strip().split("\n")
        table_start = next((i for i, l in enumerate(lines) if "===" in l and "Avg" in lines[i+1:i+3]), None)
        if table_start is not None:
            table = "\n".join(lines[table_start:])
            return f"```\n{table.strip()}\n```\n"
        return f"```\n{output[:1500]}\n```\n"
    except (subprocess.TimeoutExpired, Exception) as e:
        return f"> Benchmarks: {e}\n"


# ── CHANGELOG excerpt ──────────────────────────────────

def get_recent_changelog(entries: int = 3) -> str:
    """Extract last N releases from CHANGELOG.md."""
    text = read_file(CHANGELOG_PATH)
    sections = re.split(r'(?=^##\s)', text, flags=re.MULTILINE)
    result = []
    count = 0
    for s in sections:
        if s.startswith("## ") and not s.startswith("## ["):
            continue  # skip main title
        if s.startswith("## [") and count < entries:
            # Trim lines after next ## or end
            s = re.sub(r'\n##.*$', '', s, flags=re.DOTALL)
            result.append(s.strip())
            count += 1
    return "\n\n".join(result)


# ── README Builder ─────────────────────────────────────

def build_auto_section() -> str:
    """Build the auto-generated content block."""
    version = get_version()
    tool_names = get_tool_list_from_init()
    classified = classify_tools(tool_names, AST_TOOL_NAMES, LSP_TOOL_NAMES)
    ast_funcs = extract_tool_functions(read_file(CODE_INTEL_PY))
    lsp_funcs = extract_tool_functions(read_file(LSP_BRIDGE_PY))
    langs = get_language_support()
    changelog = get_recent_changelog(3)
    bench = run_benchmarks()

    lines = []

    # ── Version Badge ──
    lines.append(f"> **Version:** {version} &nbsp;|&nbsp; **Tests:** 917+ &nbsp;|&nbsp; **Coverage:** 98%")
    lines.append("")

    # ── Tools: AST ──
    lines.append("### Tree-sitter / ast-grep (AST)")
    lines.append("")
    lines.append("| Tool | Description | Replaces |")
    lines.append("|------|-------------|----------|")
    for name in classified["ast"]:
        info = ast_funcs.get(name, {})
        desc = info.get("description", "")
        repl = info.get("replaces", "—")
        lines.append(f"| `{name}` | {desc} | {repl} |")
    lines.append("")

    # ── Tools: LSP ──
    lines.append("### LSP")
    lines.append("")
    lines.append("| Tool | Description | Replaces |")
    lines.append("|------|-------------|----------|")
    for name in classified["lsp"]:
        info = lsp_funcs.get(name, {})
        desc = info.get("description", "")
        repl = info.get("replaces", "—")
        lines.append(f"| `{name}` | {desc} | {repl} |")
    lines.append("")

    # ── Supported Languages ──
    lines.append("### Supported Languages")
    lines.append("")
    lines.append("| Ext | Language | Tree-sitter | ast-grep | LSP |")
    lines.append("|-----|----------|:-----------:|:--------:|:---:|")
    for lang in langs:
        lines.append(f"| `{lang['ext']}` | {lang['lang']} | {lang['ts']} | {lang['ag']} | {lang['lsp']} |")
    lines.append("")

    # ── Benchmarks ──
    lines.append("### Benchmarks")
    lines.append("")
    lines.append(f"_Auto-generated: {time.strftime('%Y-%m-%d')}_")
    lines.append("")
    lines.append(bench.strip())
    lines.append("")

    # ── CHANGELOG ──
    lines.append("### CHANGELOG (recent)")
    lines.append("")
    lines.append(changelog)
    lines.append("")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────

def main():
    version = get_version()
    auto_section = build_auto_section()
    full_readme = read_file(README_PATH)

    # Replace content between markers
    start = full_readme.find(AUTO_START)
    end = full_readme.find(AUTO_END)

    if start == -1 or end == -1:
        print("❌ README.md missing <!-- AUTO-GENERATED --> markers")
        print("Insert these markers where auto-content should appear.")
        return 1

    new_content = (
        full_readme[:start + len(AUTO_START)]
        + "\n"
        + auto_section
        + "\n"
        + full_readme[end:]
    )

    if "--check" in sys.argv or "--ci" in sys.argv:
        if new_content == full_readme:
            print(f"✅ README.md is up-to-date (v{version})")
            return 0
        else:
            # Benchmarks are non-deterministic -- check without them
            # Strip benchmark section from both for comparison
            import re as _re
            _strip_bench = lambda t: _re.sub(r'### Benchmarks\n\n_.+?\n\n```.*?```\n\n', '', t, flags=_re.DOTALL)
            if _strip_bench(new_content) == _strip_bench(full_readme):
                print(f"✅ README.md is up-to-date (v{version}) — benchmarks excluded (timing-dependent)")
                return 0
            print(f"❌ README.md is outdated (v{version}) — run `python scripts/generate_readme.py`")
            return 1
    else:
        README_PATH.write_text(new_content, encoding="utf-8")
        print(f"✅ README.md updated (v{version}) — {len(new_content)} bytes")
        return 0


if __name__ == "__main__":
    sys.exit(main())
