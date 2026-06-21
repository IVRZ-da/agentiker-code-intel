"""Unit tests for tools/git.py — 4 neue Git-Tools.

Testet code_todo_finder_tool, code_merge_conflict_finder_tool,
code_git_log_symbol_tool und code_git_diff_file_tool.

Mockt subprocess.run und _find_git_root (via patch.object),
sodass kein echtes Git-Repository benötigt wird.

Pro Tool: 1 Normalfall + 1 Error (kein git repo, timeout) = mind. 8 Tests.
Plus ein paar Edge Cases (keine Funde, Pfad nicht gefunden).
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_intel.tools import git as git_tools


# ===========================================================================
# Helper
# ===========================================================================


def _mock_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Baue einen mock subprocess.CompletedProcess."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_git_root():
    """_find_git_root gibt ein fake git-root zurück → drin im Repo."""
    with patch.object(git_tools, "_find_git_root", return_value="/fake/repo") as m:
        yield m


@pytest.fixture
def mock_git_root_none():
    """_find_git_root gibt None zurück → kein Git-Repo."""
    with patch.object(git_tools, "_find_git_root", return_value=None) as m:
        yield m


@pytest.fixture
def mock_subprocess():
    """subprocess.run wird gemockt."""
    with patch.object(git_tools.subprocess, "run") as m:
        yield m


@pytest.fixture
def mock_path_for_file():
    """Path-Operationen für existierende Datei mocken.

    Stellt sicher, dass Path(path).expanduser().resolve() ein Mock-Objekt
    mit exists()=True und den nötigen Attributen zurückgibt.
    """
    target = MagicMock()
    target.exists.return_value = True
    target.is_file.return_value = True
    target.name = "test.py"
    target.parent = Path("/fake/repo")

    with patch.object(git_tools, "Path") as mp:
        mp.return_value.expanduser.return_value.resolve.return_value = target
        yield target


# ===========================================================================
# 1) code_todo_finder_tool
# ===========================================================================


class TestTodoFinder:
    """Tests für code_todo_finder_tool."""

    def test_happy_path(self, mock_git_root, mock_subprocess):
        """Normalfall: git grep findet TODO/FIXME/HACK."""
        out = (
            "src/main.py:12:TODO: implement error handling\n"
            "src/utils.py:45:FIXME: remove debug print\n"
            "src/main.py:67:HACK: temporary workaround\n"
        )
        mock_subprocess.return_value = _mock_completed(stdout=out)

        result = git_tools.code_todo_finder_tool(path="/some/path")
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["total"] == 3
        assert data["files"] == 2  # main.py + utils.py
        assert data["findings"][0]["file"] == "src/main.py"
        assert data["findings"][0]["line"] == 12
        assert data["findings"][0]["text"] == "TODO: implement error handling"

    def test_no_git_repo(self, mock_git_root_none):
        """Error: kein Git-Repository → fmt_err."""
        result = git_tools.code_todo_finder_tool(path="/outside")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Not inside a git repository" in data["error"]
        assert "/outside" in data["error"]

    def test_subprocess_timeout(self, mock_git_root, mock_subprocess):
        """Error: subprocess.run wirft TimeoutExpired."""
        mock_subprocess.side_effect = subprocess.TimeoutExpired(
            cmd=["git", "grep"], timeout=60
        )

        result = git_tools.code_todo_finder_tool(path="/some/path")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Search failed" in data["error"]
        assert "timed out" in data["error"].lower()

    def test_no_findings(self, mock_git_root, mock_subprocess):
        """Randfall: keine Treffer → ok mit total=0."""
        mock_subprocess.return_value = _mock_completed(stdout="", returncode=1)

        result = git_tools.code_todo_finder_tool(path="/some/path")
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["total"] == 0
        assert "No TODO/FIXME/HACK comments found" in data["message"]


# ===========================================================================
# 2) code_merge_conflict_finder_tool
# ===========================================================================


class TestMergeConflictFinder:
    """Tests für code_merge_conflict_finder_tool."""

    def test_happy_path(self, mock_git_root, mock_subprocess):
        """Normalfall: git grep findet Konflikt-Marker."""
        out = (
            "src/main.py:42:<<<<<<< HEAD\n"
            "src/main.py:43:=======\n"
            "src/main.py:44:>>>>>>> branch\n"
            "src/other.py:10:<<<<<<< feature\n"
        )
        mock_subprocess.return_value = _mock_completed(stdout=out)

        result = git_tools.code_merge_conflict_finder_tool(path="/some/path")
        data = json.loads(result)

        # Tool nutzt fmt_err für gefundene Konflikte
        assert data["status"] == "error"
        assert data["error"]["total"] == 4
        assert data["error"]["files_affected"] == 2
        assert len(data["error"]["markers"]) == 4
        assert data["error"]["markers"][0]["file"] == "src/main.py"
        assert data["error"]["markers"][0]["line"] == 42

    def test_no_git_repo(self, mock_git_root_none):
        """Error: kein Git-Repository."""
        result = git_tools.code_merge_conflict_finder_tool(path="/outside")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Not inside a git repository" in data["error"]

    def test_subprocess_timeout(self, mock_git_root, mock_subprocess):
        """Error: subprocess.run wirft TimeoutExpired."""
        mock_subprocess.side_effect = subprocess.TimeoutExpired(
            cmd=["git", "grep"], timeout=30
        )

        result = git_tools.code_merge_conflict_finder_tool(path="/some/path")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Git grep failed" in data["error"]
        assert "timed out" in data["error"].lower()

    def test_no_conflicts(self, mock_git_root, mock_subprocess):
        """Randfall: keine Konflikte → ok mit total=0."""
        mock_subprocess.return_value = _mock_completed(stdout="", returncode=1)

        result = git_tools.code_merge_conflict_finder_tool(path="/some/path")
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["total"] == 0
        assert "No merge conflict markers found" in data["message"]


# ===========================================================================
# 3) code_git_log_symbol_tool
# ===========================================================================


class TestGitLogSymbol:
    """Tests für code_git_log_symbol_tool."""

    @pytest.fixture
    def mock_source_file(self, mock_path_for_file):
        """Ergänzt read_text für die _find_symbol_line-Helfer-Funktion."""
        mock_path_for_file.read_text.return_value = (
            "def my_function():\n    pass\n"
        )
        return mock_path_for_file

    def test_happy_path(
        self, mock_git_root, mock_subprocess, mock_source_file
    ):
        """Normalfall: git log + git blame liefern Commit-Daten."""
        log_out = (
            "abc123def|Alice|2024-01-15 10:00:00 +0000|Added new feature\n"
            "def456abc|Bob|2024-01-10 08:30:00 +0000|Initial implementation\n"
        )
        blame_out = (
            "author Alice\n"
            "author-time 1705334400\n"
        )

        # Zwei subprocess.run-Aufrufe: erst log, dann blame
        mock_subprocess.side_effect = [
            _mock_completed(stdout=log_out),
            _mock_completed(stdout=blame_out),
        ]

        result = git_tools.code_git_log_symbol_tool(
            path="/fake/repo/test.py", line=1, max_count=5
        )
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["total_commits"] == 2
        assert data["symbol_line"] == 1  # Zeile 1 ist 'def my_function():'
        assert data["commits"][0]["hash"] == "abc123de"
        assert data["commits"][0]["author"] == "Alice"
        assert data["last_modified_by"] == "Alice"
        assert "last_modified_at" in data

    def test_no_git_repo(self, mock_git_root_none, mock_source_file):
        """Error: kein Git-Repository."""
        result = git_tools.code_git_log_symbol_tool(
            path="/fake/repo/test.py", line=1
        )
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Not inside a git repository" in data["error"]

    def test_subprocess_timeout(
        self, mock_git_root, mock_subprocess, mock_source_file
    ):
        """Error: subprocess.run wirft TimeoutExpired beim git log."""
        mock_subprocess.side_effect = subprocess.TimeoutExpired(
            cmd=["git", "log"], timeout=30
        )

        result = git_tools.code_git_log_symbol_tool(
            path="/fake/repo/test.py", line=1
        )
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Git operation failed" in data["error"]
        assert "timed out" in data["error"].lower()

    def test_path_not_found(self):
        """Error: Datei existiert nicht → fmt_err."""
        target = MagicMock()
        target.exists.return_value = False
        with patch.object(git_tools, "Path") as mp:
            mp.return_value.expanduser.return_value.resolve.return_value = target

            result = git_tools.code_git_log_symbol_tool(
                path="/nonexistent/file.py", line=1
            )
            data = json.loads(result)

            assert data["status"] == "error"
            assert "Path not found" in data["error"]


# ===========================================================================
# 4) code_git_diff_file_tool
# ===========================================================================


class TestGitDiffFile:
    """Tests für code_git_diff_file_tool."""

    def test_happy_path(self, mock_git_root, mock_subprocess, mock_path_for_file):
        """Normalfall: git diff liefert Änderungen."""
        diff_out = (
            "--- a/test.py\n"
            "+++ b/test.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def foo():\n"
            "+    print('hello')\n"
            "     pass\n"
        )
        mock_subprocess.return_value = _mock_completed(stdout=diff_out)

        result = git_tools.code_git_diff_file_tool(
            path="/fake/repo/test.py", staged=False, context_lines=3
        )
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["has_changes"] is True
        assert data["lines_added"] == 1
        assert data["lines_removed"] == 0
        assert data["diff"] is not None

    def test_no_git_repo(self, mock_git_root_none, mock_path_for_file):
        """Error: kein Git-Repository."""
        result = git_tools.code_git_diff_file_tool(path="/fake/repo/test.py")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Not inside a git repository" in data["error"]

    def test_subprocess_timeout(
        self, mock_git_root, mock_subprocess, mock_path_for_file
    ):
        """Error: subprocess.run wirft TimeoutExpired."""
        mock_subprocess.side_effect = subprocess.TimeoutExpired(
            cmd=["git", "diff"], timeout=30
        )

        result = git_tools.code_git_diff_file_tool(path="/fake/repo/test.py")
        data = json.loads(result)

        assert data["status"] == "error"
        assert "Git diff failed" in data["error"]
        assert "timed out" in data["error"].lower()

    def test_no_changes(self, mock_git_root, mock_subprocess, mock_path_for_file):
        """Randfall: keine Änderungen → ok mit has_changes=False."""
        mock_subprocess.return_value = _mock_completed(stdout="")

        result = git_tools.code_git_diff_file_tool(path="/fake/repo/test.py")
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["has_changes"] is False
        assert "No uncommitted changes" in data["message"]

    def test_path_not_found(self):
        """Error: Pfad existiert nicht."""
        target = MagicMock()
        target.exists.return_value = False
        with patch.object(git_tools, "Path") as mp:
            mp.return_value.expanduser.return_value.resolve.return_value = target

            result = git_tools.code_git_diff_file_tool(
                path="/nonexistent/file.py"
            )
            data = json.loads(result)

            assert data["status"] == "error"
            assert "Path not found" in data["error"]

    def test_staged_diff(self, mock_git_root, mock_subprocess, mock_path_for_file):
        """Staged-Diff: --cached wird gesetzt."""
        diff_out = (
            "--- a/test.py\n"
            "+++ b/test.py\n"
            "@@ -1 +1,2 @@\n"
            "-old line\n"
            "+new line\n"
        )
        mock_subprocess.return_value = _mock_completed(stdout=diff_out)

        result = git_tools.code_git_diff_file_tool(
            path="/fake/repo/test.py", staged=True
        )
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["staged"] is True
        assert data["lines_added"] == 1
        assert data["lines_removed"] == 1
