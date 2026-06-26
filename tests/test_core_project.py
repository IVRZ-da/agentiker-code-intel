"""Tests for project root detection, cache key, and cache path logic.

Extracted from test_core_tools.py — project infrastructure domain.
"""

import os

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if tree-sitter is not installed
# ---------------------------------------------------------------------------
pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.code_tools import (
    _PERSIST_DIR,
    _cache_key_for_path,
    _find_project_root,
    _project_cache_path,
)

# ===========================================================================
# Project root detection — _find_project_root()
# ===========================================================================


class TestFindProjectRoot:
    """_find_project_root() edge cases: env var, CWD fallback, markers."""

    def test_with_filepath_git_marker(self, tmp_path):
        """Walk up looking for .git."""
        git_dir = tmp_path / "myproject"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()
        src_dir = git_dir / "src"
        src_dir.mkdir()
        f = src_dir / "main.py"
        f.write_text("")
        root = _find_project_root(str(f))
        assert root == str(git_dir.resolve())

    def test_with_filepath_pyproject(self, tmp_path):
        """Detect pyproject.toml marker."""
        proj = tmp_path / "mylib"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("[project]\n")
        mod = proj / "src" / "mylib"
        mod.mkdir(parents=True)
        f = mod / "__init__.py"
        f.write_text("")
        root = _find_project_root(str(f))
        assert root == str(proj.resolve())

    def test_env_var_used_when_no_filepath(self, tmp_path, monkeypatch):
        """When filepath is empty, HERMES_PROJECT_ROOT takes priority."""
        monkeypatch.setenv("HERMES_PROJECT_ROOT", str(tmp_path))
        root = _find_project_root("")
        assert root == str(tmp_path.resolve())

    def test_env_var_ignored_when_not_a_dir(self, monkeypatch):
        """When HERMES_PROJECT_ROOT points to nonexistent, fall back to CWD."""
        monkeypatch.setenv("HERMES_PROJECT_ROOT", "/nonexistent/path/12345")
        root = _find_project_root("")
        # Should fall back to CWD
        assert root != "/nonexistent/path/12345"

    def test_fall_back_to_cwd(self, monkeypatch):
        """Without env var, fall back to CWD."""
        monkeypatch.delenv("HERMES_PROJECT_ROOT", raising=False)
        root = _find_project_root("")
        assert os.path.isdir(root)

    def test_monorepo_marker_pnpm(self, tmp_path):
        """pnpm-workspace.yaml detected as monorepo root."""
        root_dir = tmp_path / "monoroot"
        root_dir.mkdir()
        (root_dir / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
        sub = root_dir / "packages" / "pkg_a"
        sub.mkdir(parents=True)
        f = sub / "index.ts"
        f.write_text("")
        root = _find_project_root(str(f))
        assert root == str(root_dir.resolve())

    def test_go_mod_marker(self, tmp_path):
        """go.mod detection for Go projects."""
        proj = tmp_path / "goproj"
        proj.mkdir()
        (proj / "go.mod").write_text("module example.com/proj\n")
        f = proj / "main.go"
        f.write_text("")
        root = _find_project_root(str(f))
        assert root == str(proj.resolve())

    def test_file_at_root_no_markers(self, tmp_path):
        """No markers found — return parent of file."""
        f = tmp_path / "standalone.py"
        f.write_text("")
        root = _find_project_root(str(f))
        assert root == str(tmp_path.resolve())


# ===========================================================================
# Cache key generation — _cache_key_for_path()
# ===========================================================================


class TestCacheKeyPath:
    def test_cache_key_relative(self, tmp_path):
        """When file is under project root, key is relative."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        src = proj / "src"
        src.mkdir()
        f = src / "mod.py"
        f.write_text("")
        key = _cache_key_for_path(str(f))
        assert "src/mod.py" in key

    def test_cache_key_absolute_when_outside_project(self, tmp_path):
        """When file is under project root, key is project-relative (outside.py)."""
        f = tmp_path / "outside.py"
        f.write_text("")
        key = _cache_key_for_path(str(f))
        # File's parent is the project root (no markers found), so key is just the filename
        assert "outside.py" in key


# ===========================================================================
# Project cache path — _project_cache_path()
# ===========================================================================


class TestProjectCachePath:
    def test_returns_stable_path(self, monkeypatch):
        """Cache path is deterministic per project root."""
        monkeypatch.setattr("code_intel.tools.cache._find_project_root", lambda x="": "/test/root")
        path = _project_cache_path()
        assert path.startswith(_PERSIST_DIR)
        assert "symidx_" in path
        assert path.endswith(".json")

    def test_uses_provided_root(self):
        """When project_root given, hash is based on that."""
        p1 = _project_cache_path("/project/alpha")
        p2 = _project_cache_path("/project/beta")
        assert p1 != p2
