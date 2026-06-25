"""Tests for code_timeline tool (symbol evolution over git history).

Mocks subprocess.run to avoid needing real git repos.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

from code_intel.tools.timeline import (
    _get_symbol_name,
    _git_log_for_symbol,
    code_timeline_tool,
)

# --- Pure helper tests ---


@patch("subprocess.run")
def test_git_log_with_commits(mock_run):
    mock_run.return_value = MagicMock(
        stdout="abc123|Alice|2024-01-01|Initial commit\ndef456|Bob|2024-01-02|Fix bug\n",
        returncode=0,
    )
    result = _git_log_for_symbol("test.py", 10, 5)
    assert len(result) == 2
    assert result[0]["hash"] == "abc123"
    assert result[1]["author"] == "Bob"


@patch("subprocess.run")
def test_git_log_empty(mock_run):
    mock_run.return_value = MagicMock(stdout="", returncode=0)
    assert _git_log_for_symbol("test.py", 10, 5) == []


@patch("subprocess.run")
def test_git_log_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired("git", 30)
    assert _git_log_for_symbol("test.py", 10, 5) == []


def test_get_symbol_name_from_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("def my_function():\n    pass\n")
    name = _get_symbol_name(str(f), 1)
    assert name == "my_function"


def test_get_symbol_name_class(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("class MyClass:\n    pass\n")
    name = _get_symbol_name(str(f), 1)
    assert name == "MyClass"


def test_get_symbol_name_not_found(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")
    name = _get_symbol_name(str(f), 1)
    assert "symbol at line" in name


# --- Tool tests ---


def test_file_not_found(tmp_path):
    """Non-existent file returns error."""
    result = json.loads(code_timeline_tool(str(tmp_path / "nonexistent.py"), 1))
    assert "error" in result


@patch("subprocess.run")
def test_basic_timeline(mock_run, tmp_path):
    """Basic timeline returns commits."""
    f = tmp_path / "test.py"
    f.write_text("def foo(): pass\n")
    (tmp_path / ".git").mkdir()

    def mock_sub(args, **kwargs):
        cmd = " ".join(args) if isinstance(args, list) else args
        m = MagicMock()
        m.returncode = 0
        if "rev-parse" in cmd:
            m.stdout = str(tmp_path) + "\n"
        elif "log -L" in cmd:
            m.stdout = "abc123|Alice|2024-01-01|Add foo\n"
        else:
            m.stdout = ""
        return m

    mock_run.side_effect = mock_sub
    result = json.loads(code_timeline_tool(str(f), 1))
    assert result["symbol"] == "foo"
    assert result["total_commits"] >= 1


@patch("subprocess.run")
def test_no_git_history(mock_run, tmp_path):
    """No history returns empty commits list."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")
    (tmp_path / ".git").mkdir()

    def mock_sub(args, **kwargs):
        cmd = " ".join(args) if isinstance(args, list) else args
        m = MagicMock()
        m.returncode = 0
        if "rev-parse" in cmd:
            m.stdout = str(tmp_path) + "\n"
        elif "log -L" in cmd:
            m.stdout = ""
        else:
            m.stdout = ""
        return m

    mock_run.side_effect = mock_sub
    result = json.loads(code_timeline_tool(str(f), 1))
    assert "commits" in result
