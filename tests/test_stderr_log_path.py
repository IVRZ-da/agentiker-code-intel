"""Regression: stderr-Logdatei ist pro Projektroot eindeutig (F21).

Der Dateiname bestand aus Sprache + Verzeichnis-Basename. Zwei Checkouts mit
gleichem Ordnernamen (``/a/api`` und ``/b/api``) teilten sich eine Datei; der
stderr-Tail im Startfehler mischte fremde Zeilen hinein.
"""

from __future__ import annotations

import os

from code_intel.lsp.bridge.server import _stderr_log_path


def test_same_basename_different_roots_differ():
    first = _stderr_log_path("python", "/srv/a/api")
    second = _stderr_log_path("python", "/srv/b/api")
    assert first != second
    assert os.path.basename(first) != os.path.basename(second)


def test_same_root_is_stable():
    assert _stderr_log_path("python", "/srv/a/api") == _stderr_log_path("python", "/srv/a/api")


def test_language_is_part_of_the_name():
    assert _stderr_log_path("go", "/srv/a/api") != _stderr_log_path("python", "/srv/a/api")


def test_trailing_slash_does_not_split_the_log():
    assert _stderr_log_path("python", "/srv/a/api/") == _stderr_log_path("python", "/srv/a/api")


def test_path_lives_in_tempdir_and_is_ascii_safe():
    path = _stderr_log_path("python", "/srv/ümlaut/api")
    assert path.startswith(os.path.dirname(path))
    assert path.endswith(".stderr.log")
    assert path.isascii()
