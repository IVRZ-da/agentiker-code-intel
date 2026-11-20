"""Tests for uncovered lines in __init__.py register() function.

Abgedeckt:
- LSP-registration except-Zweig (Warnung statt Crash)
- symbol-cache restore (Log nur bei count > 0)
- keine Mutation von Core-Tool-Schemas (Ersatz fuer das entfernte Schema-Patching)
- Subagent-Steering via oeffentlichem pre_llm_call-Hook
- Source-Guards gegen die zurueckgebauten Core-Rebinds

Hintergrund: Der frueher hier getestete Preset-Append
(``toolsets.TOOLSETS["hermes-acp"]`` / ``["hermes-api-server"]``) und das
Patchen der ``description`` von Core-Tools (``search_files``, ``read_file``,
``patch``, ...) sind im Hermes-Review (PR #157345) abgelehnt und entfernt
worden.  Der Steering-Text laeuft jetzt ueber
``ctx.register_system_prompt_section("code-intel", text)``.

Die Tests mocken ``sys.modules`` fuer ``tools``/``code_intel.code_tools``,
damit ``import tools.registry`` und ``from . import code_tools`` innerhalb von
register() auf kontrollierte Mocks treffen.
"""

import logging
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx():
    ctx = MagicMock()
    ctx.register_skill = MagicMock()
    ctx.register_command = MagicMock()
    ctx.register_hook = MagicMock()
    ctx.register_system_prompt_section = MagicMock()
    return ctx


def _make_tools_pkg():
    """Create a mock 'tools' package hierarchy for sys.modules injection.

    Returns (mock_tools_mod, mock_registry_obj, mock_dt_mod, mock_entry).

    The mocks exist as tripwires: register() darf sie nicht mehr anfassen.
    ``mock_tools_mod.__path__`` wird gesetzt, damit Python das Modul als Paket
    behandelt (noetig, falls doch wieder ein Submodul importiert wird).
    """
    # ---- tools.registry sub-module ----
    mock_reg_mod = MagicMock()
    mock_reg_mod.__name__ = "tools.registry"
    mock_entry = MagicMock()
    mock_entry.schema = {"description": ""}
    mock_registry_obj = MagicMock()
    mock_registry_obj.get_entry.return_value = mock_entry
    mock_reg_mod.registry = mock_registry_obj

    # ---- tools.delegate_tool sub-module ----
    orig_prompt_mock = MagicMock(return_value="base prompt text")
    orig_agent_mock = MagicMock(return_value="agent_result")

    mock_dt_mod = MagicMock()
    mock_dt_mod.__name__ = "tools.delegate_tool"
    mock_dt_mod.DEFAULT_TOOLSETS = ["default_ts"]
    mock_dt_mod._SUBAGENT_TOOLSETS = ["terminal", "file"]
    mock_dt_mod._TOOLSET_LIST_STR = "'terminal', 'file'"
    mock_dt_mod._build_child_system_prompt = orig_prompt_mock
    mock_dt_mod._build_child_agent = orig_agent_mock
    mock_dt_mod._EXCLUDED_TOOLSET_NAMES = []
    mock_dt_mod.DELEGATE_BLOCKED_TOOLS = []

    # ---- tools package ----
    mock_tools_mod = MagicMock(spec=["__path__", "__name__"])
    mock_tools_mod.__path__ = ["/mock/tools"]
    mock_tools_mod.__name__ = "tools"
    mock_tools_mod.registry = mock_reg_mod
    mock_tools_mod.delegate_tool = mock_dt_mod

    return mock_tools_mod, mock_registry_obj, mock_dt_mod, mock_entry


def _make_ci_mod(load_cache_return=0):
    """Mock for the code_intel.code_tools sub-module."""
    mock_ci = MagicMock()
    mock_ci.__name__ = "code_intel.code_tools"
    mock_ci.load_symbol_cache.return_value = load_cache_return
    return mock_ci


def _make_lsp_mod(raises=False):
    """Mock for the code_intel.lsp_bridge sub-module."""
    mock_lsp = MagicMock()
    mock_lsp.__name__ = "code_intel.lsp_bridge"
    if raises:
        mock_lsp.register_lsp_tools.side_effect = Exception("LSP mocked failure")
    return mock_lsp


def _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod):
    """Build the dict for ``patch.dict('sys.modules', ...)``.

    Kein ``toolsets``-Eintrag mehr: das Plugin importiert das Host-Modul nicht
    und darf es auch nicht schreiben.
    """
    return {
        "tools": mock_tools_mod,
        "tools.registry": mock_tools_mod.registry,
        "tools.delegate_tool": mock_tools_mod.delegate_tool,
        "code_intel.code_tools": mock_ci_mod,
        "code_intel.lsp_bridge": mock_lsp_mod,
    }


