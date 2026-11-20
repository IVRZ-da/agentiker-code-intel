"""LSP-Auto-Installation ist opt-in und versionsgepinnt.

Hermes-Review PR #157345, Punkt 5: ein Tool-Aufruf, der keinen Language-Server
findet, installierte ungefragt globale Pakete (npm -g, go install @latest,
pip --user, apt). Jetzt: Default AUS, nur mit ``CODE_INTEL_LSP_AUTO_INSTALL=1``,
und jede Version ist gepinnt.
"""

from __future__ import annotations

import subprocess

import pytest

from code_intel.lsp import auto_install


@pytest.fixture(autouse=True)
def _reset_attempts(monkeypatch):
    """Opt-in-Zustand und Idempotenz-Set pro Test zuruecksetzen."""
    monkeypatch.delenv(auto_install.ENV_AUTO_INSTALL, raising=False)
    auto_install._install_attempted.clear()
    yield
    auto_install._install_attempted.clear()


@pytest.fixture()
def spy(monkeypatch):
    """Ersetzt subprocess.run und protokolliert die Kommandos."""
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    def _run(cmd, *args, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
        return _Result()

    monkeypatch.setattr(auto_install.subprocess, "run", _run)
    return calls


def test_disabled_by_default(spy):
    assert auto_install.auto_install_enabled() is False
    assert auto_install._auto_install_lsp("pyright-langserver", "python") is False
    assert spy == [], f"ohne Opt-in darf nichts installiert werden, aber: {spy}"


def test_other_values_do_not_enable(monkeypatch, spy):
    for value in ("true", "yes", "0", "", "  "):
        monkeypatch.setenv(auto_install.ENV_AUTO_INSTALL, value)
        assert auto_install._auto_install_lsp("pyright-langserver", "python") is False
    assert spy == []


def test_opt_in_triggers_install(monkeypatch, spy):
    monkeypatch.setenv(auto_install.ENV_AUTO_INSTALL, "1")
    auto_install._auto_install_lsp("pyright-langserver", "python")
    assert spy, "mit Opt-in muss installiert werden"
    assert spy[0][:3] == ["npm", "install", "-g"]


def test_hint_only_entry_never_installs(monkeypatch, spy):
    monkeypatch.setenv(auto_install.ENV_AUTO_INSTALL, "1")
    assert auto_install._auto_install_lsp("java-language-server", "java") is False
    assert spy == []


_EXPECTED_PINS = {
    "pylsp": ["python-lsp-server==1.15.0"],
    "pyright-langserver": ["pyright@1.1.414"],
    "typescript-language-server": ["typescript-language-server@6.1.0", "typescript@7.0.2"],
    "gopls": ["golang.org/x/tools/gopls@v0.24.0"],
}


@pytest.mark.parametrize("cmd_name", sorted(_EXPECTED_PINS))
def test_install_commands_are_pinned(cmd_name):
    """Jedes installierbare Paket traegt eine feste Version."""
    command = auto_install._LSP_INSTALL_MAP[cmd_name]["command"]
    for pin in _EXPECTED_PINS[cmd_name]:
        assert pin in command, f"{cmd_name}: Pin {pin!r} fehlt in {command}"


def test_no_latest_anywhere():
    joined = " ".join(
        " ".join(cfg.get("command") or []) for cfg in auto_install._LSP_INSTALL_MAP.values()
    )
    assert "@latest" not in joined, "ungepinntes @latest in einem Install-Kommando"


def test_every_entry_has_hint():
    """Ohne Installationsrecht bleibt der Hinweis der einzige Weg."""
    for name, cfg in auto_install._LSP_INSTALL_MAP.items():
        assert cfg.get("hint"), f"{name}: kein Install-Hinweis hinterlegt"
        assert "Install via:" in auto_install._get_install_hint(name)


def test_install_is_not_run_in_parallel_twice(monkeypatch, spy):
    monkeypatch.setenv(auto_install.ENV_AUTO_INSTALL, "1")
    auto_install._auto_install_lsp("pyright-langserver", "python")
    first = len(spy)
    auto_install._auto_install_lsp("pyright-langserver", "python")
    assert len(spy) == first, "zweiter Aufruf darf nicht erneut installieren"


def test_subprocess_module_untouched():
    """Sanity: der Spy-Fixture patcht nur das Modul, nicht das echte subprocess."""
    assert callable(subprocess.run)
