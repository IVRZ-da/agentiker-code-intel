# Roadmap: Code-Verständnis mit leistungsschwachen Modellen

Ziel: `agentiker-code-intel` soll auch mit 4B–30B-/Flash-Tier-Modellen zuverlässig
Code verstehen — messbar, nicht gefühlt.

Ausgangslage: v0.6.31 (7 Namespace-Dispatcher, 4.788 Schema-Tok/Turn, ANSI-Ausgaben,
Profile wirkungslos, Benchmark ohne Diskrimination).
Analyse: `~/code-intel-schwache-modelle.md` · Evidenz: `ci-research-a.md` / `-b.md` ·
Messwerte: `ci-inventory.md`.

Prinzipien für die Umsetzung:
- Jede Änderung ein Release in 0.00.01-Schritten, Pre-Commit-Hook als CI-Ersatz.
- Jede Änderung hat einen prüfbaren Verify, der fehlschlagen kann.
- Eine Achse pro Messung. Erst messen, dann behaupten.
- Kein Retrieval-/Index-Ausbau, solange Interface und Rückmeldung nicht sauber sind.

---

## Übersicht

| Phase | Inhalt | Releases | Aufwand | Gate |
|---|---|---|---|---|
| 0 | Fundament: Op-Metadaten-Registry | 0.6.32 | 1 JD | Registry deckt alle 70 Ops |
| 1 | Messinstrument (bench-Repo) | bench 0.3.0 | 5 JD | Pass-Raten im Band, Localization-Metrik aktiv, Kontrollarm schlägt reproduzierbar schlechter |
| 2 | Interface + Output | 0.6.33–0.6.36 | 3,5 JD | Audit ≤ 2.100 Tok, 0 ANSI im Default, jeder Fehler mit `fix.call` |
| 3 | Verifier-Loop | 0.6.37 | 1 JD | Edit liefert Diagnostics im selben Call |
| 4 | Profile + Kontext | 0.6.38–0.6.40 | 3,5 JD | `weak`-Profil 4 Tools / < 1.200 Tok |
| 5 | Messung + Entscheidung | 0.7.0 | 3 JD | vorab festgelegte MDE-basierte Schwellen (§5) |
| 6 | Nur mit Beleg | offen | — | Evidenz vorhanden |

Gesamt bis Gate 5: ~18 JD (Single Source of Truth; frühere 14 JD waren zu knapp kalkuliert —
Kalibrierung, Judge-Setup und Auswertung waren nicht eingerechnet).
Phase 1 vor Phase 2 (ohne Diskrimination ist kein Effekt belegbar).

---

## Phase 0 — Fundament: Op-Metadaten-Registry

Ohne eine maschinenlesbare Beschreibung „welcher op braucht welche Felder" sind
Schema-Narrowing, `op=help` und `fix.call` drei getrennte Basteleien. Deshalb zuerst
eine Quelle der Wahrheit.

### 0.6.32 — `tools/op_meta.py`

- Eine Tabelle pro op:
  `{"tier": 1|2|3, "required": [...], "optional": [...], "caps": {"max_items": 50, "max_tokens": 1500}, "example": {...}, "returns": "...", "next": [...]}`
  (`caps` gehört in Phase 0, weil 0.6.36 die Caps aus der Registry liest — ohne das Feld
  wäre Phase 2 auf eine zweite Registry-Änderung angewiesen.)
- `tier 1` = der Alltagsfall (path/line/query), `tier 2` = nützlich, `tier 3` = Spezialfall.
- Quelle: die bestehenden Handler-Schemas in `tools/*.py` (nicht neu erfinden, generieren
  und dann kuratieren).
- Konsistenztest: jeder registrierte op hat einen Eintrag; jeder Eintrag einen Handler.

