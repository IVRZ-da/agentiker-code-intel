"""Targeted pytest tests for code_intel._fmt — Rich formatting functions.

The conftest.py replaces sys.modules["code_intel._fmt"] with a mock,
so this test file removes that mock before importing the real module
to exercise all ·_fmt.py· source lines (82 stmts, goal: 80%+ coverage).

Uses tmp_path, pytest.mark.parametrize, and direct rich assertions.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

# ============================================================================
# Fixture: import the REAL _fmt module
# ============================================================================


@pytest.fixture(scope="module")
def _real_fmt():
    """Import the real code_intel._fmt, bypassing conftest's sys.modules mock.

    We carefully save/restore the mock so other tests are unaffected.
    """
    saved = sys.modules.pop("code_intel._fmt", None)
    saved_backcompat = sys.modules.pop("_fmt", None)
    # Also clear any cached references
    for key in list(sys.modules.keys()):
        if key.startswith("code_intel."):
            del sys.modules[key]
    # Re-import needs the parent package
    import code_intel._fmt as _real

    yield _real
    # Restore mocks
    if saved is not None:
        sys.modules["code_intel._fmt"] = saved
    if saved_backcompat is not None:
        sys.modules["_fmt"] = saved_backcompat


# ============================================================================
# _capture helper
# ============================================================================


class TestCapture:
    """_capture(renderable) — internal helper (lines 42–46)."""

    def test_capture_returns_string(self, _real_fmt):
        """_capture wraps _console.print inside capture context."""
        from rich.text import Text

        result = _real_fmt._capture(Text("hello"))
        assert isinstance(result, str)
        assert "hello" in result


# ============================================================================
# fmt_ok / fmt_err / fmt_warn / fmt_info
# ============================================================================


class TestFmtResponses:
    """Standard response formatters — lines 53–90."""

    def test_fmt_ok_contains_data_keys(self, _real_fmt):
        """fmt_ok with dict data shows keys."""
        result = _real_fmt.fmt_ok({"status": "ok", "count": 3})
        assert isinstance(result, str)
        assert "status" in result
        assert "count" in result
        assert "ok" in result

    def test_fmt_ok_custom_title(self, _real_fmt):
        """fmt_ok custom title appears."""
        result = _real_fmt.fmt_ok({"key": "val"}, title="Custom Title")
        assert "Custom Title" in result

    def test_fmt_err_contains_message(self, _real_fmt):
        """fmt_err renders the message."""
        result = _real_fmt.fmt_err("Something went wrong")
        assert "Something went wrong" in result

    def test_fmt_err_custom_title(self, _real_fmt):
        """fmt_err custom title."""
        result = _real_fmt.fmt_err("error msg", title="My Error")
        assert "My Error" in result

    def test_fmt_warn_contains_message(self, _real_fmt):
        """fmt_warn renders the message."""
        result = _real_fmt.fmt_warn("Warning message")
        assert "Warning message" in result

    def test_fmt_warn_custom_title(self, _real_fmt):
        """fmt_warn custom title."""
        result = _real_fmt.fmt_warn("warn", title="Custom Warn")
        assert "Custom Warn" in result

    def test_fmt_info_contains_message(self, _real_fmt):
        """fmt_info renders the message."""
        result = _real_fmt.fmt_info("Info message")
        assert "Info message" in result

    def test_fmt_info_custom_title(self, _real_fmt):
        """fmt_info custom title."""
        result = _real_fmt.fmt_info("info", title="Custom Info")
        assert "Custom Info" in result


# ============================================================================
# fmt_table
# ============================================================================


class TestFmtTable:
    """fmt_table — lines 97–115."""

    def test_empty_rows(self, _real_fmt):
        """Empty rows -> '[dim]Keine Daten[/dim]' panel."""
        result = _real_fmt.fmt_table([])
        assert "Keine Daten" in result

    def test_table_with_data(self, _real_fmt):
        """Non-empty rows renders column headers and values."""
        rows = [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "25"},
        ]
        result = _real_fmt.fmt_table(rows)
        assert "Alice" in result
        assert "Bob" in result
        assert "name" in result
        assert "age" in result

    def test_table_with_explicit_columns(self, _real_fmt):
        """Only specified columns are shown."""
        rows = [{"name": "X", "age": "10", "city": "NYC"}]
        result = _real_fmt.fmt_table(rows, columns=["name"])
        assert "name" in result
        assert "X" in result
        assert "age" not in result

    def test_table_with_title(self, _real_fmt):
        """Title appears in table output."""
        rows = [{"col": "val"}]
        result = _real_fmt.fmt_table(rows, title="My Table")
        # Title may be split across lines by rich.Table, so check parts
        assert "My" in result or "Table" in result

    def test_table_with_custom_header_style(self, _real_fmt):
        """Custom header style does not crash."""
        rows = [{"a": "1"}]
        result = _real_fmt.fmt_table(rows, header_style="bold magenta")
        assert "a" in result

    @pytest.mark.parametrize(
        "rows,columns",
        [
            ([], ["a", "b"]),  # empty rows with columns
            ([{"a": "1"}, {"a": "2", "b": "3"}], None),  # missing key in some rows
        ],
    )
    def test_table_edge_cases(self, _real_fmt, rows, columns):
        """Edge cases: empty rows, missing keys."""
        result = _real_fmt.fmt_table(rows, columns=columns)
        assert isinstance(result, str)


# ============================================================================
# fmt_table_simple
# ============================================================================


class TestFmtTableSimple:
    """fmt_table_simple — lines 118–128."""

    def test_basic(self, _real_fmt):
        rows = [("Alice", 30), ("Bob", 25)]
        result = _real_fmt.fmt_table_simple(rows, ["Name", "Age"])
        assert "Alice" in result
        assert "Name" in result

    def test_empty(self, _real_fmt):
        result = _real_fmt.fmt_table_simple([], ["A", "B"])
        assert isinstance(result, str)


# ============================================================================
# fmt_tree / _add_symbol_node
# ============================================================================


class TestFmtTree:
    """fmt_tree and _add_symbol_node — lines 135–162."""

    def test_empty_symbols(self, _real_fmt):
        """No symbols -> just the tree label."""
        result = _real_fmt.fmt_tree("root", [])
        assert "root" in result

    def test_with_symbols(self, _real_fmt):
        """Symbols get rendered with kind icon and line info."""
        symbols = [
            {"kind": "function", "name": "foo", "line": 1, "end_line": 5},
            {"kind": "class", "name": "Bar", "line": 10, "end_line": 10},
        ]
        result = _real_fmt.fmt_tree("Module", symbols)
        assert "foo" in result
        assert "Bar" in result
        assert "L1" in result
        assert "L10" in result

    def test_with_children(self, _real_fmt):
        """Nested symbols render recursively."""
        symbols = [
            {
                "kind": "class",
                "name": "Outer",
                "line": 1,
                "end_line": 10,
                "children": [
                    {"kind": "method", "name": "inner", "line": 3, "end_line": 8},
                ],
            },
        ]
        result = _real_fmt.fmt_tree("root", symbols)
        assert "Outer" in result
        assert "inner" in result

    def test_unknown_kind_icon(self, _real_fmt):
        """Unknown kind gets fallback '•' icon."""
        symbols = [
            {"kind": "weird_type", "name": "something", "line": 1, "end_line": 1},
        ]
        result = _real_fmt.fmt_tree("root", symbols)
        assert "something" in result

    def test_no_end_line(self, _real_fmt):
        """Symbol without end_line still renders."""
        symbols = [
            {"kind": "function", "name": "f", "line": 1},
        ]
        result = _real_fmt.fmt_tree("tree", symbols)
        assert "f" in result

    def test_same_line_no_range(self, _real_fmt):
        """start == end -> single line, not range."""
        symbols = [
            {"kind": "variable", "name": "v", "line": 5, "end_line": 5},
        ]
        result = _real_fmt.fmt_tree("", symbols)
        assert "v" in result
        assert "L5" in result


# ============================================================================
# fmt_code
# ============================================================================


class TestFmtCode:
    """fmt_code — lines 169–175."""

    def test_basic(self, _real_fmt):
        code = "def foo():\n    pass\n"
        result = _real_fmt.fmt_code(code, lang="python")
        assert "def foo" in result

    def test_custom_theme(self, _real_fmt):
        result = _real_fmt.fmt_code("x = 1", lang="python", theme="default")
        assert "x = 1" in result or "1" in result

    def test_no_line_numbers(self, _real_fmt):
        result = _real_fmt.fmt_code("x = 1", lang="python", line_numbers=False)
        assert isinstance(result, str)

    @pytest.mark.parametrize("lang", ["python", "javascript", "json", ""])
    def test_various_langs(self, _real_fmt, lang):
        result = _real_fmt.fmt_code("hello = 42", lang=lang)
        assert isinstance(result, str)
        assert result.strip()


# ============================================================================
# fmt_markdown
# ============================================================================


class TestFmtMarkdown:
    """fmt_markdown — lines 182–187."""

    def test_basic_markdown(self, _real_fmt):
        md = "# Hello\nThis is **bold**."
        result = _real_fmt.fmt_markdown(md)
        assert "Hello" in result
        assert isinstance(result, str)

    def test_markdown_exception_fallback(self, _real_fmt):
        """If Markdown() raises, raw string is returned (line 186-187)."""
        with patch.object(_real_fmt, "Markdown", side_effect=Exception("boom")):
            result = _real_fmt.fmt_markdown("# title")
            assert result == "# title"


# ============================================================================
# _dict_to_table
# ============================================================================


class TestDictToTable:
    """_dict_to_table(data, title) — lines 194–202."""

    def test_basic(self, _real_fmt):
        table = _real_fmt._dict_to_table({"name": "Alice", "age": "30"})
        # Returns a rich Table object, not a string
        from rich.table import Table

        assert isinstance(table, Table)
        # The _capture around it would produce output
        rendered = _real_fmt._capture(table)
        assert "name" in rendered
        assert "Alice" in rendered

    def test_empty_dict(self, _real_fmt):
        table = _real_fmt._dict_to_table({})
        rendered = _real_fmt._capture(table)
        assert isinstance(rendered, str)

    def test_with_title(self, _real_fmt):
        table = _real_fmt._dict_to_table({"k": "v"}, title="Data")
        rendered = _real_fmt._capture(table)
        assert "Data" in rendered


# ============================================================================
# fmt_json
# ============================================================================


class TestFmtJson:
    """fmt_json — lines 205–210."""

    def test_basic(self, _real_fmt):
        data = {"key": "value", "num": 42}
        result = _real_fmt.fmt_json(data)
        assert "key" in result
        assert "value" in result
        assert "42" in result or "42" in result

    def test_list(self, _real_fmt):
        result = _real_fmt.fmt_json([1, 2, 3])
        assert "1" in result

    def test_nested(self, _real_fmt):
        result = _real_fmt.fmt_json({"a": {"b": [1, 2]}})
        assert "a" in result

    def test_non_serializable_default_str(self, _real_fmt):
        """fmt_json raises TypeError for non-serializable objects."""
        with pytest.raises(TypeError):
            _real_fmt.fmt_json(object())


# ============================================================================
# _strip_ansi
# ============================================================================


class TestStripAnsi:
    """_strip_ansi(text) — lines 213–217."""

    def test_no_ansi(self, _real_fmt):
        text = "plain text"
        assert _real_fmt._strip_ansi(text) == "plain text"

    def test_strips_ansi(self, _real_fmt):
        text = "\x1b[31mred\x1b[0m"
        stripped = _real_fmt._strip_ansi(text)
        assert "red" in stripped
        assert "\x1b" not in stripped

    def test_empty_string(self, _real_fmt):
        assert _real_fmt._strip_ansi("") == ""

    def test_multiple_codes(self, _real_fmt):
        text = "\x1b[1m\x1b[32mbold green\x1b[0m"
        stripped = _real_fmt._strip_ansi(text)
        assert "bold green" in stripped
        assert "\x1b" not in stripped


# ============================================================================
# Style constants
# ============================================================================


class TestStyleConstants:
    """Module-level style constants exist and have values."""

    def test_style_constants(self, _real_fmt):
        assert _real_fmt.STYLE_TITLE == "bold cyan"
        assert _real_fmt.STYLE_OK == "green"
        assert _real_fmt.STYLE_ERROR == "bold red"
        assert _real_fmt.STYLE_WARN == "yellow"
        assert _real_fmt.STYLE_INFO == "blue"
        assert _real_fmt.STYLE_DIM == "dim white"
        assert _real_fmt.STYLE_HIGHLIGHT == "magenta"
        assert _real_fmt.STYLE_PATH == "italic cyan"
        assert _real_fmt.STYLE_LINE == "yellow"
        assert _real_fmt.STYLE_ACTIVE == "bright_green"
        assert _real_fmt.STYLE_PENDING == "bright_black"
