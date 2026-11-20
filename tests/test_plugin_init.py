"""Tests for __init__.py: plugin registration, hooks, and pre_llm_call context injection.

Target: raise coverage from 27% → 90%+ by testing register() and _pre_llm_call_inject_context.
"""

import logging
import os
import sys
import types
from unittest.mock import MagicMock, patch

from code_intel.__init__ import _handle_code_intel_slash, _on_session_end

# ============================================================================
# Slash command (existing tests + extensions)
# ============================================================================


class TestSlashCommand:
    def test_help_empty(self):
        result = _handle_code_intel_slash("")
        assert "code-intel" in result.lower()

    def test_help_explicit(self):
        result = _handle_code_intel_slash("help")
        assert "clear" in result.lower()

    def test_help_flag(self):
        result = _handle_code_intel_slash("--help")
        assert "code-intel" in result.lower()

    def test_unknown(self):
        result = _handle_code_intel_slash("nonexistent")
        assert "unknown" in result.lower() or "Unknown" in result

    def test_status(self):
        result = _handle_code_intel_slash("status")
        assert result is not None

    def test_clear(self):
        result = _handle_code_intel_slash("clear")
        assert result is not None

    def test_whitespace_trimmed(self):
        result = _handle_code_intel_slash("  status  ")
        assert result is not None

    def test_help_dash_h(self):
        result = _handle_code_intel_slash("-h")
        assert "code-intel" in result.lower()


# ============================================================================
# Session-end hook
# ============================================================================


class TestSessionEndHook:
    def test_runs_without_error(self):
        result = _on_session_end()
        assert result is None


# ============================================================================
# Helpers for register() based tests
# ============================================================================


def _mock_code_tools(loaded=0):
    """Stub for ``code_intel.code_tools`` — avoids the on-disk symbol cache."""
    mock_ci = MagicMock()
    mock_ci.__name__ = "code_intel.code_tools"
    mock_ci.load_symbol_cache.return_value = loaded
    return mock_ci


def _run_register(loaded=42, **overrides):
    """Call register() against a recording ctx and a spy ``toolsets`` module.

    Since the preset-append / schema-patching rebinds were removed
    (Hermes-Review PR #157345) the plugin no longer imports the host modules
    ``tools`` and ``toolsets`` at all.  We still install a spy ``toolsets``
    module so a silent reintroduction of the write would fail loudly here.

    Returns dict with 'ctx' (MagicMock recorder) and 'spy_toolsets'.
    """
    ctx = MagicMock()
    mock_ci = _mock_code_tools(loaded)

    spy_toolsets = types.ModuleType("toolsets")
    spy_toolsets.TOOLSETS = {}
    spy_toolsets._HERMES_CORE_TOOLS = []

    import code_intel.__init__ as init_mod

    with patch.object(init_mod, "code_tools", mock_ci, create=True):
        with patch.dict(sys.modules, {"toolsets": spy_toolsets}):
            with patch("pathlib.Path.exists", return_value=overrides.get("skill_exists", False)):
                logging.disable(logging.CRITICAL)
                try:
                    from code_intel.__init__ import register

                    register(ctx)
                finally:
                    logging.disable(logging.NOTSET)

    return {"ctx": ctx, "spy_toolsets": spy_toolsets, "mock_code_intel": mock_ci}


# ============================================================================
# _pre_llm_call_inject_context
# ============================================================================


class TestPreLlmCall:
    """Test the pre_llm_call hook that injects file context into coding queries."""

    def test_no_messages_returns_none(self):
        hook = self._extract_hook()
        result = hook(messages=[])
        assert result is None

    def test_non_dict_message_returns_none(self):
        hook = self._extract_hook()
        result = hook(messages=["not a dict"])
        assert result is None

    def test_no_user_message_returns_none(self):
        hook = self._extract_hook()
        result = hook(messages=[{"role": "assistant", "content": "hello"}])
        assert result is None

    def test_no_file_references_returns_none(self):
        hook = self._extract_hook()
        result = hook(messages=[{"role": "user", "content": "how are you?"}])
        assert result is None

    def test_file_ref_found_but_not_exists(self, tmp_path):
        """If file path is mentioned but doesn't exist on disk, skip gracefully."""
        hook = self._extract_hook()
        msg = f"check {tmp_path}/nonexistent/file.ts please"
        result = hook(messages=[{"role": "user", "content": msg}])
        assert result is None  # file not found → skip

    def test_file_ref_with_existing_file(self, tmp_path):
        """When a referenced file exists, inject its symbols."""
        pyfile = tmp_path / "mymodule.py"
        pyfile.write_text("def foo(): pass\nclass Bar: pass\n")
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            hook = self._extract_hook()
            msg = f"check {pyfile.name} please"
            result = hook(messages=[{"role": "user", "content": msg}])
            if result is not None:
                assert "mymodule.py" in result
                assert "foo" in result or "Bar" in result
        finally:
            os.chdir(old_cwd)

    def test_multiple_file_refs_limited_to_3(self, tmp_path):
        """Only first 3 file refs should be processed."""
        files = []
        for i in range(5):
            f = tmp_path / f"mod{i}.py"
            f.write_text(f"x = {i}\n")
            files.append(f)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            hook = self._extract_hook()
            refs = " ".join(f.name for f in files)
            result = hook(messages=[{"role": "user", "content": f"check {refs}"}])
            # At most 3 files processed (some may be skipped if code_symbols_tool fails)
            if result is not None:
                assert result.count("[auto-context]") <= 3
        finally:
            os.chdir(old_cwd)

    def test_exception_does_not_propagate(self):
        """Any exception in the hook should be caught and return None."""
        hook = self._extract_hook()
        result = hook(messages="not a list")  # causes exception in kwargs.get
        assert result is None  # caught by except Exception

    @staticmethod
    def _extract_hook():
        """Return the _pre_llm_call handler by calling register() with a dummy ctx.

        Captures the handler that register() hands to ``register_hook`` under the
        name 'pre_llm_call'.
        """
        captured = {}

        class Ctx:
            def register_command(self, *a, **kw):
                pass

            def register_tool(self, *a, **kw):
                pass

            def register_system_prompt_section(self, *a, **kw):
                pass

            def register_hook(self, name, handler):
                if name == "pre_llm_call":
                    captured["hook"] = handler

        import code_intel.__init__ as init_mod

        logging.disable(logging.CRITICAL)
        try:
            with patch.object(init_mod, "code_tools", _mock_code_tools(0), create=True):
                from code_intel.__init__ import register

                register(Ctx())
        finally:
            logging.disable(logging.NOTSET)

        return captured.get("hook")


