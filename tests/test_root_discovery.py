"""Tests for workspace root discovery functions."""

from code_intel.lsp_bridge import (
    _find_workspace_root,
    _find_tsconfig_root,
    _find_workspace_folders,
    _WORKSPACE_ROOT_CACHE,
)


class TestFindWorkspaceRoot:
    def setup_method(self):
        _WORKSPACE_ROOT_CACHE.clear()

    def test_finds_git_root(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()
        subdir = repo / "src" / "lib"
        subdir.mkdir(parents=True)
        f = subdir / "file.py"
        f.write_text("")
        root = _find_workspace_root(str(f))
        assert root == str(repo)

    def test_finds_pnpm_workspace(self, tmp_path):
        repo = tmp_path / "monorepo"
        repo.mkdir()
        (repo / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
        apps = repo / "apps" / "backend"
        apps.mkdir(parents=True)
        f = apps / "file.ts"
        f.write_text("")
        root = _find_workspace_root(str(f))
        assert root == str(repo)

    def test_finds_package_json(self, tmp_path):
        proj = tmp_path / "node-app"
        proj.mkdir()
        (proj / "package.json").write_text("{}")
        f = proj / "index.js"
        f.write_text("")
        root = _find_workspace_root(str(f))
        assert root == str(proj)

    def test_no_marker_returns_parent_dir(self, tmp_path):
        d = tmp_path / "a" / "b" / "c"
        d.mkdir(parents=True)
        f = d / "orphan.rs"
        f.write_text("")
        root = _find_workspace_root(str(f))
        assert root is not None
        assert "orphan" not in root

    def test_skips_monorepo_root_for_sub_project(self, tmp_path):
        """Monorepo root with workspaces is skipped; sub-project root preferred."""
        root = tmp_path / "monorepo"
        root.mkdir()
        (root / "package.json").write_text('{"workspaces": ["apps/*"]}')
        sub = root / "apps" / "storefront"
        sub.mkdir(parents=True)
        (sub / "next.config.ts").write_text("")
        (sub / "package.json").write_text("{}")
        f = sub / "page.tsx"
        f.write_text("")
        result = _find_workspace_root(str(f))
        assert result == str(sub), f"Expected {sub}, got {result}"

    def test_prefers_sub_project_over_mono_root(self, tmp_path):
        """tsconfig.json + package.json in sub-project preferred over .git in parent."""
        root = tmp_path / "repo"
        root.mkdir()
        (root / ".git").mkdir()
        sub = root / "apps" / "backend"
        sub.mkdir(parents=True)
        (sub / "tsconfig.json").write_text("{}")
        (sub / "package.json").write_text("{}")
        f = sub / "service.ts"
        f.write_text("")
        result = _find_workspace_root(str(f))
        assert result == str(sub), f"Expected {sub}, got {result}"

    def test_medusa_config_detected(self, tmp_path):
        """medusa-config.ts marks the sub-project root."""
        root = tmp_path / "repo"
        root.mkdir()
        (root / "package.json").write_text('{"workspaces": ["apps/*"]}')
        backend = root / "apps" / "backend"
        backend.mkdir(parents=True)
        (backend / "medusa-config.ts").write_text("")
        api_dir = backend / "src" / "api"
        api_dir.mkdir(parents=True)
        f = api_dir / "route.ts"
        f.write_text("")
        result = _find_workspace_root(str(f))
        assert result == str(backend), f"Expected {backend}, got {result}"

    def test_non_monorepo_keeps_old_behavior(self, tmp_path):
        """Simple package.json without workspaces still works as root."""
        root = tmp_path / "simple-app"
        root.mkdir()
        (root / "package.json").write_text('{"name": "test"}')
        sub = root / "src"
        sub.mkdir()
        f = sub / "index.js"
        f.write_text("")
        result = _find_workspace_root(str(f))
        assert result == str(root), f"Expected {root}, got {result}"

    def test_pyproject_root_still_found(self, tmp_path):
        """Python projects still find pyproject.toml."""
        root = tmp_path / "python-app"
        root.mkdir()
        (root / "pyproject.toml").write_text("")
        sub = root / "src" / "lib"
        sub.mkdir(parents=True)
        f = sub / "main.py"
        f.write_text("")
        result = _find_workspace_root(str(f))
        assert result == str(root), f"Expected {root}, got {result}"


class TestFindTsconfigRoot:
    def test_finds_nearest_tsconfig(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "tsconfig.json").write_text("{}")
        src = proj / "src"
        src.mkdir()
        f = src / "file.ts"
        f.write_text("")
        root = _find_tsconfig_root(str(f))
        assert root == str(proj)

    def test_no_tsconfig_returns_none(self, tmp_path):
        d = tmp_path / "no-tsconfig"
        d.mkdir()
        f = d / "file.ts"
        f.write_text("")
        root = _find_tsconfig_root(str(f))
        assert root is None

    def test_tsconfig_in_same_dir(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "tsconfig.json").write_text("{}")
        f = proj / "file.ts"
        f.write_text("")
        root = _find_tsconfig_root(str(f))
        assert root == str(proj)


class TestFindWorkspaceFolders:
    def test_pnpm_workspace_returns_packages(self, tmp_path):
        root = tmp_path / "monorepo"
        root.mkdir()
        (root / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
        apps = root / "apps"
        apps.mkdir()
        (apps / "backend").mkdir()
        (apps / "frontend").mkdir()
        folders = _find_workspace_folders(str(root))
        assert any("backend" in f for f in folders)
        assert any("frontend" in f for f in folders)

    def test_nx_returns_apps_and_packages(self, tmp_path):
        root = tmp_path / "nx-app"
        root.mkdir()
        (root / "nx.json").write_text("{}")
        (root / "apps").mkdir()
        (root / "packages").mkdir()
        folders = _find_workspace_folders(str(root))
        assert len(folders) >= 2

    def test_no_workspace_config_returns_empty(self, tmp_path):
        root = tmp_path / "simple"
        root.mkdir()
        f = root / "file.ts"
        f.write_text("")
        folders = _find_workspace_folders(str(root))
        assert folders == []

    def test_lerna_returns_conventional_folders(self, tmp_path):
        root = tmp_path / "lerna-app"
        root.mkdir()
        (root / "lerna.json").write_text("{}")
        (root / "packages").mkdir()
        (root / "apps").mkdir()
        folders = _find_workspace_folders(str(root))
        assert len(folders) >= 2
