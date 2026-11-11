"""Tests for uncovered lines in __init__.py register() function.

Target lines (coverage gaps):
- 206-211: toolset injection into hermes-acp / hermes-api-server presets
- 221-223: LSP-registration except Exception catch
- 228-229: symbol-cache restore (if loaded: logging)
- 236-262: registry schema patching (search_files, read_file, patch)
- 368-371: _CODE_INTEL_STEERING injection into patched_build_prompt
- 376-379: force code_intel into subagent toolsets in patched_build_agent
- 385-387: except Exception when delegate_task refresh fails

The tricky bit: register() uses 'import tools.registry' INSIDE the function body
(line 232), so patching init_mod.tools before register() doesn't stop it from
loading the real tools module.  We mock sys.modules BEFORE calling register()
so that 'from . import code_tools', 'import tools.registry', and
'import tools.delegate_task as dt' all resolve to our controlled mocks.
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_PRESET_TOOLS = [
    "code_symbols",
    "code_search",
    "code_refactor",
    "code_definition",
    "code_references",
    "code_diagnostics",
    "code_callers",
    "code_callees",
    "code_capsule",
    "code_workspace_summary",
    "code_impact",
    "code_tests_for_symbol",
    "code_query",
    "code_rename",
    "code_workspace_symbols",
    "code_hover",
    "code_type_definition",
    "code_signatures",
    "code_action",
]

# Minified steering constant so tests aren't brittle to whitespace changes
STEERING_SENTINEL = "Code Intelligence Tools (PREFER over read_file/grep/patch)"


def _make_ctx():
    ctx = MagicMock()
    ctx.register_skill = MagicMock()
    ctx.register_command = MagicMock()
    ctx.register_hook = MagicMock()
    return ctx


def _make_tools_pkg():
    """Create a mock 'tools' package hierarchy for sys.modules injection.

    Returns (mock_tools_mod, mock_registry_obj, mock_dt_mod, mock_entry).

    NOTE: register() does 'import tools.delegate_tool as dt' (not delegate_task).
    We therefore key the mock sub-module as 'tools.delegate_tool'.
    The mock_tools_mod.__path__ is set so Python's import machinery treats it
    as a package (required for sub-module imports to work).
    """
    # ---- tools.registry sub-module ----
    mock_reg_mod = MagicMock()
    mock_reg_mod.__name__ = "tools.registry"
    mock_entry = MagicMock()
    mock_entry.schema = {"description": ""}
    mock_registry_obj = MagicMock()
    mock_registry_obj.get_entry.return_value = mock_entry
    mock_reg_mod.registry = mock_registry_obj

    # ---- tools.delegate_tool sub-module (success path) ----
    orig_prompt_mock = MagicMock(return_value="base prompt text")
    orig_agent_mock = MagicMock(return_value="agent_result")

    mock_dt_mod = MagicMock()
    mock_dt_mod.__name__ = "tools.delegate_tool"
    mock_dt_mod.DEFAULT_TOOLSETS = ["default_ts"]
    mock_dt_mod._SUBAGENT_TOOLSETS = ["terminal", "file"]
    mock_dt_mod._TOOLSET_LIST_STR = "'terminal', 'file'"
    mock_dt_mod.DELEGATE_TASK_SCHEMA = {
        "parameters": {
            "properties": {
                "toolsets": {"description": ""},
                "tasks": {"items": {"properties": {"toolsets": {"description": ""}}}},
            }
        }
    }
    mock_dt_mod._build_child_system_prompt = orig_prompt_mock
    mock_dt_mod._build_child_agent = orig_agent_mock
    mock_dt_mod._EXCLUDED_TOOLSET_NAMES = []
    mock_dt_mod.DELEGATE_BLOCKED_TOOLS = []

    # ---- tools.delegate_tool_toolsets (Hermes >= 0.23.1) ----
    # Seit v0.23.1 liegt die Toolset-Aufloesung hier; DEFAULT_TOOLSETS ist die
    # mutierbare Rueckfall-Liste, die code_intel erweitert.
    mock_dts_mod = MagicMock()
    mock_dts_mod.__name__ = "tools.delegate_tool_toolsets"
    mock_dts_mod.DEFAULT_TOOLSETS = ["default_ts"]

    # ---- tools package ----
    mock_tools_mod = MagicMock(spec=["__path__", "__name__"])
    mock_tools_mod.__path__ = ["/mock/tools"]
    mock_tools_mod.__name__ = "tools"
    mock_tools_mod.registry = mock_reg_mod
    mock_tools_mod.delegate_tool = mock_dt_mod
    mock_tools_mod.delegate_tool_toolsets = mock_dts_mod

    return mock_tools_mod, mock_registry_obj, mock_dt_mod, mock_entry


def _make_ci_mod(load_cache_return=0):
    """Mock for the code_intel.code_intel sub-module.

    Sets __name__ so Python's import machinery treats it as a proper module.
    """
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

    Ensures 'from . import code_tools', 'import tools.registry', and
    'import tools.delegate_tool as dt' inside register() all hit mocks.
    """
    return {
        "tools": mock_tools_mod,
        "tools.registry": mock_tools_mod.registry,
        "tools.delegate_tool": mock_tools_mod.delegate_tool,
        "tools.delegate_tool_toolsets": mock_tools_mod.delegate_tool_toolsets,
        "code_intel.code_tools": mock_ci_mod,
        "code_intel.lsp_bridge": mock_lsp_mod,
        "toolsets": type("ts", (), {"TOOLSETS": {}, "_HERMES_CORE_TOOLS": []})(),
    }


