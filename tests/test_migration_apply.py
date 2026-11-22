"""Regression: ``code_migration`` schreibt wirklich (Bughunt Runde 5, F1+F2).

Zwei Defekte in 0.6.38:

* ``find_all(pattern)`` übergab den String positionell — ast-grep-py 0.51.1
  erwartet ``find_all(config=None, **rule)``, also ``TypeError`` bei jedem Aufruf.
* ``m.replace()`` liefert ein ``Edit``; ohne ``commit_edits`` wurde das
  unveränderte ``source`` geschrieben. Der Report meldete trotzdem Änderungen.
"""

from __future__ import annotations

from code_intel.tools.migration import _expand_rewrite, code_migration_tool

PY_RULE = [{"pattern": "old_name($A)", "rewrite": "new_name($A)", "file_glob": "*.py"}]


def _write(tmp_path, name="mod.py", body="old_name(1)\nold_name(2)\n"):
    f = tmp_path / name
    f.write_text(body, encoding="utf-8")
    return f


def test_apply_rewrites_the_file(tmp_path):
    f = _write(tmp_path)
    code_migration_tool(path=str(tmp_path), rules=PY_RULE, dry_run=False)
    assert f.read_text(encoding="utf-8") == "new_name(1)\nnew_name(2)\n"


def test_dry_run_leaves_file_untouched(tmp_path):
    f = _write(tmp_path)
    code_migration_tool(path=str(tmp_path), rules=PY_RULE, dry_run=True)
    assert f.read_text(encoding="utf-8") == "old_name(1)\nold_name(2)\n"


def test_file_without_match_is_untouched(tmp_path):
    f = _write(tmp_path, body="something_else(1)\n")
    code_migration_tool(path=str(tmp_path), rules=PY_RULE, dry_run=False)
    assert f.read_text(encoding="utf-8") == "something_else(1)\n"


def test_multi_node_metavariable_is_refused_not_written(tmp_path):
    """``$$$VAR`` darf keinen Dateischaden erzeugen (klarer Regel-Fehler)."""
    f = _write(tmp_path, body="wrap(a, b)\n")
    rules = [{"pattern": "wrap($$$A)", "rewrite": "call($$$A)", "file_glob": "*.py"}]
    code_migration_tool(path=str(tmp_path), rules=rules, dry_run=False)
    assert f.read_text(encoding="utf-8") == "wrap(a, b)\n"


def test_expand_rewrite_replaces_single_metavariable():
    import ast_grep_py as sg

    root = sg.SgRoot("old_name(42)\n", "python")
    match = root.root().find_all(pattern="old_name($A)")[0]
    assert _expand_rewrite("new_name($A)", match) == "new_name(42)"


def test_expand_rewrite_without_metavariables_is_literal():
    import ast_grep_py as sg

    root = sg.SgRoot("old_name(42)\n", "python")
    match = root.root().find_all(pattern="old_name($A)")[0]
    assert _expand_rewrite("fixed()", match) == "fixed()"
