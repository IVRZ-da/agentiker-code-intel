"""Tests for code_diff_analysis tool (branch comparison).

Mocks subprocess.run to avoid needing real git repos.
"""

import json
from unittest.mock import MagicMock, patch

from code_intel.tools.diff_analysis import (
    _git_diff_files,
    _git_diff_stat,
    code_diff_analysis_tool,
)

# --- Pure helper tests ---


@patch("subprocess.run")
def test_git_diff_files_with_changes(mock_run):
    mock_run.return_value = MagicMock(stdout="file1.py\nfile2.py\n", returncode=0)
    result = _git_diff_files("/fake/repo", "main", "HEAD")
    assert result == ["file1.py", "file2.py"]


@patch("subprocess.run")
def test_git_diff_files_no_changes(mock_run):
    mock_run.return_value = MagicMock(stdout="", returncode=0)
    result = _git_diff_files("/fake/repo", "main", "HEAD")
    assert result == []


@patch("subprocess.run")
def test_git_diff_files_error(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    result = _git_diff_files("/fake/repo", "main", "HEAD")
    assert result == []


@patch("subprocess.run")
def test_git_diff_stat_output(mock_run):
    mock_run.return_value = MagicMock(stdout="1 file changed\n", returncode=0)
    result = _git_diff_stat("/fake/repo", "main", "HEAD")
    assert "1 file changed" in result


# --- Tool tests ---


def test_not_a_git_repo(tmp_path):
    """Not a git repo returns error."""
    result = json.loads(code_diff_analysis_tool(str(tmp_path)))
    assert "error" in result or "Not a git repository" in str(result)


@patch("subprocess.run")
def test_no_changes(mock_run, tmp_path):
    """No changes returns empty summary."""
    (tmp_path / ".git").mkdir()
    mock_run.return_value = MagicMock(stdout="", returncode=0)
    result = json.loads(code_diff_analysis_tool(str(tmp_path)))
    assert result["files_changed"] == 0


@patch("subprocess.run")
def test_one_file_changed(mock_run, tmp_path):
    """One changed file returns file analysis."""
    (tmp_path / ".git").mkdir()
    f = tmp_path / "test.py"
    f.write_text("def foo(): pass\n")

    def mock_sub(args, **kwargs):
        cmd = " ".join(args) if isinstance(args, list) else args
        m = MagicMock()
        m.returncode = 0
        if "diff --name-only" in cmd:
            m.stdout = "test.py\n"
        elif "diff --stat" in cmd:
            m.stdout = "1 file changed\n"
        elif "test.py" in cmd:
            m.stdout = "+def foo():\n+    pass\n- old code\n"
        else:
            m.stdout = ""
        return m

    mock_run.side_effect = mock_sub
    result = json.loads(code_diff_analysis_tool(str(tmp_path)))
    assert result["files_changed"] == 1
    assert len(result["files"]) == 1


@patch("subprocess.run")
def test_max_files_limit(mock_run, tmp_path):
    """max_files limits how many files are analyzed."""
    (tmp_path / ".git").mkdir()
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x=1\n")

    def mock_sub(args, **kwargs):
        cmd = " ".join(args) if isinstance(args, list) else args
        m = MagicMock()
        m.returncode = 0
        if "diff --name-only" in cmd:
            m.stdout = "\n".join(f"f{i}.py" for i in range(5))
        elif "diff --stat" in cmd:
            m.stdout = "5 files changed\n"
        else:
            m.stdout = ""
        return m

    mock_run.side_effect = mock_sub
    result = json.loads(code_diff_analysis_tool(str(tmp_path), max_files=2))
    assert result["files_analyzed"] <= 2
