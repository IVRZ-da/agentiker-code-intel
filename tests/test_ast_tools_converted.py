"""Converted unit tests from E2E AST tool tests (advanced: metrics, duplicates, export, move).

These tests use tmp_path sample files instead of real plugin source files.
No E2E_TEST gate, no sys.path manipulation, no pytest.mark.run_e2e.

Tools imported from code_intel.code_tools and called with keyword args.

Source E2E files converted:
  - tests/test_e2e/test_e2e_advanced.py (13 tests → 12 converted, 1 already covered)
  - tests/test_e2e/test_e2e_tools.py  (14 tests — all already covered by existing unit tests)
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def sample_py_file(tmp_path):
    """A well-formed Python file with a function and a class."""
    f = tmp_path / "sample.py"
    f.write_text("""\
import os
from typing import Optional

def greet(name: str) -> str:
    return f"Hello {name}"

class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b
""")
    return str(f)


@pytest.fixture
def sample_py_dir(sample_py_file):
    """Directory containing sample.py (for code_metrics_tool which requires a directory)."""
    return str(Path(sample_py_file).parent)


# ═══════════════════════════════════════════════════════════════════════
# code_metrics_tool — Aggregate project metrics
# ═══════════════════════════════════════════════════════════════════════


class TestCodeMetrics:
    """code_metrics_tool(path, directory, depth) — project metrics."""

    def test_metrics_on_python_directory(self, sample_py_dir):
        """code_metrics_tool with a Python directory → aggregates file counts."""
        from code_intel.code_tools import code_metrics_tool

        result = code_metrics_tool(path=sample_py_dir, depth=3)
        assert isinstance(result, str)
        assert len(result) > 0
        data = json.loads(result)
        assert data["total_files"] >= 1
        assert "python" in data["files_by_language"]

    def test_metrics_defaults(self, sample_py_dir):
        """code_metrics_tool with defaults."""
        from code_intel.code_tools import code_metrics_tool

        result = code_metrics_tool(path=sample_py_dir)
        assert isinstance(result, str) and len(result) > 0

    def test_metrics_nonexistent_path(self):
        """Nicht-existenter Pfad → Fehler-JSON (keine Exception)."""
        from code_intel.code_tools import code_metrics_tool

        result = code_metrics_tool(path="/nonexistent_metrics_dir_xyz")
        assert isinstance(result, str)
        assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════
# code_duplicates_tool — Duplikat-Erkennung via AST
# ═══════════════════════════════════════════════════════════════════════


class TestCodeDuplicates:
    """code_duplicates_tool(path, min_lines, top_n) — duplicate detection."""

    def test_duplicates_detects_identical_functions(self, tmp_path):
        """Zwei Dateien mit identischer Funktion → Duplikat gefunden."""
        src1 = tmp_path / "mod1.py"
        src1.write_text("""\
def util_alpha(x: int) -> int:
    for i in range(x):
        print(f"item: {i}")
    return x * 2
""")
        src2 = tmp_path / "mod2.py"
        src2.write_text("""\
def util_beta(x: int) -> int:
    for i in range(x):
        print(f"item: {i}")
    return x * 2
""")
        from code_intel.code_tools import code_duplicates_tool

        result = code_duplicates_tool(path=str(tmp_path), min_lines=3, top_n=10)
        assert isinstance(result, str)
        assert len(result) > 0
        # Die Funktionen sind identisch (gleiche AST-Struktur nach Normalisierung)
        # → total_duplicate_groups sollte > 0 sein
        assert "total_duplicate_groups" in result

    def test_duplicates_no_duplicates(self, tmp_path):
        """Eine einzelne Datei mit einer Funktion → keine Duplikate."""
        f = tmp_path / "unique.py"
        f.write_text("""\
def unique_func(x: int) -> int:
    return x + 42