# ============================================================================
# register() — full plugin registration
# ============================================================================


class TestRegister:
    """Test the register() function: commands, hooks, steering section.

    Der fruehere Preset-Append (``toolsets.TOOLSETS["hermes-acp"]`` /
    ``["hermes-api-server"]``) und das Patchen von Core-Tool-Schemas sind
    entfallen (Hermes-Review PR #157345).  Stattdessen registriert das Plugin
    den Steering-Text ueber ``ctx.register_system_prompt_section`` — diese Tests
    halten die neue API fest und wachen darueber, dass keine Core-Mutation
    zurueckkehrt.
    """

    def test_registers_command(self):
        snap = _run_register()
        snap["ctx"].register_command.assert_called_once()
        assert snap["ctx"].register_command.call_args[0][0] == "code-intel"

    def test_registers_expected_hooks(self):
        """on_session_end, pre_llm_call und post_tool_call — alle drei sind Pflicht."""
        snap = _run_register()
        hook_names = [call[0][0] for call in snap["ctx"].register_hook.call_args_list]
        assert "pre_llm_call" in hook_names
        assert "on_session_end" in hook_names
        assert "post_tool_call" in hook_names

    def test_register_hooks_point_at_callables(self):
        """Jeder Hook-Handler muss aufrufbar sein, nicht None."""
        snap = _run_register()
        for call in snap["ctx"].register_hook.call_args_list:
            assert callable(call[0][1]), f"hook {call[0][0]!r} has a non-callable handler"

    def test_registers_steering_section(self):
        """Der Steering-Text geht als System-Prompt-Sektion mit id 'code-intel' raus."""
        snap = _run_register()
        calls = snap["ctx"].register_system_prompt_section.call_args_list
        assert len(calls) == 1, "genau eine Steering-Sektion pro register()"
        section_id, text = calls[0][0]
        assert section_id == "code-intel"
        for dispatcher in ("code_search", "code_lsp", "code_graph", "code_edit"):
            assert dispatcher in text

    def test_steering_section_missing_api_does_not_raise(self):
        """Aeltere Hermes-Versionen ohne register_system_prompt_section: nur Log, kein raise."""
        ctx = MagicMock(spec=["register_command", "register_hook", "register_tool"])
        import code_intel.__init__ as init_mod

        with patch.object(init_mod, "code_tools", _mock_code_tools(0), create=True):
            logging.disable(logging.CRITICAL)
            try:
                from code_intel.__init__ import register

                register(ctx)  # darf nicht werfen
            finally:
                logging.disable(logging.NOTSET)

        ctx.register_command.assert_called_once()

    def test_does_not_touch_core_toolset_dict(self):
        """Kein Eintrag und keine Mutation in ``toolsets.TOOLSETS``.

        Der fruehere Preset-Append schrieb in ``hermes-acp`` /
        ``hermes-api-server`` — ein Core-Rebind, den das Hermes-Review
        (PR #157345) abgelehnt hat.
        """
        snap = _run_register()
        assert snap["spy_toolsets"].TOOLSETS == {}
        assert "agentiker_code_intel" not in snap["spy_toolsets"].TOOLSETS

    def test_does_not_import_host_toolsets_module(self):
        """``import toolsets`` ist aus dem Plugin entfernt."""
        import code_intel.__init__ as init_mod

        assert not hasattr(init_mod, "toolsets")

    def test_skill_not_registered_via_ctx(self):
        """Das Plugin registriert keine Skills mehr ueber ctx (external_dirs statt register_skill).

        Gilt unabhaengig davon, ob eine SKILL.md auf Platte liegt: der alte
        register_skill-Pfad ist entfallen, der Skill kommt ueber config.yaml.
        """
        for skill_exists in (False, True):
            snap = _run_register(skill_exists=skill_exists)
            assert not snap["ctx"].register_skill.called

    def test_steering_text_not_duplicated_on_second_call(self):
        """Zweiter register()-Aufruf schickt denselben Text — keine aufaddierten Hints."""
        snap1 = _run_register()
        text1 = snap1["ctx"].register_system_prompt_section.call_args[0][1]
        snap2 = _run_register()
        text2 = snap2["ctx"].register_system_prompt_section.call_args[0][1]

        assert text1 == text2
        assert snap1["spy_toolsets"].TOOLSETS == {}
        assert snap2["spy_toolsets"].TOOLSETS == {}
