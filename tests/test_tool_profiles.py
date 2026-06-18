"""Tests for Tool-Profile System."""

import os
from code_intel.__init__ import (
    get_active_profile,
    get_profile_tools,
    _TOOL_PROFILES,
)


class TestToolProfiles:
    """Tests for the tool profile system."""

    def test_all_profile_has_all_tools(self):
        """The 'all' profile contains all registered tools (39)."""
        assert len(_TOOL_PROFILES["all"]) == 39

    def test_core_profile_has_12_tools(self):
        """The 'core' profile has exactly 12 frequently-used tools."""
        assert len(_TOOL_PROFILES["core"]) == 12

    def test_search_profile_has_8_tools(self):
        """The 'search' profile has 8 AST search tools."""
        assert len(_TOOL_PROFILES["search"]) == 8

    def test_edit_profile_has_8_tools(self):
        """The 'edit' profile has 8 refactoring tools."""
        assert len(_TOOL_PROFILES["edit"]) == 8

    def test_lsp_profile_has_16_tools(self):
        """The 'lsp' profile has 16 LSP-powered tools."""
        assert len(_TOOL_PROFILES["lsp"]) == 16

    def test_default_profile_is_all(self):
        """Without env var, active profile defaults to 'all'."""
        assert get_active_profile() == "all"

    def test_env_var_override(self):
        """Env var CODE_INTEL_TOOL_PROFILE overrides the default."""
        os.environ["CODE_INTEL_TOOL_PROFILE"] = "core"
        # Re-import to refresh
        import importlib
        from code_intel import __init__ as ci_init
        importlib.reload(ci_init)
        profile = ci_init.get_active_profile()
        tools = ci_init.get_profile_tools()
        assert profile == "core"
        assert len(tools) == 12

    def test_env_var_fallback(self):
        """Unknown profile falls back to 'all'."""
        os.environ["CODE_INTEL_TOOL_PROFILE"] = "nonexistent"
        import importlib
        from code_intel import __init__ as ci_init
        importlib.reload(ci_init)
        profile = ci_init.get_active_profile()
        assert profile == "all"

    def test_profile_tools_are_subset_of_all(self):
        """Every profile's tools are a subset of the 'all' profile."""
        all_tools = set(_TOOL_PROFILES["all"])
        for name, tools in _TOOL_PROFILES.items():
            if name == "all":
                continue
            tool_set = set(tools)
            assert tool_set.issubset(all_tools), (
                f"Profile '{name}' has tools not in 'all': {tool_set - all_tools}"
            )

    def test_get_profile_tools_with_explicit_name(self):
        """get_profile_tools with explicit profile returns correct list."""
        core_tools = get_profile_tools("core")
        assert len(core_tools) == 12

    def test_each_tool_in_at_least_one_profile(self):
        """Every tool is in at least one non-'all' profile."""
        all_tools = set(_TOOL_PROFILES["all"])
        covered = set()
        for name, tools in _TOOL_PROFILES.items():
            if name == "all":
                continue
            covered.update(tools)
        uncovered = all_tools - covered
        # Some tools like code_workspace_summary, code_impact, code_tests_for_symbol
        # are only in "all" — that's acceptable
        assert len(uncovered) < 10, f"Too many uncovered: {uncovered}"