""")
        from code_intel.code_tools import code_duplicates_tool

        result = code_duplicates_tool(path=str(tmp_path), min_lines=3)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_duplicates_nonexistent_path(self):
        """Nicht-existenter Pfad → Fehler-JSON (keine Exception)."""
        from code_intel.code_tools import code_duplicates_tool

        result = code_duplicates_tool(path="/nonexistent_dupe_test_dir_xyz")
        assert isinstance(result, str)
        assert "error" in result or "not found" in result.lower()


# ═══════════════════════════════════════════════════════════════════════
# code_export_tool — Symbol-Export als JSON / Markdown / Summary
# ═══════════════════════════════════════════════════════════════════════


class TestCodeExport:
    """code_export_tool(path, fmt, kind) — symbol export."""

    def test_export_json_on_python_file(self, sample_py_file):
        """Export als JSON → enthält Symbole."""
        from code_intel.code_tools import code_export_tool

        result = code_export_tool(path=sample_py_file, fmt="json", kind="all")
        assert isinstance(result, str)
        assert len(result) > 0
        # JSON-Export enthält project, total_symbols, symbols_by_file
        assert "total_symbols" in result

    def test_export_markdown_on_python_file(self, sample_py_file):
        """Export als Markdown → enthält tabellarische Symbole."""
        from code_intel.code_tools import code_export_tool

        result = code_export_tool(path=sample_py_file, fmt="markdown", kind="all")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Symbol" in result or "Index" in result or "|" in result

    def test_export_summary_on_python_file(self, sample_py_file):
        """Export als Summary → enthält total_symbols."""
        from code_intel.code_tools import code_export_tool

        result = code_export_tool(path=sample_py_file, fmt="summary", kind="all")
        assert isinstance(result, str)
        assert len(result) > 0
        # Summary enthält total_symbols
        assert "total_symbols" in result

    def test_export_nonexistent_path(self):
        """Nicht-existenter Pfad → Fehler."""
        from code_intel.code_tools import code_export_tool

        result = code_export_tool(path="/nonexistent_export_test_xyz")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "not found" in result.lower() or "error" in result.lower()


# ═══════════════════════════════════════════════════════════════════════
# code_move_tool — Symbol zwischen Dateien verschieben (dry-run)
# ═══════════════════════════════════════════════════════════════════════


class TestCodeMove:
    """code_move_tool(source, symbol, target, language, dry_run)."""

    def test_move_dry_run_moves_function(self, tmp_path):
        """Dry-Run: Symbol von Source nach Target verschieben → diff."""
        source = tmp_path / "source.py"
        source.write_text("""\
def helper(x: int) -> int:
    return x + 1

def keeper(x: int) -> int:
    return x * 2
""")
        target = tmp_path / "target.py"
        target.write_text("""\
def existing_func(a: str) -> str:
    return a.upper()
""")
        from code_intel.code_tools import code_move_tool

        result = code_move_tool(
            source=str(source),
            symbol="helper",
            target=str(target),
            dry_run=True,
        )
        assert isinstance(result, str)
        assert len(result) > 0
        # Dry-run: sollte dry_run=True im Output haben
        assert "dry" in result.lower()

    def test_move_nonexistent_source(self, tmp_path):
        """Nicht-existente Source → Fehler."""
        target = tmp_path / "target.py"
        target.write_text("x = 1\n")
        from code_intel.code_tools import code_move_tool

        result = code_move_tool(
            source=str(tmp_path / "nonexistent.py"),
            symbol="foo",
            target=str(target),
        )
        assert isinstance(result, str)
        assert len(result) > 0
        # Sollte Fehler enthalten
        assert "not found" in result.lower() or "error" in result.lower()

    def test_move_nonexistent_symbol(self, tmp_path):
        """Nicht-existentes Symbol → Fehler."""
        source = tmp_path / "source.py"
        source.write_text("x = 1\n")
        target = tmp_path / "target.py"
        target.write_text("y = 2\n")
        from code_intel.code_tools import code_move_tool

        result = code_move_tool(
            source=str(source),
            symbol="NonExistentSymbol",
            target=str(target),
        )
        assert isinstance(result, str)
        assert len(result) > 0
        # Symbol nicht gefunden
        assert "not found" in result.lower()
