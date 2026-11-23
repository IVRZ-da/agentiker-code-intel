"""Regression: der Write blockiert nie unter ``_lock`` (F10).

0.6.38 hielt ``_lock`` über ``stdin.write`` + ``flush``. Lief die Pipe voll,
weil der Language-Server nicht las, blockierte der Aufrufer unter dem Lock —
``shutdown()`` und jeder Timeout warteten auf dasselbe Lock. Die Bridge hing
dauerhaft, obwohl ``_LSP_REQUEST_TIMEOUT`` konfiguriert ist.

Jetzt serialisiert eine Queue den Schreibvorgang, ein eigener Thread schreibt;
``_lock`` schützt nur noch den Zustand.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from code_intel.lsp.bridge import server as server_mod
from code_intel.lsp.bridge.server import LSPBridge

FIXTURE = Path(__file__).parent / "fixtures" / "fake_lsp.py"


@pytest.fixture()
def quiet_bridge(tmp_path):
    """Bridge mit einem Server, der stdin nicht liest."""
    bridge = LSPBridge(command=sys.executable, args=[str(FIXTURE), "quiet"],
                       root_uri=str(tmp_path), language_id="python")
    bridge._process = subprocess.Popen(
        [sys.executable, str(FIXTURE), "quiet"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    bridge._alive = True
    yield bridge
    bridge._alive = False
    bridge._reap_process()


def _big_message(size: int = 1_000_000) -> dict:
    return {"jsonrpc": "2.0", "method": "textDocument/didOpen",
            "params": {"text": "x" * size}}


def test_lock_stays_free_while_writer_blocks(quiet_bridge):
    """Der Kern von F10: kein Lock-Halten über den blockierenden Write."""
    quiet_bridge._write_message(_big_message())
    time.sleep(0.2)  # Writer hängt jetzt im Pipe-Write
    assert quiet_bridge._lock.acquire(timeout=2), \
        "_lock wird während des Schreibens gehalten — shutdown() würde hängen"
    quiet_bridge._lock.release()


def test_full_queue_raises_instead_of_hanging(quiet_bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_LSP_WRITE_TIMEOUT", 0.3)
    started = time.monotonic()
    raised = 0
    for _ in range(200):
        try:
            quiet_bridge._write_message(_big_message(200_000))
        except RuntimeError:
            raised += 1
            break
    elapsed = time.monotonic() - started
    assert raised == 1, "volle Queue muss einen Fehler melden, nicht blockieren"
    assert elapsed < 10, f"Abbruch dauerte {elapsed:.1f}s — zu nah an einem Hänger"


def test_sender_does_not_wait_for_the_server(quiet_bridge):
    """Kleine Nachrichten kehren sofort zurück, auch wenn niemand liest."""
    started = time.monotonic()
    quiet_bridge._write_message({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    assert time.monotonic() - started < 1.0


def test_writer_thread_is_daemon_and_single(quiet_bridge):
    quiet_bridge._write_message({"jsonrpc": "2.0", "method": "x", "params": {}})
    first = quiet_bridge._writer_thread
    quiet_bridge._write_message({"jsonrpc": "2.0", "method": "x", "params": {}})
    assert quiet_bridge._writer_thread is first, "zweiter Aufruf darf keinen neuen Thread starten"
    assert first is not None and first.daemon
    assert isinstance(first, threading.Thread)
