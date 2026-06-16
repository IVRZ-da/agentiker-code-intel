"""Tests for __init__.py: slash commands, hooks, and plugin registration helpers."""
from code_intel.__init__ import _handle_code_intel_slash


class TestCodeIntelSlashCommand:
    """Test the /code-intel slash command handler."""

    def test_help_message(self):
        result = _handle_code_intel_slash("")
        assert "code-intel" in result.lower() or "help" in result.lower()

    def test_help_explicit(self):
        result = _handle_code_intel_slash("help")
        assert "clear" in result.lower()

    def test_help_flag(self):
        result = _handle_code_intel_slash("--help")
        assert "code-intel" in result.lower()

    def test_clear_unknown_subcommand(self):
        result = _handle_code_intel_slash("nonexistent")
        assert "unknown" in result.lower() or "Unknown" in result

    def test_status_returns_info(self):
        """Status should return something with bridge/server info."""
        result = _handle_code_intel_slash("status")
        assert result is not None
        assert "Status" in result or "code_intel" in result.lower()

    def test_clear_subcommand(self):
        """'clear' subcommand invokes symbol cache clearing."""
        result = _handle_code_intel_slash("clear")
        assert result is not None

    def test_trailing_whitespace(self):
        result = _handle_code_intel_slash("  status  ")
        assert "Status" in result or "code_intel" in result.lower()


class TestSessionEndHook:
    """Test the on_session_end hook."""

    def test_session_end_runs_without_error(self):
        """The hook should not raise when called without args."""
        from code_intel.__init__ import _on_session_end
        result = _on_session_end()
        assert result is None
