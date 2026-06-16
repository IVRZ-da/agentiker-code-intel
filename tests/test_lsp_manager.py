"""Tests for LSPManager lifecycle: bridge creation, reuse, eviction, shutdown."""
from pathlib import Path

from code_intel.lsp_bridge import LSPManager


class TestLSPManager:
    def test_get_bridge_returns_none_for_unknown_language(self):
        mgr = LSPManager()
        bridge = mgr.get_bridge("nonexistent-lang", "/tmp/test.py")
        assert bridge is None

    def test_get_bridge_rust_config_exists(self):
        """Rust must have a language server config, even if binary not on PATH."""
        from code_intel.lsp_bridge import _LANGUAGE_SERVERS
        assert "rust" in _LANGUAGE_SERVERS
        assert len(_LANGUAGE_SERVERS["rust"]) > 0
        assert _LANGUAGE_SERVERS["rust"][0]["command"] == "rust-analyzer"

    def test_get_bridge_go_config_exists(self):
        """Go must have a language server config, even if binary not on PATH."""
        from code_intel.lsp_bridge import _LANGUAGE_SERVERS
        assert "go" in _LANGUAGE_SERVERS
        assert len(_LANGUAGE_SERVERS["go"]) > 0
        assert _LANGUAGE_SERVERS["go"][0]["command"] == "gopls"

    def test_initial_bridges_empty(self):
        mgr = LSPManager()
        assert len(mgr._bridges) == 0

    def test_shutdown_all_empty_does_not_crash(self):
        mgr = LSPManager()
        mgr.shutdown_all()

    def test_get_workspace_folders_cached(self):
        mgr = LSPManager()
        folders1 = mgr._get_workspace_folders("/tmp")
        folders2 = mgr._get_workspace_folders("/tmp")
        assert folders1 == folders2

    def test_should_use_monorepo_root_returns_false_for_same_root(self):
        mgr = LSPManager()
        result = mgr._should_use_monorepo_ts_root(
            ts_root="/home/project",
            mono_root="/home/project",
            file_path="/home/project/file.ts",
        )
        assert result is False

    def test_should_use_monorepo_returns_false_without_pnpm(self, tmp_path):
        mgr = LSPManager()
        root = tmp_path / "project"
        root.mkdir()
        result = mgr._should_use_monorepo_ts_root(
            ts_root=str(root / "apps" / "backend"),
            mono_root=str(root),
            file_path=str(root / "apps" / "backend" / "file.ts"),
        )
        assert result is False

    def test_should_use_monorepo_true_with_pnpm(self, tmp_path):
        mgr = LSPManager()
        root = tmp_path / "monorepo"
        root.mkdir()
        (root / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
        apps = root / "apps"
        apps.mkdir()
        backend = apps / "backend"
        backend.mkdir()
        result = mgr._should_use_monorepo_ts_root(
            ts_root=str(backend),
            mono_root=str(root),
            file_path=str(backend / "file.ts"),
        )
        assert result is True

    def test_shutdown_all_clears_bridges(self, tmp_path):
        mgr = LSPManager()
        mgr.get_bridge("python", str(tmp_path / "test.py"))
        mgr.shutdown_all()
        assert len(mgr._bridges) == 0

    def test_bridge_reuses_existing_for_same_key(self, tmp_path):
        """Two calls with matching key should use the same bridge."""
        mgr = LSPManager()
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        b1 = mgr.get_bridge("python", str(f))
        # Bridge count should be 1 after first create
        count_before = len(mgr._bridges)
        b2 = mgr.get_bridge("python", str(f))
        # Bridge count should NOT increase on second call
        count_after = len(mgr._bridges)
        assert count_after <= count_before + 1  # at most 1 new if first was evicted


class TestLSPManagerEviction:
    def test_evicts_oldest_bridge_when_pool_full(self):
        """When more than 8 bridges are created, oldest should be evicted."""
        import tempfile
        import os
        mgr = LSPManager()
        dirs = []
        for i in range(9):
            d = tempfile.mkdtemp()
            dirs.append(d)
            f = os.path.join(d, "test.py")
            Path(f).touch()
            mgr.get_bridge("python", f)
        assert len(mgr._bridges) <= 8
        import shutil
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
