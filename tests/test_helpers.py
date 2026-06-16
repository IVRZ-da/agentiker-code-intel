"""Tests for helper functions in lsp_bridge: URI, location, language detection, context reading."""
from pathlib import Path
from code_intel.lsp_bridge import (
    LSPBridge,
    _detect_language_for_lsp,
    _resolve_command,
    _read_context_lines,
    _location_to_dict,
    _auto_detect_identifier_column,
)

# Static methods on LSPBridge that we test as standalone
_uri_to_path = LSPBridge._uri_to_path
_normalize_locations = LSPBridge._normalize_locations


# ---------------------------------------------------------------------------
# _uri_to_path
# ---------------------------------------------------------------------------


class TestUriToPath:
    def test_strips_file_prefix(self):
        assert _uri_to_path("file:///tmp/test.ts") == "/tmp/test.ts"

    def test_preserves_windows_path(self):
        assert _uri_to_path("file:///C:/Users/test.ts") == "/C:/Users/test.ts"

    def test_no_prefix_returns_unchanged(self):
        assert _uri_to_path("/raw/path.ts") == "/raw/path.ts"

    def test_empty_string(self):
        assert _uri_to_path("") == ""


# ---------------------------------------------------------------------------
# _normalize_locations
# ---------------------------------------------------------------------------


class TestNormalizeLocations:
    def test_none_returns_none(self):
        assert _normalize_locations(None) is None

    def test_single_location_dict(self):
        loc = {"uri": "file:///a.ts", "range": {"start": {"line": 0}}}
        result = _normalize_locations(loc)
        assert result == [loc]

    def test_location_link(self):
        link = {
            "targetUri": "file:///b.ts",
            "targetRange": {"start": {"line": 5}},
        }
        result = _normalize_locations(link)
        assert result == [{"uri": "file:///b.ts", "range": {"start": {"line": 5}}}]

    def test_list_of_locations(self):
        locs = [
            {"uri": "file:///a.ts", "range": {}},
            {"uri": "file:///b.ts", "range": {}},
        ]
        result = _normalize_locations(locs)
        assert len(result) == 2

    def test_empty_list(self):
        assert _normalize_locations([]) is None

    def test_mixed_list_with_location_links(self):
        locs = [
            {"uri": "file:///a.ts", "range": {}},
            {"targetUri": "file:///b.ts", "targetSelectionRange": {}},
        ]
        result = _normalize_locations(locs)
        assert len(result) == 2
        assert result[0]["uri"] == "file:///a.ts"
        assert result[1]["uri"] == "file:///b.ts"


# ---------------------------------------------------------------------------
# _detect_language_for_lsp
# ---------------------------------------------------------------------------


class TestDetectLanguageForLsp:
    def test_python(self):
        assert _detect_language_for_lsp("/tmp/foo.py") == "python"

    def test_typescript(self):
        assert _detect_language_for_lsp("/tmp/foo.ts") == "typescript"

    def test_tsx(self):
        assert _detect_language_for_lsp("/tmp/foo.tsx") == "tsx"

    def test_unknown_returns_none(self):
        assert _detect_language_for_lsp("/tmp/foo.xyz") is None

    def test_case_insensitive(self):
        lang = _detect_language_for_lsp("/tmp/FOO.PY")
        assert lang is not None


# ---------------------------------------------------------------------------
# _resolve_command
# ---------------------------------------------------------------------------


class TestResolveCommand:
    def test_known_command_returns_path(self):
        result = _resolve_command("python3")
        assert result is not None
        assert Path(result).exists()

    def test_unknown_command_returns_none(self):
        assert _resolve_command("nonexistent-command-xyzzy-12345") is None


# ---------------------------------------------------------------------------
# _read_context_lines
# ---------------------------------------------------------------------------


class TestReadContextLines:
    def test_returns_lines_around_line(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\nd\ne\n")
        result = _read_context_lines(str(f), line=2, context=1)
        assert "b" in result
        assert "c" in result
        assert "d" in result

    def test_beginning_of_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\n")
        result = _read_context_lines(str(f), line=0, context=2)
        assert "a" in result
        assert "c" in result

    def test_end_of_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\n")
        result = _read_context_lines(str(f), line=2, context=1)
        assert "b" in result
        assert "c" in result or "c\n" in result

    def test_nonexistent_file(self):
        result = _read_context_lines("/nonexistent/file.py", line=0)
        assert result == []


# ---------------------------------------------------------------------------
# _location_to_dict
# ---------------------------------------------------------------------------


class TestLocationToDict:
    def test_simple_location(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("const x = 1;\n")
        loc = {
            "uri": f"file://{f}",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 12}},
        }
        result = _location_to_dict(loc)
        assert result["line"] == 1  # 1-based
        assert result["path"] == str(f)

    def test_location_outside_file_range(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        loc = {
            "uri": f"file://{f}",
            "range": {"start": {"line": 999, "character": 0}},
        }
        result = _location_to_dict(loc)
        assert result["line"] == 1000

    def test_uri_without_file_scheme(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        loc = {
            "uri": str(f),
            "range": {"start": {"line": 0, "character": 0}},
        }
        result = _location_to_dict(loc)
        assert result["file"] == str(f)


# ---------------------------------------------------------------------------
# _auto_detect_identifier_column (LSPBridge static usage)
# ---------------------------------------------------------------------------


class TestAutoDetectIdentifierColumn:
    def test_finds_identifier_on_line(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("myFunction(args);\n")
        col = _auto_detect_identifier_column(str(f), 0)
        assert col is not None and col > 0

    def test_no_identifier_returns_none(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("    \n")
        col = _auto_detect_identifier_column(str(f), 0)
        assert col is None or col == 0

    def test_nonexistent_file_returns_none(self):
        col = _auto_detect_identifier_column("/nonexistent.py", 0)
        assert col is None

    def test_blank_line_returns_none(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("\n\n\n")
        col = _auto_detect_identifier_column(str(f), 1)
        assert col is None or col == 0
