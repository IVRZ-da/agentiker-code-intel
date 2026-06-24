"""Plugin-Lifecycle Tests für code_intel.

MIGRIERT AUS: test_e2e_lifecycle.py (top-level + test_e2e/ — waren Duplikate)
Grund: E2E-Tests die teils als Integration, teils als Unit-Test laufen können.

KLASSIFIZIERUNG:
- ImportGraph: reiner Unit-Test (keine externen Dependencies)
- register_lsp_tools + _inject_toolsets + toolsets-Check: brauchen Hermes Runtime → Integration
- LSP-Bridge Init: braucht echten pyright/typescript-language-server → Integration
"""

from unittest.mock import MagicMock

import pytest

# ─── Integration-Tests (brauchen Hermes Runtime + LSP Server) ────────────


@pytest.mark.integration
class TestPluginLifecycle:
    """Plugin-Lifecycle — Load, Registry, LSP-Init.

    Diese Tests brauchen die echte Hermes-Umgebung (toolsets, registry)
    und/oder installierte LSP-Server (pyright, typescript-language-server).
    """

    @pytest.mark.xfail(reason="Test-Interaktion: relative import beyond top-level package bei Suite-Run", strict=False)
    def test_register_lsp_tools_without_error(self):
        """C1: register_lsp_tools(ctx) läuft ohne Fehler."""
        from code_intel.lsp_bridge import register_lsp_tools
        ctx = MagicMock()
        try:
            register_lsp_tools(ctx)
            assert True
        except Exception as exc:
            assert False, f"register_lsp_tools failed: {exc}"

    def test_inject_toolsets(self):
        """_inject_toolsets() fügt code_intel zu TOOLSETS hinzu."""
        import toolsets
        from code_intel.__init__ import _inject_toolsets

        _inject_toolsets()

        assert "agentiker_code_intel" in toolsets.TOOLSETS
        tools_def = toolsets.TOOLSETS["agentiker_code_intel"]
        assert "tools" in tools_def
        known_tools = {"code_symbols", "code_search", "code_definition", "code_references"}
        assert known_tools.issubset(set(tools_def["tools"]))

    def test_lsp_bridge_initializes_pyright(self, tmp_path):
        """LSPBridge initialisiert pyright-langserver."""
        from code_intel.lsp_bridge import get_lsp_manager

        manager = get_lsp_manager()
        bridge = manager.get_bridge("python", str(tmp_path))
        assert bridge is not None
        assert bridge.ensure_initialized()

    def test_lsp_bridge_initializes_tsserver(self, tmp_path):
        """LSPBridge initialisiert typescript-language-server."""
        tmp = tmp_path / "test.ts"
        tmp.write_text('const x: number = 1;\n')
        from code_intel.lsp_bridge import get_lsp_manager

        manager = get_lsp_manager()
        bridge = manager.get_bridge("typescript", str(tmp))
        assert bridge is not None
        assert bridge.ensure_initialized()

    def test_all_tools_registered_in_toolsets(self, monkeypatch):
        """Alle 39+ Tools sollten in TOOLSETS enthalten sein."""
        from unittest.mock import patch

        import code_intel.__init__ as init_mod
        import toolsets
        # Eintrag löschen falls von vorherigem Test
        toolsets.TOOLSETS.pop("agentiker_code_intel", None)
        toolsets._HERMES_CORE_TOOLS.clear()
        with patch.object(init_mod, 'get_active_profile', return_value='all'):
            init_mod._inject_toolsets()

        tools = toolsets.TOOLSETS["agentiker_code_intel"]["tools"]
        assert len(tools) >= 39, f"Erwartet >=39 Tools, habe {len(tools)}"

        # Kerntools prüfen
        expected = {"code_symbols", "code_search", "code_refactor",
                    "code_definition", "code_references", "code_diagnostics",
                    "code_complexity", "code_hot_paths", "code_blast_radius",
                    "code_overview", "code_cycle_detector", "code_dependency_graph"}
        missing = expected - set(tools)
        assert not missing, f"Fehlende Tools: {missing}"


# ─── Unit-Tests (keine externen Dependencies) ────────────────────────────


class TestImportGraphBasics:
    """ImportGraph kann importiert und instanziiert werden."""

    def test_import_graph_importable(self, tmp_path):
        """ImportGraph lässt sich importieren und instanziieren."""
        from code_intel._import_graph import ImportGraph
        g = ImportGraph(str(tmp_path))
        assert g is not None
        assert g.project_root == tmp_path.resolve()
