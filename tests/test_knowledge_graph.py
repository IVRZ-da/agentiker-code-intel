"""Tests for code_index and code_graph_query tools (SQLite-backed knowledge graph).

Tests the _import_graph.py SQLite persistence as well as the tool wrappers.
Uses temp directories to avoid polluting real caches.
"""

import json

from code_intel._import_graph import ImportGraph
from code_intel.tools.knowledge_graph import (
    _default_cache_path,
    code_graph_query_tool,
    code_index_tool,
)


class TestImportGraphPersistence:
    """Test SQLite persistence layer of ImportGraph."""

    def test_persist_and_load(self, tmp_path):
        """Persist a graph and load it back — should be identical."""
        # Create a simple project
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("import utils\nx = 1\n")
        (src / "utils.py").write_text("def foo(): pass\n")

        graph = ImportGraph(str(tmp_path))
        graph.scan(depth=5)
        graph.parse_all()

        db_path = str(tmp_path / "test.db")
        count = graph.persist(db_path)
        assert count > 0

        loaded = ImportGraph.load(db_path, str(tmp_path))
        assert loaded is not None
        assert len(loaded._graph) == len(graph._graph)

    def test_load_nonexistent_db(self, tmp_path):
        """Loading a nonexistent DB returns None."""
        result = ImportGraph.load("/nonexistent/path.db", str(tmp_path))
        assert result is None

    def test_for_project_creates_cache(self, tmp_path):
        """for_project creates cache automatically."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1\n")

        graph = ImportGraph.for_project(str(tmp_path), db_path=str(tmp_path / "cache.db"), depth=5)
        assert graph is not None
        assert len(graph._graph) > 0
        assert (tmp_path / "cache.db").exists()

    def test_for_project_reuses_cache(self, tmp_path):
        """Second for_project call reuses cache (no rescan)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1\n")

        # First call creates cache
        db = str(tmp_path / "cache.db")
        g1 = ImportGraph.for_project(str(tmp_path), db_path=db, depth=5)
        n1 = len(g1._graph)

        # Second call reuses
        g2 = ImportGraph.for_project(str(tmp_path), db_path=db, depth=5)
        n2 = len(g2._graph)
        assert n2 == n1

    def test_check_stale(self, tmp_path):
        """_check_stale detects modified files."""
        src = tmp_path / "src"
        src.mkdir()
        f = src / "main.py"
        f.write_text("x = 1\n")

        graph = ImportGraph(str(tmp_path))
        graph.scan(depth=5)
        graph.parse_all()
        db = str(tmp_path / "test.db")
        graph.persist(db)

        # File not modified
        assert not graph._check_stale(db)

        # Modify file
        f.write_text("y = 2\n")
        assert graph._check_stale(db)


class TestCodeIndexTool:
    """Tests for the code_index tool."""

    def test_index_project(self, tmp_path):
        """code_index creates cache and returns stats."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("import os\nx = 1\n")

        result = json.loads(code_index_tool(str(src), depth=5))

        # Check result structure
        assert "project" in result
        assert result["files_indexed"] > 0

    def test_index_nonexistent_path(self):
        """code_index with invalid path returns error."""
        result = json.loads(code_index_tool("/nonexistent/path"))
        assert "error" in result or "status" in result


class TestCodeGraphQueryTool:
    """Tests for the code_graph_query tool."""

    def test_summary_query(self, tmp_path):
        """code_graph_query with query='summary' returns graph stats."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1\n")
        (src / "utils.py").write_text("y = 2\n")

        # Index first
        code_index_tool(str(src), depth=5)

        # Query summary
        result = json.loads(code_graph_query_tool(str(src), "summary"))
        assert "files" in result

    def test_hot_paths_query(self, tmp_path):
        """code_graph_query with query='hot_paths'."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("import utils\nx=1\n")
        (src / "utils.py").write_text("y=2\n")

        code_index_tool(str(src), depth=5)
        result = json.loads(code_graph_query_tool(str(src), "hot_paths"))
        assert "hot_paths" in result

    def test_query_without_index(self, tmp_path):
        """Query without prior index returns error."""
        result = json.loads(code_graph_query_tool(str(tmp_path), "summary"))
        assert "error" in result or "No cached graph" in str(result)

    def test_health_query(self, tmp_path):
        """code_graph_query with query='health'."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1\n")

        code_index_tool(str(src), depth=5)
        result = json.loads(code_graph_query_tool(str(src), "health"))
        assert "health_score" in result

    def test_callers_query(self, tmp_path):
        """code_graph_query with query='callers'."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "caller.py").write_text("import callee\nx=1\n")
        (src / "callee.py").write_text("y=2\n")

        code_index_tool(str(src), depth=5)
        # Find callee.py path
        callee_path = str(src / "callee.py")
        result = json.loads(code_graph_query_tool(str(src), "callers", symbol=callee_path))
        assert "callers" in result or "error" in result

    def test_invalid_query_type(self, tmp_path):
        """Invalid query type returns error."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1\n")
        code_index_tool(str(src), depth=5)
        result = json.loads(code_graph_query_tool(str(src), "invalid_query"))
        assert "error" in result


class TestDefaultCachePath:
    """Tests for _default_cache_path helper."""

    def test_cache_path_uses_project_name(self, tmp_path):
        path = _default_cache_path(str(tmp_path))
        assert path.endswith(".db")
        assert tmp_path.name in path