def _register(ctx, mock_tools_mod, mock_ci_mod, mock_lsp_mod, quiet=False):
    """Call register(ctx) with the given sys.modules mocks in place."""
    with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
        with patch("pathlib.Path.exists", return_value=False):
            from code_intel.__init__ import register

            if quiet:
                logging.disable(logging.CRITICAL)
            try:
                register(ctx)
            finally:
                if quiet:
                    logging.disable(logging.NOTSET)


# ============================================================================
# Test: LSP-registration catch
# ============================================================================


class TestLspRegistrationFailure:
    """except Exception when register_lsp_tools() fails.

    Verified via caplog to capture the warning issued by the except block.
    """

    def _register(self, lsp_raises):
        mock_tools_mod, _, _, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod(raises=lsp_raises)

        _register(_make_ctx(), mock_tools_mod, mock_ci_mod, mock_lsp_mod)
        return mock_lsp_mod

    def test_lsp_failure_logs_warning(self, caplog):
        """When register_lsp_tools raises, a warning is logged (not an error)."""
        caplog.set_level(logging.WARNING, logger="agentiker_code_intel")
        self._register(lsp_raises=True)
        assert any("LSP tool registration failed" in rec.message for rec in caplog.records), (
            "Expected warning about LSP failure"
        )

    def test_lsp_success_no_warning(self, caplog):
        """When register_lsp_tools succeeds, no warning is logged."""
        caplog.set_level(logging.WARNING, logger="agentiker_code_intel")
        self._register(lsp_raises=False)
        warnings = [r for r in caplog.records if "LSP tool registration" in r.message]
        assert len(warnings) == 0


# ============================================================================
# Test: Symbol-cache restore logging
# ============================================================================


class TestSymbolCacheRestore:
    """if loaded: after code_tools.load_symbol_cache() returns > 0."""

    def _register(self, load_return):
        from code_intel import code_tools as ct_mod

        mock_tools_mod, _, _, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(load_cache_return=load_return)
        mock_lsp_mod = _make_lsp_mod()

        ctx = _make_ctx()
        # Clear cached tools entries (not code_intel — needs to stay importable)
        for name in list(sys.modules.keys()):
            if name in ("tools", "tools.registry", "tools.delegate_tool"):
                del sys.modules[name]
        with patch.object(ct_mod, "load_symbol_cache", return_value=load_return):
            _register(ctx, mock_tools_mod, mock_ci_mod, mock_lsp_mod)
        return mock_ci_mod

    def test_restore_zero_no_log(self, caplog):
        """When load_symbol_cache returns 0, no info log is emitted."""
        caplog.set_level(logging.INFO, logger="agentiker_code_intel")
        self._register(load_return=0)
        infos = [r for r in caplog.records if "Restored" in r.message]
        assert len(infos) == 0

    def test_restore_positive_logs_count(self, caplog):
        """When load_symbol_cache returns > 0, the count is logged."""
        caplog.set_level(logging.INFO, logger="agentiker_code_intel")
        self._register(load_return=42)
        matching = [r for r in caplog.records if "Restored" in r.message]
        assert len(matching) >= 1
        assert "42" in matching[0].message


# ============================================================================
# Test: keine Core-Schema-Mutation (Ersatz fuer das entfernte Schema-Patching)
# ============================================================================


class TestNoCoreSchemaMutation:
    """Ersatz fuer ``TestRegistrySchemaPatching``.

    Frueher hat ``_inject_steering_hints()`` die ``description`` von Core-Tools
    (``search_files``, ``read_file``, ``patch``, ``code_definition`` …) im
    Host-Registry umgeschrieben.  Das ist eine Mutation fremder Schemas und im
    Hermes-Review (PR #157345) entfernt worden.  Der Test haelt ein echtes,
    beschreibbares Registry-Objekt unter ``sys.modules`` und prueft, dass
    register() es weder liest noch schreibt.
    """

    CORE_TOOLS = [
        "search_files",
        "read_file",
        "patch",
        "code_definition",
        "code_references",
        "code_symbols",
    ]

    def _make_spy_tools(self):
        entries = {
            name: types.SimpleNamespace(schema={"description": f"original {name}"})
            for name in self.CORE_TOOLS
        }
        spy_registry = MagicMock()
        spy_registry.get_entry.side_effect = lambda name: entries.get(name)

        reg_mod = types.ModuleType("tools.registry")
        reg_mod.registry = spy_registry
        tools_mod = types.ModuleType("tools")
        tools_mod.__path__ = ["/mock/tools"]
        tools_mod.registry = reg_mod
        return entries, spy_registry, tools_mod, reg_mod

    def test_core_tool_schemas_untouched(self):
        entries, spy_registry, tools_mod, reg_mod = self._make_spy_tools()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        sys_modules = {
            "tools": tools_mod,
            "tools.registry": reg_mod,
            "code_intel.code_tools": mock_ci_mod,
            "code_intel.lsp_bridge": mock_lsp_mod,
        }
        with patch.dict("sys.modules", sys_modules):
            with patch("pathlib.Path.exists", return_value=False):
                logging.disable(logging.CRITICAL)
                try:
                    from code_intel.__init__ import register

                    register(ctx)
                finally:
                    logging.disable(logging.NOTSET)

        # 1) Kein Lookup im Core-Registry
        spy_registry.get_entry.assert_not_called()
        # 2) Keine description veraendert
        for name, entry in entries.items():
            assert entry.schema["description"] == f"original {name}", (
                f"Core-Schema von {name!r} wurde mutiert"
            )
        # 3) Steering geht ueber die oeffentliche API, nicht ueber Schema-Patches
        section_calls = ctx.register_system_prompt_section.call_args_list
        assert len(section_calls) == 1
        assert section_calls[0][0][0] == "code-intel"


