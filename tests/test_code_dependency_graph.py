"""Tests for code_dependency_graph — visual dependency graph."""

import json
import tempfile
from pathlib import Path

from code_intel.code_tools import code_dependency_graph_tool


def _make_project(files: dict) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for rel_path, content in files.items():
        full = tmp / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return tmp


class TestCodeDependencyGraph:
    """Tests for code_dependency_graph_tool."""

    def test_mermaid_output(self):
        """Mermaid output contains graph directive and edges."""
        project = _make_project({
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "",
        })
        result = code_dependency_graph_tool(str(project), format="mermaid")
        assert "graph LR" in result or "graph TD" in result
        assert "a.py" in result
        assert "b.py" in result
        assert "c.py" in result

    def test_mermaid_direction_td(self):
        """TD direction produces graph TD."""
        project = _make_project({
            "a.py": "import b\n",
            "b.py": "",
        })
        result = code_dependency_graph_tool(str(project), format="mermaid", direction="TD")
        assert "graph TD" in result

    def test_tree_output(self):
        """Tree output shows hierarchical structure."""
        project = _make_project({
            "a.py": "import b\n",
            "b.py": "",
        })
        result = code_dependency_graph_tool(str(project), format="tree")
        # Tree should show root nodes and their imports
        assert "a.py" in result or "b.py" in result
        assert len(result) > 10  # meaningful output

    def test_invalid_path(self):
        """Non-existent directory returns an error."""
        result = json.loads(code_dependency_graph_tool("/nonexistent/path"))
        assert "error" in result

    def test_empty_directory(self):
        """Empty directory returns an error."""
        tmp = Path(tempfile.mkdtemp())
        result = json.loads(code_dependency_graph_tool(str(tmp)))
        assert "error" in result

    def test_invalid_format(self):
        """Unknown format returns an error."""
        project = _make_project({"a.py": ""})
        result = json.loads(code_dependency_graph_tool(str(project), format="unknown"))
        assert "error" in result

    def test_module_level_flag(self):
        """module_level=True produces shorter labels."""
        project = _make_project({
            "src/modules/a.py": "import b\n",
            "src/modules/b.py": "",
        })
        result_full = code_dependency_graph_tool(str(project), format="mermaid", module_level=False)
        result_module = code_dependency_graph_tool(str(project), format="mermaid", module_level=True)
        # With module_level, full paths like src/modules/ should be shortened
        assert len(result_module) < len(result_full) or result_module != result_full