**Dateien:** `tools/op_meta.py` (neu), `tests/test_op_meta.py` (neu), `namespaces.py`,
`scripts/assert_audit.py` (neu: Gate-Skript, das `tool_schema_audit.py --json` auswertet und
mit Exit ≠ 0 abbricht, wenn ein Budget verletzt ist).
**DoD:** `pytest tests/test_op_meta.py` grün; Abdeckung 70/70 Ops; `op=help`-Text pro op
aus der Registry generierbar.
**Verifikation:** `python3 scripts/assert_audit.py --max-eager 4900 --max-tool-tokens code_edit=1000`
(Exit 0 = Budget gehalten, Exit 1 = verletzt) und `pytest -q tests/test_op_meta.py`.
Prosa-Gates wie „unverändert 4.788 Tok" sind bewusst ersetzt: das Audit-Skript druckt nur
JSON und exitet immer 0, ist also als Gate untauglich.
**Risiko:** Registry veraltet, wenn neue Ops ohne Eintrag entstehen → Pre-Commit-Check
„op register ohne Meta = Fehler".

---

## Phase 1 — Messinstrument (Repo `~/agentiker-plugins-bench`)

Ohne diesen Schritt ist jede folgende Änderung unbelegt. Die 28 vorhandenen Tasks
bleiben als Regressionssuite, taugen aber nicht als Messinstrument (100 % Pass).

### bench 0.3.0 — Diskrimination + Reliabilität

1. **Task-Kalibrierung.** Kandidaten aus echten Merges (letzte gemergte PRs der eigenen
   Repos, revisionsgepinnt), Kalibrierungslauf gegen 3 Modelle. Band **pro Zielmodell**
   definieren (das schwache Modell darf bei 20–80 % liegen, das starke bei 60–95 % —
   aggregierte Bänder verwischen genau den Unterschied, den wir messen).
   Leakage-Check: PR-Datum vs. Modell-Cutoff; bei Verdacht auf fremde Repos ausweichen.
   **k ≥ 3 Runs pro (Task, Modell)**, `p_i` mit Wilson-CI berichten; nur Tasks behalten,
   deren CI **vollständig** im Band 0,20–0,80 liegt. Bei 1 Run ist `p_i` bei n = 3 ein
   Münzwurf (SE ≈ 0,29) — dann filtert die Kalibrierung nichts.
   Ziel: 40 Tasks. Nicht jeder gemergte PR ergibt einen Code-Verständnis-Task — Vorab-Filter
   (Diff berührt ≥ 2 Symbole, Frage formulierbar) einplanen.
2. **Localization-Metrik.** Verifier liefert zusätzlich
   `loc: {file_hit, symbol_hit, line_band_hit}` + `turns`, `tool_calls`, `tokens_in/out`,
   `cpst`. Getrennt von Pass/Fail speichern, nie hineinrechnen.
3. **`k` Wiederholungen** (k = 3) pro (Task, Modus) → `pass@1` und `pass^k`.
4. **AGS v2** in `sweep_runner.py`/Report: Risiko-Differenz in pp + normierter Gain
   (nur wenn `p_base < 1`; bei `p_base = 1` → `n/a`, eigene Kategorie „gesättigt", nie als
   0 mitteln). **Headline-Intervall ist der gepaarte Bootstrap-CI des Deltas** — die
   Subtraktion zweier Wilson-Grenzen ist kein gepaarter Test und kann bei echtem +10 pp
   negativ ausfallen; sie bleibt höchstens Sensitivitätsanalyse.
5. **Statistik-Helfer:** Wilson-Intervall, gepaarter Bootstrap, McNemar. **MDE vorab
   rechnen** (aus erwarteter Diskordanz), nicht pauschal „15–20 pp": bei 30 %/10 %-Diskordanz
   und n = 40 liegt die Power nur bei ~0,5. Regel: n so wählen, dass MDE ≤ Zielschwelle;
   ist das nicht bezahlbar, wird die Schwelle angehoben — nicht die Aussage weichgespült.
6. **Fairness-Stempel** im Ergebnis-JSON: `model_id`, `provider`, `system_fingerprint`,
   `temperature`, `seed`, `date`, `plugin_version`, `git_sha`, frischer `run_id`.
7. **Ablations-Matrix** in `modes.yaml`: `today | iface | iface+errors | iface+verifier
   | iface+verifier+context`.

