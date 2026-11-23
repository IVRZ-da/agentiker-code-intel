"""Regression: Reader-Tod lässt keinen Language-Server zurück (F11).

0.6.38 setzte im ``finally`` nur ``_alive = False``. Der Prozess lief weiter,
der nächste Start überschrieb ``self._process`` — der alte Server war eine
Waise, die auch ``shutdown()`` nicht mehr erreichte.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from code_intel.lsp.bridge.server import LSPBridge

FIXTURE = Path(__file__).parent / "fixtures" / "fake_lsp.py"


def _spawn(mode: str):
    return subprocess.Popen(
        [sys.executable, str(FIXTURE), mode],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


def _wait_dead(proc, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return proc.poll() is not None


def test_broken_header_kills_the_process(tmp_path):
    bridge = LSPBridge(command=sys.executable, args=[str(FIXTURE), "badheader"],
                       root_uri=str(tmp_path), language_id="python")
    proc = _spawn("badheader")
    bridge._process = proc
    bridge._alive = True

    reader = threading.Thread(target=bridge._read_loop, daemon=True)
    reader.start()
    reader.join(timeout=10)

    assert not reader.is_alive(), "Reader lief nach kaputtem Content-Length weiter"
    assert _wait_dead(proc), "Language-Server lebt als Waise weiter"
    assert bridge._process is None, "Prozess-Referenz muss nach dem Reap leer sein"


def test_eof_kills_the_process(tmp_path):
    """Auch ein sauberes EOF (Server beendet sich) darf keine Reste lassen."""
    bridge = LSPBridge(command=sys.executable, args=[str(FIXTURE), "badheader"],
                       root_uri=str(tmp_path), language_id="python")
    proc = _spawn("badheader")
    bridge._process = proc
    bridge._alive = True
    bridge._process.stdout.close()  # EOF für den Reader

    reader = threading.Thread(target=bridge._read_loop, daemon=True)
    reader.start()
    reader.join(timeout=10)
    assert _wait_dead(proc)


def test_shutdown_does_not_wait_for_a_dead_reader(tmp_path):
    """Regression: ``_alive`` wurde vor dem Handshake gelöscht -> 5 s Timeout."""
    bridge = LSPBridge(command=sys.executable, args=[str(FIXTURE), "wellbehaved"],
                       root_uri=str(tmp_path), language_id="python")
    bridge._process = _spawn("wellbehaved")
    bridge._alive = True
    bridge._initialized = True
    bridge._reader_thread = threading.Thread(target=bridge._read_loop, daemon=True)
    bridge._reader_thread.start()

    started = time.monotonic()
    bridge.shutdown()
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"shutdown() brauchte {elapsed:.1f}s (Handshake lief ins Timeout)"
    assert bridge._process is None


@pytest.mark.parametrize("mode", ["badheader"])
def test_reader_marks_bridge_dead(tmp_path, mode):
    bridge = LSPBridge(command=sys.executable, args=[str(FIXTURE), mode],
                       root_uri=str(tmp_path), language_id="python")
    bridge._process = _spawn(mode)
    bridge._alive = True
    bridge._read_loop()  # synchron: endet beim kaputten Header
    assert bridge._alive is False
