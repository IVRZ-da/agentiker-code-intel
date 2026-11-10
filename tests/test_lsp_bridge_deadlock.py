"""tests/test_lsp_bridge_deadlock.py — Regression: LSP-Start-Fehler darf nie blockieren.

Bug (gefunden 2026-11-10): ``ensure_initialized()`` haelt ``_init_lock`` und ruft
``_start_and_init()``. Schlug der Start fehl, rief ``_start_and_init()`` ``shutdown()``
auf — das denselben, nicht-reentranten Lock erneut anfordert. Ergebnis: Selbst-Deadlock,
der Tool-Call hing fuer immer (in ``tests/test_test_coverage.py`` als Haenger sichtbar).

Fix: ``_shutdown_locked()`` (ohne Lock) fuer die Fehlerpfade; ``shutdown()`` nimmt
weiterhin den Lock.
"""

from __future__ import annotations

import time
from pathlib import Path

from code_intel.lsp.bridge.server import LSPBridge

MAX_START_SECONDS = 15.0


def _bridge(command: str, root: str = "/tmp") -> LSPBridge:
    return LSPBridge(command=command, args=[], root_uri=root, language_id="python")


class TestStartFailureDoesNotBlock:
    def test_missing_binary_fails_fast(self):
        """Nicht aufloesbares Binary -> False, ohne zu blockieren."""
        bridge = _bridge("definitiv-nicht-vorhanden-xyz-123")
        started = time.monotonic()
        ok = bridge.ensure_initialized()
        elapsed = time.monotonic() - started
        assert ok is False
        assert elapsed < MAX_START_SECONDS, f"ensure_initialized blockierte {elapsed:.1f}s"

    def test_crashing_server_fails_fast(self, tmp_path: Path):
        """Binary startet und exitet sofort -> Fehlerpfad mit _shutdown_locked()."""
        bad = tmp_path / "bad-lsp"
        bad.write_text("#!/bin/sh\nexit 1\n")
        bad.chmod(0o755)

        bridge = _bridge(str(bad), root=str(tmp_path))
        started = time.monotonic()
        ok = bridge.ensure_initialized()
        elapsed = time.monotonic() - started
        assert ok is False
        assert elapsed < MAX_START_SECONDS, f"Fehlerpfad blockierte {elapsed:.1f}s"

    def test_shutdown_after_failed_start_is_idempotent(self, tmp_path: Path):
        bad = tmp_path / "bad-lsp"
        bad.write_text("#!/bin/sh\nexit 1\n")
        bad.chmod(0o755)

        bridge = _bridge(str(bad), root=str(tmp_path))
        bridge.ensure_initialized()

        started = time.monotonic()
        bridge.shutdown()
        bridge.shutdown()
        elapsed = time.monotonic() - started
        assert elapsed < MAX_START_SECONDS


class TestShutdownStructure:
    def test_init_lock_is_reentrant(self):
        """Struktur-Guard: der Init-Lock muss reentrant sein.

        ``_start_and_init()`` laeuft unter ``_init_lock`` und ruft im Fehlerfall
        ``shutdown()``, das den Lock erneut nimmt. Mit ``threading.Lock`` war das
        ein Selbst-Deadlock.
        """
        bridge = _bridge("irrelevant")
        lock = bridge._init_lock
        # threading.RLock ist eine Fabrikfunktion, keine Klasse -> ueber
        # RLock-spezifische Attribute pruefen (_release_save fehlt beim Lock).
        assert hasattr(lock, "_release_save") and hasattr(lock, "_is_owned"), (
            f"_init_lock ist kein RLock, sondern {type(lock).__name__}"
        )
        # Reentrancy beweisen: zweimal acquire im selben Thread darf nicht blockieren.
        assert lock.acquire(timeout=1) is True
        try:
            assert lock.acquire(timeout=1) is True
            lock.release()
        finally:
            lock.release()

    def test_failed_start_leaves_bridge_shut_down(self, tmp_path: Path):
        bad = tmp_path / "bad-lsp"
        bad.write_text("#!/bin/sh\nexit 1\n")
        bad.chmod(0o755)

        bridge = _bridge(str(bad), root=str(tmp_path))
        assert bridge.ensure_initialized() is False
        assert bridge.is_alive is False
