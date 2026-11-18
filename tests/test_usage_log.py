"""Nutzungs-Messung für code_intel + Bridge-Muster im Steering-Text.

Zwei Dinge werden hier festgenagelt:

1. ``usage.on_post_tool_call`` schreibt genau eine Zeile pro ``code_*``-Aufruf
   (und keine für Fremd-Tools) — die Grundlage jeder späteren „wird das genutzt?"-Aussage.
2. Der Subagenten-Steering-Text enthält ein *parsbares* ``tool_call``-Muster:
   sechs der sieben Dispatcher sind deferred, ein Name ohne Aufrufpfad ist ein
   toter Verweis.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

import usage  # noqa: E402


@pytest.fixture()
def logfile(tmp_path, monkeypatch):
    target = tmp_path / "plugin-usage.jsonl"
    monkeypatch.setenv(usage.LOG_ENV, str(target))
    return target


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ─── 1. Messung ────────────────────────────────────────────────────────────

def test_records_dispatcher_call_with_op(logfile):
    usage.on_post_tool_call(tool_name="code_search", arguments={"op": "symbols", "path": "x.py"},
                            session_id="s42")
    (row,) = _rows(logfile)
    assert row["plugin"] == "code_intel"
    assert (row["tool"], row["op"], row["session_id"]) == ("code_search", "symbols", "s42")
    assert row["error"] is False


def test_accepts_json_string_arguments(logfile):
    usage.on_post_tool_call(tool_name="code_lsp", arguments='{"op": "references", "line": 3}')
    assert _rows(logfile)[0]["op"] == "references"


def test_counts_legacy_tool_names(logfile):
    usage.on_post_tool_call(tool_name="code_symbols")
    usage.on_post_tool_call(tool_name="code_impact")
    assert [r["tool"] for r in _rows(logfile)] == ["code_symbols", "code_impact"]


def test_ignores_foreign_tools(logfile):
    for name in ("read_file", "terminal", "analysis", "plan_create", "codex"):
        usage.on_post_tool_call(tool_name=name)
    assert not logfile.exists() or _rows(logfile) == []


def test_never_raises_on_bad_payload(logfile):
    usage.on_post_tool_call(tool_name="code_edit", arguments="{kein json")
    usage.on_post_tool_call()
    (row,) = _rows(logfile)
    assert row["op"] is None


def test_shares_the_log_with_scout(logfile):
    """Beide Plugins schreiben in dieselbe Datei — eine Auswertung, zwei Quellen."""
    usage.on_post_tool_call(tool_name="code_graph", arguments={"op": "impact"})
    assert _rows(logfile)[0]["plugin"] == "code_intel"
    assert usage.log_path() == logfile


# ─── 2. Steering-Text (deferred Dispatcher brauchen den Aufrufpfad) ────────

def test_steering_namespaced_carries_parsable_tool_call():
    text = (PLUGIN_ROOT / "hooks.py").read_text(encoding="utf-8")
    block = re.search(r"tool_call\((\{.*?\})\)", text, re.DOTALL)
    assert block, "kein tool_call-Muster im Steering-Text"
    payload = json.loads(block.group(1))
    call = payload["calls"][0]
    assert call["name"].startswith("code_")
    assert "op" in call["arguments"]


def test_steering_mentions_all_seven_dispatchers():
    text = (PLUGIN_ROOT / "hooks.py").read_text(encoding="utf-8")
    for name in ("code_search", "code_lsp", "code_graph", "code_edit",
                 "code_review", "code_git", "code_meta"):
        assert name in text, name
