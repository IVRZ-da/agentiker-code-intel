"""Tests for code_review_assistant tool.

Tests the automated PR review functionality with mocked git commands.
"""

import json
from unittest.mock import MagicMock, patch

from code_intel.tools.review_assistant import (
    _check_added_imports,
    _check_debug_code,
    _check_todo_comments,
    _estimate_complexity,
    code_review_assistant_tool,
)


class TestHelperFunctions:
    """Tests for the pure helper functions."""

    def test_check_added_imports_finds_new_imports(self):
        diff = "+import os\n+from pathlib import Path\n x = 1"
        result = _check_added_imports(diff)
        assert len(result) == 2
        assert any("import os" in r for r in result)

    def test_check_added_imports_empty_diff(self):
        assert _check_added_imports("") == []

    def test_check_todo_comments_finds_todos(self):
        diff = "+x = 1\n+# TODO: fix this\n+y = 2"
        result = _check_todo_comments(diff)
        assert len(result) == 1
        assert "TODO" in result[0]

    def test_check_todo_comments_no_todos(self):
        assert _check_todo_comments("+x = 1\n+y = 2") == []

    def test_check_debug_code_finds_prints(self):
        diff = "+console.log('test')\n+x = 1"
        result = _check_debug_code(diff)
        assert len(result) >= 1

    def test_check_debug_code_clean(self):
        assert _check_debug_code("+x = 1\n+return x") == []

    def test_estimate_complexity_simple(self):
        result = _estimate_complexity("def f(): return 1")
        assert result["estimated_complexity"] >= 1
        assert result["rank"] in ("A", "B", "C", "D", "E")

    def test_estimate_complexity_high(self):
        code = """def f():
            if x: a
            elif y: b
            else: c
            for i in r: pass
            while True: break
            try: pass
            except: pass
            return 1"""
        result = _estimate_complexity(code)
        assert result["branches"] >= 2
        assert result["loops"] >= 1


class TestCodeReviewAssistant:
    """Tests for the main code_review_assistant_tool function."""

    def test_not_a_git_repo(self, tmp_path):
        """Not a git repo returns error."""
        result = json.loads(code_review_assistant_tool(str(tmp_path)))
        assert "error" in result or "Not a git repository" in str(result)

    @patch("subprocess.run")
    def test_no_changes(self, mock_run, tmp_path):
        """No changes between refs returns empty summary."""
        # Make it look like a git repo
        (tmp_path / ".git").mkdir()
        mock_run.return_value = MagicMock(stdout="", returncode=0)

        result = json.loads(code_review_assistant_tool(str(tmp_path)))
        assert "summary" in result
        assert result["summary"]["files_changed"] == 0

    @patch("subprocess.run")
    def test_single_file_changed(self, mock_run, tmp_path):
        """Single file with changes returns review."""
        (tmp_path / ".git").mkdir()

        # Mock git diff --name-only
        def mock_subprocess(args, **kwargs):
            cmd = " ".join(args) if isinstance(args, list) else args
            m = MagicMock()
            m.returncode = 0
            if "diff --name-only" in cmd:
                m.stdout = "test.py\n"
            elif "diff --stat" in cmd:
                m.stdout = "1 file changed\n"
            elif "test.py" in cmd:
                m.stdout = "+def foo():\n+    pass\n"
            else:
                m.stdout = ""
            return m

        mock_run.side_effect = mock_subprocess

        # Create the file
        (tmp_path / "test.py").write_text("def foo():\n    pass\n")

        result = json.loads(code_review_assistant_tool(str(tmp_path)))
        assert result["summary"]["files_changed"] == 1
        assert "files" in result

    @patch("subprocess.run")
    def test_file_with_issues(self, mock_run, tmp_path):
        """File with debug code and TODOs should flag issues."""
        (tmp_path / ".git").mkdir()

        def mock_subprocess(args, **kwargs):
            cmd = " ".join(args) if isinstance(args, list) else args
            m = MagicMock()
            m.returncode = 0
            if "diff --name-only" in cmd:
                m.stdout = "buggy.py\n"
            elif "diff --stat" in cmd:
                m.stdout = "1 file changed\n"
            elif "buggy.py" in cmd:
                m.stdout = "+console.log('debug')\n+# TODO: fix later\n+x = 1\n"
            else:
                m.stdout = ""
            return m

        mock_run.side_effect = mock_subprocess

        (tmp_path / "buggy.py").write_text("x = 1\n")

        result = json.loads(code_review_assistant_tool(str(tmp_path)))
        total_issues = result["summary"]["total_issues"]
        total_warnings = result["summary"]["total_warnings"]
        assert total_issues > 0 or total_warnings > 0
