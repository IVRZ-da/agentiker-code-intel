"""Backup-Helfer für die AST-Edit-Ops.

Alle vier Ops legten ihre ``.bak``-Datei direkt mit ``write_bytes`` an: ohne
``guard_write``, ohne Symlink-Prüfung. Ein vorhandenes ``mod.py.bak``-Symlink
wurde damit zum Schreibziel — die „Sicherung" überschrieb eine fremde Datei
(Bughunt-Runde 5, F13). Zusätzlich schrieben die Rollback-Zweige die
Originalbytes erneut ins Backup statt in die beschädigte Datei.

Beides läuft jetzt hier zentral.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..._write_guard import guard_write

logger = logging.getLogger(__name__)


def backup_path_for(target: Path) -> Path:
    """Pfad der Sicherungsdatei neben ``target``."""
    return target.with_suffix(target.suffix + ".bak")


def create_backup(target: Path, source_bytes: bytes) -> Optional[str]:
    """Schreibt ``source_bytes`` nach ``<target>.bak``.

    Returns:
        Fehlertext, wenn die Sicherung nicht angelegt werden darf/durfte —
        sonst ``None``.
    """
    backup = backup_path_for(target)
    if backup.is_symlink():
        return f"Backup denied: '{backup}' is a symlink."
    denied = guard_write(backup, verb="Backup")
    if denied:
        return denied
    try:
        backup.write_bytes(source_bytes)
    except OSError as exc:
        return f"Cannot create backup: {exc}"
    return None


def restore_from_backup(target: Path, source_bytes: bytes) -> Optional[str]:
    """Rollback: schreibt die Originalbytes zurück in ``target``."""
    try:
        target.write_bytes(source_bytes)
    except OSError as exc:
        return f"Cannot restore file: {exc}"
    return None


def drop_backup(target: Path) -> None:
    """Entfernt die Sicherungsdatei nach erfolgreichem Schreiben."""
    try:
        backup_path_for(target).unlink()
    except OSError as exc:
        logger.debug("cleanup backup unlink (%s): %s", target.name, exc)