# ============================================================================
# Test: Delegate-task monkeypatching
# ============================================================================


class TestSubagentSteeringViaHook:
    """Seit v0.6.27 laeuft das Subagent-Steering ueber den oeffentlichen Hook
    ``pre_llm_call`` statt ueber einen Patch von
    ``tools.delegate_tool._build_child_system_prompt``.

    Ein Subagent-Turn ist an ``parent_session_id`` erkennbar — genau das Feld,
    das Hermes beim Hook-Aufruf mitgibt (agent/turn_context.py).
    """

    def test_child_turn_receives_steering(self):
        from code_intel.hooks import on_pre_llm_call

        out = on_pre_llm_call(parent_session_id="parent-1", messages=[])
        assert out and "Code Intelligence" in out

    def test_parent_turn_receives_no_steering(self):
        from code_intel.hooks import on_pre_llm_call

        assert on_pre_llm_call(parent_session_id="", messages=[]) is None

    def test_steering_names_published_tool_surface(self, monkeypatch):
        """Der Text muss die Tools nennen, die im aktiven Modus existieren."""
        from code_intel import hooks

        monkeypatch.delenv("CODE_INTEL_NAMESPACES", raising=False)
        namespaced = hooks.on_pre_llm_call(parent_session_id="p", messages=[])
        assert "op=symbols" in namespaced
        assert "code_symbols(path)" not in namespaced

        monkeypatch.setenv("CODE_INTEL_NAMESPACES", "0")
        legacy = hooks.on_pre_llm_call(parent_session_id="p", messages=[])
        assert "code_symbols(path)" in legacy

    def test_steering_is_stable_across_turns(self):
        from code_intel.hooks import on_pre_llm_call

        first = on_pre_llm_call(parent_session_id="p", messages=[])
        second = on_pre_llm_call(parent_session_id="p", messages=[])
        assert first == second
        assert first.count("## Code Intelligence") == 1

    def test_steering_never_breaks_a_turn(self, monkeypatch):
        from code_intel import hooks

        monkeypatch.setattr(hooks, "_child_steering_text", lambda kwargs: hooks._STEERING_LEGACY)
        assert "Code Intelligence" in hooks.on_pre_llm_call(parent_session_id="p", messages=[])


class TestNoCoreRebinds:
    """Die Core-Rebinds aus der Analyse sind entfernt bzw. ersetzt.

    Referenz: references/core-override-analysis.md (VERDICTs a-e).
    Der Source-Check haelt sie entfernt — ein Wiedereinbau waere ein stiller
    Rueckfall in das alte Verhalten.
    """

    @staticmethod
    def _source() -> str:
        """Nur Code-Zeilen — Kommentare duerfen die entfernten Namen erklaeren."""
        from pathlib import Path

        import code_intel

        raw = Path(code_intel.__file__).read_text(encoding="utf-8")
        return "\n".join(
            line for line in raw.splitlines() if not line.lstrip().startswith("#")
        )

    def test_no_static_toolset_write(self):
        assert 'TOOLSETS["agentiker_code_intel"]' not in self._source(), "(a) ist zurueck"

    def test_no_core_tools_append(self):
        assert "_HERMES_CORE_TOOLS.append" not in self._source(), "(b) ist zurueck"

    def test_no_default_toolsets_append(self):
        assert "DEFAULT_TOOLSETS.append" not in self._source(), "(d) ist zurueck"

    def test_no_delegate_task_patch(self):
        src = self._source()
        assert "_patch_delegate_task" not in src, "(e) ist zurueck"
        assert "import tools.delegate_tool as dt" not in src

    def test_no_preset_append(self):
        """(c) ist entfallen: der Preset-Append wurde im Review abgelehnt.

        Frueher hat das Plugin ``toolsets.TOOLSETS["hermes-acp"]`` und
        ``["hermes-api-server"]`` direkt befuellt.  Der Quelltext darf weder
        die Preset-Namen noch das Host-Modul ``TOOLSETS`` nennen.
        """
        src = self._source()
        assert "hermes-acp" not in src, "(c) ist zurueck"
        assert "hermes-api-server" not in src, "(c) ist zurueck"
        assert "TOOLSETS" not in src, "(c) ist zurueck"
        assert "import toolsets" not in src, "(c) ist zurueck"
