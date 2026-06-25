"""Tests for code_hot_paths_tool — ImportGraph-based hot path detection."""

import tempfile
from pathlib import Path

from code_intel.code_tools import code_hot_paths_tool


def _make_project(files: dict) -> str:
    tmp = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return str(tmp)


class TestCodeHotPaths:
    def test_empty_dir_returns_error(self):
        path = _make_project({})
        result = code_hot_paths_tool(path=path)
        assert "error" in result

    def test_nonexistent_path(self):
        result = code_hot_paths_tool(path="/nonexistent")
        assert "error" in result

    def test_simple_project(self):
        path = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "import os\n",
            }
        )
        result = code_hot_paths_tool(path=path, top_n=5)
        assert "hot_paths" in result
        assert "project" in result
        assert "total_files" in result

    def test_file_is_rejected(self):
        tmp = Path(tempfile.mkdtemp()) / "test.py"
        tmp.write_text("x = 1\n")
        result = code_hot_paths_tool(path=str(tmp))
        assert "error" in result

    def test_with_cross_imports(self):
        path = _make_project(
            {
                "main.py": "from . import utils\nfrom . import models\n",
                "utils.py": "",
                "models.py": "",
            }
        )
        result = code_hot_paths_tool(path=path, top_n=5)
        import json

        data = json.loads(result)
        assert data["total_files"] >= 2
        assert "hot_paths" in data
