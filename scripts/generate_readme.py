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

    _TOOL_DESCRIPTIONS = {
        # ── AST Core (16 Tools) ──
        "code_symbols": "Extract functions, classes, and methods via AST — no read_file needed",
        "code_search": "AST-based structural search — find imports, decorators, try/catch blocks",
        "code_refactor": "Structural search-and-replace with ast-grep (dry-run by default)",
        "code_definition": "LSP-powered go-to-definition with AST fallback",
        "code_references": "LSP-powered cross-file reference search with AST fallback",
        "code_diagnostics": "LSP diagnostics: errors, warnings, hints for a file",
        "code_callers": "Find all callers of a symbol (transitive)",
        "code_callees": "Find all callees of a symbol (transitive)",
        "code_capsule": "Compact symbol overview: signature, doc, references, imports",
        "code_explain": "Structured symbol explanation with complexity + callers",
        "code_diagram_symbol": "Generate ASCII/Mermaid diagrams for functions and classes",
        "code_workspace_summary": "Compact monorepo overview: apps, packages, dependencies",
        "code_impact": "Impact analysis before refactoring — blast radius + test coverage",
        "code_tests_for_symbol": "Find tests that cover a specific symbol",
        "code_query": "Smart query router — auto-selects the best tool for your intent",
        "code_rename": "LSP-powered symbol rename across the entire project",
        # ── LSP Standard (16 Tools) ──
        "code_hover": "LSP hover: type information and docstring at cursor",
        "code_signatures": "LSP signature help: parameter info for function calls",
        "code_type_definition": "LSP type definition: jump to type definition of a symbol",
        "code_workspace_symbols": "LSP workspace symbol search across the entire project",
        "code_implementations": "LSP implementations: find all implementations of an interface",
        "code_document_symbols": "LSP document symbols: all symbols in the current file",
        "code_call_hierarchy": "LSP call hierarchy: incoming and outgoing calls",
        "code_type_hierarchy": "LSP type hierarchy: subtypes and supertypes of a type",
        "code_highlight": "LSP document highlight: all occurrences of a symbol in a file",
        "code_inlay_hints": "LSP inlay hints: type hints inline (parameters, variables)",
        "code_format": "LSP formatting: auto-format a file using language server",
        "code_action": "LSP code actions: auto-fixes, refactoring suggestions, quick-fixes",
        "code_completion": "LSP completion: auto-completion at cursor position",
        "code_code_lens": "LSP code lens: run/debug/test links above functions",
        "code_folding_range": "LSP folding ranges: code fold regions for a file",
        "code_selection_range": "LSP selection ranges: hierarchical selection regions",
        # ── LSP 3.18 Extra (6 Tools) ──
        "code_linked_editing": "LSP linked editing: coupled editing (JSX tags, CSS classes)",
        "code_prepare_rename": "LSP prepare rename: check if a symbol can be renamed",
        "code_semantic_tokens": "LSP semantic tokens: colored syntax highlighting via LSP",
        "code_document_links": "LSP document links: clickable links in docs and comments",
        "code_inline_values": "LSP inline values: value display for variables at debug time",
        # ── Search (10 Tools) ──
        "code_search_by_error": "Find all places that handle a specific error type",
        "code_hot_paths": "Rank files by transitive import frequency",
        "code_blast_radius": "What breaks if you change this symbol? Callers + tests",
        "code_pr_impact": "PR impact analysis: diff + call graph + test coverage",
        "code_unused_finder": "Find unused imports and unused functions",
        "code_duplicates": "Find duplicate or similar code blocks via AST comparison",
        "code_metrics": "Aggregated project metrics: LOC, files, comment ratio",
        "code_complexity": "Cyclomatic complexity per function with rank A-E",
        "code_cycle_detector": "Find circular import chains with Tarjan's SCC algorithm",
        "code_dependency_graph": "Visual dependency graph as Mermaid diagram",
        "code_dependency_risk": "Rate dependency risks (score 0-10)",
        # ── Edit/Refactoring (5 Tools) ──
        "code_replace_body": "Replace the entire definition of a symbol (AST-based)",
        "code_safe_delete": "Delete a symbol ONLY if it has no external references",
        "code_insert_before": "Insert code BEFORE a symbol definition (AST-based)",
        "code_insert_after": "Insert code AFTER a symbol definition (AST-based)",
        "code_move": "Move a symbol between files via AST extraction",
        # ── Git (5 Tools) ──
        "code_git_blame": "Per-line git blame for a file",
        "code_git_diff_file": "Show uncommitted git diff for a file",
        "code_git_log_symbol": "Git log for a specific symbol (author, date, message)",
        "code_todo_finder": "Find TODO/FIXME/HACK/KNOWN-BUG comments in codebase",
        "code_merge_conflict_finder": "Find merge conflict markers (<<<<<<<, =======)",
        # ── Custom/Extra (8 Tools) ──
        "code_docstring_generate": "Generate docstring template from AST signature",
        "code_batch_refactor": "ast-grep bulk refactoring across multiple files (dry-run)",
        "code_security_scan": "Security scan: hardcoded secrets, SQL injection, path traversal",
        "code_generate_tests": "Generate test scaffold from a function signature",
        "code_migration": "YAML-based bulk migrations across a project",
        "code_diff_analysis": "Compare two git refs: complexity delta + blast radius",
        "code_review_assistant": "Automated code review between git refs (diff + security)",
        "code_export": "Export symbol index as JSON/Markdown for documentation",
        # ── Knowledge Graph (3 Tools) ──
        "code_index": "Build a persistent Knowledge Graph (SQLite) for a project",
        "code_graph_query": "Query the Knowledge Graph: callers, callees, hot paths, cycles",
        "code_overview": "Compact symbol overview of a file as tree view",
        # ── Timeline (1 Tool) ──
        "code_timeline": "Track symbol evolution across git history",
    }

    def get_tools(self) -> list[dict]:
        """Extract tool names from _TOOL_PROFILES (Python import) + descriptions."""
        import sys as _sys
        _plugin_parent = str(self.plugin_dir.parent)
        if _plugin_parent not in _sys.path:
            _sys.path.insert(0, _plugin_parent)
        from code_intel._profiles import _TOOL_PROFILES

        names = list(_TOOL_PROFILES.get("all", []))

        # Deduplicate (preserve order)
        seen = set()
        names = [n for n in names if not (n in seen or seen.add(n))]

        # Build descriptions from _TOOL_DESCRIPTIONS dict
        schema_descs = {name: desc for name, desc in self._TOOL_DESCRIPTIONS.items() if name in names}

        # Fallback: existing descriptions from README for tools not in our dict
        existing = read_existing_descriptions(self.readme_path)

        return merge_descriptions(names, existing, schema_descs=schema_descs)

    def get_profiles(self) -> list[dict]:
        """Extract profile info from _TOOL_PROFILES (Python import)."""
        import sys as _sys
        _plugin_parent = str(self.plugin_dir.parent)
        if _plugin_parent not in _sys.path:
            _sys.path.insert(0, _plugin_parent)
        from code_intel._profiles import _TOOL_PROFILES

        descriptions = {
            "all": "All 70 tools (default)",
            "core": "AST core tools: symbols, search, definition, references",
            "search": "Code search and analysis: search_by_error, duplicates, hot_paths",
            "edit": "AST-based code editing: replace_body, safe_delete, insert",
            "lsp": "LSP integration: definition, references, diagnostics, completion",
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
