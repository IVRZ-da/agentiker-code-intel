"""Runtime-Gegenprobe für den Core-Override-Umbau (v0.6.27 → v0.6.37).

Die statische Validierung (``hermes plugins validate``) beweist nur, dass kein
Core-Attribut zugewiesen wird. Sie beweist NICHT, dass die Tools zur Laufzeit
noch sichtbar sind und dass die Steering weiterhin ankommt.

Geprüft wird:

1. **Registry-Mitgliedschaft** — das Toolset existiert über die Registrierung
   (``ctx.register_tool(..., toolset=...)``), ohne Eintrag im statischen
   ``TOOLSETS``-Dict und ohne Append in die Presets ``hermes-acp`` /
   ``hermes-api-server`` (entfallen in v0.6.37, Hermes-Review PR #157345).
2. **Steering ohne Core-Patch** — der Hinweis auf die Dispatcher kommt aus
   ``ctx.register_system_prompt_section``, nicht mehr aus gepatchten
   ``description``-Feldern fremder Tools.
3. **Subagent-Vererbung** — ein Kind erbt das Toolset vom Parent; der globale
   ``DEFAULT_TOOLSETS``-Fallback bleibt unangetastet (ersetzt Rebind (d)).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from code_intel import _register_steering_section
from code_intel.namespaces import GROUPS

DISPATCHERS = set(GROUPS)


# ─── 1. Steering ohne Core-Patch ─────────────────────────────────────────────


class _SteeringRecorder:
    """Minimal-ctx: sammelt register_system_prompt_section-Aufrufe."""

    def __init__(self) -> None:
        self.sections: list[tuple[str, str]] = []

    def register_system_prompt_section(self, section_id, content, **kwargs):
        self.sections.append((section_id, content))


def test_steering_registered_as_system_prompt_section():
    ctx = _SteeringRecorder()
    _register_steering_section(ctx)

    assert ctx.sections, "Steering wurde nicht als System-Prompt-Sektion registriert"
    section_id, content = ctx.sections[0]
    assert section_id == "code-intel"
    for name in ("code_search", "code_lsp", "code_graph", "code_edit"):
        assert name in content, f"Steering-Text nennt {name} nicht"


def test_steering_survives_missing_api():
    """Ältere Hermes-Version ohne register_system_prompt_section darf nicht crashen."""

    class _OldCtx:
        pass

    _register_steering_section(_OldCtx())  # kein raise


def test_no_core_toolset_mutation_left():
    """Rebind (c) ist entfallen: kein toolsets-Import, keine Inject-Funktion."""
    import code_intel as init_mod

    assert not hasattr(init_mod, "_inject_toolsets")
    assert not hasattr(init_mod, "_inject_steering_hints")
    assert not hasattr(init_mod, "toolsets"), (
        "Modul importiert weiterhin das Host-toolsets-Modul — Core-Mutation droht"
    )


# ─── 2. Registry statt statischem Dict ((a) ist entfallen) ───────────────────


def test_toolset_is_published_without_static_entry(monkeypatch):
    """Ohne TOOLSETS-Eintrag registriert register() alle Dispatcher im Toolset."""
    from code_intel import _register_lsp_and_cache
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
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        # Der Probe laeuft absichtlich im selben Interpreter (Closure-Semantik des
        # Hosts wird getestet). Fehlen dort die Hermes-Abhaengigkeiten, ist das
        # eine Eigenschaft der Testumgebung, kein Verhaltensbefund — vorher war es
        # ein harter Failure (ruamel fehlt unter /usr/bin/python3).
        pytest.skip(f"Test-Interpreter hat die Hermes-Deps nicht: {proc.stderr.strip().splitlines()[-1]}")
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
