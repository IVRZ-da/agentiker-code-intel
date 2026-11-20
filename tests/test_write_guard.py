"""Tests für den zentralen Schreibschutz (Hermes-Review PR #157345, Punkt 3).

Der Guard delegiert an ``agent.file_safety`` des Hosts — dieselbe Denylist wie
``write_file``/``patch``. In der Testumgebung ist ``agent`` nicht installiert,
deshalb stellt ``_deny_host_paths`` das Modul über ``sys.modules`` bereit.
Bewusst NICHT über ``monkeypatch.setattr(code_intel._write_guard, …)``: die
conftest-Isolation löscht ``code_intel.*``-Submodule zwischen Tests, ein
Attribut-Patch ginge dabei verloren.
"""

from __future__ import annotations

import sys
import types

import pytest

from code_intel import _write_guard as wg


# ─── Host-Guard-Attrappe ────────────────────────────────────────────────────


def _install_host_guard(monkeypatch, *, deny: bool, deny_suffix: str | None = None):
    """Setzt ``agent.file_safety`` in sys.modules (wird pro Aufruf importiert)."""

    def _is_write_denied(path: str) -> bool:
        if not deny:
            return False
        return deny_suffix is None or str(path).endswith(deny_suffix)

    def _get_write_denied_error(path: str, *, verb: str = "Write", entry: bool = False):
        return f"{verb} denied: '{path}' is a protected file."

    module = types.ModuleType("agent.file_safety")
    setattr(module, "is_write_denied", _is_write_denied)
    setattr(module, "get_write_denied_error", _get_write_denied_error)
    package = types.ModuleType("agent")
    setattr(package, "file_safety", module)
    setattr(package, "__path__", [])  # damit "agent.file_safety" als Submodul gilt
    monkeypatch.setitem(sys.modules, "agent", package)
    monkeypatch.setitem(sys.modules, "agent.file_safety", module)


@pytest.fixture()
def deny_all(monkeypatch):
    _install_host_guard(monkeypatch, deny=True)


# ─── Guard-Grundlagen ───────────────────────────────────────────────────────


def test_guard_is_noop_without_host(monkeypatch):
    monkeypatch.delitem(sys.modules, "agent.file_safety", raising=False)
    monkeypatch.delitem(sys.modules, "agent", raising=False)
    assert wg.guard_write("/etc/passwd") is None


def test_guard_denies_host_path(monkeypatch, tmp_path):
    _install_host_guard(monkeypatch, deny=True, deny_suffix=".env")
    denied = wg.guard_write(tmp_path / "secret.env")
    allowed = wg.guard_write(tmp_path / "module.py")
    assert denied and "denied" in denied
    assert allowed is None


def test_is_within_root(tmp_path):
    inside = tmp_path / "pkg" / "a.py"
    inside.parent.mkdir()
    inside.write_text("x = 1\n")
    assert wg.is_within_root(inside, tmp_path)
    assert not wg.is_within_root(tmp_path.parent / "elsewhere.py", tmp_path)


def test_guard_write_in_root_denies_outside(monkeypatch, tmp_path):
    _install_host_guard(monkeypatch, deny=False)
    message = wg.guard_write_in_root(tmp_path.parent / "outside.py", tmp_path)
    assert message and "outside" in message


def test_guard_write_in_root_allows_inside(monkeypatch, tmp_path):
    _install_host_guard(monkeypatch, deny=False)
    inside = tmp_path / "a.py"
    inside.write_text("x = 1\n")
    assert wg.guard_write_in_root(inside, tmp_path) is None


# ─── Handler respektieren den Guard ─────────────────────────────────────────


def _src(tmp_path, name="m.py", text="def foo():\n    return 1\n\ndef bar():\n    return 2\n"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_replace_body_denied(deny_all, tmp_path):
    from code_intel.tools.ast_edit_tools.replace_body import code_replace_body_tool

    path = _src(tmp_path)
    out = code_replace_body_tool(str(path), "foo", "def foo():\n    return 99\n", dry_run=False)
    assert "denied" in out
    assert "return 1" in path.read_text(encoding="utf-8")


def test_safe_delete_denied(deny_all, tmp_path):
    from code_intel.tools.ast_edit_tools.safe_delete import code_safe_delete_tool

    path = _src(tmp_path)
    out = code_safe_delete_tool(str(path), "bar", force=True, dry_run=False)
    assert "denied" in out
    assert "def bar" in path.read_text(encoding="utf-8")


def test_insert_denied(deny_all, tmp_path):
    from code_intel.tools.ast_edit_tools.insert import code_insert_before_tool

    path = _src(tmp_path)
    out = code_insert_before_tool(str(path), "foo", "VALUE = 1\n", dry_run=False)
    assert "denied" in out
    assert "VALUE = 1" not in path.read_text(encoding="utf-8")


def test_move_denied(deny_all, tmp_path):
    from code_intel.tools.ast_edit_tools.move import code_move_tool

    source = _src(tmp_path, "a.py")
    target = _src(tmp_path, "b.py", "def other():\n    return 0\n")
    out = code_move_tool(str(source), "foo", str(target), language="python", dry_run=False)
    assert "denied" in out
    assert "def foo" in source.read_text(encoding="utf-8")


def test_pattern_apply_denied(deny_all, tmp_path):
    from code_intel.tools.pattern import _apply_refactor_changes

    path = _src(tmp_path)
    source_lines = path.read_text(encoding="utf-8").splitlines()

    class _Point:
        line, column = 1, 0

    class _Range:
        start = end = _Point()

    class _Match:
        def range(self):
            return _Range()

    result = _apply_refactor_changes(
        [{"replacement": "return 42"}], [_Match()], source_lines, path, False,
    )
    assert isinstance(result, dict) and "denied" in result["error"]
    assert "return 42" not in path.read_text(encoding="utf-8")


def test_apply_edits_by_file_denied(deny_all, tmp_path):
    from code_intel.lsp.bridge.server import _apply_edits_by_file

    path = _src(tmp_path)
    edit = {"range": {"start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 10}},
            "newText": "changed"}
    applied = _apply_edits_by_file({str(path): [edit]})
    assert applied[0]["status"].startswith("error")
    assert "denied" in applied[0]["status"]
    assert "changed" not in path.read_text(encoding="utf-8")


def test_apply_workspace_edit_denied(deny_all, tmp_path):
    from code_intel.lsp.extra.actions import _apply_workspace_edit

    path = _src(tmp_path)
    edit = {"range": {"start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 10}},
            "newText": "changed"}
    applied = _apply_workspace_edit({"changes": {f"file://{path}": [edit]}})
    assert applied[0]["status"].startswith("error")
    assert "denied" in applied[0]["status"]
    assert "changed" not in path.read_text(encoding="utf-8")


def test_batch_refactor_denied_at_root(deny_all, tmp_path):
    """Der uebergebene Baum selbst wird vor dem Walk geprueft."""
    from code_intel.tools.batch import code_batch_refactor_tool

    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")

    out = code_batch_refactor_tool(str(root), "x = 1", "x = 2", dry_run=False)
    assert "denied" in out
    assert (root / "a.py").read_text(encoding="utf-8") == "x = 1\n"
