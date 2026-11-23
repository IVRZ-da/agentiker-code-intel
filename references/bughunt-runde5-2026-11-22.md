# Bughunt Runde 5 — code_intel 0.6.38

Datum: 2026-11-22 · Session `3f4021a5-8f0` · Plan `codeintel-bughunt-2026-11-22`
Basis-Commit: `15eb4cd` (0.6.38) · Fokus: Regressionen der Wellen 0.6.35–0.6.38 und
offene Klassen in LSP-Bridge, Ast-Edit/Write-Guard, Dispatcher.

## Ergebnis

| Severity | Anzahl | Bedeutung |
|---|---|---|
| P0 | 0 | — |
| P1 | 12 | Werkzeug liefert falsche Ergebnisse oder zerstört Daten, ohne zu warnen |
| P2 | 7 | falsches Verhalten in realistischen Randfällen, Workaround vorhanden |
| P3 | 3 | Kosmetik/Cache-Hygiene |

Baseline vor dem Hunt: `3259 passed, 3 failed, 42 skipped, 4 xfailed`, Coverage 82 %.
`ruff check` und `compileall` sauber. Die 22 Findings unten sind alle grün getestet —
die Suite deckt keines davon ab.

## Severity-Roadmap

**Welle 1 — Datenverlust (P1, sofort):**
F1, F2, F3, F4, F6, F7, F12 — Migration/Symbols/Batch-Move/Delete sind nachweislich
defekt. Jeder dieser Pfade meldet Erfolg, während er nichts tut oder Daten entfernt.
F4 (util-linux `sg`) und F1 (`find_all`-API) machen zwei komplette Ops unbenutzbar.

**Welle 2 — Bridge-Stabilität (P1): ✅ gefixt in 0.6.40**
F8, F9, F10, F11 — Request/Response-Verwechslung, unbeantwortete Server-Requests,
Lock über blockierendem Write, Waisenprozess. Diese vier erklären die bisher
unauffälligen „LSP server unavailable"-Fälle.
Umsetzung: `lsp/bridge/server.py` (`_dispatch`, `_answer_server_request`,
Write-Queue + `_writer_loop`, `_reap_process`); Regressionstests
`tests/test_lsp_dispatch_requests.py`, `tests/test_lsp_write_lock.py`,
`tests/test_lsp_reader_kill.py` mit `tests/fixtures/fake_lsp.py`.
Nebenbefund: `shutdown()` verlor die `shutdown`-Response, weil `_alive` zu früh
gelöscht wurde (5 s Timeout pro Stopp) — mitgefixt.

**Welle 3 — stille Vertragsbrüche (P2/P3): ✅ gefixt in 0.6.41**
F5 (in 0.6.39), F13–F22 — Backups über `*.bak`-Symlinks, UTF-16-Positionen,
todo_finder-Rauschen, `usage.py`, Hook-Injection, `op=help`, Zähler,
Symbol-Cache, stderr-Log, Verzeichnis-Cache. Neue Tests: `test_backup_safety.py`,
`test_lsp_apply_edits_utf16.py`, `test_todo_finder_quality.py`,
`test_usage_tracking.py`, `test_namespace_registration.py`,
`test_hook_injection.py`, `test_op_help_contract.py`,
`test_symbol_cache_single.py`, `test_stderr_log_path.py`,
`test_directory_symbol_cache.py`.

---

## P1

### F1 · `code_migration` crasht bei jedem Aufruf — `find_all(pattern)` positional
`tools/migration.py:175`
ast-grep-py 0.51.1 (die im `pyproject` gepinnte Version) hat die Signatur
`find_all(config=None, **rule)`. Der Aufruf übergibt den Pattern-String positionell:
`TypeError: argument 'config': 'str' object is not an instance of 'dict'`.
Fix: `find_all(pattern=pattern)`. Test mit der gepinnten Version.

### F2 · `code_migration` ist zusätzlich ein stiller No-op
`tools/migration.py:180-193`
`m.replace(rewrite)` liefert ein Edit-Objekt, der Rückgabewert wird verworfen;
`write_text(source)` schreibt den unveränderten Originalstring. `file_changes`
wird trotzdem hochgezählt → Report meldet eine Änderung, die nicht stattgefunden hat.
Fix: Edits sammeln, `root.commit_edits(edits)`, danach `root.root().text()` schreiben.

### F3 · `code_symbols`: `split(b"\\n")` und `sig_end == sig_start` — Signaturen leer
`tools/symbols_extractor.py:98` (und `:55-57`)
`source.split(b"\\n")` splittet auf Backslash+n: `source_lines` hat ein Element
(gemessen: 1 bei 46 Zeilen). Mehrzeilige Symbole ab Zeile > 1 bekommen damit `[]` → `""`.
Zusätzlich ist `sig_end = min(end_point[0], sig_start + 2)` bei einzeiligen Symbolen
gleich `sig_start` → ebenfalls leer; bei Symbolen ab Zeile 0 wird das ganze File zur
Signatur. Fix: `splitlines()`, `sig_end = min(end_line, sig_start + 2)`.

