"""Tests for the namespace tool fusion (P3.3).

The fusion replaces 70 individual ``code_*`` tool schemas with 7 dispatchers.
The critical invariant: nothing may disappear — every tool in the real
registration catalog must be reachable through exactly one ``op``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from code_intel import namespaces as ns


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def catalog():
    """The real registration catalog (AST tools + LSP tools)."""
    from code_intel import _register_ast_tools
    from code_intel.lsp.handlers import register_lsp_tools

    ns.reset_catalog()
    cat = ns.build_catalog(_register_ast_tools, register_lsp_tools)
    assert cat, "catalog must not be empty"
    return cat


@pytest.fixture
def fake_catalog():
    """Two ops with required parameters, one without."""
    return {
        "code_alpha": {
            "schema": {
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "file"}, "line": {"type": "integer"}},
                    "required": ["path"],
                }
            },
            "handler": lambda args: json.dumps({"status": "ok", "echo": args}),
            "description": "alpha",
        },
        "code_beta": {
            "schema": {
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "q"}},
                    "required": [],
                }
            },
            "handler": lambda args: json.dumps({"status": "ok", "beta": args}),
            "description": "beta",
        },
    }


# ─── Flag ────────────────────────────────────────────────────────────────────


class TestFlag:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv(ns.ENV_FLAG, raising=False)
        assert ns.namespaces_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "legacy", "FALSE"])
    def test_explicit_off(self, monkeypatch, value):
        monkeypatch.setenv(ns.ENV_FLAG, value)
        assert ns.namespaces_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_explicit_on(self, monkeypatch, value):
        monkeypatch.setenv(ns.ENV_FLAG, value)
        assert ns.namespaces_enabled() is True


# ─── Coverage: nothing may be lost ───────────────────────────────────────────


class TestCoverage:
    def test_union_of_ops_equals_catalog(self, catalog):
        declared = [op for cfg in ns.GROUPS.values() for op in cfg["ops"]]
        expected = {name.removeprefix("code_") for name in catalog}

        assert len(declared) == len(set(declared)), (
            f"op doppelt zugeordnet: {[o for o in declared if declared.count(o) > 1]}"
        )

        missing = expected - set(declared)
        unknown = set(declared) - expected
        assert not missing, f"Tools ohne Namespace-Op (würden verloren gehen): {sorted(missing)}"
        assert not unknown, f"Ops ohne echtes Tool: {sorted(unknown)}"

    def test_seven_groups(self):
        assert len(ns.GROUPS) == 7

    def test_every_group_has_ops_in_catalog(self, catalog):
        for group, cfg in ns.GROUPS.items():
            present = [op for op in cfg["ops"] if f"code_{op}" in catalog]
            assert present, f"{group} hat keine auflösbaren Ops"


class TestSchemas:
    def test_one_spec_per_group(self, catalog):
        specs = ns.build_tool_specs(catalog)
        assert len(specs) == 7
        assert {s["name"] for s in specs} == set(ns.GROUPS)

    def test_schema_shape(self, catalog):
        for spec in ns.build_tool_specs(catalog):
            params = spec["schema"]["parameters"]
            assert params["type"] == "object"
            assert params["required"] == ["op"]
            props = params["properties"]
            assert props["op"]["enum"] == spec["ops"], f"{spec['name']}: op-Enum falsch"
            assert len(props) > 1, f"{spec['name']}: keine Parameter übernommen"

    def test_required_params_of_every_tool_are_available(self, catalog):
        """A tool's required args must be expressible in its namespace schema."""
        for spec in ns.build_tool_specs(catalog):
            for op in spec["ops"]:
                tool_params = (catalog[f"code_{op}"]["schema"] or {}).get("parameters") or {}
                required = tool_params.get("required") or []
                missing = [r for r in required if r not in spec["schema"]["parameters"]["properties"]]
                assert not missing, f"{spec['name']}/op={op}: Pflichtparameter fehlen im Schema: {missing}"

    def test_all_declared_params_have_types(self, catalog):
        for spec in ns.build_tool_specs(catalog):
            for key, val in spec["schema"]["parameters"]["properties"].items():
                assert val.get("type") in {"string", "integer", "boolean", "array", "number", "object"}, (
                    f"{spec['name']}.{key}: type fehlt"
                )


class TestTokenBudget:
    def test_namespaces_are_much_cheaper_than_legacy(self, catalog):
        legacy = json.dumps(
            [
                {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": (tool.get("schema") or {}).get("parameters") or {},
                }
                for name, tool in catalog.items()
            ],
            ensure_ascii=False,
        )
        fused = json.dumps(
            [
                {"name": s["name"], "description": s["schema"]["description"], "parameters": s["schema"]["parameters"]}
                for s in ns.build_tool_specs(catalog)
            ],
            ensure_ascii=False,
        )

        legacy_tok = len(legacy) / 3.7
        fused_tok = len(fused) / 3.7
        assert fused_tok < legacy_tok * 0.75, (
            f"Fusion spart zu wenig: legacy {legacy_tok:.0f} Tok vs "
            f"namespaces {fused_tok:.0f} Tok "
            f"({(1 - fused_tok / legacy_tok) * 100:.0f} % Ersparnis, erwartet >25 %)")


