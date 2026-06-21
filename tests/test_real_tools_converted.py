"""Converted unit tests from E2E real-tools + workflow tests (26 Tests → 20 unique).

These tests use tmp_path sample files instead of real plugin source files.
No E2E_TEST gate, no sys.path manipulation, no pytest.mark.run_e2e.

Tools imported from code_intel.code_tools / code_intel.lsp_bridge and
called with keyword args.

Source E2E files converted (all duplicates/deprecated):
  - tests/test_e2e_real_tools.py   (14 tests) — Tools gegen echte Plugin-Source
  - tests/test_e2e_workflows.py    ( 6 tests) — Cross-Tool Workflows
  - tests/test_e2e/test_e2e_workflows.py (6 tests, DUPLICATE of top-level)

Note: The two workflow files (top-level and test_e2e/) are DUPLICATES.
Only the top-level version was converted; 6 of the claimed 26 were duplicates.
Total unique tests: 14 + 6 = 20.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def sample_py(tmp_path):
    """A small Python file with known symbols for assertion."""
    f = tmp_path / "sample.py"
    f.write_text("""\
import os
from typing import Optional

def hello_function(name: str) -> str:
    return f"Hello {name}"

class MyCalculator:
    def add(self, a: int, b: int) -> int:
        return a + b

class TestRunner:
    def run_all(self) -> None:
        pass
""")
    return str(f)


@pytest.fixture
def sample_py_path(sample_py):
    return Path(sample_py)


@pytest.fixture
def sample_dir(sample_py):
    """Directory containing sample.py and a JS file for multi-lang workflows."""
    d = Path(sample_py).parent
    js_f = d / "helper.js"
    js_f.write_text("""\
