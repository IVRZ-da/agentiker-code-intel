"""Nutzdaten liegen pro Profil unter ``<hermes-home>/plugin-data/...``.

Hermes-Review PR #157345, Empfehlung 1: Caches, Marker und der Knowledge-Graph
hingen an ``~/.hermes/...`` — alle Profile teilten sich denselben Store und
``HERMES_HOME`` wurde ignoriert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_intel import _paths


def test_plugin_data_dir_follows_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    expected = tmp_path / "plugin-data" / "agentiker-code-intel"
    assert _paths.plugin_data_dir() == expected
    assert _paths.cache_dir() == expected / "cache"
    assert _paths.plugin_data_dir("code_graph") == expected / "code_graph"


def test_plugin_data_dir_default_without_env(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert _paths.plugin_data_dir().parent.parent == Path.home() / ".hermes"


def test_plugin_slug_is_catalog_name():
    assert _paths.PLUGIN_SLUG == "agentiker-code-intel"


def test_cache_dirs_point_into_plugin_data():
    """Die Modul-Konstanten zeigen in den neuen Plugin-Datenbereich."""
    from code_intel import code_tools
    from code_intel.tools import base, cache

    for module in (code_tools, base, cache):
        persist = Path(module._PERSIST_DIR)
        assert persist.parent.name == "agentiker-code-intel", persist
        assert persist.name == "cache", persist
        assert "plugins/code_intel" not in str(persist), f"alter Pfad in {module.__name__}"


def test_markers_path_is_per_profile():
    from code_intel.lsp import discovery

    assert Path(discovery._USER_MARKERS_PATH).name == "markers.json"
    assert "plugin-data" in discovery._USER_MARKERS_PATH


def test_no_hardcoded_hermes_plugin_paths_left():
    """Keine der migrierten Dateien enthaelt noch den alten Hartkodier-Pfad."""
    plugin_root = Path(__file__).resolve().parent.parent
    for name in ("tools/base.py", "tools/cache.py", "code_tools.py", "lsp/discovery.py"):
        source = (plugin_root / name).read_text(encoding="utf-8")
        assert '"~/.hermes/plugins/code_intel/.cache"' not in source, name
        assert '"~/.hermes/code_intel_markers.json"' not in source, name


@pytest.mark.parametrize("parts", [("cache",), ("code_graph",), ("markers.json",)])
def test_data_dir_is_under_plugin_slug(parts):
    path = _paths.plugin_data_dir(*parts)
    assert path.parts[-len(parts) - 1] == "agentiker-code-intel"
