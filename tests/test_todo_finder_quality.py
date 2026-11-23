"""Regression: code_todo_finder liefert nur echte Kommentar-Marker (F15).

0.6.40 suchte ``git grep -i -e TODO`` — damit matchten Identifier
(``_TODO_PATTERNS``), Strings (``return "TODO"``) und Prosa. Im Repo standen
200 Treffer (Deckel) bei einer Handvoll echter TODOs; der Treffertext war
zusätzlich beschnitten.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from code_intel.tools.git import _is_actionable, _match_marker, code_todo_finder_tool

CODE = '''# TODO: echte Aufgabe
x = 1  # FIXME: zweite echte Aufgabe
TODO_PATTERNS = ["TODO"]
def bug():
    return "TODO im String"
todo = 5
# HACK: dritte
"""Ein Docstring mit TODO darin."""
'''

PROSE = "Ein Text ueber TODO-Listen.\n# TODO: Doku-Punkt\n"


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text(CODE, encoding="utf-8")
    (tmp_path / "b.md").write_text(PROSE, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _result(path) -> dict:
    raw = code_todo_finder_tool(path=str(path))
    start = raw.find("{")
    return json.loads(raw[start:]) if start >= 0 else {"raw": raw}


def test_only_comment_markers_survive(repo):
    data = _result(repo)
    by_file = {}
    for f in data["findings"]:
        by_file.setdefault(f["file"], []).append(f["text"])
    texts = [f["text"] for f in data["findings"]]
    # a.py: 3 echte Kommentare; b.md: 2 Zeilen (Prosa + Kommentar)
    assert len(by_file.get("a.py", [])) == 3, by_file
    assert data["total"] == 5, data
    assert any("echte Aufgabe" in t for t in texts)
    assert any("zweite echte Aufgabe" in t for t in texts)
    assert any("dritte" in t for t in texts)
    assert not any("TODO_PATTERNS" in t for t in texts)
    assert not any("Todo im String" in t for t in texts)
    assert not any(t.startswith("todo = 5") for t in texts)
    assert not any("Docstring" in t for t in texts)


def test_result_reports_raw_and_filtered(repo):
    data = _result(repo)
    assert data["raw_hits"] > data["total"]
    assert data["filtered_out"] == data["raw_hits"] - data["total"]


def test_text_is_the_original_line(repo):
    data = _result(repo)
    first = next(f for f in data["findings"] if f["line"] == 1 and f["file"] == "a.py")
    assert first["text"] == "# TODO: echte Aufgabe"
    assert first["marker"] == "TODO"


def test_marker_requires_word_boundary():
    assert _match_marker("_TODO_PATTERNS = []", ["TODO"]) is None
    assert _match_marker("TODO_LIST", ["TODO"]) is None
    assert _match_marker("# TODO: x", ["TODO"]) == "TODO"


def test_actionable_requires_comment_in_code():
    assert _is_actionable("a.py", "x = 1  # TODO: y", "TODO")
    assert not _is_actionable("a.py", 'return "TODO"', "TODO")
    assert not _is_actionable("a.py", "todo = 5", "TODO")
    assert _is_actionable("notes.md", "TODO-Liste", "TODO")


def test_own_patterns_are_honoured(repo):
    data = _result(repo)
    assert data["total"] >= 1
    raw = code_todo_finder_tool(path=str(repo), include_patterns=["HACK"])
    assert "HACK" in raw
