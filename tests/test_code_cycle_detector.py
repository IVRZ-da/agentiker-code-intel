"""Tests for code_cycle_detector — circular import detection."""

import json
import tempfile
from pathlib import Path

from code_intel.code_tools import code_cycle_detector_tool


def _make_project(files: dict) -> Path:
    """Create a temporary project with files.

    files: {"src/main.py": "content", "src/utils.py": "content", ...}
    """
    tmp = Path(tempfile.mkdtemp())
    for rel_path, content in files.items():
        full = tmp / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return tmp


class TestCodeCycleDetector:
    """Tests for code_cycle_detector_tool."""

    def test_no_cycles(self):
        """A project without circular imports returns an empty list."""
        project = _make_project({
            "main.py": "import utils\n",
            "utils.py": "import helpers\n",
            "helpers.py": "import json\n",
        })
        result = json.loads(code_cycle_detector_tool(str(project)))
        assert result["cycles_found"] == 0
        assert result["cycles"] == []
        assert result["total_files"] == 3

    def test_direct_cycle_python(self):
        """A -> B -> A is detected as a 2-file cycle."""
        project = _make_project({
            "a.py": "import b\n",
            "b.py": "import a\n",
        })
        result = json.loads(code_cycle_detector_tool(str(project)))
        assert result["cycles_found"] >= 1
        # Check that the cycle contains both a.py and b.py
        cycle_files = set()
        for c in result["cycles"]:
            for f in c["cycle"]:
                cycle_files.add(Path(f).name)
        assert "a.py" in cycle_files
        assert "b.py" in cycle_files

    def test_transitive_cycle_python(self):
        """A -> B -> C -> A is detected as a 3-file cycle."""
        project = _make_project({
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "import a\n",
        })
        result = json.loads(code_cycle_detector_tool(str(project)))
        assert result["cycles_found"] >= 1
        # Verify the cycle spans all 3 files
        cycle_files = set()
        for c in result["cycles"]:
            for f in c["cycle"]:
                cycle_files.add(Path(f).name)
        assert "a.py" in cycle_files
        assert "b.py" in cycle_files
        assert "c.py" in cycle_files

    def test_within_subdir(self):
        """Cycle detection works within subdirectories (TypeScript)."""
        project = _make_project({
            "src/a.ts": "import { b } from './b';\n",
            "src/b.ts": "import { a } from './a';\n",
        })
        result = json.loads(code_cycle_detector_tool(str(project)))
        assert result["cycles_found"] >= 1
        cycle_files = set()
        for c in result["cycles"]:
            for f in c["cycle"]:
                cycle_files.add(Path(f).name)
        assert "a.ts" in cycle_files
        assert "b.ts" in cycle_files

    def test_invalid_path(self):
        """Non-existent directory returns an error."""
        result = json.loads(code_cycle_detector_tool("/nonexistent/path"))
        assert "error" in result

    def test_empty_directory(self):
        """Empty directory with no source files returns an error."""
        tmp = Path(tempfile.mkdtemp())
        result = json.loads(code_cycle_detector_tool(str(tmp)))
        assert "error" in result

    def test_max_cycles_limits_output(self):
        """max_cycles parameter limits the number of reported cycles."""
        project = _make_project({
            "a.py": "import b\n",
            "b.py": "import a, c\n",
            "c.py": "import b\n",
        })
        result_all = json.loads(code_cycle_detector_tool(str(project), max_cycles=0))
        result_limited = json.loads(code_cycle_detector_tool(str(project), max_cycles=1))
        # With max_cycles=0 (unlimited), we should get all cycles
        assert result_all["cycles_found"] > 0
        # With max_cycles=1, we should have at most 1 cycle
        # (or 0 if cycles_found is 0, but we know there are cycles)
        assert result_limited["cycles_found"] <= 1
        assert len(result_limited["cycles"]) <= 1
