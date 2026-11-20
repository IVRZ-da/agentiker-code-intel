"""Validierung von git-Refs, bevor sie in eine argv-Liste wandern.

Hermes-Review PR #157345, Punkt 6: ``base``/``head`` kamen ungeprüft in
``git diff {base}..{head}``. Die Aufrufe laufen zwar als argv-Liste ohne Shell,
aber **git selbst** parst seine Argumente: ein Wert wie ``--output=/tmp/x`` oder
``-p`` wird als Option gelesen, nicht als Ref. Ergebnis: ein Modell-Tool-Call
konnte git-Optionen setzen.

Zwei Schutzschichten:

* :func:`validate_git_ref` — Form-Prüfung (kein führendes ``-``, keine
  Steuerzeichen, kein Leerraum) mit klarer Fehlermeldung.
* ``--end-of-options`` vor dem Ref-Argument — git parst danach nichts mehr als
  Option. Das schützt zusätzlich gegen künftige Schreibweisen.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["validate_git_ref", "check_git_refs"]

# Erlaubte Zeichen in einem Ref/Range: Buchstaben, Zahlen und die ueblichen
# git-Operatoren. Bewusst streng — seltene Sonderformen sind kein Tool-Input.
_ALLOWED = re.compile(r"^[A-Za-z0-9._/~^@{}+\-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def validate_git_ref(ref: object, *, label: str = "git ref") -> Optional[str]:
    """Fehlertext, wenn ``ref`` kein sicherer git-Ref ist — sonst ``None``."""
    if not isinstance(ref, str):
        return f"{label} must be a string, got {type(ref).__name__}"
    value = ref.strip()
    if not value:
        return f"{label} must not be empty"
    if value.startswith("-"):
        return f"{label} '{value}' is not allowed (leading '-')"
    if _CONTROL.search(value) or any(ch.isspace() for ch in value):
        return f"{label} '{value}' contains whitespace or control characters"
    if not _ALLOWED.match(value):
        bad = sorted({ch for ch in value if not re.match(r"[A-Za-z0-9._/~^@{}+\-]", ch)})
        return f"{label} '{value}' contains unsupported characters: {''.join(bad)}"
    return None


def check_git_refs(*, base: str, head: str) -> Optional[str]:
    """Beide Refs prüfen; gibt den ersten Fehler zurück (oder ``None``)."""
    for label, ref in (("base", base), ("head", head)):
        error = validate_git_ref(ref, label=label)
        if error:
            return error
    return None
