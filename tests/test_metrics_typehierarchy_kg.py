"""Tests for tools/metrics.py, tools/type_hierarchy.py, tools/knowledge_graph.py."""
from __future__ import annotations

from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# tools/metrics.py
# ═══════════════════════════════════════════════════════════════════════════

class TestCountFileLines:
    """_count_file_lines() counts code/comment/blank lines by language."""

    def test_python_counts(self):
        from code_intel.tools.metrics import _count_file_lines
        src = "# comment\na = 1\nb = 2\n\n# another\ndef f():\n    pass\n"
        total, code, blank, comment = _count_file_lines(src, "python")
        assert total == 7
        assert code >= 4  # a=1, b=2, def f():, pass
        assert blank == 1
        assert comment == 2

    def test_empty_source(self):
        from code_intel.tools.metrics import _count_file_lines
        total, code, blank, comment = _count_file_lines("", "python")
        assert total == 0
        assert code == 0

    def test_jsx_count(self):
        from code_intel.tools.metrics import _count_file_lines
        src = "// comment\nconst x = 1;\n\n/* block */\nfunction y() {}\n"
        total, code, blank, comment = _count_file_lines(src, "jsx")
        assert total == 5
        assert code >= 3

    def test_go_count(self):
        from code_intel.tools.metrics import _count_file_lines
        src = "// comment\npackage main\n\nfunc main() {}\n"
        total, code, blank, comment = _count_file_lines(src, "go")
        assert total == 4
        assert code >= 2
        assert blank == 1


class TestFormatMetricsResult:
    """_format_metrics_result() builds the metrics output dict."""

    def test_basic_format(self):
        from code_intel.tools.metrics import _format_metrics_result
        result = _format_metrics_result(
            "/project", 5, {"python": 3, "go": 2},
            500, 300, 100, 100,
            [{"total": 5}, {"total": 10}, {"total": 15}]
        )
        assert result["total_files"] == 5
        assert result["files_by_language"]["python"] == 3
        assert result["total_lines"] == 500
        assert result["code_lines"] == 300
        assert result["blank_lines"] == 100
        assert result["comment_lines"] == 100
        assert result["avg_complexity"] == 10.0
        assert result["functions_analyzed"] == 3
        assert len(result["top_complexity"]) == 3

    def test_empty_complexity(self):
        from code_intel.tools.metrics import _format_metrics_result
        result = _format_metrics_result("/p", 0, {}, 0, 0, 0, 0, [])
        assert result["avg_complexity"] == 0
        assert result["functions_analyzed"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# tools/type_hierarchy.py
# ═══════════════════════════════════════════════════════════════════════════

class TestTypeHierarchyConstants:
    """Structural constants in type_hierarchy module."""

    def test_fallback_langs_contains_python(self):
        from code_intel.tools.type_hierarchy import _TYPE_HIERARCHY_FALLBACK_LANGS
        assert "python" in _TYPE_HIERARCHY_FALLBACK_LANGS

    def test_python_extends_has_valid_patterns(self):
        from code_intel.tools.type_hierarchy import _PYTHON_CLASS_EXTENDS
        assert len(_PYTHON_CLASS_EXTENDS) > 0
        # Should have a class pattern
        patterns_text = str(_PYTHON_CLASS_EXTENDS)
        assert "class" in patterns_text

    def test_ts_extends_has_valid_patterns(self):
        from code_intel.tools.type_hierarchy import _TS_CLASS_EXTENDS
        assert len(_TS_CLASS_EXTENDS) > 0


class TestFindTargetClassName:
    """_find_target_class_name() extracts class name from AST."""

    def test_finds_python_class_name_via_ast(self, tmp_path):
        from code_intel.tools.type_hierarchy import _find_target_class_name
        src = tmp_path / "mod.py"
        src.write_text("class MyClass(Base):\n    pass\n")
        try:
            result = _find_target_class_name(str(src), 1, "python")
            assert result == "MyClass"
        except Exception as e:
            # May fail without tree-sitter parser
            if "parser" in str(e).lower() or "language" in str(e).lower():
                pytest.skip(f"tree-sitter not available: {e}")
            raise


# ═══════════════════════════════════════════════════════════════════════════
# tools/knowledge_graph.py
# ═══════════════════════════════════════════════════════════════════════════

class TestDefaultCachePath:
    """_default_cache_path() returns a SQLite path."""

    def test_returns_db_path(self):
        from code_intel.tools.knowledge_graph import _default_cache_path
        path = _default_cache_path("/some/project")
        assert path.endswith(".db")
        assert "project" in path or "some" in path

    def test_includes_code_graph_dir(self):
        from code_intel.tools.knowledge_graph import _default_cache_path
        path = _default_cache_path("dummy")
        assert "code_graph" in path


class TestCodeIndexTool:
    """code_index_tool() builds knowledge graph index."""

    def test_invalid_path_returns_error(self):
        from code_intel.tools.knowledge_graph import code_index_tool
        result = code_index_tool("/nonexistent/path_xyz_123")
        assert "error" in result or "Error" in result or "❌" in result

    def test_indexes_current_project(self):
        # Use the code_intel project itself (small scope)
        import tempfile

        from code_intel.tools.knowledge_graph import code_index_tool
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "test.py").write_text("x = 1\n")
            result = code_index_tool(td, force_rescan=True)
            # Should succeed or return meaningful result
            assert result is not None


class TestCodeGraphQueryTool:
    """code_graph_query_tool() queries the knowledge graph."""

    def test_no_index_returns_error(self):
        from code_intel.tools.knowledge_graph import code_graph_query_tool
        result = code_graph_query_tool("/nonexistent_path_xyz", "summary")
        assert "error" in result or "Error" in result or "❌" in result

    def test_invalid_query_type(self):
        from code_intel.tools.knowledge_graph import code_graph_query_tool
        result = code_graph_query_tool("/tmp", "invalid_query_type_xyz")
        assert "error" in result or "Error" in result or "❌" in result
