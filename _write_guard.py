"""Zentraler Schreibschutz für alle code_intel-Schreibpfade.

Hermes-Review (PR #157345, Punkt 3): Jeder Schreibpfad nahm einen vom Modell
gelieferten Pfad ohne Prüfung entgegen. Ein prompt-injizierter Tool-Aufruf
konnte damit ``~/.ssh/authorized_keys``, Shell-rc-Dateien oder Hermes' ``.env``
überschreiben, obwohl die Core-Tools (``write_file``/``patch``) genau das
verweigern.

Diese Datei ist der EINZIGE Ort, an dem die Prüfung passiert. Jeder Handler,
der eine Datei verändert, ruft vorher ``guard_write()`` (und bei Verzeichnis-
Walks zusätzlich ``guard_write_in_root()``) und gibt den Fehlertext zurück,
statt zu schreiben.

Der Guard delegiert an ``agent.file_safety`` aus dem Host — dieselbe Denylist
und derselbe ``HERMES_WRITE_SAFE_ROOT`` wie bei den Core-Tools. Ist das Modul
nicht importierbar (Standalone-Nutzung, Unit-Tests ohne Hermes-Runtime), ist
der Guard bewusst ein No-op: das Plugin soll ohne Host lauffähig bleiben.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Tuple

__all__ = ["guard_write", "guard_write_in_root", "is_within_root"]

_HostGuard = Tuple[Callable[[str], bool], Callable[..., Optional[str]]]


def _host_guard() -> Optional[_HostGuard]:
    """``(is_write_denied, get_write_denied_error)`` aus dem Host, sonst ``None``."""
    try:  # noqa: PLC0415 — Host-Modul, im Standalone-Betrieb nicht vorhanden
        from agent.file_safety import get_write_denied_error, is_write_denied
    except Exception:  # noqa: BLE001 — jedes Import-Problem = kein Host
        return None
    return is_write_denied, get_write_denied_error


def _resolved(path: object) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def guard_write(path: object, *, verb: str = "Write") -> Optional[str]:
    """Fehlertext, wenn ``path`` nicht geschrieben werden darf — sonst ``None``.

    Prüft gegen die Host-Denylist (Credentials, Shell-rc, ``.env``,
    ``HERMES_WRITE_SAFE_ROOT``). Der Pfad wird vorher realpath-aufgelöst, damit
    ein Symlink die Prüfung nicht umgeht.
    """
    resolved = _resolved(path)
    guard = _host_guard()
    if guard is None:
        return None
    is_write_denied, get_write_denied_error = guard
    try:
        if is_write_denied(resolved):
            message = get_write_denied_error(resolved, verb=verb)
            return message or f"{verb} denied: '{path}' is a protected file."
    except Exception:  # noqa: BLE001 — Guard darf den Tool-Aufruf nie sprengen
        return None
    return None


def is_within_root(path: object, root: object) -> bool:
    """``True``, wenn ``path`` (realpath) unter ``root`` (realpath) liegt."""
    resolved = _resolved(path)
    root_resolved = _resolved(root)
    try:
        return os.path.commonpath([resolved, root_resolved]) == root_resolved
    except ValueError:  # unterschiedliche Laufwerke
        return False


def guard_write_in_root(path: object, root: object, *, verb: str = "Write") -> Optional[str]:
    """Wie :func:`guard_write`, zusätzlich Confinement auf ``root``.

    Für Werkzeuge, die über ein Verzeichnis laufen (z. B. ``code_batch_refactor``):
    ein vom Modell gelieferter Pfad darf den Projektbaum nicht verlassen, auch
    wenn die Host-Denylist ihn nicht kennt.
    """
    if not is_within_root(path, root):
        return f"{verb} denied: '{path}' is outside {root}."
    return guard_write(path, verb=verb)


def as_path(path: object) -> Path:
    """Kleiner Helfer für Handler, die ``Path`` und ``str`` gemischt bekommen."""
    return Path(os.path.expanduser(str(path)))