**Dateien:** `runner/config/modes.yaml`, `runner/sweep_runner.py`, `db.py`,
`dashboard/` (Report), `tasks/<neu>/*`, `docs/methodology.md`.
**DoD:** Gold-Lösung 100 %, leerer Patch 0 %, format-only-Patch 0 %, Pass-Raten im Band
(Verteilung nicht U-förmig), **Kontrollarm schlägt reproduzierbar schlechter**. Der Kontrollarm ist ein bewusst degradierter Lauf (z. B.
Schema mit vertauschten Feldnamen oder halbiertes Kontextbudget) — ein zufällig
beobachteter negativer Gain beweist nichts, ein konstruierter muss schlechter sein.
**Verifikation:** `./benchmarkctl sweep all --config sweep-configs/ablation.yaml` und
`grep -c '"loc"' results/raw/*/*.json` > 0; Report enthält CI-Spalten.
**Risiko:** Kalibrierung kostet Modell-Budget (3 Läufe × 60 Tasks). Realistisch 1 Tag
Laufzeit, parallelisiert.

---

## Phase 2 — Interface + Output

### 0.6.33 — Schema-Narrowing

- `build_tool_specs` erzeugt pro Dispatcher nur noch:
  `op` (Enum der sichtbaren Ops) + Parameter der **Tier-1-Ops** (Union, aber flach,
  max. 6 Felder, keine `anyOf`/`oneOf`, keine verschachtelten Objekte).
- Tier-2/3-Ops werden im Enum geführt, aber ihre Felder stehen nicht im Schema —
  `op=help` (bzw. `namespaces.py`-Fehlerpfad) nennt Pflichtfelder + Beispiel.
- Konkrete Input-Beispiele: **eine** kurze gemeinsame Description pro Dispatcher mit
  Beispiel für die häufigste Tier-1-Op (Parameter-Accuracy 72 % → 90 % laut Evidenz).
  Beispiele für alle 70 Ops im Schema würden das 2.100-Token-Budget sprengen und sind
  technisch nicht als per-Enum-Value-Description abbildbar — die gehören in `op=help`.
- Feature-Flag `CODE_INTEL_SCHEMA=narrow|wide` (Default in einem Release: `wide`),
  damit die Baseline messbar bleibt und ein Rollback möglich ist.

**DoD:** Audit `eager_tokens` ≤ 2.100 (heute 4.788); kein Schema enthält `anyOf`/`oneOf`
(Test prüft rekursiv); alle 7 Dispatcher weiterhin registriert.
**Verifikation:** `python3 scripts/tool_schema_audit.py . --json` → `eager_tokens <= 2100`
und `pytest -q tests/test_namespace_schema.py` (neu: keine Union-Typen, ≤ 6 Felder, `required=["op"]`).

### 0.6.34 — Lenient Parsing + Koerktion

- Alias-Map: `file|filepath|fname|filename → path`, `symbol|name|func|function → name`,
  `lineno|start_line|line_no|startLine → line`, `query|q|search → query`.
- **Roh-Emission loggen**, bevor koerziert wird. Sonst misst der Benchmark die Güte des
  Parsers statt die des Modells: in der Auswertung zählt ein Aufruf nur dann als korrekt,
  wenn er ohne Koerktion korrekt war; koerzierte Aufrufe laufen als eigene Kategorie.
- Flag `CODE_INTEL_COERCION=0|1` (Default `1`), damit der Effekt isolierbar ist.
- Defaults: `limit=50`, `max_results`, `case_insensitive` je op aus der Registry.
- Unbekannte Felder: nicht verwerfen, sondern in `_extra` durchreichen (Handler ignorieren
  sie) und im Result quittieren: `"coerced": {"file": "path"}`.
- Stringifizierte Objekte/Arrays (Qwen-double-stringify) einmalig `json.loads`-reparieren.

**DoD:** 30 fehlerhafte Payload-Varianten (Fixture-Datei) laufen ohne Exception und ohne
Retry; Koertionsrate wird geloggt (`code_intel.coercion` Counter).
**Verifikation:** `pytest -q tests/test_coercion.py` (neu, 30 Cases) und
`grep -c coerced` in einer Test-Session.

### 0.6.35 — Fehler als Korrekturanweisung

- Einheitliches Fehlerobjekt (auch für die 108 `fmt_err`-Stellen):
  `{"status":"error","op":...,"error":"missing path","fix":{"tool":"code_search","args":{"op":"symbols","path":"<datei>"}},"options":[...],"help":"code_search op=help op=symbols"}`
- Jede Fehlerklasse (fehlendes Feld, unbekannter op, Datei nicht gefunden, keine LSP,
  Parse-Fehler) liefert genau **einen** kopierbaren Folge-Call.
