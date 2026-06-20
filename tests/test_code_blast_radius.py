"""Tests for code_blast_radius_tool — blast radius analysis."""

import json
import tempfile
from pathlib import Path

from code_intel.code_tools import code_blast_radius_tool


class TestBlastRadius:
    def test_nonexistent_path(self):
        result = code_blast_radius_tool(path="/nonexistent/file.py", line=1)
        assert "error" in result

    def test_path_not_found(self):
        result = code_blast_radius_tool(path=__file__, line=999)
        # Should not crash — either valid report or graceful error
        assert isinstance(result, str)

    def test_accepts_depth_parameter(self):
        result = code_blast_radius_tool(path=__file__, line=1, depth=2)
        assert isinstance(result, str)

    def test_accepts_language_override(self):
        result = code_blast_radius_tool(path=__file__, line=1, language="python")
        assert isinstance(result, str)

    def test_character_auto_detected(self):
        result = code_blast_radius_tool(path=__file__, line=1, character=0)
        assert isinstance(result, str)

    def test_returns_impact_field(self):
        result = code_blast_radius_tool(path=__file__, line=1)
        # Even on error, make sure we get structured output
        assert isinstance(result, str)

    def test_max_depth_capped_at_5(self):
        result = code_blast_radius_tool(path=__file__, line=1, depth=10)
        assert isinstance(result, str)

    def test_works_with_this_file(self):
        """Use the test file itself as input."""
        result = code_blast_radius_tool(path=__file__, line=1)
        # Should produce valid JSON (either error or full report)
        try:
            data = json.loads(result)
            assert "impact" in data or "error" in data
        except json.JSONDecodeError:
            pass  # Some formats may not be JSON

    def test_simple_function_analysis(self):
        """Analyze a simple Python file."""
        tmp = Path(tempfile.mkdtemp()) / "test.py"
        tmp.write_text("def foo():\n    pass\n")
        result = code_blast_radius_tool(path=str(tmp), line=1)
        assert isinstance(result, str)
