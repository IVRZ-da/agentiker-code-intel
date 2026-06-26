#!/usr/bin/env python3
"""README auto-generator for code_intel — uses shared generate_readme_base.py.

Usage:
    python3 scripts/generate_readme.py          # update README.md in place
    python3 scripts/generate_readme.py --check  # exit 1 if README is stale
    python3 scripts/generate_readme.py --verbose  # show debug info
"""

import re
import sys
from pathlib import Path

# Shared base
BASE = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "generate_readme_base.py"
if not BASE.exists():
    # Fallback: direkt im Hermes Ordner
    BASE = Path.home() / ".hermes" / "scripts" / "generate_readme_base.py"
sys.path.insert(0, str(BASE.parent))

from generate_readme_base import ReadmeGenerator, merge_descriptions, read_existing_descriptions

PLUGIN_DIR = Path(__file__).resolve().parent.parent


class CodeIntelReadmeGenerator(ReadmeGenerator):

    def get_tools(self) -> list[dict]:
        """Extract tool names from _TOOL_PROFILES['all'] + descriptions from README."""
        init = self.plugin_dir / "__init__.py"
        text = init.read_text("utf-8")

        # Extract tool names
        names: list[str] = []
        # Try _TOOL_PROFILES
        for p in [r'TOOL_PROFILES\s*:\s*dict\s*=\s*\{.*?"all"\s*:\s*\[(.*?)\]',
                   r'"all"\s*:\s*\[(.*?)\]',
                   r'TOOLSETS\["agentiker_code_intel"\]\["tools"\]\s*=\s*\[(.*?)\]',
                   r'"tools":\s*\[(.*?)\]']:
            m = re.search(p, text, re.DOTALL)
            if m:
                names = re.findall(r'"([^"]+)"', m.group(1))
                break

        # Filter non-tools
        NON_TOOLS = {"agentiker_code_intel", "description"}
        names = [n for n in names if n not in NON_TOOLS and not n.startswith("AST-aware")]

        # Deduplicate
        seen = set()
        names = [n for n in names if not (n in seen or seen.add(n))]

        # Get descriptions from existing README
        existing = read_existing_descriptions(self.readme_path)

        return merge_descriptions(names, existing)

    def get_profiles(self) -> list[dict]:
        """Extract profile info from _TOOL_PROFILES dict."""
        init = self.plugin_dir / "__init__.py"
        text = init.read_text("utf-8")

        m = re.search(r'_TOOL_PROFILES\s*(?::\s*dict)?\s*=\s*\{(.*?)\}', text, re.DOTALL)
        if not m:
            return []

        profiles = re.findall(r'"(\w+)"\s*:\s*\[(.*?)\]', m.group(1), re.DOTALL)
        return [
            {"name": name, "tool_count": len(re.findall(r'"([^"]+)"', tools)), "description": ""}
            for name, tools in profiles
        ]

    def get_languages(self) -> list[str]:
        """Extract AST languages from code_tools.py or lsp_bridge."""
        # Try tools/language.py first
        lang_file = self.plugin_dir / "tools" / "language.py"
        if lang_file.exists():
            text = lang_file.read_text("utf-8")
            m = re.search(r'_EXT_TO_LANG\s*=\s*\{(.*?)^\}', text, re.M | re.DOTALL)
            if m:
                return list(set(re.findall(r':\s*"([^"]+)"', m.group(1))))

        # Fallback: lsp/bridge.py
        bridge = self.plugin_dir / "lsp" / "bridge.py"
        if bridge.exists():
            text = bridge.read_text("utf-8")
            m = re.search(r'_LANGUAGE_SERVERS\s*(?::\s*Dict.*?)?=\s*\{(.*?)^\}', text, re.M | re.DOTALL)
            if m:
                return list(set(re.findall(r'^\s+"([^"]+)":', m.group(1), re.M)))

        return []


if __name__ == "__main__":
    gen = CodeIntelReadmeGenerator(PLUGIN_DIR)
    sys.exit(gen.run())