- `fmt_err` bleibt für TTY, Default ist JSON.

**DoD:** Test, dass jede der 5 Fehlerklassen `fix.tool` + `fix.args` enthält; kein
Handler gibt mehr eine reine Prosa-Fehlermeldung zurück.
**Verifikation:** `pytest -q tests/test_error_contract.py` (neu).

### 0.6.36 — Output-Policy

- Default JSON (kompakt, `separators=(",",":")`), rich/ANSI nur bei `sys.stdout.isatty()`.
- Harte Caps pro op aus der Registry (`symbols`: 200 Symbole / 1.500 Tok als **harte**
  Obergrenze, `search`: 50, `overview`: 80, `impact`: 100). Die frühere Formulierung
  „Cap 1.500, Verifikation ≤ 2.000" war widersprüchlich — es gilt 1.500. Bei Kürzung:
  `"truncated": true` +
  `"next": {"tool":"code_search","args":{"op":"symbols","path":"...","offset":200}}`.
- Jede Antwort endet mit genau einer Affordance (`next`), nie mit mehreren.
- Token-Messung pro op gegen drei Referenzdateien (klein/mittel/groß).

**DoD:** Kein Default-Output > 1.500 Tok bei `symbols` und > 2.000 Tok im globalen
Rahmen; kein ANSI-Byte im Default-Output (`\x1b[` kommt im Test nicht vor); `fmt_compact`
wird genutzt (vorher 0×); jeder gekürzte Output trägt `truncated: true` **und** `next`.
**Verifikation:** `pytest -q tests/test_output_budget.py` (neu) und
`python3 scripts/tool_schema_audit.py . --json` unverändert.

---

## Phase 3 — Verifier-Loop

### 0.6.37 — Diagnostics als Pflichtschritt

- `code_edit`-Ops (`refactor`, `rename`, `replace_body`, `insert_*`, `format`) hängen nach
  dem Write automatisch `diagnostics` an: max. 10 Fehler, je `path:line`, Message,
  `fix`-Hinweis.
- Neuer op `code_edit op=verify`: format-check + diagnostics + **optional** Testlauf.
  Testkommando ausschließlich aus expliziter Repo-Config (`.codeintel.json`), Timeout 30 s,
  Allowlist, Default = nur Diagnostics. Kein Discovery, kein „irgendwas, das nach Tests
  aussieht" — ein hängender Auto-Test blockiert sonst jeden Agentenlauf.
- Flag `CODE_INTEL_EDIT_VERIFY=0|1` (Default `1`).
- Wenn kein Language-Server: graceful `{"diagnostics":"unavailable","reason":"no-lsp"}`
  (kein Abbruch, keine Exception).
- Steering-Zeile: „nach jedem Edit `op=verify`".

**DoD:** Vier E2E-Fälle: (a) korrekter Edit → keine Diagnostics, keine Zeitstrafe;
(b) absichtlich fehlerhafter Edit → Diagnostics im selben Call; (c) LSP-Timeout/kein
Language-Server → Edit erfolgreich, `{"diagnostics":"unavailable"}`, kein Crash;
(d) Datei mit 50 Fehlern → Cap 10 greift, `truncated: true`.
**Verifikation:** `pytest -q tests/test_edit_verify_loop.py` (neu), `npx tsc --noEmit`
im Test-Repo als externer Gegencheck.
**Risiko:** LSP-Startzeit pro Call. Lösung: Bridge wiederverwenden (`lsp_bridge.py`),
Timeout 5 s, Ergebnis cachen.

---

## Phase 4 — Profile + Kontext

### 0.6.38 — Profile-Bugfix + `weak`-Profil

- Bug: `CODE_INTEL_TOOL_PROFILE=core|all` liefert identisch 7 bzw. 70 Tools. Ursache:
  Profile filtern die Registrierung nicht (`_profiles.py` liefert Listen mit
  Legacy-Namen, im Namespace-Modus ungenutzt).
- Fix: Profile filtern `GROUPS`/Ops, nicht Toolnamen. Zwei Achsen:
  `CODE_INTEL_TOOL_PROFILE=weak|core|all` (welche Dispatcher) und
  `CODE_INTEL_OPS=weak|all` (welche Ops je Dispatcher).
