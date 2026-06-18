"""
E2E Tests — Phase C: Plugin-Lifecycle.

Testet Plugin-Load, Registry, LSP-Init und Unload.
Nutzt die ECHTE Registry — keine Mocks.
"""

import os
from pathlib import Path

RUN_E2E = os.environ.get("E2E_TEST") == "1"

PLUGIN_DIR = Path(__file__).resolve().parent.parent


class TestE2eLifecycle:
    """Plugin-Lifecycle — Load, Registry, LSP-Init, Unload."""

    def test_e2e_plugin_loads_all_lsp_tools(self):
        """C1: register_lsp_tools() läuft ohne Fehler."""
        from code_intel.lsp_bridge import register_lsp_tools
        # Wenn kein Fehler fliegt, ist der Load erfolgreich
        try:
            register_lsp_tools()
            assert True
        except Exception as exc:
            assert False, f"register_lsp_tools failed: {exc}"

    def test_e2e_plugin_injects_toolsets(self):
        """_inject_toolsets() fügt code_intel zu TOOLSETS hinzu."""
        from code_intel.__init__ import _inject_toolsets
        import toolsets

        _inject_toolsets()

        assert "agentiker_code_intel" in toolsets.TOOLSETS
        tools_def = toolsets.TOOLSETS["agentiker_code_intel"]
        assert "tools" in tools_def
        # Mindestens code_intel tools sollten da sein
        known_tools = {"code_symbols", "code_search", "code_definition", "code_references"}
        assert known_tools.issubset(set(tools_def["tools"]))

    def test_e2e_lsp_bridge_initializes_pyright(self):
        """LSPBridge initialisiert pyright-langserver auf einer .py-Datei."""
        from code_intel.lsp_bridge import get_lsp_manager

        manager = get_lsp_manager()
        bridge = manager.get_bridge("python", str(PLUGIN_DIR))
        assert bridge is not None
        assert bridge.ensure_initialized()

    def test_e2e_lsp_bridge_initializes_tsserver(self):
        """LSPBridge initialisiert typescript-language-server auf einer .ts-Datei."""
        # Temporäre .ts-Datei erstellen
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "test.ts"
        tmp.write_text('const x: number = 1;\n')
        try:
            from code_intel.lsp_bridge import get_lsp_manager

            manager = get_lsp_manager()
            bridge = manager.get_bridge("typescript", str(tmp))
            assert bridge is not None
            assert bridge.ensure_initialized()
        finally:
            tmp.unlink()

    def test_e2e_import_graph_importable(self):
        """ImportGraph kann importiert und instanziiert werden."""
        from code_intel._import_graph import ImportGraph
        g = ImportGraph(str(PLUGIN_DIR))
        assert g is not None
        assert g.project_root == PLUGIN_DIR.resolve()

    def test_e2e_all_31_tools_registered(self):
        """Alle 31 Tools sollten in TOOLSETS enthalten sein."""
        import toolsets

        # Stelle sicher dass code_intel geladen ist
        from code_intel.__init__ import _inject_toolsets
        _inject_toolsets()

        tools = toolsets.TOOLSETS["agentiker_code_intel"]["tools"]
        assert len(tools) == 39, f"Erwartet 39 Tools, habe {len(tools)}: {tools}"

        # Prüfe dass ALLE neuen Tools da sind
        expected = {
            "code_symbols", "code_search", "code_refactor",
            "code_definition", "code_references", "code_diagnostics",
            "code_callers", "code_callees", "code_capsule",
            "code_workspace_summary", "code_impact", "code_tests_for_symbol",
            "code_query", "code_rename", "code_workspace_symbols",
            "code_hover", "code_type_definition",
            "code_signatures", "code_action",
            "code_format", "code_implementations",
            "code_highlight", "code_inlay_hints", "code_document_symbols",
            "code_call_hierarchy", "code_type_hierarchy",
            "code_complexity", "code_search_by_error",
            "code_hot_paths", "code_blast_radius",
            "code_pr_impact",
            "code_replace_body",
            "code_safe_delete",
            "code_insert_before",
            "code_insert_after",
            "code_overview",
            "code_cycle_detector",
            "code_dependency_graph",
            "code_unused_finder",
        }
        missing = expected - set(tools)
        assert not missing, f"Fehlende Tools: {missing}"
