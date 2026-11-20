"""Regressionstests fuer den Live-Debug 2026-11-19 (alle 70 Ops).

Bug-Klasse, die diese Datei festhaelt
-------------------------------------
Tool-Funktionen gaben Rich-Panels zurueck, andere Tools parsten diesen Text mit
``json.loads`` und schluckten die Exception. Ergebnis: der Op meldete
"✅ Success", lieferte aber leere Felder (``symbol: None``, ``coverage: none``).
Zusaetzlich zeigte ``_auto_detect_identifier_column`` in Python immer auf
``def`` statt auf den Symbolnamen, wodurch references/rename/hover auf dem
falschen Token arbeiteten.

Die Tests sind bewusst auf Vertragsebene (JSON parsebar, Feld gefuellt,
Beispiel schema-konform) — sie schlagen bei jedem Rueckfall fehl.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
FIXTURE = PLUGIN_DIR / "namespaces.py"


def _symbol_line() -> int:
    """Zeile von ``def build_catalog(`` — dynamisch, damit Edits an der Datei
    den Test nicht brechen (vorher: fester Pin auf Zeile 195)."""
    for number, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("def build_catalog("):
            return number
    raise AssertionError(f"build_catalog nicht gefunden in {FIXTURE}")


SYMBOL_LINE = _symbol_line()


@pytest.fixture(scope="module")
def real_fmt():
    """Echtes ``_fmt`` laden (conftest ersetzt es sonst durch einen Mock)."""
    src = PLUGIN_DIR / "_fmt.py"
    spec = importlib.util.spec_from_file_location("_code_intel_real_fmt_regr", src)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# JSON-Vertrag
# ---------------------------------------------------------------------------


class TestFmtJsonContract:
    def test_long_values_stay_parseable(self, real_fmt):
        """Lange Strings duerfen nicht umgebrochen werden (war: abgeschnitten)."""
        payload = {
            "signature": "def build_catalog(\n    register_ast: Callable[[Any], None], "
            + "x" * 400
            + "\n) -> None:",
            "nested": {"list": [{"k": "v" * 300}]},
        }
        raw = real_fmt.fmt_json(payload)
        assert json.loads(raw) == payload

    def test_no_ansi_codes(self, real_fmt):
        raw = real_fmt.fmt_json({"a": "b"})
        assert "\x1b" not in raw

    def test_multi_line_and_unescaped_unicode(self, real_fmt):
        raw = real_fmt.fmt_json({"text": "Grüße\nzweite Zeile"})
        assert json.loads(raw)["text"] == "Grüße\nzweite Zeile"

    def test_non_serializable_raises(self, real_fmt):
        with pytest.raises(TypeError):
            real_fmt.fmt_json({"o": object()})


class TestFmtResult:
    def test_raw_returns_json(self, real_fmt):
        out = real_fmt.fmt_result({"a": 1}, raw=True)
        assert json.loads(out) == {"a": 1}

    def test_panel_returns_rendered_text(self, real_fmt):
        out = real_fmt.fmt_result({"a": 1}, raw=False)
        with pytest.raises(ValueError):
            json.loads(out)


# ---------------------------------------------------------------------------
# Identifier-Erkennung (Sprache: Python)
# ---------------------------------------------------------------------------


class TestIdentifierColumn:
    def test_def_skipped(self):
        """Früher kam hier 1 zurueck — also die Spalte von 'def'."""
        from code_intel.lsp_bridge import _auto_detect_identifier_column

        assert _auto_detect_identifier_column(str(FIXTURE), SYMBOL_LINE - 1) == 5

    def test_async_def_skipped(self, tmp_path):
        from code_intel.lsp_bridge import _auto_detect_identifier_column

        f = tmp_path / "m.py"
        f.write_text("async def handler(request):\n    pass\n", encoding="utf-8")
        assert _auto_detect_identifier_column(str(f), 0) == 11

    def test_class_and_return(self, tmp_path):
        from code_intel.lsp_bridge import _auto_detect_identifier_column

        f = tmp_path / "m.py"
        f.write_text("class Widget:\n    return some_value\n", encoding="utf-8")
        assert _auto_detect_identifier_column(str(f), 0) == 7
        assert _auto_detect_identifier_column(str(f), 1) == 12


# ---------------------------------------------------------------------------
# _raw-Vertrag der Tool-Funktionen
# ---------------------------------------------------------------------------


class TestRawContract:
    def test_symbols_raw_is_json(self):
        from code_intel.code_tools import code_symbols_tool

        data = json.loads(code_symbols_tool(path=str(FIXTURE), _raw=True))
        assert data["symbol_count"] > 0
        assert data["symbols"]

    def test_symbols_default_is_not_raw(self):
        """Default nutzt ``fmt_result(raw=False)`` — unterscheidbar am Mock.

        Das echte Panel laesst sich hier nicht pruefen (conftest ersetzt
        ``_fmt`` durch JSON-Mocks), aber der Vertrag ist eindeutig: nur der
        raw-Zweig liefert reines JSON ohne ``status``-Feld.
        """
        from code_intel.code_tools import code_symbols_tool

        panel_mock = json.loads(code_symbols_tool(path=str(FIXTURE)))
        raw = json.loads(code_symbols_tool(path=str(FIXTURE), _raw=True))
        assert panel_mock.get("status") == "ok"
        assert "status" not in raw

    def test_tests_find_symbol_name(self):
        """War None: Panel statt JSON + falsche Feldnamen (start_line)."""
        from code_intel.tools.test_coverage import _tests_find_symbol_name

        assert _tests_find_symbol_name(str(FIXTURE), SYMBOL_LINE, "python") == "build_catalog"

    def test_tests_find_references_uses_bridge(self, monkeypatch):
        """Der Referenz-Lookup darf nicht mehr code_references_tool parsen."""
        import code_intel.tools.test_coverage as tc

        sentinel = {"file:///x.py": [{"line": 1}]}
        monkeypatch.setattr(tc, "_tests_find_references", lambda *a, **k: sentinel)
        assert tc._tests_find_references(str(FIXTURE), SYMBOL_LINE, "python") is sentinel


# ---------------------------------------------------------------------------
# op_meta-Beispiele muessen zum echten Handler-Schema passen
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalog():
    import code_intel
    from code_intel.namespaces import build_catalog

    def _ast(target):
        code_intel._register_ast_tools(target)

    def _lsp(target):
        from code_intel.lsp_bridge import register_lsp_tools

        register_lsp_tools(target)

    return build_catalog(_ast, _lsp)


class TestOpMetaExamples:
    """Ein Beispiel, das ein Agent 1:1 kopiert, muss durchlaufen.

    Der Live-Debug zeigte zwei Beispiele, die garantiert scheiterten:
    ``graph_query`` mit ``callers(Foo)`` (Handler kennt nur nackte Query-Namen)
    und ``export`` mit ``kind=tree`` (Schema erlaubt all/function/class/method).
    """

    def test_every_example_respects_enum(self, catalog):
        from code_intel.tools import op_meta

        problems = []
        for op in op_meta.OP_META:
            example = op_meta.example_for(op)
            tool = catalog.get(f"code_{op}")
            if not tool:
                continue
            props = (tool.get("parameters") or {}).get("properties") or {}
            for field, value in example.items():
                if field == "op" or field not in props:
                    continue
                allowed = props[field].get("enum")
                if allowed and value not in allowed:
                    problems.append(f"{op}.{field}={value!r} nicht in {allowed}")
        assert not problems, "\n".join(problems)

    def test_every_required_field_in_example(self):
        from code_intel.tools import op_meta

        for op, meta in op_meta.OP_META.items():
            example = op_meta.example_for(op)
            missing = [f for f in meta.required if f not in example]
            assert not missing, f"{op}: Beispiel ohne Pflichtfelder {missing}"
