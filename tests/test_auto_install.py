"""Tests for lsp/auto_install.py — LSP Server Auto-Installation."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from code_intel.lsp.auto_install import (
    _LSP_INSTALL_MAP,
    _auto_install_lsp,
    _check_prerequisites,
    _get_install_hint,
    _install_attempted,
)


class TestLSPInstallMap:
    """Jeder Server in _LANGUAGE_SERVERS hat einen Eintrag in _LSP_INSTALL_MAP."""

    def test_all_language_servers_have_install_config(self):
        """Alle Server aus _LANGUAGE_SERVERS muessen in _LSP_INSTALL_MAP sein."""
        from code_intel.lsp.bridge.server import _LANGUAGE_SERVERS

        configured_servers = set()
        for configs in _LANGUAGE_SERVERS.values():
            for cfg in configs:
                configured_servers.add(cfg["command"])

        installable = set(_LSP_INSTALL_MAP.keys())
        # pylsp ist der Fallback fuer Python — muss nicht automatisch installiert
        # werden, da in den meisten Hermes-Venvs bereits vorhanden
        expected_missing = {"ccls", "pylsp"}
        missing = configured_servers - installable - expected_missing
        assert not missing, (
            f"Server ohne Install-Config: {missing}"
        )

    def test_install_map_has_required_keys(self):
        """Jeder Eintrag in _LSP_INSTALL_MAP hat alle benoetigten Keys."""
        required = {"command", "check_cmd", "timeout", "needs_sudo", "package_manager", "hint"}
        for cmd_name, config in _LSP_INSTALL_MAP.items():
            missing_keys = required - set(config.keys())
            assert not missing_keys, (
                f"{cmd_name}: fehlende Keys: {missing_keys}"
            )


class TestCheckPrerequisites:
    """Vorbedingungen fuer Installationen."""

    def test_pip_no_special_prereqs(self):
        """pip-Installationen haben keine speziellen prereqs."""
        config = _LSP_INSTALL_MAP["pylsp"]
        result = _check_prerequisites(config)
        assert result is None, f"pip install sollte keine prereqs brauchen: {result}"

    @patch("shutil.which")
    def test_npm_requires_npm(self, mock_which):
        """npm-Installation schlaegt fehl wenn npm nicht existiert."""
        def side_effect(cmd):
            if cmd == "npm":
                return None
            return "/usr/bin/" + cmd
        mock_which.side_effect = side_effect

        config = _LSP_INSTALL_MAP["typescript-language-server"]
        result = _check_prerequisites(config)
        assert result is not None
        assert "npm" in result

    @patch("shutil.which")
    def test_rust_requires_rustup(self, mock_which):
        """rust-analyzer Installation erfordert rustup auf PATH."""
        def side_effect(cmd):
            if cmd == "rustup":
                return None
            return "/usr/bin/" + cmd
        mock_which.side_effect = side_effect

        config = _LSP_INSTALL_MAP["rust-analyzer"]
        result = _check_prerequisites(config)
        assert result is not None
        assert "rustup" in result

    @patch("shutil.which")
    def test_go_requires_go(self, mock_which):
        """gopls Installation erfordert go auf PATH."""
        def side_effect(cmd):
            if cmd == "go":
                return None
            return "/usr/bin/" + cmd
        mock_which.side_effect = side_effect

        config = _LSP_INSTALL_MAP["gopls"]
        result = _check_prerequisites(config)
        assert result is not None
        assert "go" in result


class TestAutoInstall:
    """Auto-Installation selbst."""

    @patch("code_intel.lsp.auto_install.subprocess.run")
    @patch("code_intel.lsp.auto_install.shutil.which")
    def test_successful_install(self, mock_which, mock_run):
        """Erfolgreiche Installation erkennt das Binary hinterher."""
        mock_which.return_value = "/usr/bin/pylsp"
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        result = _auto_install_lsp("pylsp", "python")
        assert result is True

    @patch("code_intel.lsp.auto_install.subprocess.run")
    @patch("code_intel.lsp.auto_install.shutil.which")
    def test_install_timeout(self, mock_which, mock_run):
        """Timeout wird abgefangen und als Fehlschlag gemeldet."""
        mock_which.return_value = "/usr/bin/some-tool"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)

        result = _auto_install_lsp("pylsp", "python")
        assert result is False

    @patch("code_intel.lsp.auto_install.subprocess.run")
    @patch("code_intel.lsp.auto_install.shutil.which")
    def test_install_binary_not_on_path_after_success(self, mock_which, mock_run):
        """Wenn das Binary nach 'erfolgreicher' Installation nicht auf PATH ist, Fehlschlag."""
        def which_side_effect(cmd):
            # pylsp ist waehrend der Installation da (fuer prerequisites)
            # aber nachher nicht auf PATH (simuliert Fehler)
            if cmd == "pylsp" or cmd == "npm":
                return "/usr/bin/" + cmd
            return None
        mock_which.side_effect = which_side_effect
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        # Nach erfolgreichem run checkt _auto_install_lsp nochmal shutil.which
        # fuer das check_cmd binary — das fallt hier durch
        result = _auto_install_lsp("typescript-language-server", "typescript")
        assert result is False

    def test_idempotent_no_second_install(self):
        """Einmal versuchte Installation wird nicht wiederholt."""
        _install_attempted.clear()

        with patch("code_intel.lsp.auto_install._install_attempted", {"pylsp"}):
            result = _auto_install_lsp("pylsp", "python")
            assert result is False, "bereits versucht -> kein erneuter Versuch"

    @patch("code_intel.lsp.auto_install.subprocess.run")
    @patch("code_intel.lsp.auto_install.shutil.which")
    def test_clangd_sudo_required(self, mock_which, mock_run):
        """clangd via apt braucht sudo — schlaegt fehlerfrei fehl wenn nicht verfuegbar."""
        mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd in ("sudo", "apt-get") else None
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="password required")

        result = _auto_install_lsp("clangd", "c")
        assert result is False, "clangd Install sollte ohne sudo fehlschlagen"


class TestGetInstallHint:
    """Installations-Hinweise fuer User-Feedback."""

    def test_pip_hint(self):
        hint = _get_install_hint("pylsp")
        assert hint is not None
        assert "pip" in hint

    def test_apt_hint_includes_sudo(self):
        hint = _get_install_hint("clangd")
        assert hint is not None
        assert "sudo" in hint

    def test_unknown_server_returns_none(self):
        hint = _get_install_hint("nonexistent-server")
        assert hint is None

    def test_all_servers_have_hints(self):
        """Jeder Server in _LSP_INSTALL_MAP hat einen lesbaren Hint."""
        for cmd_name in _LSP_INSTALL_MAP:
            hint = _get_install_hint(cmd_name)
            assert hint is not None, f"{cmd_name} hat keinen Hint"
            assert len(hint) > 5, f"{cmd_name} Hint ist zu kurz"


class TestImportCleanup:
    """Nach jedem Test _install_attempted zuruecksetzen."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        _install_attempted.clear()
        yield
        _install_attempted.clear()
