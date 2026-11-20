"""Plugin-Lifecycle Tests für code_intel.

MIGRIERT AUS: test_e2e_lifecycle.py (top-level + test_e2e/ — waren Duplikate)
Grund: E2E-Tests die teils als Integration, teils als Unit-Test laufen können.

KLASSIFIZIERUNG:
- ImportGraph: reiner Unit-Test (keine externen Dependencies)
- register_lsp_tools + LSP-Bridge: brauchen Hermes Runtime → Integration
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

    def test_register_lsp_tools_without_error(self):
        """C1: register_lsp_tools(ctx) läuft ohne Fehler."""
        from code_intel.lsp_bridge import register_lsp_tools

        ctx = MagicMock()
        try:
            register_lsp_tools(ctx)
            assert True
        except Exception as exc:
            assert False, f"register_lsp_tools failed: {exc}"

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
        tmp.write_text("const x: number = 1;\n")
        from code_intel.lsp_bridge import get_lsp_manager

        manager = get_lsp_manager()
        bridge = manager.get_bridge("typescript", str(tmp))
        assert bridge is not None
        assert bridge.ensure_initialized()

# ─── Unit-Tests (keine externen Dependencies) ────────────────────────────


class TestImportGraphBasics:
    """ImportGraph kann importiert und instanziiert werden."""

    def test_import_graph_importable(self, tmp_path):
        """ImportGraph lässt sich importieren und instanziieren."""
        from code_intel._import_graph import ImportGraph

        g = ImportGraph(str(tmp_path))
        assert g is not None
        assert g.project_root == tmp_path.resolve()