- `weak`: 5 Dispatcher (`code_search`, `code_lsp`, `code_edit`, `code_graph`,
  `code_meta`), nur Tier-1-Ops, JSON-only, Caps halbiert, `op=help` immer verfügbar.
  `code_graph` gehört dazu, weil das Steering sonst auf ein Tool verweist, das im Profil
  fehlt (Impact vor Refactoring ist der wichtigste Einzelhinweis für schwache Modelle).

**DoD:** Testmatrix `{weak, core, all} × {ops=weak, all}` mit **unterschiedlichen**
Tool-/Op-Zahlen für `core` vs. `all` (der alte Bug war, dass beide gleich waren — das muss
ein Test dauerhaft verhindern); `weak` = 5 Tools, < 1.500 Tok.
**Verifikation:** `CODE_INTEL_TOOL_PROFILE=weak python3 scripts/assert_audit.py --max-tools 5 --max-eager 1500`
und `pytest -q tests/test_tool_profiles.py` (Matrix-Assertions).

### 0.6.39 — Proaktiver Kontext

- Session-Start (einmal, gecacht): Repo-Map ≈ 400 Tok — Top-Level-Verzeichnisse,
  30 wichtigste Symbole mit `path:line`, Hinweis auf `code_search op=overview`.
  Nur wenn ein Index existiert, sonst still.
- Natural-Language-Auflösung in `pre_llm_call`: Statt nur wörtlicher Dateipfade auch
  Bezeichner aus der Nachricht → `workspace_symbols`-Suche → 3 beste Treffer mit
  Signatur + `path:line`. Budget: 300 Tok. Caching 120 s (bestehende Struktur nutzen).
- Zwei-Stufen-Regel in jeder Antwort: Übersicht first, Detail über `next`.

**DoD:** Bei 10 typischen Fragen ohne Pfadnennung enthält der injizierte Kontext
mindestens ein relevantes Symbol; bei fehlendem Index kein Fehler.
Budget: Repo-Map 400 + NL-Kontext 300 + Steering 200 = **≤ 900 Tok Zusatz**, und
zusammen mit dem Schema (≤ 2.100) muss ein `weak`-Turn ≤ 3.000 Tok bleiben — als
Gesamttest, nicht als drei Einzelbudgets, die sich dann summieren.
**Verifikation:** `pytest -q tests/test_pre_llm_context.py` (neu, 10 Fixtures) +
manuelle Stichprobe im Benchmark-Log.

### 0.6.40 — Steering in der Hauptsession

- Ein-Zeiler (≤ 200 Tok) in `pre_llm_call` für die Hauptsession, nicht nur Subagenten:
  „vor Änderungen `code_graph op=impact`; nach Edits `code_edit op=verify`;
  Datei-Überblick `code_search op=overview`".
- Der Text wird **aus dem aktiven Profil generiert** (Registry + Profil), nie statisch
  formuliert — sonst verweist Steering auf Ops, die im `weak`-Profil fehlen.
  Test: jede im Steering genannte Tool/Op-Kombination existiert im aktiven Profil.
- Kein Duplikat der Tool-Descriptions, keine Workflow-Prosa.

**DoD:** Prompt-Zuwachs ≤ 200 Tok gemessen; Steering erscheint genau einmal pro Turn.
**Verifikation:** `pytest -q tests/test_steering.py` (neu).

---

## Phase 5 — Messung + Entscheidung

### 0.7.0 — `weak-model-ready` (nur wenn belegt)

Ablationsläufe: 40 Tasks × 4 Konfigurationen × 3 Runs = 480 Runs mit **dem Zielmodell
(schwach)**. Die Modell-Kontrollachse (mittel/stark) läuft separat und reduziert:
20 Tasks × 1 Run × dieselben 4 Konfigurationen = 80 Runs. Nicht alles kreuzen —
1.440 Runs sind in 3 JD nicht zu schaffen.

