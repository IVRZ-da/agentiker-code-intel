"""Tests for tools/type_hierarchy.py — AST type hierarchy fallback.

Target: bring coverage from ~8% to 40% by covering error paths + basic
extraction in _ast_type_hierarchy_supertypes/subtypes and helpers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_intel.tools.type_hierarchy import (
    _ast_type_hierarchy_supertypes,
    _ast_type_hierarchy_subtypes,
    _find_target_class_name,
    _scan_subtypes_in_project,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _py_file(path: Path, name: str, content: str) -> Path:
    f = path / name
    f.write_text(content)
    return f


class TestAstTypeHierarchySupertypes:
    """_ast_type_hierarchy_supertypes — error paths + basic extraction."""

    def test_nonexistent_path(self):
        """Non-existent file → None."""
        result = _ast_type_hierarchy_supertypes("/nonexistent/file.py", 1)
        assert result is None

    def test_unsupported_language(self, tmp_path):
        """File with unsupported extension → None."""
        f = _py_file(tmp_path, "test.rb", "class Foo; end")
        result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_language_obj_none(self, tmp_path):
        """_get_language returns None → None."""
        f = _py_file(tmp_path, "test.py", "class Foo: pass")
        with patch("code_intel.code_tools._get_language", return_value=None):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
            assert result is None

    def test_parser_none(self, tmp_path):
        """_get_parser returns None → None."""
        f = _py_file(tmp_path, "test.py", "class Foo: pass")
        with (
            patch("code_intel.code_tools._get_language") as ml,
            patch("code_intel.code_tools._get_parser", return_value=None),
        ):
            ml.return_value = MagicMock()
            result = _ast_type_hierarchy_supertypes(str(f), 1)
            assert result is None

    def test_query_compile_fails(self, tmp_path):
        """Query compile raises → None (bare except)."""
        f = _py_file(tmp_path, "test.py", "class Foo: pass")
        with (
            patch("code_intel.code_tools._get_language") as ml,
            patch("code_intel.code_tools._get_parser") as mp,
            patch("code_intel.tools.type_hierarchy.Query", side_effect=Exception("boom")),
        ):
            ml.return_value = MagicMock()
            mp.return_value = MagicMock()
            result = _ast_type_hierarchy_supertypes(str(f), 1)
            assert result is None


class TestAstTypeHierarchySubtypes:
    """_ast_type_hierarchy_subtypes — error paths."""

    def test_nonexistent_path(self):
        """Non-existent file → None."""
        result = _ast_type_hierarchy_subtypes("/nonexistent/file.py", 1)
        assert result is None

    def test_unsupported_language(self, tmp_path):
        """File with unsupported extension → None."""
        f = _py_file(tmp_path, "test.rb", "class Foo; end")
        result = _ast_type_hierarchy_subtypes(str(f), 1)
        assert result is None


class TestFindTargetClassName:
    """_find_target_class_name — extraction from AST."""

    def test_no_class_at_line(self, tmp_path):
        """No class at given line → None."""
        f = _py_file(tmp_path, "test.py", "x = 1\n")
        from code_intel.tools.language import _get_language, _get_parser
        lang = _get_language("python")
        parser = _get_parser("python")
        if lang is None or parser is None:
            pytest.skip("tree-sitter python parser not available")
        from tree_sitter import Query
        q = Query(lang, "(class_definition name: (identifier) @cn) @cd")
        source = f.read_bytes()
        result = _find_target_class_name(str(f), 99, "python", parser, q, source)
        assert result is None


class TestScanSubtypesInProject:
    """_scan_subtypes_in_project — directory scanning."""

    def test_empty_dir(self, tmp_path):
        """Empty directory → empty list."""
        result = _scan_subtypes_in_project("MyClass", tmp_path, MagicMock(), MagicMock(), "python")
        assert result == []

    def test_ignores_node_modules(self, tmp_path):
        """Skips node_modules."""
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "test.py").write_text("class Child(MyClass): pass")
        result = _scan_subtypes_in_project("MyClass", tmp_path, MagicMock(), MagicMock(), "python")
        assert result == []

    def test_ignores_venv(self, tmp_path):
        """Skips .venv."""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "test.py").write_text("class Child(MyClass): pass")
        result = _scan_subtypes_in_project("MyClass", tmp_path, MagicMock(), MagicMock(), "python")
        assert result == []

    def test_os_error_skipped(self):
        """Non-existent directory → empty list."""
        result = _scan_subtypes_in_project("MyClass", Path("/nonexistent"), MagicMock(), MagicMock(), "python")
        assert result == []