### F4 · `code_batch_refactor` hält util-linux `sg` für ast-grep
`tools/batch.py:28-45`
`shutil.which("sg")` findet `/usr/bin/sg` (setgid-Werkzeug, util-linux 2.41.5);
`sg --version` liefert rc=0, die Prüfung greift. Der Scan mit ast-grep-Argumenten
scheitert, `_ast_grep_scan` gibt `[]` zurück, der Aufrufer meldet `success` mit
0 Treffern und schreibt nie. Fix: am stdout auf `ast-grep` prüfen oder `ast_grep_py`
direkt nutzen.

### F5 · Guard fail-open, wenn der Vorab-Scan scheitert
`tools/batch.py:326-336`
`for file_result in (preview or [])` läuft bei `None`/`[]` nie; danach schreibt
`_ast_grep_apply` ungeprüft. Genau der Fall, für den der Guard in 0.6.37 kam.
Fix: bei leerem/`None`-Preview abbrechen (fail closed).

### F6 · `code_move` zerstört das Symbol bei Teilfehler
`tools/ast_edit_tools/move.py:188-194`
Source-Write vor Target-Write, kein Rollback; das Source-Backup ist zu diesem
Zeitpunkt schon gelöscht. Fix: Target zuerst, Source erst nach Erfolg; oder beide
Inhalte puffern und im Fehlerfall beide `.bak` zurückspielen.

### F7 · `code_safe_delete` löscht referenzierte Symbole
`tools/ast_edit_tools/safe_delete.py:62`
Referenzsuche läuft nur über `target.parent`. Reproduziert: `helper()` in
`pkg/helper.py` wird gelöscht (`safe: true`), obwohl `other/consumer.py`
`from pkg.helper import helper` enthält. Fix: ab Projektwurzel suchen; ohne
bestimmbare Wurzel konservativ abbrechen.

### F8 · Server→Client-Request mit kollidierender `id` wird als Antwort konsumiert
`lsp/bridge/server.py:845`
`msg["id"] in self._pending` unterscheidet Request und Response nicht. Server-Requests
(`workspace/configuration`, `client/registerCapability`) tragen eigene kleine ids;
bei Kollision bekommt der Waiter `result=None`, der Server wartet auf eine Antwort,
die nie kommt → `initialize timed out`, Prozess wird gekillt.
Fix: `method`-Feld als Unterscheidung, Requests getrennt behandeln.

### F9 · Server→Client-Requests werden nie beantwortet
`lsp/bridge/server.py:849ff`
`_dispatch` kennt nur Methoden ohne id. pyright/gopls warten auf die Antwort auf
`workspace/configuration`, bevor sie Diagnostics liefern → Hang.
Fix: für jeden Request eine Response senden (`result: null` bzw. leere Liste).

### F10 · `_write_message` hält `_lock` über den blockierenden `stdin`-Write
`lsp/bridge/server.py:770-776`
Bei voller Pipe (großes `didOpen`, hängender Server) blockiert der Write unter dem
Lock; `shutdown()` und die Timeouts warten auf dasselbe Lock.
Fix: nur das Serialisieren unter Lock, Write mit Timeout außerhalb.

### F11 · Kaputter `Content-Length`-Header tötet den Reader — Prozess wird zur Waise
`lsp/bridge/server.py:805-840`
`finally` setzt `_alive=False` und weckt Waiter, terminiert den Prozess aber nicht.
Der nächste Start überschreibt `self._process`; der alte Server läuft weiter und
überlebt `shutdown()`. Fix: im `finally` terminieren (`terminate`→`kill`) und
`_process = None`.

### F12 · `$HERMES_HOME` wird im Fallback ignoriert
`_paths.py:27-36`
`_hermes_home()` fällt auf `Path.home()/".hermes"` zurück, ohne `HERMES_HOME` zu
lesen. `plugin_data_dir()` und `cache_dir()` erben den Fehler; der Test
`test_plugin_data_dir_follows_hermes_home` ist rot. Fix: Env-Variable im Fallback
zuerst prüfen.

---

## P2

### F13 · `.bak`-Backups umgehen `guard_write` und folgen Symlinks
`tools/ast_edit_tools/insert.py:78-80` (auch `replace_body.py:118`, `safe_delete.py:113`, `move.py:121`)
`write_bytes` ohne Guard und ohne `is_symlink()`-Check; ein vorhandenes
`mod.py.bak`-Symlink wird mit dem Quelltext überschrieben. Fix: Backup-Pfad prüfen,
Symlinks ablehnen, `tempfile` im Zielverzeichnis nutzen.

### F14 · `_apply_edits_by_file` rechnet in Code-Points mit `splitlines()`
`lsp/bridge/server.py:216-235`
LSP-Positionen sind UTF-16-Code-Units in `\n`-Zeilen. Der Code nutzt str-Offsets
und `splitlines(keepends=True)` (trennt auch `\x0b`, `\x0c`, `\u2028`, `\x85`).
Bei Emoji/Formfeed verschiebt sich der Offset, der Rename schreibt an die falsche
Stelle — mit `status: ok`. Fix: UTF-16-Offsets oder `offsetEncoding: utf-8`.

