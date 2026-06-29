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
        """Extract tool names from _TOOL_PROFILES (Python import) + descriptions from README."""
        # Python import statt Regex — _TOOL_PROFILES liegt in _profiles.py
        import sys as _sys
        _plugin_parent = str(self.plugin_dir.parent)
        if _plugin_parent not in _sys.path:
            _sys.path.insert(0, _plugin_parent)
        from code_intel._profiles import _TOOL_PROFILES

        names = list(_TOOL_PROFILES.get("all", []))

        # Deduplicate (preserve order)
        seen = set()
        names = [n for n in names if not (n in seen or seen.add(n))]

        # Get descriptions from existing README
        existing = read_existing_descriptions(self.readme_path)

        return merge_descriptions(names, existing)

    def get_profiles(self) -> list[dict]:
        """Extract profile info from _TOOL_PROFILES (Python import)."""
        import sys as _sys
        _plugin_parent = str(self.plugin_dir.parent)
        if _plugin_parent not in _sys.path:
            _sys.path.insert(0, _plugin_parent)
        from code_intel._profiles import _TOOL_PROFILES

        descriptions = {
            "all": "Sämtliche 70 Tools (Standard)",
            "core": "AST-Basis-Tools: symbols, search, definition, references",
            "search": "Code-Suche und Analyse: search_by_error, duplicates, hot_paths",
            "lsp": "LSP-Integration: definition, references, diagnostics, completion",
            "edit": "AST-basierte Code-Editierung: replace_body, safe_delete, insert",
        }
        return [
            {"name": name, "tool_count": len(tools), "description": descriptions.get(name, "")}
            for name, tools in _TOOL_PROFILES.items()
            if name != "all" or True  # include all profiles
        ]

    def get_changelog_entries(self, count: int = 1) -> str:
        """Override: nur der neuste CHANGELOG-Eintrag."""
        return super().get_changelog_entries(count=1)

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
