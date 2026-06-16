#!/usr/bin/env python3
"""
README Auto-Generator for agentiker-code-intel-plugin.

Regenerates sections between ``<!-- AUTO-GENERATED -->`` markers in README.md
from the actual plugin code (version, tools, LSP languages, changelog).

Usage:
    python scripts/generate_readme.py          # update README.md in place
    python scripts/generate_readme.py --check  # exit 1 if README is stale (for CI)
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
README_PATH = PLUGIN_DIR / "README.md"
PYPROJECT_PATH = PLUGIN_DIR / "pyproject.toml"
CHANGELOG_PATH = PLUGIN_DIR / "CHANGELOG.md"
LSP_BRIDGE_PATH = PLUGIN_DIR / "lsp_bridge.py"
INIT_PATH = PLUGIN_DIR / "__init__.py"


def _get_version() -> str:
    """Extrahiere Version aus pyproject.toml."""
    text = PYPROJECT_PATH.read_text("utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "unknown"


def _get_tool_list() -> list[str]:
    """Extrahiere Tool-Liste aus __init__.py (_inject_toolsets())."""
    text = INIT_PATH.read_text("utf-8")
    m = re.search(r'"tools":\s*\[(.*?)\]', text, re.DOTALL)
    if not m:
        return []
    raw = m.group(1)
    tools = re.findall(r'"([^"]+)"', raw)
    return tools


def _get_lsp_languages() -> list[str]:
    """Extrahiere LSP-Sprachen aus lsp_bridge.py (_LANGUAGE_SERVERS)."""
    text = LSP_BRIDGE_PATH.read_text("utf-8")
    m = re.search(r'_LANGUAGE_SERVERS\s*:\s*Dict\[str,\s*List\[Dict\[str,\s*Any\]\]\]\s*=\s*\{(.*?)^\}', text, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    langs = re.findall(r'^\s+"([^"]+)":', body, re.MULTILINE)
    return langs


def _get_last_changelog_entries(count: int = 3) -> str:
    """Lese die letzten count CHANGELOG-Einträge."""
    text = CHANGELOG_PATH.read_text("utf-8") if CHANGELOG_PATH.exists() else ""
    entries = re.findall(r'^##\s+\[.*?\].*?(?=^##\s+\[|\Z)', text, re.MULTILINE | re.DOTALL)
    selected = []
    for e in entries[:count]:
        # Nur die ersten 15 Zeilen pro Eintrag
        lines = e.strip().split("\n")
        selected.append("\n".join(lines[:15]))
    return "\n\n".join(selected)


def _get_test_stats() -> str:
    """Führe pytest --collect-only aus um Test-Count zu ermitteln."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
            capture_output=True, text=True, timeout=30, cwd=str(PLUGIN_DIR),
        )
        for line in result.stderr.strip().split("\n"):
            m = re.search(r'(\d+)\s+(collected|tests?\s+selected)', line)
            if m:
                return m.group(1) + " tests"
        return "?"
    except Exception:
        return "?"


def generate_auto_section() -> str:
    """Generiere den Inhalt zwischen den AUTO-GENERATED Markern."""
    version = _get_version()
    tools = _get_tool_list()
    lsp_langs = _get_lsp_languages()
    changelog = _get_last_changelog_entries(3)
    test_count = _get_test_stats()

    lines = [
        "<!-- AUTO-GENERATED -->",
        "",
        f"**Version:** {version}",
        f"**Tests:** {test_count}",
        f"**Tools ({len(tools)}):** {', '.join(tools)}",
    ]
    if lsp_langs:
        lines.append(f"**LSP Languages:** {', '.join(lsp_langs)}")
    lines.extend([
        "",
        "### Recent Changelog",
        "",
        changelog,
        "",
        "<!-- END AUTO-GENERATED -->",
    ])
    return "\n".join(lines)


def update_readme() -> bool:
    """Update README.md zwischen den Markern. Returns True wenn geändert."""
    if not README_PATH.exists():
        print(f"README not found: {README_PATH}")
        return False

    old_text = README_PATH.read_text("utf-8")
    auto_section = generate_auto_section()

    marker_pattern = r'<!-- AUTO-GENERATED -->.*?<!-- END AUTO-GENERATED -->'
    if not re.search(marker_pattern, old_text, re.DOTALL):
        print("No AUTO-GENERATED markers found in README. Appending at end.")
        new_text = old_text.rstrip() + "\n\n" + auto_section + "\n"
    else:
        new_text = re.sub(marker_pattern, auto_section, old_text, flags=re.DOTALL)

    changed = new_text != old_text
    README_PATH.write_text(new_text, "utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate README auto sections")
    parser.add_argument("--check", action="store_true", help="Check if README is current (exit 1 if stale)")
    args = parser.parse_args()

    changed = update_readme()

    if args.check and changed:
        print("❌ README.md is stale — regenerate with: python scripts/generate_readme.py")
        return 1
    if changed:
        print(f"✅ README.md updated ({README_PATH})")
    else:
        print("✅ README.md is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
