"""Regression: ast-grep-Erkennung (Bughunt Runde 5, F4).

``shutil.which("sg")`` liefert auf Debian/Ubuntu ``/usr/bin/sg`` — das
setgid-Werkzeug aus util-linux. Es antwortet auf ``--version`` mit rc=0 und
wurde deshalb als ast-grep verwendet; jeder Batch-Refactor lief danach ins
Leere und meldete „success" mit 0 Treffern.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from code_intel.tools import batch


class _FakeRun:
    def __init__(self, stdout: str, rc: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = rc


def test_prefers_real_ast_grep_over_util_linux_sg(monkeypatch):
    def fake_which(name):
        return {"ast-grep": "/opt/bin/ast-grep", "sg": "/usr/bin/sg"}.get(name)

    def fake_run(cmd, **kw):
        if cmd[0].endswith("ast-grep"):
            return _FakeRun("ast-grep 0.38.7")
        return _FakeRun("sg von util-linux 2.41.5")

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert batch._find_ast_grep() == "/opt/bin/ast-grep"


def test_rejects_util_linux_sg(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sg" if name == "sg" else None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _FakeRun("sg von util-linux 2.41.5"))
    assert batch._find_ast_grep() is None


def test_missing_binary_is_none(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert batch._find_ast_grep() is None


@pytest.mark.skipif(shutil.which("ast-grep") is None, reason="ast-grep CLI nicht installiert")
def test_real_ast_grep_is_detected():
    found = batch._find_ast_grep()
    assert found is not None
    assert shutil.which(found) or found.endswith("ast-grep")
