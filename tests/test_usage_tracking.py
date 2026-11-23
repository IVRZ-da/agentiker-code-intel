"""Regression: usage.py versteht den Host-Vertrag (F16).

Der Host ruft ``post_tool_call`` mit ``args`` (dict), ``status`` und
``duration_ms`` auf (``model_tools._emit_post_tool_call_hook``). 0.6.40 las
``arguments``/``tool_args`` und ``error``/``is_error`` — jede Zeile der
Nutzungsmessung trug damit ``op=null`` und ``error=false``.
"""

from __future__ import annotations

import json

import pytest

from code_intel import usage


@pytest.fixture()
def logfile(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv(usage.LOG_ENV, str(path))
    return path


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_host_payload_yields_op_and_status(logfile):
    usage.on_post_tool_call(
        tool_name="code_search", args={"op": "symbols", "path": "x.py"},
        status="ok", session_id="s1", result="{}", duration_ms=12,
    )
    rec = _records(logfile)[0]
    assert rec["op"] == "symbols"
    assert rec["error"] is False
    assert rec["status"] == "ok"
    assert rec["tool"] == "code_search"


def test_host_status_error_is_flagged(logfile):
    usage.on_post_tool_call(
        tool_name="code_lsp", args={"op": "diagnostics"}, status="error",
        error_type="ToolError", error_message="boom",
    )
    rec = _records(logfile)[0]
    assert rec["op"] == "diagnostics"
    assert rec["error"] is True


@pytest.mark.parametrize("kwargs,expected", [
    ({"args": {"op": "search"}}, "search"),
    ({"arguments": '{"op": "symbols"}'}, "symbols"),
    ({"tool_args": {"op": "overview"}}, "overview"),
    ({"args": {}}, None),
    ({"args": "kein json"}, None),
    ({}, None),
])
def test_op_source_order(kwargs, expected):
    assert usage._op_of(kwargs) == expected


@pytest.mark.parametrize("kwargs,expected", [
    ({"status": "ok"}, False),
    ({"status": "error"}, True),
    ({"status": "success"}, False),
    ({"is_error": True}, True),
    ({"error": "boom"}, True),
    ({"error": False}, False),
    ({"error_message": "x"}, True),
    ({}, False),
])
def test_error_detection(kwargs, expected):
    assert usage._is_error(kwargs) is expected


def test_non_code_intel_tool_is_ignored(logfile):
    usage.on_post_tool_call(tool_name="terminal", args={"command": "ls"}, status="ok")
    assert not logfile.exists()


def test_broken_log_path_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv(usage.LOG_ENV, str(tmp_path / "nested" / "dir" / "u.jsonl"))
    usage.on_post_tool_call(tool_name="code_search", args={"op": "search"}, status="ok")
    assert (tmp_path / "nested" / "dir" / "u.jsonl").exists()
