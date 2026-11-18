"""Tests für die Op-Metadaten-Registry (tools/op_meta.py, Release 0.6.32).

Die Registry ist die gemeinsame Quelle für Schema-Narrowing (0.6.33), ``op=help``
(0.6.34) und Output-Caps (0.6.36). Der kritische Punkt ist deshalb nicht die
Vollständigkeit der Tabelle, sondern die **Drift** gegen die echten Handler-Schemas:
ein neuer op ohne Meta oder ein geändertes ``required`` muss hier auffallen, nicht
erst im Schema-Narrowing.
"""

from __future__ import annotations

import copy

import pytest

from code_intel import _register_ast_tools
from code_intel import namespaces as ns
from code_intel.tools import op_meta as om


@pytest.fixture(scope="module")
def catalog():
    """Der echte Registrierungs-Catalog (AST- plus LSP-Tools)."""
    from code_intel.lsp.handlers import register_lsp_tools

    ns.reset_catalog()
    cat = ns.build_catalog(_register_ast_tools, register_lsp_tools)
    yield cat
    ns.reset_catalog()


def _group_ops() -> set[str]:
    return {op for cfg in ns.GROUPS.values() for op in cfg["ops"]}


# ─── Abdeckung ───────────────────────────────────────────────────────────────


class TestCoverage:
    def test_registry_has_70_entries(self):
        assert len(om.OP_META) == 70

    def test_every_group_op_has_meta(self):
        missing = sorted(_group_ops() - set(om.OP_META))
        assert missing == [], f"ops ohne Metadaten: {missing}"

    def test_every_meta_belongs_to_a_group(self):
        orphan = sorted(set(om.OP_META) - _group_ops())
        assert orphan == [], f"Metadaten ohne Dispatcher-op: {orphan}"

    def test_group_field_matches_dispatcher(self):
        for group, cfg in ns.GROUPS.items():
            for op in cfg["ops"]:
                assert om.OP_META[op].group == group, f"{op}: group {om.OP_META[op].group} != {group}"

    def test_op_names_is_complete(self):
        assert set(om.op_names()) == set(om.OP_META)


# ─── Drift gegen die echten Handler-Schemas ──────────────────────────────────


class TestCatalogDrift:
    def test_no_drift_against_real_catalog(self, catalog):
        problems = om.validate_against(catalog)
        assert problems == [], "Registry driftet:\n" + "\n".join(problems)

    def test_missing_handler_is_reported(self, catalog):
        broken = {k: v for k, v in catalog.items() if k != "code_symbols"}
        problems = om.validate_against(broken)
        assert any("symbols" in p and "ohne registrierten Handler" in p for p in problems)

    def test_required_drift_is_reported(self, catalog):
        broken = copy.deepcopy(catalog)
        broken["code_capsule"]["schema"]["parameters"]["required"] = ["path"]
        problems = om.validate_against(broken)
        assert any("capsule" in p and "required-Drift" in p for p in problems)

    def test_optional_drift_is_reported(self, catalog):
        broken = copy.deepcopy(catalog)
        broken["code_hover"]["schema"]["parameters"]["properties"]["new_field"] = {"type": "string"}
        problems = om.validate_against(broken)
        assert any("hover" in p and "optional-Drift" in p for p in problems)

    def test_build_registry_fails_closed_on_drift(self, catalog):
        broken = {k: v for k, v in catalog.items() if k != "code_impact"}
        with pytest.raises(ValueError, match="impact"):
            om.build_registry(broken)

    def test_build_registry_accepts_real_catalog(self, catalog):
        registry = om.build_registry(catalog)
        assert registry.keys() == om.OP_META.keys()


# ─── Plausibilität der Einträge ──────────────────────────────────────────────


