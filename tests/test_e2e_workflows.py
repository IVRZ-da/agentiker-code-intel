"""
E2E Tests — Phase B: Cross-Tool-Workflows.

Testet mehrere Tools in realistischer Reihenfolge,
genau wie Hermes sie in einer echten Session nutzt.
"""

import json
import os
from pathlib import Path

RUN_E2E = os.environ.get("E2E_TEST") == "1"

PLUGIN_DIR = Path(__file__).resolve().parent.parent
CODE_INTEL_PY = str(PLUGIN_DIR / "code_intel.py")
LSP_BRIDGE_PY = str(PLUGIN_DIR / "lsp_bridge.py")


class TestE2eWorkflows:
    """Mehrere Tools in realistischer Reihenfolge — wie in einer echten Session."""

    def test_e2e_workflow_error_to_definition(self):
        """B1: Suche Error → Finde Definition.

        Wie Hermes in der Session 2026-06-17:
        1. code_search_by_error('FileNotFoundError')
        2. code_definition(path, line) auf ersten Treffer
        """
        from code_intel.code_intel import code_search_by_error_tool
        from code_intel.lsp_bridge import code_definition_tool

        errors = code_search_by_error_tool(
            path=str(PLUGIN_DIR), error="FileNotFoundError"
        )
        data = json.loads(errors)
        raise_sites = data.get("results", {}).get("raise/throw", [])

        if raise_sites:
            first = raise_sites[0]
            defn = code_definition_tool(path=first["file"], line=first["line"])
            assert isinstance(defn, str)
            assert len(defn) > 20

    def test_e2e_workflow_symbols_to_capsule(self):
        """B4: Symbole extrahieren → Details per Capsule.

        Wie Hermes wenn sie eine neue Datei versteht:
        1. code_symbols(path) → finde Funktion
        2. code_capsule(path, line) → Details + References
        """
        from code_intel.code_intel import code_symbols_tool, code_capsule_tool

        symbols = code_symbols_tool(CODE_INTEL_PY)
        # code_symbols findet code_blast_radius_tool
        assert "code_blast_radius_tool" in symbols

        # Finde Line von code_blast_radius_tool in symbols
        capsule = code_capsule_tool(CODE_INTEL_PY, line=30)
        assert isinstance(capsule, str)
        assert len(capsule) > 20

    def test_e2e_workflow_complexity_then_search(self):
        """B3: Complexity analysieren → Nach Error suchen.

        Wie Hermes wenn sie die Wartbarkeit prüft:
        1. code_complexity(path, function="...")
        2. code_search_by_error(path, error="FileNotFoundError")
        """
        from code_intel.code_intel import code_complexity_tool, code_search_by_error_tool

        complexity = code_complexity_tool(path=CODE_INTEL_PY)
        data = json.loads(complexity)
        assert data["total"] >= 1

        errors = code_search_by_error_tool(
            path=str(PLUGIN_DIR), error="FileNotFoundError"
        )
        error_data = json.loads(errors)
        assert "total" in error_data

    def test_e2e_workflow_hot_paths_to_blast(self):
        """B2: Hot Paths → Blast Radius auf dem heissesten Pfad.

        Wie Hermes beim Refactoring:
        1. code_hot_paths(path) → finde Kerndatei
        2. code_blast_radius(path) → Impact-Analyse
        """
        from code_intel.code_intel import code_hot_paths_tool, code_blast_radius_tool

        hot = code_hot_paths_tool(path=str(PLUGIN_DIR), top_n=3)
        data = json.loads(hot)
        hot_files = data.get("hot_paths", [])

        # Blast Radius auf Top-Datei (wenn vorhanden)
        if hot_files:
            top_file = hot_files[0].get("file", "")
            if top_file:
                blast = code_blast_radius_tool(path=top_file, line=1)
                assert isinstance(blast, str)
                assert len(blast) > 20

    def test_e2e_workflow_call_hierarchy_then_blast(self):
        """Call-Hierarchie → Blast Radius als Add-on.

        Wie Hermes beim Verstehen einer unbekannten Funktion:
        1. code_call_hierarchy(path, line)
        2. code_blast_radius(path, line, depth=2)
        """
        from code_intel.lsp_bridge import code_call_hierarchy_tool
        from code_intel.code_intel import code_blast_radius_tool

        hierarchy = code_call_hierarchy_tool(
            path=CODE_INTEL_PY, line=1, direction="incoming"
        )
        assert isinstance(hierarchy, str)

        blast = code_blast_radius_tool(path=CODE_INTEL_PY, line=1, depth=2)
        assert isinstance(blast, str)

    def test_e2e_workflow_document_symbols_to_highlight(self):
        """Document-Symbols → Highlight auf erstem Symbol.

        Wie Hermes beim Erkunden einer Datei.
        """
        from code_intel.lsp_bridge import (
            code_document_symbols_tool,
            code_highlight_tool,
        )

        symbols = code_document_symbols_tool(path=CODE_INTEL_PY)
        assert isinstance(symbols, str)
        assert len(symbols) > 20

        highlight = code_highlight_tool(path=CODE_INTEL_PY, line=1)
        assert isinstance(highlight, str)
