"""Regression: Dispatch trennt Server-Requests von Responses (F8 + F9).

0.6.38 entschied nur ``"id" in msg``. Damit wurde
* eine Server-Anfrage (``workspace/configuration``) mit kollidierender id als
  Antwort konsumiert — der Waiter bekam ``result=None``, der Server blieb
  unbeantwortet und wartete,
* und echte Antworten liefen in den ``method``-Zweig.

Die Tests arbeiten auf einer Bridge ohne Prozess: ``_write_message`` wird
abgefangen, der Reader-Pfad direkt aufgerufen.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from code_intel.lsp.bridge.server import LSPBridge


@pytest.fixture()
def bridge():
    return LSPBridge(command="fake", args=[], root_uri="/tmp", language_id="python")


def test_server_request_does_not_wake_waiter(bridge):
    """Server-Request mit id, die zufällig einem offenen Request entspricht."""
    event = threading.Event()
    bridge._pending[1] = event
    sent = []
    with patch.object(bridge, "_write_message", side_effect=sent.append):
        bridge._dispatch({
            "jsonrpc": "2.0", "id": 1,
            "method": "workspace/configuration",
            "params": {"items": [{"section": "a"}, {"section": "b"}]},
        })
    assert not event.is_set(), "Server-Request darf den Waiter nicht wecken"
    assert sent, "Server-Request muss beantwortet werden"
    assert sent[0]["id"] == 1
    assert sent[0]["result"] == [None, None]
    assert 1 in bridge._pending, "offener eigener Request bleibt offen"


def test_real_response_wakes_waiter(bridge):
    event = threading.Event()
    bridge._pending[7] = event
    with patch.object(bridge, "_write_message") as write:
        bridge._dispatch({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})
    assert event.is_set()
    assert bridge._responses[7] == {"ok": True}
    write.assert_not_called()


def test_unknown_server_request_gets_null(bridge):
    sent = []
    with patch.object(bridge, "_write_message", side_effect=sent.append):
        bridge._dispatch({"jsonrpc": "2.0", "id": 42, "method": "$/setTrace", "params": {}})
    assert sent[0] == {"jsonrpc": "2.0", "id": 42, "result": None}


def test_notification_is_not_answered(bridge):
    sent = []
    with patch.object(bridge, "_write_message", side_effect=sent.append):
        bridge._dispatch({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                          "params": {"uri": "file:///tmp/x.py", "diagnostics": []}})
    assert sent == [], "Notifications dürfen keine Antwort erzeugen"


def test_apply_edit_request_is_declined(bridge):
    sent = []
    with patch.object(bridge, "_write_message", side_effect=sent.append):
        bridge._dispatch({"jsonrpc": "2.0", "id": 3, "method": "workspace/applyEdit",
                          "params": {"edit": {}}})
    assert sent[0]["result"] == {"applied": False}


def test_response_without_id_is_ignored(bridge):
    with patch.object(bridge, "_write_message") as write:
        bridge._dispatch({"jsonrpc": "2.0", "result": None})
    write.assert_not_called()


def test_initialize_with_server_side_configuration_request(tmp_path):
    """End-to-End: der Server fragt ``workspace/configuration`` mit id=1 — genau
    der id, die unser ``initialize`` belegt. Ohne F8/F9 läuft das in den
    Request-Timeout, weil die Antwort als eigener Request verrechnet wird."""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).parent / "fixtures" / "fake_lsp.py"
    bridge = LSPBridge(
        command=sys.executable,
        args=[str(script), "wellbehaved"],
        root_uri=str(tmp_path),
        language_id="python",
    )
    bridge._process = subprocess.Popen(
        [sys.executable, str(script), "wellbehaved"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    bridge._alive = True
    bridge._reader_thread = threading.Thread(target=bridge._read_loop, daemon=True)
    bridge._reader_thread.start()
    try:
        result = bridge._send_request("initialize", {"capabilities": {}}, timeout=5)
        assert result == {"capabilities": {}}
    finally:
        bridge._alive = False
        bridge._reap_process()
