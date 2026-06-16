"""Tests for workspace root discovery functions."""

from code_intel.lsp_bridge import (
    _find_workspace_root,
    _find_tsconfig_root,
    _find_workspace_folders,
)


class TestFindWorkspaceRoot:
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
