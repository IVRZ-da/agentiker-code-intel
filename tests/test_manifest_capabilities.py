"""Contract tests: plugin.yaml `provides_*` must match what register() registers.

`hermes plugins validate` (the Hermes plugin-catalog CI gate) diffs two sets:

* ``undeclared = registered - provides_*``  -> hard failure
* ``unregistered = provides_* - registered`` -> warning only

The plugin registers either 7 namespace dispatchers (default) or the 70 legacy
``code_*`` tools (``CODE_INTEL_NAMESPACES=0``). Only the default set is declared:
the catalog validates the default configuration, and declaring 70 legacy names
would emit 70 "declared but not registered" warnings on every run.

These tests pin that decision so a future registration change cannot silently
break the catalog gate again (PR #4 on GitHub was exactly that failure).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from code_intel import namespaces as ns

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PLUGIN_ROOT / "plugin.yaml"


# ─── Recorders ───────────────────────────────────────────────────────────────


class _HookRecorder:
    """Records hooks/commands; everything else is a no-op."""

    def __init__(self) -> None:
        self.hooks: list[str] = []
        self.commands: list[str] = []

    def register_hook(self, name: str, callback=None, **kwargs) -> None:
        self.hooks.append(name)

    def register_command(self, name: str, **kwargs) -> None:
        self.commands.append(name)

    def __getattr__(self, item: str):
        def _noop(*args, **kwargs):
            return None

        return _noop


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def manifest() -> dict:
    with MANIFEST.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def legacy_catalog() -> dict:
    """Every tool the legacy (1:1) registration path publishes."""
    from code_intel import _register_ast_tools
    from code_intel.lsp.handlers import register_lsp_tools

    ns.reset_catalog()
    catalog = ns.build_catalog(_register_ast_tools, register_lsp_tools)
    assert catalog, "real registration catalog must not be empty"
    return catalog


# ─── Default mode (catalog-relevant) ─────────────────────────────────────────


def test_declares_every_namespace_dispatcher(manifest):
    """Default mode: unregistered/undeclared must both be empty."""
    declared = set(manifest.get("provides_tools") or [])
    dispatchers = set(ns.GROUPS)

    assert dispatchers - declared == set(), (
        "register() publishes these namespace tools but plugin.yaml does not "
        f"declare them -> hermes plugins validate fails: {sorted(dispatchers - declared)}"
    )
    assert declared - dispatchers == set(), (
        f"plugin.yaml declares tools that are never registered: {sorted(declared - dispatchers)}"
    )


def test_declares_every_registered_hook(manifest):
    """Hooks registered in _register_command_and_hooks must be declared."""
    from code_intel import _register_command_and_hooks

    recorder = _HookRecorder()
    _register_command_and_hooks(recorder)

    declared = set(manifest.get("provides_hooks") or [])
    registered = set(recorder.hooks)

    assert registered - declared == set(), (
        f"hooks registered but not declared in provides_hooks: {sorted(registered - declared)}"
    )
    assert declared - registered == set(), (
        f"provides_hooks declares hooks that are never registered: {sorted(declared - registered)}"
    )
    assert registered, "expected _register_command_and_hooks to register at least one hook"


# ─── Legacy mode (deliberately not catalog-ready) ────────────────────────────


def test_legacy_mode_is_deliberately_undeclared(manifest, legacy_catalog):
    """Documented trade-off: CODE_INTEL_NAMESPACES=0 is not catalog-capable."""
    declared = set(manifest.get("provides_tools") or [])
    legacy = set(legacy_catalog)

    # The legacy catalog and the dispatchers are mostly disjoint names: only
    # ``code_search`` is both (it is a dispatcher group *and* an op name). Names
    # like code_lsp/code_graph/code_meta/code_review/code_git/code_edit exist
    # only as dispatchers.
    overlap = set(ns.GROUPS) & legacy
    assert overlap == {"code_search"}, (
        "unexpected overlap between dispatchers and legacy tool names: "
        f"{sorted(overlap)}"
    )

    undeclared = legacy - declared
    assert undeclared, (
        "legacy mode is expected to publish tools that are not declared; if this "
        "ever becomes empty, update plugin.yaml and drop the documented exception"
    )


def test_every_legacy_tool_maps_to_a_dispatcher_op(legacy_catalog):
    """No capability may exist only in legacy mode (guards against silent loss)."""
    ops = {op for spec in ns.GROUPS.values() for op in spec["ops"]}
    mapped = {f"code_{op}" for op in ops}

    assert set(legacy_catalog) == mapped, (
        "legacy catalog and namespace ops drifted apart: "
        f"only legacy={sorted(set(legacy_catalog) - mapped)}, "
        f"only ops={sorted(mapped - set(legacy_catalog))}"
    )
