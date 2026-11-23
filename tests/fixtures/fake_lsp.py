"""Minimaler Fake-Language-Server für die Bridge-Tests.

Aufruf: python fake_lsp.py <modus>

  quiet      — liest stdin nicht, antwortet nicht (simuliert hängenden Server)
  badheader  — schreibt einen kaputten Content-Length-Header und wartet
  wellbehaved— beantwortet initialize/shutdown korrekt
"""

from __future__ import annotations

import json
import sys
import time


def _read_message(stream):
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii", "replace").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", 0))
    return json.loads(stream.read(length).decode("utf-8"))


def _write_message(stream, msg):
    body = json.dumps(msg).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "quiet"
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    if mode == "quiet":
        while True:
            time.sleep(0.2)
        return 0

    if mode == "badheader":
        stdout.write(b"Content-Length: kaputt\r\n\r\n")
        stdout.flush()
        while True:
            time.sleep(0.2)
        return 0

    if mode == "wellbehaved":
        while True:
            msg = _read_message(stdin)
            if msg is None:
                return 0
            method = msg.get("method")
            msg_id = msg.get("id")
            if msg_id is not None and method == "initialize":
                # Wie pyright: erst eine Server-Anfrage, dann die Antwort.
                _write_message(stdout, {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "workspace/configuration",
                    "params": {"items": [{"section": "python"}, {"section": "editor"}]},
                })
                # Auf die Antwort warten, bevor initialize beantwortet wird.
                while True:
                    reply = _read_message(stdin)
                    if reply is None:
                        return 0
                    if reply.get("id") == 1 and "result" in reply:
                        break
                _write_message(stdout, {
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"capabilities": {}},
                })
            elif msg_id is not None and method == "shutdown":
                _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": None})
            elif method == "exit":
                return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