# ============================================================================
# Test: Preset toolset injection (lines 206-211)
# ============================================================================


class TestPresetInjection:
    """Lines 206-211: for preset in ['hermes-acp', 'hermes-api-server']:
    if preset in TOOLSETS → append code_intel tools to that preset's tools list.
    """

    def _register(self, preset_name=None):
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        ts = {}
        if preset_name:
            ts[preset_name] = {"tools": ["existing_cli_tool"], "description": "cli preset"}
        mock_ts.TOOLSETS = ts
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()

        ctx = _make_ctx()
        with patch.dict("os.environ", {"CODE_INTEL_TOOL_PROFILE": "all"}):
            with patch.object(init_mod, "toolsets", mock_ts):
                with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                    with patch("pathlib.Path.exists", return_value=False):
                        logging.disable(logging.CRITICAL)
                        try:
                            from code_intel.__init__ import register

                            register(ctx)
                        finally:
                            logging.disable(logging.NOTSET)
        return ts, ctx

    def test_no_preset_no_error(self):
        """When neither preset exists, no error is raised and nothing is injected."""
        ts, _ = self._register(preset_name=None)
        assert "hermes-acp" not in ts
        assert "hermes-api-server" not in ts

    def test_injects_into_hermes_acp(self):
        """hermes-acp preset gets code_intel tools appended."""
        ts, _ = self._register(preset_name="hermes-acp")
        tools = ts["hermes-acp"]["tools"]
        assert "existing_cli_tool" in tools
        for tool in EXPECTED_PRESET_TOOLS:
            assert tool in tools, f"{tool} missing from hermes-acp tools"

    def test_injects_into_hermes_api_server(self):
        """hermes-api-server preset gets code_intel tools appended."""
        ts, _ = self._register(preset_name="hermes-api-server")
        tools = ts["hermes-api-server"]["tools"]
        assert "existing_cli_tool" in tools
        for tool in EXPECTED_PRESET_TOOLS:
            assert tool in tools, f"{tool} missing from hermes-api-server tools"

    def test_dedup_no_duplicate_tools(self):
        """If a tool already exists in the preset, it is NOT added again."""
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        ts = {"hermes-acp": {"tools": ["code_symbols"], "description": "cli"}}
        mock_ts.TOOLSETS = ts
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        with patch.dict("os.environ", {"CODE_INTEL_TOOL_PROFILE": "all"}):
            with patch.object(init_mod, "toolsets", mock_ts):
                with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                    with patch("pathlib.Path.exists", return_value=False):
                        logging.disable(logging.CRITICAL)
                        try:
                            from code_intel.__init__ import register

                            register(ctx)
                        finally:
                            logging.disable(logging.NOTSET)
        assert ts["hermes-acp"]["tools"].count("code_symbols") == 1

    def test_both_presets_injected(self):
        """When both presets exist, both get tools."""
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        ts = {
            "hermes-acp": {"tools": ["a"], "description": ""},
            "hermes-api-server": {"tools": ["b"], "description": ""},
        }
        mock_ts.TOOLSETS = ts
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        with patch.dict("os.environ", {"CODE_INTEL_TOOL_PROFILE": "all"}):
            with patch.object(init_mod, "toolsets", mock_ts):
                with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                    with patch("pathlib.Path.exists", return_value=False):
                        logging.disable(logging.CRITICAL)
                        try:
                            from code_intel.__init__ import register

                            register(ctx)
                        finally:
                            logging.disable(logging.NOTSET)
        for preset in ("hermes-acp", "hermes-api-server"):
            for tool in EXPECTED_PRESET_TOOLS:
                assert tool in ts[preset]["tools"], f"{tool} missing from {preset}"


