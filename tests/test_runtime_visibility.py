"""Runtime-Gegenprobe für den Core-Override-Umbau (v0.6.27).

Die statische Validierung (``hermes plugins validate``) beweist nur, dass kein
Core-Attribut mehr zugewiesen wird. Sie beweist NICHT, dass die Tools zur Laufzeit
noch sichtbar sind — genau das war das Ziel des Umbaus
(``references/core-override-analysis.md``).

Geprüft wird:

1. **Preset-Membership** — die Tools landen weiterhin in ``hermes-acp`` und
   ``hermes-api-server`` (einzige verbliebene Core-Mutation, (c)).
2. **Registry-Mitgliedschaft** — das Toolset existiert über die Registrierung,
   ohne Eintrag im statischen ``TOOLSETS``-Dict (ersetzt Rebind (a)).
3. **Subagent-Vererbung** — ein Kind erbt das Toolset vom Parent; der globale
   ``DEFAULT_TOOLSETS``-Fallback bleibt unangetastet (ersetzt Rebind (d)).
4. **Steering im Child-Turn** — siehe ``test_plugin_init_registry``
   (``TestSubagentSteeringViaHook``), ersetzt Rebind (e).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from code_intel import _inject_toolsets, _register_lsp_and_cache
from code_intel.namespaces import GROUPS

DISPATCHERS = set(GROUPS)


def _real_toolsets():
    """Das echte Hermes-``toolsets``-Modul, sonst skip."""
    try:
        import toolsets  # noqa: F401 — nur Verfügbarkeit prüfen
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"kein Hermes-Runtime im Interpreter: {exc}")
    return toolsets


# ─── 1. Preset-Membership ((c) bleibt, aber muss wirken) ─────────────────────


@pytest.mark.parametrize("preset", ["hermes-acp", "hermes-api-server"])
def test_preset_bundle_receives_tools(preset, monkeypatch):
    ts = _real_toolsets()
    if preset not in ts.TOOLSETS:
        pytest.skip(f"{preset} in dieser Hermes-Version nicht vorhanden")

    monkeypatch.setenv("CODE_INTEL_TOOL_PROFILE", "all")
    before = set(ts.TOOLSETS[preset]["tools"])
    _inject_toolsets()
    after = set(ts.TOOLSETS[preset]["tools"])

    missing = DISPATCHERS - after
    assert not missing, f"{preset} fehlen die Dispatcher: {sorted(missing)}"
    assert before <= after, "bestehende Preset-Tools dürfen nicht verschwinden"


def test_preset_injection_is_idempotent(monkeypatch):
    ts = _real_toolsets()
    if "hermes-acp" not in ts.TOOLSETS:
        pytest.skip("hermes-acp nicht vorhanden")

    monkeypatch.setenv("CODE_INTEL_TOOL_PROFILE", "all")
    _inject_toolsets()
    once = list(ts.TOOLSETS["hermes-acp"]["tools"])
    _inject_toolsets()
    assert ts.TOOLSETS["hermes-acp"]["tools"] == once, "zweiter Lauf darf nichts duplizieren"


# ─── 2. Registry statt statischem Dict ((a) ist entfallen) ───────────────────


def test_toolset_is_published_without_static_entry(monkeypatch):
    """Ohne TOOLSETS-Eintrag registriert register() alle Dispatcher im Toolset."""
    from code_intel import namespaces as ns

    raw = []

    class _Recorder:
        def register_tool(self, name=None, toolset=None, schema=None, handler=None,
                          description="", **kwargs):
            if name:
                raw.append((name, toolset))

        def __getattr__(self, item):
            return lambda *a, **k: None

    monkeypatch.delenv("CODE_INTEL_NAMESPACES", raising=False)
    ns.reset_catalog()
    _register_lsp_and_cache(_Recorder())

    names = {n for n, _ in raw}
    toolsets_used = {t for _, t in raw}
    assert DISPATCHERS <= names, f"fehlende Dispatcher: {sorted(DISPATCHERS - names)}"
    assert toolsets_used == {"agentiker_code_intel"}, (
        f"Tools müssen im eigenen Toolset registriert sein, gefunden: {toolsets_used}"
    )


# ─── 3. Subagent erbt das Toolset ((d) ist entfallen) ───────────────────────
#
# Import im Subprozess mit cwd=Hermes-Installation: das Plugin bringt ein eigenes
# Paket ``tools/`` mit, das im Plugin-Verzeichnis ``import tools`` kapert
# ("attempted relative import beyond top-level package").

_PROBE = r"""
import json, sys
from tools.delegate_tool_toolsets import DEFAULT_TOOLSETS, _resolve_child_toolsets

class Parent:
    enabled_toolsets = ["hermes-cli", "agentiker_code_intel"]
    disabled_toolsets = []

class Bare:
    enabled_toolsets = None
    disabled_toolsets = []

inherited, _ = _resolve_child_toolsets(Parent(), None, "leaf")
fallback, _ = _resolve_child_toolsets(Bare(), None, "leaf")
print(json.dumps({
    "default_toolsets": list(DEFAULT_TOOLSETS),
    "inherited": inherited,
    "fallback": fallback,
}))
"""


def _hermes_install_dir() -> Path:
    for candidate in (Path.home() / ".hermes" / "hermes-agent", Path("/opt/hermes")):
        if (candidate / "tools" / "delegate_tool_toolsets.py").is_file():
            return candidate
    pytest.skip("Hermes-Installation nicht gefunden (tools/delegate_tool_toolsets.py)")


def _probe_child_toolsets() -> dict:
    import json
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=str(_hermes_install_dir()),
        capture_output=True, text=True, timeout=120,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert proc.returncode == 0, f"Probe fehlgeschlagen: {proc.stderr[-400:]}"
    return json.loads(proc.stdout)


def test_child_inherits_parent_toolset():
    data = _probe_child_toolsets()
    assert "agentiker_code_intel" in data["inherited"], (
        "Kind muss das Toolset vom Parent erben — sonst fehlen Subagenten die Tools"
    )
    assert "agentiker_code_intel" not in data["default_toolsets"], (
        "Rebind (d) ist zurück: DEFAULT_TOOLSETS wird global verändert"
    )


def test_default_toolsets_fallback_unchanged():
    """Der Fallback (Parent ohne Toolsets) bleibt bewusst ohne code_intel."""
    data = _probe_child_toolsets()
    assert "agentiker_code_intel" not in data["fallback"], (
        "Fallback darf die Tools nicht global in jeden Child-Prompt ziehen"
    )
