"""Regression: ``code_move`` rollt bei Teilfehler zurück (Bughunt Runde 5, F6).

0.6.38 schrieb den Source zuerst und löschte dessen ``.bak`` sofort. Scheiterte
der Target-Write, war das Symbol aus dem Source verschwunden — dauerhaft.
"""

from __future__ import annotations

from pathlib import Path

from code_intel.tools.ast_edit_tools.move import code_move_tool

SOURCE = "def helper():\n    return 1\n\n\ndef keep():\n    return 2\n"


def _setup(tmp_path):
    src = tmp_path / "a.py"
    src.write_text(SOURCE, encoding="utf-8")
    tgt = tmp_path / "b.py"
    tgt.write_text("def existing():\n    return 0\n", encoding="utf-8")
    return src, tgt


def test_target_write_failure_rolls_source_back(tmp_path, monkeypatch):
    src, tgt = _setup(tmp_path)
    original_write = Path.write_bytes

    def exploding_write(self, data):
        if self == tgt:
            raise OSError("disk full")
        return original_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", exploding_write)

    out = code_move_tool(source=str(src), symbol="helper", target=str(tgt), dry_run=False)
    assert "zurückgerollt" in out or "Rollback" in out, out
    assert src.read_text(encoding="utf-8") == SOURCE
    assert not (src.parent / "a.py.bak").exists()


def test_successful_move_writes_both_files(tmp_path):
    src, tgt = _setup(tmp_path)
    code_move_tool(source=str(src), symbol="helper", target=str(tgt), dry_run=False)
    assert "helper" not in src.read_text(encoding="utf-8")
    assert "def helper()" in tgt.read_text(encoding="utf-8")
    assert not (src.parent / "a.py.bak").exists()
    assert not (src.parent / "b.py.bak").exists()


def test_dry_run_touches_nothing(tmp_path):
    src, tgt = _setup(tmp_path)
    code_move_tool(source=str(src), symbol="helper", target=str(tgt), dry_run=True)
    assert src.read_text(encoding="utf-8") == SOURCE
    assert tgt.read_text(encoding="utf-8") == "def existing():\n    return 0\n"