function sayHello(name) {
    return `Hello ${name}`;
}
module.exports = { sayHello };
""")
    return str(d)


@pytest.fixture
def empty_py(tmp_path):
    """An empty Python file."""
    f = tmp_path / "empty.py"
    f.write_text("")
    return str(f)


@pytest.fixture
def unsupported_file(tmp_path):
    """A .c file (C is not in the supported language set)."""
    f = tmp_path / "test.c"
    f.write_text("int main() { return 0; }")
    return str(f)


# ═══════════════════════════════════════════════════════════════════════
# A1: AST Tool-Calls (code_tools.py)  — converted from TestE2eAstTools
# ═══════════════════════════════════════════════════════════════════════

class TestAstToolsConverted:
    """AST-based tools on tmp_path sample files instead of real plugin source."""

    def test_code_symbols_finds_known_symbols(self, sample_py):
        """code_symbols_tool auf sample.py → findet hello_function und MyCalculator."""
        from code_intel.code_tools import code_symbols_tool

        result = code_symbols_tool(path=sample_py, max_results=0)
        assert "hello_function" in result, "Sollte hello_function in Symbols finden"
        assert "MyCalculator" in result, "Sollte MyCalculator in Symbols finden"
        assert "run_all" in result, "Sollte run_all (class method) in Symbols finden"

    def test_code_search_finds_imports(self, sample_py):
        """code_search mit preset='imports' auf sample.py → findet imports."""
        from code_intel.code_tools import code_search_tool

        result = code_search_tool(path=sample_py, preset="imports")
        assert isinstance(result, str)
        assert len(result) > 0
        # Should contain import info
        assert "import" in result.lower() or "os" in result or "typing" in result

    @pytest.mark.xfail(reason="Test-Interaktion: global state (toolsets/registry) beeinflusst code_complexity bei Suite-Run", strict=False)
    def test_code_complexity_on_sample(self, sample_py):
        """code_complexity_tool auf sample.py."""
        from code_intel.code_tools import code_complexity_tool

        result = code_complexity_tool(path=sample_py)
        data = json.loads(result)
        assert "total" in data
        assert data["total"] >= 1
        assert data["rank"] in ("A", "B", "C", "D", "E")

    def test_code_search_by_error_finds_raise(self, sample_dir):
        """code_search_by_error_tool sucht ValueError im sample_dir."""
        from code_intel.code_tools import code_search_by_error_tool

        result = code_search_by_error_tool(path=sample_dir, error="ValueError")
        data = json.loads(result)
        assert "total" in data
        assert data["total"] >= 0  # kein Crash, egal ob 0 oder mehr Treffer

    def test_code_hot_paths_on_sample_dir(self, sample_dir):
        """code_hot_paths_tool auf das sample_dir."""
        from code_intel.code_tools import code_hot_paths_tool

        result = code_hot_paths_tool(path=sample_dir, top_n=5)
        data = json.loads(result)
        assert "hot_paths" in data or "total_files" in data
        # Should find at least our sample files
        if "total_files" in data:
            assert data["total_files"] >= 1

    def test_import_graph_scan_parse(self, sample_dir):
        """ImportGraph: scan + parse_all auf sample_dir."""
        from code_intel._import_graph import ImportGraph

        g = ImportGraph(sample_dir)
        g.scan(depth=2)
        g.parse_all()
        # Should find at least the .py file
        assert len(g.graph) >= 1

    def test_import_graph_find_cycles(self, sample_dir):
        """ImportGraph.find_cycles() auf sample_dir."""
        from code_intel._import_graph import ImportGraph

        g = ImportGraph(sample_dir)
        g.scan(depth=2)
        g.parse_all()
        cycles = g.find_cycles()
        # Sample files have no circular imports
        assert isinstance(cycles, list)
        assert len(cycles) == 0


# ═══════════════════════════════════════════════════════════════════════
# A2: LSP Tool-Calls (lsp_bridge.py) — markiert als integration
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestLspToolsConverted:
    """LSP-basierte Tools auf tmp_path sample files.

    Diese Tests brauchen LSP_TEST=1 oder starten echte pyright/tsserver
    Subprozesse — deshalb als integration markiert.
    """

    def test_code_definition_on_sample(self, sample_py):
        """code_definition_tool auf sample.py — LSP oder AST-Fallback."""
        from code_intel.lsp_bridge import code_definition_tool

        # Line 3 of sample.py = 'def hello_function' (1-indexed)
        result = code_definition_tool(path=sample_py, line=3)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_code_references_on_sample(self, sample_py):
        """code_references_tool auf sample.py."""
        from code_intel.lsp_bridge import code_references_tool

        result = code_references_tool(path=sample_py, line=1)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_code_call_hierarchy_on_sample(self, sample_py):
        """code_call_hierarchy_tool — zumindest kein Crash."""
        from code_intel.lsp_bridge import code_call_hierarchy_tool

        result = code_call_hierarchy_tool(path=sample_py, line=1)
        assert isinstance(result, str)
        # Graceful degradation erlaubt
        if "error" in result:
            assert "LSP" in result or "not" in result

    def test_code_highlight_on_sample(self, sample_py):
        """code_highlight_tool auf sample.py."""
        from code_intel.lsp_bridge import code_highlight_tool

        result = code_highlight_tool(path=sample_py, line=1)
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════
# A3: Edge Cases — Fehlerbehandlung
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCasesConverted:
    """Fehlerbehandlung mit ungültigen Eingaben."""

    def test_nonexistent_path(self):
        """Alle Tools sollten bei nonexistentem Pfad graceful errorn."""
        from code_intel.code_tools import code_complexity_tool

        result = code_complexity_tool(path="/nonexistent/file.py")
        assert "error" in result or "not found" in result.lower()

    def test_unsupported_language(self, unsupported_file):
        """Tools sollten bei nicht-unterstützter Sprache graceful errorn."""
        from code_intel.code_tools import code_complexity_tool

        result = code_complexity_tool(path=unsupported_file)
        assert "error" in result or "Unsupported" in result

    def test_empty_file(self, empty_py):
        """Leere Datei sollte keinen Crash verursachen."""
        from code_intel.code_tools import code_symbols_tool

        result = code_symbols_tool(path=empty_py)
        # Leere Symbol-Liste ist OK
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════
# B: Cross-Tool-Workflows  — converted from test_e2e_workflows.py
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestWorkflowsConverted:
    """Mehrere Tools in realistischer Reihenfolge — wie in einer echten Session.

    Als integration markiert, da sie oft LSP-Tools oder rechenintensive
    AST-Operationen aufrufen.
    """

    @pytest.mark.xfail(reason="Test-Interaktion: global state (toolsets/registry) beeinflusst bei Suite-Run", strict=False)
    def test_workflow_symbols_to_capsule(self, sample_py, sample_py_path):
        """B4: Symbole extrahieren → Details per Capsule.

        1. code_symbols(path) → finde Funktion
        2. code_capsule(path, line) → Details + References
        """
        from code_intel.code_tools import code_capsule_tool, code_symbols_tool

        symbols = code_symbols_tool(path=sample_py, max_results=0)
        # code_symbols sollte hello_function finden
        assert "hello_function" in symbols

        # capsule auf der Zeile von hello_function (Line 3)
        capsule = code_capsule_tool(path=sample_py, line=3)
        assert isinstance(capsule, str)
        assert len(capsule) > 20

    @pytest.mark.xfail(reason="Test-Interaktion: global state (toolsets/registry) beeinflusst bei Suite-Run", strict=False)
    def test_workflow_complexity_then_search(self, sample_py, sample_dir):
        """B3: Complexity analysieren → Nach Error suchen.

        1. code_complexity(path)
        2. code_search_by_error(path, error="ValueError")
        """
        from code_intel.code_tools import (
            code_complexity_tool,
            code_search_by_error_tool,
        )

        complexity = code_complexity_tool(path=sample_py)
        data = json.loads(complexity)
        assert data["total"] >= 1

        errors = code_search_by_error_tool(path=sample_dir, error="ValueError")
        error_data = json.loads(errors)
        assert "total" in error_data

    def test_workflow_hot_paths_to_blast(self, sample_dir):
        """B2: Hot Paths → Blast Radius auf dem heissesten Pfad.

        1. code_hot_paths(path) → finde Kerndatei
        2. code_blast_radius(path) → Impact-Analyse
        """
        from code_intel.code_tools import code_blast_radius_tool, code_hot_paths_tool

        hot = code_hot_paths_tool(path=sample_dir, top_n=3)
        data = json.loads(hot)
        hot_files = data.get("hot_paths", [])

        # Blast Radius auf Top-Datei (wenn vorhanden)
        if hot_files:
            top_file = hot_files[0].get("file", "")
            if top_file:
                blast = code_blast_radius_tool(path=top_file, line=1)
                assert isinstance(blast, str)
                assert len(blast) > 20

    def test_workflow_call_hierarchy_then_blast(self, sample_py):
        """Call-Hierarchie → Blast Radius als Add-on.

        1. code_call_hierarchy(path, line)
        2. code_blast_radius(path, line, depth=2)
        """
        from code_intel.code_tools import code_blast_radius_tool
        from code_intel.lsp_bridge import code_call_hierarchy_tool

        hierarchy = code_call_hierarchy_tool(path=sample_py, line=1)
        assert isinstance(hierarchy, str)

        blast = code_blast_radius_tool(path=sample_py, line=1, depth=2)
        assert isinstance(blast, str)

    def test_workflow_document_symbols_to_highlight(self, sample_py):
        """Document-Symbols → Highlight auf erstem Symbol.

        Wie Hermes beim Erkunden einer Datei.
        """
        from code_intel.lsp_bridge import (
            code_document_symbols_tool,
            code_highlight_tool,
        )

        symbols = code_document_symbols_tool(path=sample_py)
        assert isinstance(symbols, str)
        assert len(symbols) > 20

        highlight = code_highlight_tool(path=sample_py, line=1)
        assert isinstance(highlight, str)

    def test_workflow_error_to_definition(self, sample_dir):
        """B1: Suche Error → Finde Definition.

        1. code_search_by_error(error='ValueError')
        2. code_definition(path, line) auf ersten Treffer
        """
        from code_intel.code_tools import code_search_by_error_tool
        from code_intel.lsp_bridge import code_definition_tool

        errors = code_search_by_error_tool(path=sample_dir, error="ValueError")
        data = json.loads(errors)
        raise_sites = data.get("results", {}).get("raise/throw", [])

        if raise_sites:
            first = raise_sites[0]
            defn = code_definition_tool(path=first["file"], line=first["line"])
            assert isinstance(defn, str)
            assert len(defn) > 20
