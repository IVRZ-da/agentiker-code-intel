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
        "code_symbols": "Strukturierte Symbol-Extraktion via AST — Funktionen, Klassen, Methoden",
        "code_search": "AST-basierte Code-Suche — finde Imports, Dekorateure, try/catch",
        "code_refactor": "Strukturelle Search-and-Replace mit ast-grep (dry-run by default)",
        "code_definition": "LSP-powered Go-to-Definition mit AST-Fallback",
        "code_references": "LSP-powered Cross-File-Referenz-Suche mit AST-Fallback",
        "code_diagnostics": "LSP-Diagnostik: Fehler, Warnungen, Hinweise für eine Datei",
        "code_callers": "Alle Aufrufer eines Symbols finden (transitiv)",
        "code_callees": "Alle Aufrufe eines Symbols finden (transitiv)",
        "code_capsule": "Kompakte Symbol-Übersicht: Signatur, Doc, Referenzen, Imports",
        "code_explain": "Strukturierte Erklärung eines Symbols mit Complexity + Callern",
        "code_diagram_symbol": "ASCII/Mermaid-Diagramm für Funktionen/Klassen generieren",
        "code_workspace_summary": "Kompakte Monorepo-Übersicht: Apps, Packages, Dependencies",
        "code_impact": "Impact-Analyse vor Refactoring — Blast Radius + Testabdeckung",
        "code_tests_for_symbol": "Tests finden, die ein Symbol abdecken",
        "code_query": "Smart Query Router — wählt automatisch das beste Tool aus",
        "code_rename": "LSP-powered Symbol-Rename über das gesamte Projekt",
        # ── LSP Standard (16 Tools) ──
        "code_hover": "LSP-Hover: Typ-Information und Docstring unter dem Cursor",
        "code_signatures": "LSP-Signature-Help: Parameter-Info für Funktionsaufrufe",
        "code_type_definition": "LSP-Type-Definition: springe zur Typ-Definition eines Symbols",
        "code_workspace_symbols": "LSP-Workspace-Symbol-Suche über das gesamte Projekt",
        "code_implementations": "LSP-Implementierungen: finde alle Implementierungen eines Interfaces",
        "code_document_symbols": "LSP-Dokument-Symbole: alle Symbole in der aktuellen Datei",
        "code_call_hierarchy": "LSP-Call-Hierarchy: incoming + outgoing Calls",
        "code_type_hierarchy": "LSP-Type-Hierarchy: Subtypes + Supertypes eines Typs",
        "code_highlight": "LSP-Document-Highlight: alle Vorkommen eines Symbols in der Datei",
        "code_inlay_hints": "LSP-Inlay-Hints: Typ-Hinweise im Code (Parameter, Variablen)",
        "code_format": "LSP-Formatierung: automatische Code-Formatierung einer Datei",
        "code_action": "LSP-Code-Actions: Auto-Fixes, Refactoring-Vorschläge, Quick-Fixes",
        "code_completion": "LSP-Completion: Autovervollständigung am Cursor",
        "code_code_lens": "LSP-Code-Lens: Run/Debug/Test-Links über Funktionen",
        "code_folding_range": "LSP-Folding-Ranges: Code-Faltungsbereiche einer Datei",
        "code_selection_range": "LSP-Selection-Ranges: hierarchische Selektionsbereiche",
        # ── LSP 3.18 Extra (6 Tools) ──
        "code_linked_editing": "LSP-Linked-Editing: gekoppeltes Editieren (JSX-Tags, CSS-Klassen)",
        "code_prepare_rename": "LSP-Prepare-Rename: prüft ob ein Symbol umbenannt werden kann",
        "code_semantic_tokens": "LSP-Semantik-Tokens: farbliche Syntax-Hervorhebung via LSP",
        "code_document_links": "LSP-Document-Links: klickbare Links in Dokumentation/Kommentaren",
        "code_inline_values": "LSP-Inline-Values: Wertanzeige bei Variablen zur Debug-Zeit",
        # ── Search (10 Tools) ──
        "code_search_by_error": "Finde alle Stellen die einen bestimmten Error-Typ behandeln",
        "code_hot_paths": "Ranke Dateien nach transitiver Import-Häufigkeit",
        "code_blast_radius": "Was bricht wenn du dieses Symbol änderst? (Callers + Tests)",
        "code_pr_impact": "PR-Impact-Analyse: Diff + Call-Graph + Testabdeckung",
        "code_unused_finder": "Finde ungenutzte Imports und ungenutzte Funktionen",
        "code_duplicates": "Finde duplizierte/ähnliche Code-Blöcke via AST-Vergleich",
        "code_metrics": "Aggregierte Projekt-Metriken: LOC, Dateien, Comment-Ratio",
        "code_complexity": "Zyklomatische Komplexität pro Funktion mit Rank A-E",
        "code_cycle_detector": "Finde zirkuläre Import-Ketten mit Tarjans SCC Algorithmus",
        "code_dependency_graph": "Visueller Dependency-Graph als Mermaid-Diagramm",
        "code_dependency_risk": "Bewerte Abhängigkeitsrisiken (Score 0-10)",
        # ── Edit/Refactoring (5 Tools) ──
        "code_replace_body": "Ersetze die komplette Definition eines Symbols (AST-basiert)",
        "code_safe_delete": "Lösche ein Symbol NUR wenn es keine externen Referenzen hat",
        "code_insert_before": "Füge Code VOR einer Symbol-Definition ein (AST-basiert)",
        "code_insert_after": "Füge Code NACH einer Symbol-Definition ein (AST-basiert)",
        "code_move": "Verschiebe ein Symbol zwischen Dateien via AST-Extraktion",
        # ── Git (5 Tools) ──
        "code_git_blame": "Per-Line Git-Blame für eine Datei",
        "code_git_diff_file": "Zeige uncommitted Git-Diff für eine Datei",
        "code_git_log_symbol": "Git-Log für ein bestimmtes Symbol (Autor, Datum, Message)",
        "code_todo_finder": "Finde TODO/FIXME/HACK/KNOWN-BUG Kommentare im Codebase",
        "code_merge_conflict_finder": "Finde Merge-Konflikt-Markierungen (<<<<<<<, =======)",
        # ── Custom/Extra (8 Tools) ──
        "code_docstring_generate": "Generiere Docstring-Template aus AST-Signatur",
        "code_batch_refactor": "ast-grep Bulk-Refactoring über mehrere Dateien (dry-run)",
        "code_security_scan": "Security-Scan: hardcodierte Secrets, SQL-Injection, Path-Traversal",
        "code_generate_tests": "Generiere Test-Gerüst aus einer Funktions-Signatur",
        "code_migration": "YAML-basierte Bulk-Migrationen über ein Projekt",
        "code_diff_analysis": "Vergleiche zwei Git-Refs: Complexity-Delta + Blast Radius",
        "code_review_assistant": "Automated Code-Review zwischen Git-Refs (Diff + Security)",
        "code_export": "Exportiere Symbol-Index als JSON/Markdown für Doku",
        # ── Knowledge Graph (3 Tools) ──
        "code_index": "Baue persistierten Knowledge Graph (SQLite) für ein Projekt",
        "code_graph_query": "Query den Knowledge Graph: Callers, Callees, Hot Paths, Cycles",
        "code_overview": "Kompakte Symbol-Übersicht einer Datei als Tree-View",
        # ── Timeline (1 Tool) ──
        "code_timeline": "Evolution eines Symbols über die Git-History",
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
