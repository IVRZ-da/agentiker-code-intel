# Runde 6 — Kandidaten verifiziert (2026-11-23)

Die sechs Punkte, die nach Runde 5 unter „nicht verifiziert“ standen. Jeder wurde mit
einem eigenen Lauf geprüft; die Rohskripte liegen als `/tmp/r6_probe.py` und
`/tmp/r6_probe2.py`. Urteil: **5 verifiziert, 1 Verdacht, 0 widerlegt.**

---

## K1 — `RLIMIT_CPU=60` als Lebenszeitbudget — VERIFIZIERT (P3)

`lsp/bridge/server.py:606` setzt im Kindprozess `RLIMIT_CPU = (60, 60)`.

Beleg:
- Prozess mit `RLIMIT_CPU=(1,1)`, ohne Signal-Handler → **tödliches Signal 24
  (SIGXCPU)**, Laufzeit 1,0 s.
- Mit eigenem `SIGXCPU`-Handler → Handler greift (rc=99).

Folge: Ein Language-Server, der mehr als 60 s **CPU-Zeit** braucht (pyright-Index bei
großen TS-Projekten, gopls bei Monorepos), stirbt mitten im Betrieb. `_reap_process()`
räumt auf, loggt aber nur in die stderr-Datei; der Aufrufer sieht ausschließlich einen
Request-Timeout und kann SIGXCPU nicht von „Server hängt“ unterscheiden.

Fix-Vorschlag: Hard-Limit höher als Soft-Limit (`(120, 180)`) oder konfigurierbar, und
im Timeout-Fehler den exitcode/`_stderr_tail()` mitgeben.

---

## K2 — Env-Allowlist zu eng — VERIFIZIERT (P2)

`lsp/bridge/server.py:24-32` — 23 Namen, keine Toolchain-Credentials/CA-Pfade.

Fehlt nachweislich: `GOPRIVATE`, `SSL_CERT_FILE`, `SSH_AUTH_SOCK`, `GIT_SSL_CAINFO`
(und `TSS_LOG` wird nur für TS gesetzt, nie durchgereicht).

Folge: gopls kann private Module (`git.agentiker.de/...` in agentic-shop) nicht laden,
weil `GOPRIVATE` fehlt; bei Corporate-CA/Proxy bricht jeder HTTPS-Import im Server ab,
obwohl die Shell korrekt konfiguriert ist. Die Allowlist entstand als Fix für „Voll-Env
an Language-Server“ (0.6.37) — sie ist jetzt zu eng statt zu weit.

Fix-Vorschlag: `GOPRIVATE`, `GONOSUMDB`, `GONOSUMCHECK`, `GOPROXY`, `GOSUMDB`,
`SSL_CERT_FILE`, `SSL_CERT_DIR`, `GIT_SSL_CAINFO`, `SSH_AUTH_SOCK`, `HTTP(S)_PROXY`,
`NO_PROXY` aufnehmen (bewusst, nicht pauschal).

---

## K3 — `conftest`-Mock für `code_intel._fmt` — VERIFIZIERT (P2)

`tests/conftest.py:29-60` ersetzt `sys.modules["code_intel._fmt"]` durch ein Modul,
dessen `fmt_ok`/`fmt_err`/`fmt_info` **JSON-Strings** liefern.

Beleg (gleicher Aufruf `fmt_ok({"a": 1})`):
- echt: `╭──── ✅ Success ────╮ …` (Rich-Panel)
- Mock: `{"status": "ok", "a": 1}`

Folge: Kein Test sieht je die echte Tool-Ausgabe. Ob ein Tool JSON, ein Panel oder
Prosa liefert — was der Vertrag nach außen ist — bleibt in 3366 Tests unsichtbar. Genau
deshalb konnte F16/F18 (usage/pre_llm_call-Format) unbemerkt bleiben.

Fix-Vorschlag: Mock nur in den Tests aktivieren, die ihn brauchen (Fixture statt
`sys.modules`-Ersatz beim Import), plus eine kleine Gruppe von Vertragstests gegen die
echte `_fmt`-Ausgabe.

---

## K4 — `_glob_to_extensions` ohne Brace-Globs — VERIFIZIERT (P2)

`tools/batch.py::_glob_to_extensions`.

Beleg: `_glob_to_extensions("**/*.{ts,tsx}")` → `{'.{ts,tsx}'}` statt `{'.ts', '.tsx'}`.
Gegenprobe im Verzeichnis mit `a.ts` und `b.tsx`: der resultierende Filter erfasst
**0 von 2** Dateien.

Folge: Stiller Totalausfall. Wer `globs: "**/*.{ts,tsx}"` übergibt (übliches Muster),
bekommt „keine Treffer“ ohne Fehlermeldung — und hält es für ein korrektes Ergebnis.

Fix-Vorschlag: Brace-Expansion in `_glob_to_extensions` (rekursiv auf `{a,b}`), und
wenn nach der Expansion keine Extension übrig bleibt: laute Warnung statt leerer Menge.

---

## K5 — YAML-Typ-Coercion in Migrationsregeln — VERIFIZIERT (P3)

`tools/migration.py::_load_rules_from_yaml` gibt `data["rules"]` unverändert zurück;
`yaml.safe_load("pattern: 1")` liefert `int`.

Beleg: Regeldatei mit `pattern: 1` / `rewrite: 2` →
`TypeError: 'int' object is not subscriptable`. Dieselbe Regel als String
(`pattern: '1'`) läuft sauber durch.

Folge: Ein unquotierter YAML-Wert (Zahl, `true`, `null`) bricht den ganzen Lauf mit
einem TypeError ab, der keine Zeile und keine Regel nennt.

Fix-Vorschlag: Beim Laden `str()` auf `pattern`/`rewrite`/`file_glob`-Werte anwenden,
`None` ablehnen, und Fehler mit Regelindex melden.

---

## K6 — `_read_loop`-Race nach Restart — VERDACHT (nicht provoziert, P3)

`lsp/bridge/server.py:648` startet den Reader (`_reader_thread`), dessen `finally`
**unbedingt** `self._alive = False` setzt; ein Generationszähler fehlt.

Theorie: Stirbt der alte Prozess, während bereits ein neuer `start()` läuft, kann das
`finally` des alten Threads den frischen Server als tot markieren → jeder weitere
Request scheitert mit „LSP not running“, obwohl der Prozess lebt.

Nicht reproduziert (Timing). Bleibt als Verdacht für Runde 6; Beleg wäre ein Test mit
künstlich verzögertem `finally` (z. B. via `threading.Event` im Reader).

---

## Summe

| Kandidat | Urteil | Schwere | Aufwand |
|---|---|---|---|
| K1 RLIMIT_CPU | verifiziert | P3 | klein |
| K2 Env-Allowlist | verifiziert | P2 | klein |
| K3 `_fmt`-Mock | verifiziert | P2 | mittel |
| K4 Brace-Globs | verifiziert | P2 | klein |
| K5 YAML-Coercion | verifiziert | P3 | klein |
| K6 Reader-Race | Verdacht | P3 | mittel |
