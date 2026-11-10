"""tests/test_workspace_symbols_fallback.py — AST-Fallback fuer code_workspace_symbols.

Hintergrund: pylsp 1.14 implementiert ``workspace/symbol`` nicht und antwortet mit
``null`` (die Bridge normalisiert das zu ``[]``). ``code_workspace_symbols_tool``
faellt dann auf einen tree-sitter-Scan zurueck. Diese Tests decken Scoring,
Scan-Limits und den Fallback-Zweig ab.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_intel.lsp.tools_handler import (
    _WSS_MAX_FILE_BYTES,
    _WSS_SKIP_DIRS,
    _wss_ast_fallback,
    _wss_score,
    code_workspace_symbols_tool,
)


SAMPLE = """\
def alpha_one(a, b):
    return a + b


class BetaThing:
    def run(self):
        return alpha_one(1, 2)


def gamma_helper():
    return BetaThing()
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(SAMPLE)
    (tmp_path / "pkg" / "other.py").write_text("def delta_last():\n    pass\n")
    # Diese Verzeichnisse muessen ignoriert werden.
    for skip in ("node_modules", ".git", "__pycache__"):
        d = tmp_path / skip
        d.mkdir()
        (d / "ignored.py").write_text("def alpha_ignored():\n    pass\n")
    (tmp_path / "notes.md").write_text("alpha_one not a symbol\n")
    return tmp_path


# --------------------------------------------------------------------------- Score
class TestScore:
    def test_exact(self):
        assert _wss_score("alpha_one", "alpha_one") == 0

    def test_prefix(self):
        assert _wss_score("alpha_one_helper", "alpha") == 1

    def test_substring(self):
        assert _wss_score("my_alpha_one", "alpha") == 2

    def test_no_match(self):
        assert _wss_score("beta", "alpha") == 4

    def test_case_insensitive(self):
        assert _wss_score("AlphaOne", "alphaone") == 0

    def test_separator_normalisation(self):
        """CodeComplexity muss code_complexity exakt treffen."""
        assert _wss_score("code_complexity", "CodeComplexity") == 0

    def test_empty_query_matches_everything(self):
        assert _wss_score("irgendwas", "") == 0

    def test_no_subsequence_noise(self):
        """Frueher traf 'widget' beliebige Bezeichner per Subsequenz — bewusst entfernt."""
        assert _wss_score("test_dispatch_with_pending_none_result", "widget") == 4


# --------------------------------------------------------------------------- Fallback
class TestFallback:
    def test_finds_function(self, project: Path):
        res = _wss_ast_fallback("alpha_one", project, None, 10, "python")
        names = [s["name"] for s in res["symbols"]]
        assert "alpha_one" in names

    def test_finds_class(self, project: Path):
        res = _wss_ast_fallback("Beta", project, None, 10, "python")
        assert any(s["name"] == "BetaThing" and s["kind"] == "class" for s in res["symbols"])

    def test_kind_filter(self, project: Path):
        res = _wss_ast_fallback("alpha_one", project, "function", 10, "python")
        assert all(s["kind"] == "function" for s in res["symbols"])
        assert res["symbols"], "es sollte Function-Treffer geben"

    def test_skips_ignored_dirs(self, project: Path):
        res = _wss_ast_fallback("alpha_ignored", project, None, 10, "python")
        assert res["symbols"] == []

    def test_skips_non_source_files(self, project: Path):
        res = _wss_ast_fallback("alpha_one", project, None, 50, "python")
        files = {Path(s["file"]).suffix for s in res["symbols"]}
        assert files <= {".py"}

    def test_language_filter_excludes_others(self, project: Path):
        (project / "app.ts").write_text("export function alphaTs() { return 1; }\n")
        res = _wss_ast_fallback("alpha", project, None, 50, "python")
        assert all(Path(s["file"]).suffix == ".py" for s in res["symbols"])

    def test_limit_respected(self, project: Path):
        res = _wss_ast_fallback("a", project, None, 2, "python")
        assert len(res["symbols"]) <= 2
        assert res["truncated"] is True

    def test_exact_match_ranks_first(self, project: Path):
        res = _wss_ast_fallback("alpha_one", project, None, 5, "python")
        assert res["symbols"][0]["name"] == "alpha_one"

    def test_no_result(self, project: Path):
        res = _wss_ast_fallback("gibtesnicht", project, None, 10, "python")
        assert res["symbols"] == []
        assert res["total_found"] == 0

    def test_reports_scan_stats(self, project: Path):
        res = _wss_ast_fallback("alpha", project, None, 10, "python")
        assert res["files_scanned"] >= 2
        assert isinstance(res["duration_s"], float)
        assert res["duration_s"] < 10.0

    def test_large_file_skipped(self, project: Path):
        big = project / "big.py"
        big.write_text("def alpha_big():\n    pass\n" + "# x\n" * (_WSS_MAX_FILE_BYTES // 3))
        res = _wss_ast_fallback("alpha_big", project, None, 10, "python")
        assert res["symbols"] == []

    def test_skip_dir_list_contains_expected(self):
        for name in (".git", "node_modules", "__pycache__", ".venv"):
            assert name in _WSS_SKIP_DIRS


# --------------------------------------------------------------------------- Tool-Zweig
class TestToolFallbackBranch:
    """Tool-Zweig: leerer LSP-Response -> AST-Fallback, echter Treffer -> method=lsp.

    Gepatcht wird ``code_workspace_symbols_tool.__globals__`` (das Modul-Global der
    Funktion selbst). Ein ``patch("code_intel.lsp.tools_handler.get_lsp_manager")``
    greift im vollen Suite-Lauf nicht zuverlaessig, weil das Modul zwischen Tests neu
    importiert werden kann — die registrierte Funktion haengt dann am alten Modul.
    """

    def _patch_manager(self, bridge):
        fake_mgr = MagicMock()
        fake_mgr.get_bridge.return_value = bridge
        return patch.dict(
            code_workspace_symbols_tool.__globals__,
            {"get_lsp_manager": lambda: fake_mgr},
        )

    def test_empty_lsp_result_triggers_fallback(self, project: Path):
        target = project / "pkg" / "mod.py"
        fake_bridge = MagicMock()
        fake_bridge.ensure_initialized.return_value = True
        fake_bridge.command = "pylsp"
        fake_bridge.root_uri = f"file://{project}"
        fake_bridge.workspace_symbol.return_value = []  # pylsp-Verhalten

        with self._patch_manager(fake_bridge):
            out = code_workspace_symbols_tool(query="alpha_one", path=str(target))

        assert "ast_fallback" in out
        assert "alpha_one" in out

    def test_lsp_result_keeps_lsp_method(self, project: Path):
        target = project / "pkg" / "mod.py"
        fake_bridge = MagicMock()
        fake_bridge.ensure_initialized.return_value = True
        fake_bridge.command = "gopls"
        fake_bridge.root_uri = f"file://{project}"
        fake_bridge.workspace_symbol.return_value = [{
            "name": "alpha_one",
            "kind": 12,
            "location": {
                "uri": f"file://{target}",
                "range": {"start": {"line": 0, "character": 0}},
            },
        }]

        with self._patch_manager(fake_bridge):
            out = code_workspace_symbols_tool(query="alpha_one", path=str(target))

        assert "ast_fallback" not in out
        assert "alpha_one" in out
