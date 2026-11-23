"""Regression: LSP-TextEdits rechnen in UTF-16 und nur mit \\n-Zeilen (F14).

0.6.40 nutzte ``str``-Offsets und ``splitlines(keepends=True)``. Ein Emoji vor
der Spalte verschob den Edit um eins (der Rename schrieb an die falsche Stelle,
``status: ok``), und ein Formfeed im Text erzeugte zusätzliche „Zeilen", weil
``splitlines`` an ``\\x0b``, ``\\x0c``, ``\\u2028`` und ``\\x85`` trennt.
"""

from __future__ import annotations

from code_intel.lsp.bridge.server import _apply_edits_by_file, _line_starts, _utf16_col_to_index


def _edit(line: int, ch: int, end_ch: int, new_text: str) -> dict:
    return {"range": {"start": {"line": line, "character": ch},
                      "end": {"line": line, "character": end_ch}},
            "newText": new_text}


def test_utf16_column_conversion_handles_astral_chars():
    line = 'x = "😀"; y = 1'
    assert _utf16_col_to_index(line, 0) == 0
    # 'y' steht in UTF-16 auf Spalte 10, in Python auf Index 9
    assert _utf16_col_to_index(line, 10) == 9
    assert line[_utf16_col_to_index(line, 10)] == "y"


def test_utf16_column_conversion_beyond_end():
    assert _utf16_col_to_index("abc", 99) == 3


def test_line_starts_ignores_formfeed():
    content = "a = 1\n\x0cb = 2\n"
    assert _line_starts(content) == [0, 6, 13]


def test_edit_after_emoji_hits_the_right_character(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text('x = "😀"; y = 1\n', encoding="utf-8")

    result = _apply_edits_by_file({str(target): [_edit(0, 10, 11, "z")]})
    assert result[0]["status"] == "ok"
    assert target.read_text(encoding="utf-8") == 'x = "😀"; z = 1\n'


def test_formfeed_does_not_shift_line_numbers(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("a = 1\n\x0cb = 2\n", encoding="utf-8")

    # LSP-Zeile 1 ist "\x0cb = 2" (nur \n trennt) -> 'b' steht auf Spalte 1
    result = _apply_edits_by_file({str(target): [_edit(1, 1, 2, "c")]})
    assert result[0]["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "a = 1\n\x0cc = 2\n"


def test_multiple_edits_same_file_keep_offsets(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("alpha = 1\nbeta = 2\n", encoding="utf-8")
    result = _apply_edits_by_file({str(target): [
        _edit(0, 0, 5, "gamma"),
        _edit(1, 0, 4, "delta"),
    ]})
    assert result[0]["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "gamma = 1\ndelta = 2\n"
