"""Regression: ``.bak``-Sicherungen prüfen den Pfad (F13).

0.6.40 schrieb jede Sicherung mit ``write_bytes``: kein ``guard_write``, keine
Symlink-Prüfung. Ein vorhandenes ``mod.py.bak``-Symlink wurde damit zum
Schreibziel — die Sicherung überschrieb eine fremde Datei. Zusätzlich schrieben
die Rollback-Zweige die Originalbytes erneut ins Backup statt in die Datei.
"""

from __future__ import annotations

import json

import pytest

from code_intel.tools.ast_edit_tools import _backup
from code_intel.tools.ast_edit_tools._backup import (
    backup_path_for,
    create_backup,
    drop_backup,
    restore_from_backup,
)
from code_intel.tools.ast_edit_tools.insert import code_insert_before_tool

SRC = '''"""Modul."""

def alpha():
    return 1
'''


def test_backup_is_written_next_to_target(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(SRC, encoding="utf-8")
    assert create_backup(target, SRC.encode()) is None
    assert backup_path_for(target).read_bytes() == SRC.encode()


def test_symlinked_backup_is_refused(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(SRC, encoding="utf-8")
    victim = tmp_path / "victim.txt"
    victim.write_text("wertvoll", encoding="utf-8")
    backup_path_for(target).symlink_to(victim)

    error = create_backup(target, b"neuer inhalt")
    assert error is not None and "symlink" in error
    assert victim.read_text(encoding="utf-8") == "wertvoll"


def test_guard_denial_blocks_backup(tmp_path, monkeypatch):
    target = tmp_path / "mod.py"
    target.write_text(SRC, encoding="utf-8")
    monkeypatch.setattr(_backup, "guard_write", lambda path, verb="Write": "Backup denied: nope")
    assert create_backup(target, b"x") == "Backup denied: nope"
    assert not backup_path_for(target).exists()


def test_rollback_writes_into_file_not_backup(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("kaputt", encoding="utf-8")
    create_backup(target, SRC.encode())
    assert restore_from_backup(target, SRC.encode()) is None
    assert target.read_text(encoding="utf-8") == SRC


def test_drop_backup_removes_file(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(SRC, encoding="utf-8")
    create_backup(target, SRC.encode())
    drop_backup(target)
    assert not backup_path_for(target).exists()


def test_insert_op_refuses_symlinked_backup(tmp_path):
    """End-to-End: die Op darf bei Symlink-Backup nichts schreiben."""
    target = tmp_path / "mod.py"
    target.write_text(SRC, encoding="utf-8")
    victim = tmp_path / "victim.txt"
    victim.write_text("wertvoll", encoding="utf-8")
    backup_path_for(target).symlink_to(victim)

    result = json.loads(code_insert_before_tool(
        path=str(target), symbol="alpha", code="def beta():\n    return 2\n",
        dry_run=False,
    ))
    assert result.get("success") is not True
    assert "symlink" in json.dumps(result)
    assert victim.read_text(encoding="utf-8") == "wertvoll"
    assert target.read_text(encoding="utf-8") == SRC, "Datei darf unverändert bleiben"


def test_insert_op_writes_and_cleans_backup(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(SRC, encoding="utf-8")
    result = json.loads(code_insert_before_tool(
        path=str(target), symbol="alpha", code="def beta():\n    return 2\n",
        dry_run=False,
    ))
    assert result.get("success") is True
    assert "def beta" in target.read_text(encoding="utf-8")
    assert not backup_path_for(target).exists()


@pytest.mark.parametrize("suffix", [".py"])
def test_backup_uses_guard_verb(tmp_path, monkeypatch, suffix):
    calls = []
    target = tmp_path / f"mod{suffix}"
    target.write_text(SRC, encoding="utf-8")
    monkeypatch.setattr(_backup, "guard_write",
                        lambda path, verb="Write": calls.append(verb) or None)
    create_backup(target, SRC.encode())
    assert calls == ["Backup"]