Erfolgskriterien — **eine** präregistrierte Schwelle pro Achse, identisch mit der
Übersichtstabelle, und jede Schwelle muss ≥ MDE des Messinstruments sein:
- Interface+Output (Phase 2): +≥ 15 pp `pass@1` gegen „today", Koertionsrate < 15 %.
- Verifier-Loop (Phase 3): +≥ 15 pp gegen Phase 2.
- Kontext (Phase 4): **kein pp-Gate** — bei erwarteten +5 pp und MDE ~15 pp ist der Effekt
  mit diesem Budget grundsätzlich nicht nachweisbar. Bewertung qualitativ (Symbol im
  Kontext gefunden: ja/nein) plus Kostenprüfung; Rückbau, wenn die Tokens steigen und
  die Trefferquote nicht.
- `pass^3 < pass@1` muss sichtbar sein (sonst ist Reliabilität nicht gemessen).
- Kosten pro gelöstem Task sinkt gegen „today".
Ist eine dieser Schwellen mit dem Budget nicht erreichbar, wird die Phase nicht
„bestanden" — sie wird als nicht messbar dokumentiert. Keine nachträgliche Senkung.

Report: `results/report-weak-models.md` mit Tabelle je Konfiguration, CI, McNemar-p,
Koertionsrate, Localization-Recall.

---

## Phase 6 — Nur mit Beleg (bewusst zurückgestellt)

| Kandidat | Warum zurückgestellt | Bedingung für Aufnahme |
|---|---|---|
| Semantischer/Hybrid-Index | gegen grep-Agent nur +5,1 pp (p=0,087); Embedding-Drift, Reindex-Kosten | Phase 5 zeigt Lokalisierungs-Lücke > 15 pp, die AST nicht schließt |
| Weitere Ops/Tools | Tool-Zahl ist negativ korreliert mit Auswahlgenauigkeit | keiner — gegen Evidenz |
| Skill-/Doku-Ausbau | 92/98 Skills ohne Effekt, Ø +0,34 pp | konkrete Prozedur fehlt UND ist nicht per Tool abbildbar |
| Modell-Routing (klein sucht, groß synthetisiert) | löst kein Interface-Problem, verdoppelt Kosten | Phase 5 zeigt, dass Interface-Fixes die Lücke nur halb schließen |
| Automatische `explain`-Pflicht | kostet Tokens, Kausalität selten korrekt | — |

---

## Querschnitt: was bei jeder Änderung gilt

Checkliste pro Release:
1. `VERSION` bump (0.00.01), `CHANGELOG.md`-Eintrag mit Verifikationszeile.
2. `pytest -q` grün, `npx tsc --noEmit` (falls TS betroffen), Pre-Commit-Hook.
3. `scripts/assert_audit.py` mit expliziten Budgets laufen lassen (Exit-Code im CHANGELOG
   nennen); Audit-Zahlen vor/nach zusätzlich dokumentieren.
4. Kein ANSI im Default-Output, kein Output > 2.000 Tok, keine Union-Typen.
5. Bei verhaltensändernden Releases: ein Benchmark-Lauf gegen den Vorzustand
   (mindestens die 28 Bestandstasks als Regressionssuite).
6. Commit-Message `vX.Y.Z: <Thema>`, Tag setzen — Main-Only, direkter Push.
7. Rollback benennen: jedes verhaltensändernde Flag (`SCHEMA`, `OUTPUT`, `COERCION`,
   `EDIT_VERIFY`) hat einen Default und eine dokumentierte Rückstellung; ein Revert-Tag
   ist Pflicht. Pre-Commit ist kein CI-Ersatz (mit `--no-verify` umgehbar, kein sauberer
   Checkout) — die Gate-Läufe dieser Roadmap laufen als explizite Sequenz auf frischem
   Checkout, nicht als Hook.

Nicht-Ziele dieser Roadmap:
- Kein Umbau der AST-/LSP-Engine. Sie ist nicht das Problem.
- Keine neuen Fähigkeiten (Ops), nur bessere Bedienbarkeit der vorhandenen.
- Keine Modell-Features (kein Fine-Tuning, kein Routing) vor Phase 5.

---

## Risiken

