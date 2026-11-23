"""Regression: der Verzeichnis-Cache sieht neue Dateien (F22).

Der Cache prüfte nur die mtimes der beim Aufbau bekannten Dateien. Eine neu
angelegte Datei blieb unsichtbar, bis eine bekannte Datei sich änderte —
``file_count`` stand weiter auf 1.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from code_intel.tools import cache as cache_mod
from code_intel.tools.symbols_extractor import _symbols_scan_directory


@pytest.fixture(autouse=True)
def _clear_caches():
    cache_mod._DIR_SYMBOL_CACHE.clear()
    cache_mod._SYMBOL_CACHE.clear()
    yield
    cache_mod._DIR_SYMBOL_CACHE.clear()
    cache_mod._SYMBOL_CACHE.clear()


def _scan(path) -> dict:
    return json.loads(_symbols_scan_directory(Path(path), None, None, None, _raw=True))


def test_new_file_is_picked_up(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    assert _scan(tmp_path)["file_count"] == 1

    time.sleep(0.01)
    (tmp_path / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    assert _scan(tmp_path)["file_count"] == 2, "neue Datei muss beim zweiten Scan erscheinen"


def test_deleted_file_is_dropped(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    assert _scan(tmp_path)["file_count"] == 2

    (tmp_path / "b.py").unlink()
    assert _scan(tmp_path)["file_count"] == 1


def test_unchanged_directory_hits_the_cache(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    first = _scan(tmp_path)
    second = _scan(tmp_path)
    assert first == second
    assert cache_mod._DIR_SYMBOL_CACHE, "Cache muss gefüllt sein"


def test_modified_file_invalidates(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    assert _scan(tmp_path)["total_symbols"] == 1

    time.sleep(0.01)
    target.write_text(
        "def alpha():\n    return 1\n\n\ndef gamma():\n    return 3\n", encoding="utf-8"
    )
    assert _scan(tmp_path)["total_symbols"] == 2