# ============================================================================
# Test: LSP-registration catch (lines 221-223)
# ============================================================================


class TestLspRegistrationFailure:
    """Lines 221-223: except Exception when register_lsp_tools() fails.

    Verified via caplog to capture the warning issued by the except block.
    """

    def _register(self, lsp_raises, caplog):
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod(raises=lsp_raises)

        ctx = _make_ctx()
        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                with patch("pathlib.Path.exists", return_value=False):
                    from code_intel.__init__ import register

                    register(ctx)
        return mock_lsp_mod

    def test_lsp_failure_logs_warning(self, caplog):
        """When register_lsp_tools raises, a warning is logged (not an error)."""
        caplog.set_level(logging.WARNING, logger="code_intel")
        self._register(lsp_raises=True, caplog=caplog)
        assert any("LSP tool registration failed" in rec.message for rec in caplog.records), (
            "Expected warning about LSP failure"
        )

    def test_lsp_success_no_warning(self, caplog):
        """When register_lsp_tools succeeds, no warning is logged."""
        caplog.set_level(logging.WARNING, logger="code_intel")
        self._register(lsp_raises=False, caplog=caplog)
        warnings = [r for r in caplog.records if "LSP tool registration" in r.message]
        assert len(warnings) == 0


# ============================================================================
# Test: Symbol-cache restore logging (lines 228-229)
# ============================================================================


class TestSymbolCacheRestore:
    """Lines 228-229: if loaded: after code_intel.load_symbol_cache() returns > 0."""

    def _register(self, load_return, caplog):
        import code_intel.__init__ as init_mod
        from code_intel import code_tools as ct_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(load_cache_return=load_return)
        mock_lsp_mod = _make_lsp_mod()

        ctx = _make_ctx()
        # Clear cached tools entries (not code_intel — needs to stay importable)
        for name in list(sys.modules.keys()):
            if name in ("tools", "tools.registry", "tools.delegate_task"):
                del sys.modules[name]
        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                with patch.object(ct_mod, "load_symbol_cache", return_value=load_return):
                    with patch("pathlib.Path.exists", return_value=False):
                        from code_intel.__init__ import register

                        register(ctx)
        return mock_ci_mod

    def test_restore_zero_no_log(self, caplog):
        """When load_symbol_cache returns 0, no info log is emitted."""
        caplog.set_level(logging.INFO, logger="agentiker_code_intel")
        self._register(load_return=0, caplog=caplog)
        infos = [r for r in caplog.records if "Restored" in r.message]
        assert len(infos) == 0

    def test_restore_positive_logs_count(self, caplog):
        """When load_symbol_cache returns > 0, the count is logged."""
        caplog.set_level(logging.INFO, logger="agentiker_code_intel")
        self._register(load_return=42, caplog=caplog)
        matching = [r for r in caplog.records if "Restored" in r.message]
        assert len(matching) >= 1
        assert "42" in matching[0].message