class TestEntries:
    def test_tiers_are_valid(self):
        assert {m.tier for m in om.OP_META.values()} <= {1, 2, 3}

    def test_caps_are_positive(self):
        for meta in om.OP_META.values():
            assert meta.max_items > 0, meta.op
            assert meta.max_tokens > 0, meta.op

    def test_caps_dict_shape(self):
        assert om.OP_META["symbols"].caps == {"max_items": 200, "max_tokens": 1500}

    def test_required_and_optional_do_not_overlap(self):
        for meta in om.OP_META.values():
            overlap = set(meta.required) & set(meta.optional)
            assert overlap == set(), f"{meta.op}: {overlap} ist required und optional"

    def test_op_is_not_a_parameter(self):
        # 'op' kommt vom Dispatcher, nie aus dem Handler-Schema.
        for meta in om.OP_META.values():
            assert "op" not in meta.required and "op" not in meta.optional, meta.op

    def test_returns_text_present(self):
        for meta in om.OP_META.values():
            assert meta.returns.strip(), f"{meta.op}: keine returns-Beschreibung"

    def test_next_ops_point_to_known_ops(self):
        for meta in om.OP_META.values():
            for nxt in meta.next_ops:
                assert nxt in om.OP_META, f"{meta.op}: next {nxt} existiert nicht"

    def test_next_ops_do_not_self_reference(self):
        for meta in om.OP_META.values():
            assert meta.op not in meta.next_ops, meta.op

    def test_tier1_covers_daily_drivers(self):
        must = {"search", "symbols", "capsule", "definition", "references", "impact"}
        assert must <= {op for op, m in om.OP_META.items() if m.tier == 1}


# ─── help_text / example_for ─────────────────────────────────────────────────


class TestHelpText:
    def test_help_contains_every_relevant_field(self):
        text = om.help_text("symbols")
        assert "op=symbols" in text
        assert "required: path" in text
        assert "example:" in text and "'op': 'symbols'" in text
        assert "limits:" in text and "max_tokens=1500" in text
        assert "next:" in text and "capsule" in text

    def test_help_without_caps(self):
        assert "limits:" not in om.help_text("hover", include_caps=False)

    @pytest.mark.parametrize("op", sorted(om.OP_META))
    def test_help_is_generated_for_every_op(self, op):
        text = om.help_text(op)
        assert text.startswith(f"op={op}")
        assert "unbekannter op" not in text

    def test_unknown_op_help_lists_examples(self):
        text = om.help_text("does_not_exist")
        assert "unbekannter op" in text
        assert "search" in text

    def test_example_contains_all_required_fields(self):
        for meta in om.OP_META.values():
            example = om.example_for(meta.op)
            assert example["op"] == meta.op
            for field in meta.required:
                assert field in example, f"{meta.op}: {field} fehlt im Beispiel"

    def test_example_overrides_are_used(self):
        assert om.example_for("workspace_symbols")["query"] == "parse_config"
        assert om.example_for("pr_impact")["base_branch"] == "main"
        assert om.example_for("unknown_op") == {"op": "unknown_op"}


# ─── caps_for ────────────────────────────────────────────────────────────────


class TestCapsFor:
    def test_known_op_returns_registry_caps(self):
        assert om.caps_for("overview") == {"max_items": 80, "max_tokens": 2000}

    def test_unknown_op_returns_default(self):
        assert om.caps_for("nope") == {"max_items": 50, "max_tokens": 2000}

    def test_custom_default_is_respected(self):
        assert om.caps_for("nope", {"max_items": 1, "max_tokens": 2}) == {"max_items": 1, "max_tokens": 2}

    def test_mutating_returned_caps_does_not_leak(self):
        caps = om.caps_for("symbols")
        caps["max_items"] = 9999
        assert om.caps_for("symbols")["max_items"] == 200


# ─── as_dict ─────────────────────────────────────────────────────────────────


def test_as_dict_is_json_ready():
    payload = om.OP_META["capsule"].as_dict()
    assert payload["op"] == "capsule"
    assert payload["caps"] == {"max_items": 1, "max_tokens": 1500}
    assert isinstance(payload["required"], list)
    assert isinstance(payload["next"], list)
    assert set(payload) == {"op", "group", "tier", "required", "optional", "caps", "returns", "next"}
