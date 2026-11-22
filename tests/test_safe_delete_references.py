"""Regression: ``code_safe_delete`` sucht Referenzen ab Projektwurzel (F7).

0.6.38 durchsuchte nur ``target.parent``. Ein Symbol, das aus einem
Nachbarpaket importiert wurde, galt damit als unreferenziert und wurde
gelöscht — mit ``safe: true`` im Report.
"""

from __future__ import annotations

from code_intel.tools.ast_edit_tools.safe_delete import _project_root, code_safe_delete_tool


def _tree(tmp_path):
    (tmp_path / ".git").mkdir()
    pkg = tmp_path / "pkg"
    other = tmp_path / "other"
    pkg.mkdir()
    other.mkdir()
    helper = pkg / "helper.py"
    helper.write_text("def helper():\n    return 1\n", encoding="utf-8")
    consumer = other / "consumer.py"
    consumer.write_text("from pkg.helper import helper\n\nhelper()\n", encoding="utf-8")
    return helper, consumer


def test_project_root_is_git_root(tmp_path):
    helper, _ = _tree(tmp_path)
    assert _project_root(helper) == tmp_path


def test_project_root_without_git_falls_back_to_parent(tmp_path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    f = sub / "mod.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _project_root(f) == sub


def test_reference_in_sibling_package_blocks_delete(tmp_path):
    helper, consumer = _tree(tmp_path)
    out = code_safe_delete_tool(path=str(helper), symbol="helper", dry_run=False)
    assert "external" in out.lower() or "reference" in out.lower()
    # Datei muss unangetastet bleiben, weil die Referenz gefunden wurde.
    assert helper.read_text(encoding="utf-8") == "def helper():\n    return 1\n"
    assert consumer.exists()


def test_unreferenced_symbol_is_deleted(tmp_path):
    (tmp_path / ".git").mkdir()
    lonely = tmp_path / "lonely.py"
    lonely.write_text("def lonely():\n    return 1\n", encoding="utf-8")
    code_safe_delete_tool(path=str(lonely), symbol="lonely", dry_run=False)
    assert lonely.read_text(encoding="utf-8").strip() == ""


def test_force_deletes_even_with_references(tmp_path):
    helper, _ = _tree(tmp_path)
    code_safe_delete_tool(path=str(helper), symbol="helper", force=True, dry_run=False)
    assert helper.read_text(encoding="utf-8").strip() == ""
