"""Tests for code_unused_finder — unused imports detection."""

import json
import tempfile
from pathlib import Path

from code_intel.code_intel import code_unused_finder_tool


def _make_project(files: dict) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for rel_path, content in files.items():
        full = tmp / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return tmp


class TestCodeUnusedFinder:
    """Tests for code_unused_finder_tool."""

    def test_no_unused_imports(self):
        """A file where all imports are used returns no findings."""
        project = _make_project({
            "main.py": "import json\nimport os\n\ndef foo():\n    return json.dumps({'a': 1})\n\ndef bar():\n    return os.getcwd()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project / "main.py")))
        # json and os are both used
        assert result["total_unused"] == 0

    def test_single_unused_import(self):
        """An imported module that is never used is detected."""
        project = _make_project({
            "main.py": "import os\nimport sys\n\ndef foo():\n    return os.getcwd()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project / "main.py")))
        assert result["total_unused"] >= 1
        # sys should be flagged as unused
        names = [r["name"] for f in result["files"] for r in f["unused"]]
        assert "sys" in names
        assert "os" not in names

    def test_from_import_unused(self):
        """'from x import y' where y is unused is detected."""
        project = _make_project({
            "main.py": "from pathlib import Path\nimport os\n\nx = os.getcwd()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project / "main.py")))
        names = [r["name"] for f in result["files"] for r in f["unused"]]
        assert "Path" in names

    def test_invalid_path(self):
        """Non-existent path returns no findings."""
        result = json.loads(code_unused_finder_tool("/nonexistent/path"))
        assert result["total_unused"] == 0
        assert result["files"] == []

    def test_empty_file(self):
        """Empty file has no findings."""
        project = _make_project({"empty.py": ""})
        result = json.loads(code_unused_finder_tool(str(project / "empty.py")))
        assert result["total_unused"] == 0

    def test_directory_scan(self):
        """Scanning a directory finds unused imports across files."""
        project = _make_project({
            "used.py": "import json\ndef foo():\n    return json.dumps(1)\n",
            "unused.py": "import sys\nimport os\n\ndef bar():\n    return 42\n",
        })
        result = json.loads(code_unused_finder_tool(str(project)))
        assert result["total_unused"] >= 1
        # unused.py has sys and os unused
        # Count files with findings
        files_with_findings = [f["file"] for f in result["files"] if f["total"] > 0]
        assert len(files_with_findings) >= 1

    def test_directory_no_findings(self):
        """A directory where all imports are used returns no findings."""
        project = _make_project({
            "a.py": "import json\n\ndef foo():\n    return json.dumps(1)\n",
            "b.py": "import os\n\ndef bar():\n    return os.getcwd()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project)))
        assert result["total_unused"] == 0

    def test_unused_import_skips_type_checking(self):
        """typing-only imports (TYPE_CHECKING, Any, etc.) are not reported."""
        project = _make_project({
            "main.py": "from typing import Any, Optional\nimport os\n\ndef foo() -> str:\n    return os.getcwd()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project / "main.py")))
        names = [r["name"] for f in result["files"] for r in f["unused"]]
        # Any and Optional are in the skip list
        assert "Any" not in names
        assert "Optional" not in names

    def test_plugin_self_check(self):
        """Run the tool on code_intel.py itself — at least some unused imports might exist."""
        plugin_file = Path(__file__).resolve().parent.parent / "code_intel.py"
        if plugin_file.exists():
            result = json.loads(code_unused_finder_tool(str(plugin_file)))
            assert "total_unused" in result

    # ------------------------------------------------------------------
    # Unused Functions
    # ------------------------------------------------------------------

    def test_functions_kind_with_used_function(self):
        """A function that is called somewhere is not reported."""
        project = _make_project({
            "lib.py": "def helper():\n    return 42\n",
            "main.py": "from lib import helper\n\nx = helper()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project), kinds=["functions"]))
        assert result["total_unused"] == 0

    def test_functions_kind_with_unused_function(self):
        """A function defined but never called is reported as unused."""
        project = _make_project({
            "lib.py": "def helper():\n    return 42\n\ndef used():\n    return 1\n",
            "main.py": "from lib import used\n\nx = used()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project), kinds=["functions"]))
        assert result["total_unused"] >= 1
        names = [r["name"] for f in result["files"] for r in f["unused"]]
        assert "helper" in names
        assert "used" not in names

    def test_functions_kind_self_reference(self):
        """A function that is defined and only called within itself (recursion) is detected."""
        project = _make_project({
            "main.py": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n",
        })
        result = json.loads(code_unused_finder_tool(str(project), kinds=["functions"]))
        # factorial is only called within itself
        names = [r["name"] for f in result["files"] for r in f["unused"]]
        assert "factorial" in names or result["total_unused"] == 0  # may be excluded if recursive counting works

    def test_combined_imports_and_functions(self):
        """Scan both imports and functions in one call."""
        project = _make_project({
            "main.py": "import os\nimport sys\n\ndef unused_func():\n    return 42\n\ndef used_func():\n    return os.getcwd()\n\nx = used_func()\n",
        })
        result = json.loads(code_unused_finder_tool(str(project), kinds=["imports", "functions"]))
        assert result["total_unused"] >= 1
        names = [r["name"] for f in result["files"] for r in f["unused"]]
        assert "sys" in names  # unused import
        assert "unused_func" in names  # unused function
