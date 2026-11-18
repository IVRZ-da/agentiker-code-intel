"""Tests für das Token-Budget-Gate (scripts/assert_audit.py, Release 0.6.32).

Das Skript ersetzt die Prosa-Gates der Roadmap ("unverändert 4.788 Tok"): es muss
selbst fehlschlagen können. Genau das wird hier geprüft — nicht nur der Happy Path.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN_DIR / "scripts" / "assert_audit.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("_assert_audit_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fake_result():
    return {
        "plugin": "code_intel",
        "tool_count": 3,
        "eager_tokens": 1200,
        "total_tokens": 1500,
        "tools": [
            {"name": "code_search", "chars": 2000, "tokens": 500, "eager": True},
            {"name": "code_edit", "chars": 4000, "tokens": 1000, "eager": True},
            {"name": "code_meta", "chars": 0, "tokens": 0, "eager": False},
        ],
    }


# ─── Budget-Syntax ───────────────────────────────────────────────────────────


class TestBudgetParsing:
    def test_valid(self, mod):
        assert mod._token_budget("code_edit=1000") == ("code_edit", 1000)

    @pytest.mark.parametrize("raw", ["code_edit", "code_edit=", "=100", "code_edit=abc", ""])
    def test_invalid_raises(self, mod, raw):
        with pytest.raises(ValueError):
            mod._token_budget(raw)


# ─── check() ─────────────────────────────────────────────────────────────────


class TestCheck:
    def test_green_when_inside_budget(self, mod, fake_result):
        assert mod.check(fake_result, max_eager=1200, max_total=1500,
                         max_tool_tokens=[("code_edit", 1000)]) == []

    def test_red_on_eager_overflow(self, mod, fake_result):
        violations = mod.check(fake_result, max_eager=1000)
        assert len(violations) == 1 and "eager-Summe" in violations[0]

    def test_red_on_total_overflow(self, mod, fake_result):
        violations = mod.check(fake_result, max_total=100)
        assert len(violations) == 1 and "Gesamt-Summe" in violations[0]

    def test_red_on_tool_overflow(self, mod, fake_result):
        violations = mod.check(fake_result, max_tool_tokens=[("code_edit", 999)])
        assert len(violations) == 1 and "code_edit" in violations[0]

    def test_red_on_unknown_tool(self, mod, fake_result):
        violations = mod.check(fake_result, max_tool_tokens=[("code_nope", 10)])
        assert len(violations) == 1 and "nicht registriert" in violations[0]

    def test_no_budgets_means_no_violations(self, mod, fake_result):
        assert mod.check(fake_result) == []


# ─── CLI ─────────────────────────────────────────────────────────────────────


class TestCli:
    def test_real_plugin_green_with_high_budget(self, mod, capsys):
        assert mod.main([str(PLUGIN_DIR), "--max-eager", "99999"]) == 0
        assert "OK" in capsys.readouterr().out

    def test_real_plugin_red_with_tiny_budget(self, mod, capsys):
        assert mod.main([str(PLUGIN_DIR), "--max-eager", "1"]) == 1
        captured = capsys.readouterr()
        assert "BUDGET VERLETZT" in captured.out
        assert "eager-Summe" in captured.err

    def test_real_plugin_red_on_tool_budget(self, mod):
        assert mod.main([str(PLUGIN_DIR), "--max-tool-tokens", "code_edit=1"]) == 1

    def test_unregistered_tool_is_red(self, mod):
        assert mod.main([str(PLUGIN_DIR), "--max-tool-tokens", "code_does_not_exist=10"]) == 1

    def test_json_output(self, mod, capsys):
        code = mod.main([str(PLUGIN_DIR), "--max-eager", "99999", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["status"] == "pass"
        assert payload["violations"] == []
        assert payload["budgets"]["max_eager"] == 99999
        assert payload["tool_count"] > 0

    def test_json_reports_failure(self, mod, capsys):
        assert mod.main([str(PLUGIN_DIR), "--max-eager", "1", "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "fail" and payload["violations"]

    def test_bad_budget_syntax_exits_2(self, mod, capsys):
        assert mod.main([str(PLUGIN_DIR), "--max-tool-tokens", "kaputt"]) == 2
        assert "FEHLER" in capsys.readouterr().err
