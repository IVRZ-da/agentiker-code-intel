"""Tests: LSP-Server-stderr wird nicht mehr verworfen.

Hintergrund: ``stderr=subprocess.DEVNULL`` machte jeden Startfehler unsichtbar.
Es blieb nur "LSP server unavailable" und der Text-Fallback lief still weiter —
die Ursache (kaputte Workspace-Config, fehlende Grammatik, nicht ladbare Lib)
stand ausschliesslich auf dem verworfenen stderr. Diese Tests halten fest, dass
die letzten Zeilen in der Fehlermeldung landen.
"""

from __future__ import annotations

import logging
import os
import sys

from unittest.mock import MagicMock, patch

from code_intel.lsp_bridge import LSPBridge


def _make_bridge(language_id="python", root="/tmp", command="", args=None) -> LSPBridge:
    return LSPBridge(
        command=command,
        args=args or [],
        root_uri=root,
        language_id=language_id,
    )


class TestStderrTail:
    def test_ohne_pfad_liefert_leer(self):
        bridge = _make_bridge()
        assert bridge._stderr_tail() == ""

    def test_fehlende_datei_liefert_leer(self, tmp_path):
        bridge = _make_bridge()
        bridge._stderr_path = str(tmp_path / "gibt-es-nicht.log")
        assert bridge._stderr_tail() == ""

    def test_liefert_letzte_zeilen(self, tmp_path):
        pfad = tmp_path / "srv.stderr.log"
        pfad.write_text("zeile1\nzeile2\nzeile3\n", encoding="utf-8")
        bridge = _make_bridge()
        bridge._stderr_path = str(pfad)
        assert bridge._stderr_tail(max_lines=2) == "zeile2 | zeile3"

    def test_leere_datei_liefert_leer(self, tmp_path):
        pfad = tmp_path / "leer.log"
        pfad.write_text("", encoding="utf-8")
        bridge = _make_bridge()
        bridge._stderr_path = str(pfad)
        assert bridge._stderr_tail() == ""


class TestStartfehlerLoggtStderr:
    def test_exit_beim_start_nennt_stderr_inhalt(self, tmp_path, caplog):
        """Ein Server, der sofort mit einer Fehlermeldung stirbt, macht sie sichtbar."""
        skript = "import sys; sys.stderr.write('BOOM-STDERR\\n'); sys.exit(3)"
        bridge = _make_bridge(command=sys.executable, args=["-c", skript], root=str(tmp_path))

        with patch("code_intel.lsp_bridge._resolve_command", return_value=sys.executable):
            with caplog.at_level(logging.ERROR):
                ergebnis = bridge._start_and_init()

        assert ergebnis is False
        assert "BOOM-STDERR" in caplog.text
        # Der Pfad steht in der Meldung, damit man die volle Datei aufmachen kann.
        assert bridge._stderr_path in caplog.text

    def test_stderr_datei_wird_angelegt(self, tmp_path):
        skript = "import sys; sys.stderr.write('HALLOWELT\\n'); sys.exit(1)"
        bridge = _make_bridge(command=sys.executable, args=["-c", skript], root=str(tmp_path))

        with patch("code_intel.lsp_bridge._resolve_command", return_value=sys.executable):
            bridge._start_and_init()

        assert bridge._stderr_path is not None
        assert os.path.exists(bridge._stderr_path)
        assert "HALLOWELT" in bridge._stderr_tail()


class TestStderrLebenszyklus:
    def test_shutdown_schliesst_die_datei(self, tmp_path):
        bridge = _make_bridge(root=str(tmp_path))
        pfad = tmp_path / "srv.log"
        bridge._stderr_path = str(pfad)
        bridge._stderr_file = open(pfad, "ab", buffering=0)
        handle = bridge._stderr_file
        bridge._alive = True
        bridge._process = MagicMock()
        bridge._process.poll.return_value = None

        bridge.shutdown()

        assert bridge._stderr_file is None
        assert handle.closed is True
        # Der Pfad bleibt fuer Fehlermeldungen erhalten.
        assert bridge._stderr_path == str(pfad)

    def test_zu_grosse_datei_wird_vor_dem_start_gekuerzt(self, tmp_path):
        """Die Datei waechst ueber die Prozesslaufzeit nicht unbegrenzt."""
        bridge = _make_bridge(root=str(tmp_path), language_id="python")
        skript = "import sys; sys.exit(0)"
        from code_intel.lsp.bridge.server import _stderr_log_path
        erwarteter_pfad = _stderr_log_path("python", str(tmp_path))
        with open(erwarteter_pfad, "wb") as f:
            f.write(b"x" * 70000)

        with patch("code_intel.lsp_bridge._resolve_command", return_value=sys.executable):
            bridge = _make_bridge(command=sys.executable, args=["-c", skript], root=str(tmp_path))
            bridge._start_and_init()

        assert os.path.getsize(erwarteter_pfad) < 65536
