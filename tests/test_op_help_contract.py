"""Regression: ``op=help`` ist im Schema und antwortet als JSON (F18).

Alle sieben Dispatcher-Descriptions bewerben ``op=help``. Das Schema-Enum
enthielt es nicht — der Aufruf scheiterte an der Validierung. Der Handler
kannte ``help`` bereits, gab aber Prosa statt JSON zurück.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from code_intel.namespaces import GROUPS, build_tool_specs


def _catalog() -> dict:
    catalog: dict[str, dict[str, Any]] = {}
    for group, cfg in GROUPS.items():
        for op in list(cfg["ops"])[:2]:
            catalog[f"code_{op}"] = {
                "handler": lambda args: "{}",
                "schema": {"parameters": {"properties": {}, "required": []}},
                "description": f"op {op}",
            }
    return catalog


@pytest.fixture()
def specs():
    return build_tool_specs(_catalog())


def test_help_is_in_every_enum(specs):
    assert specs
    for spec in specs:
        enum = spec["schema"]["parameters"]["properties"]["op"]["enum"]
        assert "help" in enum, spec["name"]
        assert len(enum) >= 2


def test_help_output_is_json_per_group(specs):
    for spec in specs:
        raw = spec["handler"]({"op": "help"})
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert spec["name"] in data["help"]


def test_help_for_single_op_is_json(specs):
    spec = specs[0]
    op = [o for o in spec["schema"]["parameters"]["properties"]["op"]["enum"] if o != "help"][0]
    data = json.loads(spec["handler"]({"op": "help", "query": op}))
    assert data["status"] == "ok"
    assert op in data["help"]


def test_help_with_unknown_target_is_json_error(specs):
    spec = specs[0]
    data = json.loads(spec["handler"]({"op": "help", "query": "gibtsnicht"}))
    assert data["status"] == "error"
    assert "gibtsnicht" in data["error"]
    assert "help" in data