# ─── Dispatch ────────────────────────────────────────────────────────────────


class TestDispatch:
    def test_dispatch_passes_args(self, fake_catalog, monkeypatch):
        monkeypatch.setitem(ns.GROUPS, "code_search", {"description": "d", "ops": ["alpha", "beta"]})
        spec = ns.build_tool_specs(fake_catalog)[0]
        out = json.loads(spec["handler"]({"op": "alpha", "path": "/x.py", "line": 3}))
        assert out["status"] == "ok"
        assert out["echo"] == {"path": "/x.py", "line": 3}

    def test_op_is_not_forwarded(self, fake_catalog, monkeypatch):
        monkeypatch.setitem(ns.GROUPS, "code_search", {"description": "d", "ops": ["alpha", "beta"]})
        spec = ns.build_tool_specs(fake_catalog)[0]
        out = json.loads(spec["handler"]({"op": "beta", "query": "x"}))
        assert "op" not in out["beta"]

    def test_missing_op_lists_choices(self, fake_catalog, monkeypatch):
        monkeypatch.setitem(ns.GROUPS, "code_search", {"description": "d", "ops": ["alpha", "beta"]})
        spec = ns.build_tool_specs(fake_catalog)[0]
        out = json.loads(spec["handler"]({}))
        assert out["status"] == "error"
        assert "alpha" in out["error"] and "beta" in out["error"]

    def test_unknown_op(self, fake_catalog, monkeypatch):
        monkeypatch.setitem(ns.GROUPS, "code_search", {"description": "d", "ops": ["alpha", "beta"]})
        spec = ns.build_tool_specs(fake_catalog)[0]
        out = json.loads(spec["handler"]({"op": "gamma"}))
        assert out["status"] == "error"
        assert "unknown op" in out["error"]

    def test_missing_required_param_is_named(self, fake_catalog, monkeypatch):
        monkeypatch.setitem(ns.GROUPS, "code_search", {"description": "d", "ops": ["alpha", "beta"]})
        spec = ns.build_tool_specs(fake_catalog)[0]
        out = json.loads(spec["handler"]({"op": "alpha"}))
        assert out["status"] == "error"
        assert "path" in out["error"]

    def test_handler_exception_becomes_error_json(self, fake_catalog, monkeypatch):
        def boom(args):
            raise RuntimeError("kaputt")

        fake_catalog["code_alpha"]["handler"] = boom
        monkeypatch.setitem(ns.GROUPS, "code_search", {"description": "d", "ops": ["alpha", "beta"]})
        spec = ns.build_tool_specs(fake_catalog)[0]
        out = json.loads(spec["handler"]({"op": "alpha", "path": "/x"}))
        assert out["status"] == "error"
        assert "kaputt" in out["error"]

    def test_kwargs_are_merged(self, fake_catalog, monkeypatch):
        monkeypatch.setitem(ns.GROUPS, "code_search", {"description": "d", "ops": ["alpha", "beta"]})
        spec = ns.build_tool_specs(fake_catalog)[0]
        out = json.loads(spec["handler"](op="beta", query="kw"))
        assert out["beta"] == {"query": "kw"}


# ─── Registration ────────────────────────────────────────────────────────────


class TestRegistration:
    def test_registers_seven_tools_with_full_schema(self, catalog):
        ctx = MagicMock()
        count = ns.register_namespaces(ctx, catalog)

        assert count == 7
        assert ctx.register_tool.call_count == 7
        for call in ctx.register_tool.call_args_list:
            kwargs = call.kwargs
            name = kwargs["name"]
            schema = kwargs["schema"]
            assert schema["name"] == name
            params = schema.get("parameters")
            assert isinstance(params, dict), f"{name}: parameters fehlen"
            assert params["properties"], f"{name}: leeres Schema"
            assert callable(kwargs["handler"])
            assert kwargs["toolset"] == ns.TOOLSET

    def test_real_op_runs_real_handler(self, catalog):
        """End-to-end: op=symbols must execute the original code_symbols handler."""
        spec = [s for s in ns.build_tool_specs(catalog) if s["name"] == "code_search"][0]
        out = spec["handler"]({"op": "symbols", "path": "/home/jo/.hermes/plugins/code_intel/_fmt.py"})
        assert "def " in out or "fmt_ok" in out
