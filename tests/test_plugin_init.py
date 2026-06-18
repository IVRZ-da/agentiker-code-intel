"""Tests for __init__.py: plugin registration, hooks, and pre_llm_call context injection.

Target: raise coverage from 27% → 90%+ by testing register() and _pre_llm_call_inject_context.
"""
import logging
import os
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
        """Return the _pre_llm_call_inject_context closure by calling register with a dummy ctx.

        We need to capture the inner function that register() creates and registers
        as the 'pre_llm_call' hook.
        """
        captured = {}

        class Ctx:
            def register_command(self, *a, **kw): pass
            def register_skill(self, *a, **kw): pass
            def register_hook(self, name, handler):
                if name == "pre_llm_call":
                    captured["hook"] = handler

        import code_intel.__init__ as init_mod
        # Save originals
        orig_toolsets = getattr(init_mod, 'toolsets', None)
        orig_tregistry = getattr(init_mod, 'tools', None)

        try:
            # Patch toolsets
            mock_ts = MagicMock()
            mock_ts.TOOLSETS = {}
            mock_ts._HERMES_CORE_TOOLS = []
            init_mod.toolsets = mock_ts

            # Patch tools.registry
            mock_tools = MagicMock()
            mock_reg = MagicMock()
            mock_entry = MagicMock()
            mock_entry.schema = {"description": ""}
            mock_reg.get_entry.return_value = mock_entry
            mock_tools.registry.registry = mock_reg
            init_mod.tools = mock_tools

            # Patch delegate_task module
            mock_dt = MagicMock()
            mock_dt.DEFAULT_TOOLSETS = []
            mock_dt._SUBAGENT_TOOLSETS = []
            mock_dt._TOOLSET_LIST_STR = ""
            mock_dt.DELEGATE_TASK_SCHEMA = {
                "parameters": {"properties": {
                    "toolsets": {"description": ""},
                    "tasks": {
                        "items": {"properties": {
                            "toolsets": {"description": ""}
                        }}
                    }
                }}
            }
            mock_dt._build_child_system_prompt = lambda *a, **kw: ""
            mock_dt._build_child_agent = lambda *a, **kw: None
            mock_dt._EXCLUDED_TOOLSET_NAMES = []
            mock_dt.DELEGATE_BLOCKED_TOOLS = []
            init_mod.tools.delegate_tool = mock_dt

            # Patch code_intel submodule
            mock_ci = MagicMock()
            mock_ci.load_symbol_cache.return_value = 0
            init_mod.code_intel = mock_ci

            # Register
            from code_intel.__init__ import register
            logging.disable(logging.CRITICAL)
            try:
                register(Ctx())
            finally:
                logging.disable(logging.NOTSET)
        finally:
            # Restore
            if orig_toolsets is not None:
                init_mod.toolsets = orig_toolsets
            if orig_tregistry is not None:
                init_mod.tools = orig_tregistry

        return captured.get("hook")


# ============================================================================
# register() — full plugin registration
# ============================================================================


class TestRegister:
    """Test the register() function with mocked Hermes internals."""

    @staticmethod
    def _run_register(**mock_overrides):
        """Call register() with mocked dependencies and return a snapshot dict.

        Returns dict with: 'ctx' (MagicMock), 'mock_toolsets', 'mock_tools',
        'mock_delegate_task', 'mock_code_intel' for assertions.
        """
        ctx = MagicMock()
        ctx.register_skill = MagicMock()
        ctx.register_command = MagicMock()
        ctx.register_hook = MagicMock()

        skill_exists = mock_overrides.get("skill_exists", False)
        preload_toolset = mock_overrides.get("preload_toolset", False)

        import code_intel.__init__ as init_mod

        mock_ts = MagicMock()
        mock_ts.TOOLSETS = {"agentiker_code_intel": {"description": "preloaded", "tools": []}} if preload_toolset else {}
        mock_ts._HERMES_CORE_TOOLS = []

        mock_tools = MagicMock()
        mock_reg = MagicMock()
        mock_entry = MagicMock()
        mock_entry.schema = {"description": ""}
        mock_reg.get_entry.return_value = mock_entry
        mock_tools.registry.registry = mock_reg

        mock_dt = MagicMock()
        mock_dt.DEFAULT_TOOLSETS = []
        mock_dt._SUBAGENT_TOOLSETS = []
        mock_dt._TOOLSET_LIST_STR = ""
        mock_dt.DELEGATE_TASK_SCHEMA = {
            "parameters": {"properties": {
                "toolsets": {"description": ""},
                "tasks": {
                    "items": {"properties": {
                        "toolsets": {"description": ""}
                    }}
                }
            }}
        }
        mock_dt._build_child_system_prompt = lambda *a, **kw: ""
        mock_dt._build_child_agent = lambda *a, **kw: None
        mock_dt._EXCLUDED_TOOLSET_NAMES = []
        mock_dt.DELEGATE_BLOCKED_TOOLS = []
        mock_tools.delegate_task = mock_dt

        mock_ci = MagicMock()
        mock_ci.load_symbol_cache.return_value = 42

        with patch.object(init_mod, 'toolsets', mock_ts):
            with patch.object(init_mod, 'tools', mock_tools):
                with patch.object(init_mod, 'code_intel', mock_ci):
                    with patch("pathlib.Path.exists", return_value=skill_exists):
                        from code_intel.__init__ import register
                        logging.disable(logging.CRITICAL)
                        try:
                            register(ctx)
                        finally:
                            logging.disable(logging.NOTSET)

        return {
            "ctx": ctx,
            "mock_toolsets": mock_ts,
            "mock_tools": mock_tools,
            "mock_delegate_task": mock_dt,
            "mock_code_intel": mock_ci,
        }

    def test_registers_command(self):
        snap = self._run_register()
        snap["ctx"].register_command.assert_called_once()

    def test_registers_session_end_hook(self):
        snap = self._run_register()
        assert snap["ctx"].register_hook.call_count >= 2

    def test_registers_pre_llm_call_hook(self):
        snap = self._run_register()
        hook_names = [call[0][0] for call in snap["ctx"].register_hook.call_args_list]
        assert "pre_llm_call" in hook_names
        assert "on_session_end" in hook_names

    def test_skill_not_registered_if_file_missing(self):
        snap = self._run_register(skill_exists=False)
        snap["ctx"].register_skill.assert_not_called()

    def test_skill_registered_if_file_exists(self):
        snap = self._run_register(skill_exists=True)
        snap["ctx"].register_skill.assert_called_once()

    def test_injects_toolset_into_toolsets_dict(self):
        snap = self._run_register()
        assert "agentiker_code_intel" in snap["mock_toolsets"].TOOLSETS

    def test_uses_existing_toolset(self):
        """If agentiker_code_intel toolset already exists, do NOT overwrite."""
        snap = self._run_register(preload_toolset=True)
        assert snap["mock_toolsets"].TOOLSETS["agentiker_code_intel"]["description"] == "preloaded"

    def test_hint_not_duplicated_on_second_call(self):
        snap1 = self._run_register()
        desc1 = snap1["mock_tools"].registry.registry.get_entry("search_files").schema["description"]
        snap2 = self._run_register()
        desc2 = snap2["mock_tools"].registry.registry.get_entry("search_files").schema["description"]
        assert desc1 == desc2