### F15 · `code_todo_finder` liefert Rauschen und verstümmelt Treffer
`tools/git.py:56-67`
`git grep -i -e TODO …` matcht jede Zeile mit dem Wort als Identifier: 80 Treffer
bei 0 echten TODO-Kommentaren im Repo. Zusätzlich wird das Suchmuster aus dem
Treffertext entfernt (`for pattern in _TODO_PATTERNS:` → `for pattern in :`).
Fix: Kommentar-Präfixe je Sprache verankern, Wortgrenzen, Originalzeile ausgeben.

### F16 · `usage.py` schreibt immer `op=null` und `error=false`
`usage.py:40-49, 64`
Gelesen werden `arguments`/`tool_args` und `error`/`is_error`; der Host liefert
`args` und einen Status. Die Nutzungsmessung aus 0.6.34 ist damit wertlos.
Fix: beide Schlüsselnamen akzeptieren.

### F17 · Hook-Injection läuft nie (`messages` vs. `user_message`)
`hooks.py:143`
`pre_llm_call` liest `kwargs["messages"]`, der Host übergibt `user_message`/
`conversation_history`. Der Hook injiziert nichts und meldet keinen Fehler.
Fix: Fallback auf `user_message`, beide Formen testen.

### F18 · `op=help` ist beworben, fehlt im Schema-Enum
`namespaces.py:316`
Alle sieben Dispatcher-Descriptions nennen `op=help`; das Enum enthält es nicht,
der Aufruf scheitert an der Validierung. Fix: `help` ins Enum, JSON statt Prosa.

### F19 · `register_namespaces` zählt Versuche statt Erfolge
`namespaces.py:345-360`
`register_tool` kann `None` liefern (Namens-Shadowing); der Zähler steigt trotzdem,
die Statuszeile meldet „7 registriert" bei 0 Tools. Fix: Rückgabewert prüfen.

---

## P3

### F20 · Zwei unabhängige `_SYMBOL_CACHE`-Instanzen
`tools/base.py:39` vs. `tools/cache.py:63` — `_invalidate_cache` räumt nur eine auf;
Capsule/Export/Completion liefern nach Edits veraltete Symbole.

### F21 · stderr-Logdatei kollidiert bei gleichem Root-Basename
`lsp/bridge/server.py:1524` — Dateiname aus Sprache + Basename; zwei Projekte mit
gleichem Verzeichnisnamen teilen die Datei, der Tail mischt fremde Fehler hinein.
Fix: kurzen Hash des absoluten Roots anhängen.

### F22 · Verzeichnis-Symbol-Cache bemerkt neue Dateien nie
`tools/symbols.py:215` — geprüft wird nur die mtime bekannter Dateien; eine neue
Datei erscheint nicht (`file_count` bleibt 1 statt 2). Fix: Verzeichnis-mtime oder TTL.

---

## Interop-Befund (außerhalb code_intel)

`analysis(op="deadcode")` und `analysis(op="security")` liefern ausschließlich
`Unknown tool: code_unused_finder` / `code_search_by_error`. Der Alias existiert in
`scout/shared/tool_alias.py:85-86`, greift im analysis-Pfad aber nicht. Ursache bei
scout (Fusion 0.6.25 hat die Einzelnamen entfernt), Wirkung trifft code_intel.
Separat zu behandeln — nicht in diesem Report gefixt.

## Negativbefunde (geprüft, kein Bug)

- Keine Pfad-Traversal-Lücke in `guard_write` selbst: `..`, Symlink-Ausbruch und
  nicht-kanonische Roots werden korrekt abgewiesen.
- Keine Secrets im Repo; alle P0-Scanner-Treffer sind Fixture-Strings in
  `tests/test_code_security.py`.
- `ruff` und `compileall` sauber, keine Syntaxfehler.
- 236 der 258 Session-Findings sind Scanner-Rauschen (triagiert als `false_positive`).

## Nicht verifiziert (Kandidaten für Runde 6)

`_read_loop`-Race nach Restart, `RLIMIT_CPU=60` als Lebenszeitbudget (Server stirbt
nach ~61 s CPU), Env-Allowlist-Lücken (`GOPRIVATE`, `SSL_CERT_FILE`, `TSS_LOG="-"`),
`SIGKILL`-vs-`SIGXCPU`-Semantik, `conftest`-Mock für `code_intel._fmt` (verdeckt den
JSON-Vertrag), `_glob_to_extensions` ohne Brace-Globs, YAML-Typ-Coercion in
Migrationsregeln (`pattern: 1` → int).

## Artefakte

- `/tmp/ci_scans.md` — Baseline, Lint, Scanner-Auswertung
- `/tmp/ci_deep_lsp.md` (420 Z.), `/tmp/ci_deep_edit.md` (461 Z.), `/tmp/ci_deep_dispatch.md` (483 Z.) — Subagenten-Rohreports
- `/tmp/ci_verified.md` — Verifikationsprotokoll
- `/tmp/ci_verify_a.py`, `/tmp/ci_verify_b.py` — ausgeführte Repros