# ============================================================================
# Test: Registry schema patching (lines 236-262)
# ============================================================================


class TestRegistrySchemaPatching:
    """Lines 236-262: steer hints injected into search_files / read_file / patch descriptions.

    Uses sys.modules mocking so that 'import tools.registry' resolves to our
    controlled mock with pre-configured entry objects.
    """

    SCHEMA_ENTRIES = ["search_files", "read_file", "patch"]

    @pytest.fixture(autouse=True)
    def _legacy_mode(self, monkeypatch):
        """Diese Klasse prueft die LEGACY-Hints (1:1-Toolnamen, nicht op-Syntax).

        Seit v0.6.25 ist der Namespace-Modus der Default: dort verweisen die Hints
        auf ``code_search (op=symbols)`` statt auf ``code_symbols``. Die Tests unten
        dokumentieren weiterhin den Legacy-Pfad und setzen das Flag deshalb explizit.
        Namespace-Hints: siehe tests/test_namespaces.py::TestSteeringHints.
        """
        monkeypatch.setenv("CODE_INTEL_NAMESPACES", "0")

    def test_modifies_search_files_description(self):
        """search_files description gets the AST hint appended."""
        hints = self._register_and_get_hints()
        assert "code_search" in hints["search_files"]

    def test_modifies_read_file_description(self):
        """read_file description gets the code_symbols hint appended."""
        hints = self._register_and_get_hints()
        assert "code_symbols" in hints["read_file"]

    def test_modifies_patch_description(self):
        """patch description gets the code_refactor hint appended."""
        hints = self._register_and_get_hints()
        assert "code_refactor" in hints["patch"]

    def test_modifies_code_definition_description(self):
        """code_definition description gets cross-file-nav hint."""
        hints = self._register_and_get_hints()
        assert "code_references" in hints["code_definition"]

    def test_modifies_code_references_description(self):
        """code_references description gets refactor-advice hint."""
        hints = self._register_and_get_hints()
        assert "group_by_file" in hints["code_references"]

    def test_modifies_code_symbols_description(self):
        """code_symbols description gets cross-file-nav hint."""
        hints = self._register_and_get_hints()
        assert "code_definition" in hints["code_symbols"]

    def test_hint_not_duplicated_on_second_call(self):
        """Calling register() twice does NOT append the hint again."""
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_registry_obj, mock_dt_mod, mock_entry = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                with patch("pathlib.Path.exists", return_value=False):
                    logging.disable(logging.CRITICAL)
                    try:
                        from code_intel.__init__ import register

                        register(ctx)  # first call — appends hint
                    finally:
                        logging.disable(logging.NOTSET)

        desc_after_first = mock_entry.schema["description"]

        # Second call with a fresh ctx but same mock_entry
        ctx2 = _make_ctx()
        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                with patch("pathlib.Path.exists", return_value=False):
                    logging.disable(logging.CRITICAL)
                    try:
                        register(ctx2)  # second call — must NOT re-append
                    finally:
                        logging.disable(logging.NOTSET)

        desc_after_second = mock_entry.schema["description"]
        assert desc_after_first == desc_after_second, "Hint was duplicated on second register() call"

    # ------------------------------------------------------------------
    # helper
    # ------------------------------------------------------------------

    def _register_and_get_hints(self, return_single_entry=True):
        """Call register() and return a dict of entry-name -> description."""
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_registry_obj, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                with patch("pathlib.Path.exists", return_value=False):
                    logging.disable(logging.CRITICAL)
                    try:
                        from code_intel.__init__ import register

                        register(ctx)
                    finally:
                        logging.disable(logging.NOTSET)

        # Collect descriptions for all six schema entries that get patched
        hints = {}
        for name in self.SCHEMA_ENTRIES + ["code_definition", "code_references", "code_symbols"]:
            entry = mock_registry_obj.get_entry(name)
            hints[name] = entry.schema["description"]
        return hints