| Risiko | Wirkung | Gegenmaßnahme |
|---|---|---|
| Schema-Narrowing versteckt benötigte Felder | Modell ruft `op=help` statt zu handeln → +1 Turn | `op=help` als Freitext-Antwort im selben Tool, kein Extra-Tool; Feldnamen zusätzlich in der op-Beschreibung |
| Koerktion maskiert Modellfehler | Benchmark wird zu freundlich | `coerced`-Flag + Koertionsrate als eigene Kennzahl, Ziel < 15 % |
| LSP nicht verfügbar | Verifier-Loop wirkungslos | graceful degradation, AST-Fallback für Syntax, Tests als zweite Quelle |
| Kalibrierung findet keine Tasks im Band | kein Messinstrument | Task-Quelle erweitern (fremde Repos, mehrstufige Fragen), nicht Band aufweichen |
| Provider-Drift bei Flash-Modellen | Sweeps nicht vergleichbar | Snapshot-Stempel, alle Zellen in ≤ 2 Tagen |
| Aufwand läuft weg | Roadmap stirbt | Phase-Gates: nach jeder Phase Messung oder Abbruch, keine Phase ohne Verify |


---

## Peer Review (2026-11-18) — was sich geändert hat

23 Korrekturen eingearbeitet. Die substanziellen:

| Befund | Änderung |
|---|---|
| Verify-Kommandos waren Prosa, Audit-Skript exitet immer 0 | Phase 0 liefert `scripts/assert_audit.py` als Gate mit Exit ≠ 0; alle Gates umgestellt |
| Feature-Flags fehlten → Baseline nach Phase 2 zerstört | `CODE_INTEL_SCHEMA/OUTPUT/COERCION/EDIT_VERIFY` in jedem Release, Rollback dokumentiert (Querschnitt Punkt 7) |
| „Negativer Gain beweist Messbarkeit" war zirkulär | konstruierter Kontrollarm, der reproduzierbar schlechter sein muss (Phase 1 DoD) |
| Schwellen widersprüchlich (≥15 pp vs. 5/8/10 pp) | eine präregistrierte Tabelle, jede Schwelle ≥ MDE; Kontext ohne pp-Gate (Phase 5) |
| Aufwand 3/5/14 JD für denselben Scope | eine Tabelle, 18 JD gesamt (Phase 1: 5, Phase 5: 3) |
| `weak`-Profil ohne `code_graph`, Steering verwies darauf | 5 Dispatcher im Profil, Steering wird aus dem aktiven Profil generiert |
| Registry hatte kein `caps`, 0.6.36 braucht es | `caps` gehört zu Phase 0 |
| Caps-Widerspruch 1.500 vs. 2.000 | 1.500 hart für `symbols`, 2.000 global |
| `op=verify` konnte beliebige Tests starten | Testkommando nur aus Repo-Config, Timeout 30 s, Allowlist |
| 1.440 Runs in 3 JD | 480 Runs (Zielmodell) + 80 Runs (Kontrollachse) |
| Kalibrierung/Leakage unterspezifiziert | Band pro Zielmodell, PR-Datum vs. Cutoff, Fallback auf fremde Repos |
| Koerktion konnte Modellfehler maskieren | Roh-Emission wird geloggt, koerzierte Calls zählen als eigene Kategorie |

Nicht geändert: Phasenreihenfolge, Nicht-Ziele, die fünf Hauptmaßnahmen. Das Review hat die
Begründung geschärft, nicht die Richtung gedreht.

**Offen (bewusst, mit Triage-Status in `~/peer-review-triage-2026-11-18.md`):**

| Punkt | Entscheidung / nächster Schritt |
|---|---|
| Multiplizität der Kontraste (153 Paare statt 18) | vor dem ersten Sweep Kontrastliste festlegen, Holm-Korrektur, Testzahl im Report nennen |
| Judge-Kalibrierung „30 Items, 3 Votes" | trennen: 30 Kalibrierungs-Items + 40 Task-Items × 3 Votes = 120 Calls |
| Kosten je Run (0,30–0,60 USD) | aus Preistabelle × erwarteten Tokens herleiten, nicht schätzen |
| Split-Regel Recherche A (pro Verb vs. pro Argumentform) | entschieden: kein Split, 7 Dispatcher bleiben — Test fehlt noch |
| `r_i`-Filter (0,2 bei n = 40) | ≥ 0,3 oder als Diagnose ohne Ausschlusswirkung |
| Tokenizer-Gegenmessung (`chars/4`) | Ziel-Tokenizer messen, beide Werte berichten |
| Server-CI statt Pre-Commit | Infrastrukturentscheidung, nicht Teil dieser Roadmap |
