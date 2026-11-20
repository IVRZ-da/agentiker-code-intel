"""Der LSP-Server-Prozess bekommt nur eine Allowlist der Umgebung.

Hermes-Review PR #157345, Punkt 4: vorher ging ``{**os.environ}`` an jeden
Language-Server — inklusive aller Provider-Keys und Credentials des Hosts.
"""

from __future__ import annotations

import os

import pytest

_DENIED_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
    "HERMES_API_KEY",
)
_ALLOWED_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "GOPATH", "CARGO_HOME", "NODE_PATH", "VIRTUAL_ENV")


@pytest.fixture()
def bridge_env(monkeypatch):
    for key in _DENIED_KEYS + _ALLOWED_KEYS:
        monkeypatch.setenv(key, f"value-{key.lower()}")
    from code_intel.lsp_bridge import LSPBridge

    def _env(language_id="python"):
        return LSPBridge(command="", args=[], root_uri="/tmp", language_id=language_id)._build_env()

    return _env


def test_provider_keys_are_not_forwarded(bridge_env):
    env = bridge_env()
    leaked = sorted(k for k in _DENIED_KEYS if k in env)
    assert not leaked, f"Credentials im LSP-Prozess: {leaked}"


def test_toolchain_vars_are_forwarded(bridge_env):
    env = bridge_env()
    missing = sorted(k for k in _ALLOWED_KEYS if k not in env)
    assert not missing, f"Toolchain-Variablen fehlen: {missing}"


def test_pythonpath_is_not_forwarded(bridge_env, monkeypatch):
    """Bewusst nicht durchgereicht: fremde sys.path-Eintraege im LSP-Server."""
    monkeypatch.setenv("PYTHONPATH", "/tmp/injected")
    assert "PYTHONPATH" not in bridge_env()


def test_python_specific_vars(bridge_env):
    env = bridge_env("python")
    assert env["PYTHONWARNINGS"] == "ignore"
    assert "TSS_LOG" not in env


def test_typescript_specific_vars(bridge_env):
    env = bridge_env("typescript")
    assert env["TSS_LOG"] == "-"
    assert "PYTHONWARNINGS" not in env


def test_env_is_a_copy(bridge_env):
    """Mutationen am env duerfen os.environ nicht veraendern."""
    env = bridge_env()
    env["PATH"] = "/injected"
    assert os.environ["PATH"] != "/injected"