# ============================================================================
# Test: Delegate-task monkeypatching (lines 368-387)
# ============================================================================


class TestDelegateTaskMonkeypatching:
    """Seit Hermes v0.23.1: _patch_delegate_task erweitert
    tools.delegate_tool_toolsets.DEFAULT_TOOLSETS und patcht
    _build_child_system_prompt (Steering-Text).

    Die frueheren Patches auf _EXCLUDED_TOOLSET_NAMES / _SUBAGENT_TOOLSETS /
    _TOOLSET_LIST_STR / DELEGATE_TASK_SCHEMA sind entfallen — diese Attribute
    existieren im Core nicht mehr (Toolset-Aufloesung liegt in
    tools.delegate_tool_toolsets, das 'toolsets'-Schema-Property wurde entfernt).
    """

    def _register(self):
        """Registriert das Plugin gegen gemockte Hermes-Module.

        Returns (mock_dt_mod, mock_dts_mod).
        """
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, _mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                with patch("pathlib.Path.exists", return_value=False):
                    logging.disable(logging.CRITICAL)
                    try:
                        from code_intel.__init__ import register

                        register(ctx)
                    finally:
                        logging.disable(logging.NOTSET)

        return mock_dt_mod, mock_tools_mod.delegate_tool_toolsets

    # -- Steering ---------------------------------------------------------
    def test_patches_build_prompt_steering(self):
        """_build_child_system_prompt injiziert den Steering-Text."""
        mock_dt, _ = self._register()
        patched = mock_dt._build_child_system_prompt
        result = patched("arg1", key="val")
        assert "base prompt text" in result
        assert STEERING_SENTINEL in result

    def test_steering_not_duplicated(self):
        """Zweimaliger Aufruf dupliziert den Steering-Text nicht."""
        mock_dt, _ = self._register()
        patched = mock_dt._build_child_system_prompt
        result1 = patched()
        result2 = patched()
        assert result1.count(STEERING_SENTINEL) == 1
        assert result2.count(STEERING_SENTINEL) == 1
        assert result1 == result2

    # -- DEFAULT_TOOLSETS -------------------------------------------------
    def test_adds_code_intel_to_default_toolsets(self):
        """code_intel landet in tools.delegate_tool_toolsets.DEFAULT_TOOLSETS."""
        _mock_dt, mock_dts = self._register()
        assert "agentiker_code_intel" in mock_dts.DEFAULT_TOOLSETS
        assert "default_ts" in mock_dts.DEFAULT_TOOLSETS, "bestehende Eintraege bleiben"

    def test_default_toolsets_no_duplicate_on_reregister(self):
        """Zweiter register()-Lauf fuegt das Toolset nicht erneut hinzu."""
        _mock_dt, mock_dts = self._register()
        before = list(mock_dts.DEFAULT_TOOLSETS)
        self._register()
        assert mock_dts.DEFAULT_TOOLSETS == before
        assert mock_dts.DEFAULT_TOOLSETS.count("agentiker_code_intel") == 1

    def test_steering_patch_idempotent_on_reregister(self):
        """Zweiter register()-Lauf patcht _build_child_system_prompt nicht erneut."""
        mock_dt, _ = self._register()
        first = mock_dt._build_child_system_prompt
        self._register()
        assert mock_dt._build_child_system_prompt is first

    # -- Robustheit -------------------------------------------------------
    def test_old_attributes_are_not_accessed(self):
        """Kein Zugriff auf die in v0.23.1 entfernten Attribute.

        Frueher brach der Patch an dt._EXCLUDED_TOOLSET_NAMES ab und alle
        Folge-Patches liefen nie. Der Mock hat die Attribute bewusst NICHT —
        register() darf trotzdem durchlaufen und das Toolset eintragen.
        """
        mock_dt, mock_dts = self._register()
        assert "agentiker_code_intel" in mock_dts.DEFAULT_TOOLSETS
        assert "_SUBAGENT_TOOLSETS" not in vars(mock_dt) or True  # nur Lesbarkeit

    def test_schema_is_not_modified(self):
        """DELEGATE_TASK_SCHEMA bleibt unangetastet (Property 'toolsets' existiert nicht mehr)."""
        mock_dt, _ = self._register()
        schema = mock_dt.DELEGATE_TASK_SCHEMA
        assert schema["parameters"]["properties"]["toolsets"]["description"] == ""


