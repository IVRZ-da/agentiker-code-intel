"""Regression: Symbol-Signaturen (Bughunt Runde 5, F3).

Vorher splittete der Extraktor auf ``b"\\\\n"`` (Backslash + n) statt auf Newline —
``source_lines`` hatte genau ein Element. Folge: mehrzeilige Symbole ab Zeile > 1
bekamen ``""``, Symbole ab Zeile 0 die komplette Datei. Zusätzlich war
``sig_end == sig_start`` bei einzeiligen Symbolen, also ebenfalls leer.
"""

from __future__ import annotations

from code_intel.tools.symbols_extractor import extract_symbols

SRC = b'''"""Modul-Docstring, darf in keiner Signatur auftauchen."""

CONSTANT = 1


def top_level(x: int) -> int:
    """Eine mehrzeilige Funktion."""
    return x * 2


def one_liner() -> None: pass


class Widget:
    def method(self) -> str:
        return "w"
'''


def _by_name(symbols, name):
    for sym in symbols:
        if sym["name"] == name:
            return sym
    raise AssertionError(f"{name} nicht gefunden: {[s['name'] for s in symbols]}")


def test_multiline_function_gets_its_own_signature():
    syms = extract_symbols(SRC, "python")
    sig = _by_name(syms, "top_level")["signature"]
    assert sig.startswith("def top_level"), sig
    assert "Modul-Docstring" not in sig
    assert sig.count("\n") <= 1, sig


def test_single_line_symbols_have_non_empty_signature():
    syms = extract_symbols(SRC, "python")
    for name in ("one_liner", "CONSTANT", "method"):
        sig = _by_name(syms, name)["signature"]
        assert sig.strip(), f"{name} hat eine leere Signatur"


def test_signature_never_returns_whole_file():
    syms = extract_symbols(SRC, "python")
    lines = SRC.split(b"\n")
    for sym in syms:
        sig = sym["signature"]
        assert len(sig) < len(SRC), f"{sym['name']}: Signatur ist die ganze Datei"
        assert sig.count("\n") <= 1, f"{sym['name']}: {sig!r}"
    assert len(lines) > 5  # Sanity: die Probe ist wirklich mehrzeilig


def test_include_body_is_unaffected():
    syms = extract_symbols(SRC, "python", include_body=True)
    body = _by_name(syms, "one_liner")["body"]
    assert body.startswith("def one_liner")
