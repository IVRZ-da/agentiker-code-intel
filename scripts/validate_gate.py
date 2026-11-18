#!/usr/bin/env python3
"""Gate für `hermes plugins validate` (Hermes-Plugin-Katalog).

Warum: Der Validator ist das CI-Gate des Katalogs, aber ein einzelner roter
Check soll nicht stillschweigend durchgehen. Dieses Skript

* prüft, dass alle Checks tatsächlich vorhanden sind (Schutz gegen Umbenennung),
* schlägt fehl, sobald ein Check rot ist und nicht in der Allowlist steht,
* schlägt fehl, wenn die Allowlist einen inzwischen grünen Check nennt
  (veralteter Eintrag → Allowlist leeren).

Allowlist-Datei: references/core_override_allowlist.txt, eine Zeile je Check:
    check_name: Begruendung
Kommentare mit '#'.

Aufruf: env -u PYTHONPATH <venv>/bin/python scripts/validate_gate.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ALLOWLIST = PLUGIN_DIR / "references" / "core_override_allowlist.txt"

# Checks, deren Existenz das Gate voraussetzt. Ein Hermes-Update, das sie
# umbenennt, muss hier nachgezogen werden — sonst prüft das Gate ins Leere.
REQUIRED_CHECKS = ("declared tools", "declared hooks", "no core override")


def read_allowlist() -> dict[str, str]:
    if not ALLOWLIST.is_file():
        return {}
    allowed: dict[str, str] = {}
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, reason = line.partition(":")
        allowed[name.strip()] = reason.strip()
    return allowed


def run_validator() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # sonst erbt der Validator fremde site-packages
    proc = subprocess.run(
        ["hermes", "plugins", "validate", "--json", str(PLUGIN_DIR)],
        capture_output=True, text=True, timeout=600, env=env,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"✗ validate lieferte kein JSON (exit {proc.returncode})\n"
                 f"stdout: {proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}")


def main() -> int:
    report = run_validator()
    checks = report.get("checks") or []
    if not checks:
        sys.exit("✗ validate lieferte keine Checks (Crash?)")

    by_name = {c.get("name"): c for c in checks}
    missing = [n for n in REQUIRED_CHECKS if n not in by_name]
    if missing:
        sys.exit(f"✗ erwartete Checks fehlen (Hermes-Update?): {missing}")

    allowed = read_allowlist()
    failures: list[str] = []
    stale: list[str] = []

    for name, check in by_name.items():
        ok = bool(check.get("ok"))
        if not ok and name not in allowed:
            detail = str(check.get("detail") or "").replace("\n", " ")[:240]
            failures.append(f"{name}: {detail}")
        if ok and name in allowed:
            stale.append(name)

    if stale:
        print(f"✗ Allowlist nennt inzwischen grüne Checks: {', '.join(stale)}")
        print(f"  → Eintrag in {ALLOWLIST.relative_to(PLUGIN_DIR)} entfernen")
        return 1

    if failures:
        print("✗ validate-Gate fehlgeschlagen:")
        for f in failures:
            print(f"  - {f}")
        return 1

    green = sum(1 for c in by_name.values() if c.get("ok"))
    print(f"✅ validate-Gate: {green}/{len(by_name)} Checks grün"
          + (f" (Allowlist: {', '.join(allowed)})" if allowed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