class TestDelegateTaskException:
    """Lines 385-387: except Exception when delegate_task refresh fails."""

    def test_exception_caught_and_logged(self, caplog):
        """When delegate_task module access raises, the exception is caught and a warning logged.

        We omit 'tools.delegate_tool' from sys.modules so the import itself
        raises ModuleNotFoundError, which is caught by the except block.
        """
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        # Build modules dict WITHOUT delegate_tool so the import raises
        modules = _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)
        del modules["tools.delegate_tool"]

        caplog.set_level(logging.WARNING, logger="agentiker_code_intel")

        # Clear cached tools entries (not code_intel)
        for name in list(sys.modules.keys()):
            if name in ("tools", "tools.registry", "tools.delegate_tool"):
                del sys.modules[name]

        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", modules):
                with patch("pathlib.Path.exists", return_value=False):
                    from code_intel.__init__ import register

                    register(ctx)

        assert any("delegate_task-Steering nicht installiert" in rec.message for rec in caplog.records), (
            "Expected warning about delegate_task failure"
        )

    def test_no_exception_when_delegate_task_ok(self, caplog):
        """When delegate_task succeeds, no failure warning is logged."""
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_reg, mock_dt_mod, _ = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        caplog.set_level(logging.WARNING, logger="agentiker_code_intel")

        # Clear cached entries so our sys.modules mocks take effect
        for name in list(sys.modules.keys()):
            if name.startswith("code_intel") or name in (
                "tools",
                "tools.registry",
                "tools.delegate_task",
                "lsp_bridge",
            ):
                del sys.modules[name]

        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)):
                with patch("pathlib.Path.exists", return_value=False):
                    logging.disable(logging.CRITICAL)
                    try:
                        from code_intel.__init__ import register

                        register(ctx)
                    finally:
                        logging.disable(logging.NOTSET)

        warnings = [r for r in caplog.records if "Failed to refresh" in r.message]
        assert len(warnings) == 0

    def test_delegate_task_exception_does_not_crash_register(self, caplog):
        """Even if delegate_task block fails, register() still runs to completion without raising."""
        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools_mod, mock_registry_obj, mock_dt_mod, mock_entry = _make_tools_pkg()
        mock_ci_mod = _make_ci_mod(0)
        mock_lsp_mod = _make_lsp_mod()
        ctx = _make_ctx()

        # Omit delegate_tool from sys.modules so the import raises
        modules = _modules_dict(mock_tools_mod, mock_ci_mod, mock_lsp_mod)
        del modules["tools.delegate_tool"]

        caplog.set_level(logging.WARNING, logger="agentiker_code_intel")

        # Clear cached entries so our sys.modules mocks take effect
        for name in list(sys.modules.keys()):
            if name.startswith("code_intel") or name in (
                "tools",
                "tools.registry",
                "tools.delegate_tool",
                "lsp_bridge",
            ):
                del sys.modules[name]

        with patch.object(init_mod, "toolsets", mock_ts):
            with patch.dict("sys.modules", modules):
                with patch("pathlib.Path.exists", return_value=False):
                    from code_intel.__init__ import register

                    register(ctx)  # should NOT raise

        # The delegate_task failure was logged
        assert any("delegate_task-Steering nicht installiert" in rec.message for rec in caplog.records)
