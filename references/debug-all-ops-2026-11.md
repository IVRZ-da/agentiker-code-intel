# Live-Debug aller 70 code_intel-Ops — 2026-11-19

**Plugin:** agentiker-code-intel v0.6.34 → **0.6.35**
**Harness:** `scripts/debug_all_ops.py` (neu)
**Fixture:** `namespaces.py`, Zeile 195 (`def build_catalog`)
**Umgebung:** Hetzner, Python 3.13.5, pyright/pylsp + tsserver + gopls + rust-analyzer vorhanden

## Methode

`debug_all_ops.py` baut den echten Registrierungs-Catalog (`namespaces.build_catalog`),
erzeugt für jeden der 70 Ops die Argumente aus `tools.op_meta` (required + optional)
und ruft ihn auf **zwei Ebenen** auf:

| Ebene | Pfad | prüft |
|---|---|---|
| `handler` | `catalog["code_<op>"]["handler"](args)` | Tool-Logik, echte `_fmt`-Panels, LSP, Git |
| `namespace` | `build_tool_specs(catalog)` → Dispatcher | op-Enum, Pflichtfeld-Validierung, `op=help` |

Drei Prüfstufen pro Op:

1. **Status** — Exception, Timeout (SIGALRM), Fehler-Marker im Output.
2. **Semantik** — JSON-Vertrag (ist der Output parsbar?) und `None`-Pflichtfelder.
   Diese Stufe war nötig, weil vier Bugs als „✅ Success" mit leeren Feldern auftraten.
3. **Worktree** — `git status` vor/nach dem Lauf (dry_run-Verletzung erkennen).

Aufruf:

```bash
python scripts/debug_all_ops.py --out /tmp/ops_report.json          # alle 70, beide Ebenen
python scripts/debug_all_ops.py --only migration,refactor --mode handler -v
```

## Ergebnis

| Metrik | Baseline | Nach Fixes |
|---|---|---|
| Ops laufen ohne Fehler | 65 / 70 | **68 / 70** |
| soft_error (LSP „nichts vorhanden") | 5 | **2** |
| error / timeout | 0 | 0 |
| **semantisch auffällig** (JSON-Vertrag gebrochen, `None`-Felder) | 4 bei den ersten 6 geprüften Ops | **0** |
| volle pytest-Suite | 3200 passed / 6 failed | **3206 passed / 0 failed** |
| Worktree nach Lauf | clean | clean |

Die zwei verbleibenden `soft_error` sind `code_lsp.implementations` und
`code_lsp.linked_editing`: dort gibt es an der Testposition semantisch nichts
zurückzugeben. Sie melden das als ❌-Panel (siehe Offene Punkte 3).

## Findings

### 🔴 F1 — `fmt_json` lieferte korrumpiertes JSON (`_fmt.py`)

`fmt_json` schickte `json.dumps(...)` durch `fmt_code` (Rich `Syntax`). Das bricht
lange Zeilen an der Konsolenbreite um und **schneidet Werte ab**:

```
"signature": "def build_catalog(\n    register_ast: ..., reg  "doc_preview": "",
```

Folge: `json.loads` scheitert (`Invalid control character`, `Invalid \escape`) —
bei `export` auf ein Projekt kamen 9 MB in **einer** Zeile heraus. Betroffen waren
alle 28 `fmt_json`-Aufrufe, sobald ein Wert länger war als die Panelbreite;
`complexity` funktionierte nur zufällig (kurze Payloads).

**Fix:** `fmt_json` = `json.dumps(data, indent=2, ensure_ascii=False)`.
Anzeige-Formatierung bleibt in `fmt_code`.

### 🔴 F2 — Interne Aufrufe parsten Panel-Text als JSON (4 Stellen)

| Stelle | Aufruf | Folge |
|---|---|---|
| `tools/test_coverage.py::_tests_find_references` | `code_references_tool` | immer `{}` → `symbol: None`, `coverage: none` |
| `tools/test_coverage.py::_tests_find_symbol_name` | `code_symbols_tool` | `except` schluckte alles (zusätzlich falsche Felder `start_line` statt `line`) |
| `tools/impact.py:242` | `code_tests_for_symbol_tool` | Testabdeckung in `blast_radius` immer leer (suchte außerdem `tests` statt `test_files`) |
| `hooks.py:95,106` | `code_symbols_tool`, `code_diagnostics_tool` | `pre_llm_call`-Hook lieferte nie Symbol-/Diagnose-Kontext |

**Fix:** `_fmt.fmt_result(data, title, raw)` als zentrale Weiche; die vier
Tool-Funktionen bekamen `_raw: bool = False` (`fmt_json` bei `True`), die
Aufrufer setzen `_raw=True`. `_tests_find_references` holt die Locations direkt
aus der LSP-Bridge (wie `impact.py` es seit 2026-07 macht).

### 🔴 F3 — `_auto_detect_identifier_column` kannte keine Python/Go-Keywords (`lsp/heuristics.py:43`)

`_KEYWORDS` enthielt nur TS/JS-Begriffe. In Python ist das erste Wort einer
Definition aber `def` — die Funktion gab deshalb **Spalte 1** zurück, und
references/rename/hover/implementations/type_definition arbeiteten auf dem
Keyword statt auf dem Symbolnamen. Das erklärt `rename → "no edits"`,
leere Referenzlisten und `hover → "No hover info"`.

**Fix:** Python-Keywords (`def`, `async`-Kontext, `lambda`, `yield`, `self`, `cls`, …)
und Go-Keywords (`func`, `package`, `struct`, `range`, …) ergänzt.
Verifikation: `def build_catalog(` → Spalte 5 (vorher 1).

### 🔴 F4 — `capsule` fragte LSP mit Spalte 0 (`tools/capsule.py`)

`_capsule_get_definition` rief `goto_definition(..., 0)` — also das `def`-Keyword.
`_capsule_get_references` rechnete mit `matched["start_column"]`, das es in
Symbol-Dicts **nicht gibt** (`keys: body, end_line, kind, line, name, signature`),
und landete damit ebenfalls auf `-1`.

**Fix:** Auto-Spalte als Fallback in beiden Helfern. `capsule.definition` und
`reference_count` sind jetzt gefüllt, `explain` liefert Symbol, Kind, Signatur.

### 🟡 F5 — `op_meta`-Beispiele scheiterten garantiert (`tools/op_meta.py`)

- `graph_query`: Beispiel `{"query": "callers(Foo)"}` — der Handler kennt nur
  `callers|callees|hot_paths|cycles|health|summary`.
- `export`: Beispiel `{"kind": "tree"}` — das Schema erlaubt nur
  `all|function|class|method`.

Ein Agent, der `op=help` folgt, läuft damit in einen Fehler.

**Fix:** Beispiele korrigiert. Zusätzlich `tests/test_regression_panel_json.py::TestOpMetaExamples`
prüft jedes Beispiel gegen das echte Handler-Schema (Enums + Pflichtfelder) —
künftige Abweichungen fallen im Test auf.

### 🟡 F6 — `graph_query`: `symbol` matchte nur Dateipfade (`tools/knowledge_graph.py`)

Der Parameter heißt `symbol`, wurde aber als Dateipfad-Fragment gegen die
Graph-Knoten geprüft (`if symbol in node`). Ein Symbolname konnte nie treffen.

**Fix:** `_kg_resolve_symbol()` — erst Pfad-Teilstring, dann Definition über
Regex (`def|class|func|function|const|let|var|type|interface`) in den
indizierten Dateien. Fehlermeldung nennt jetzt beide Wege.

### 🟡 F7 — Parallelkopie `tools/symbols.py` / `tools/symbols_extractor.py`

`code_tools.py` re-exportiert `code_symbols_tool` aus `symbols_extractor.py`;
`symbols.py` stellt nur `_symbols_extract_single`/`extract_symbols` bereit.
Beim Fix fiel auf, dass die erste Änderung in der **toten** Kopie landete —
beide Dateien mussten angepasst werden. Technische Schuld: eine der beiden
sollte mittelfristig verschwinden.

### 🟡 F8 — Performance-Hotspots (nicht behoben)

| Op | Dauer | Ursache |
|---|---|---|
| `code_review.duplicates` | ~44 s (max_files=40) | Ähnlichkeitsvergleich über alle Paare |
| `code_review.pr_impact` | ~15 s | git diff + Symbol-Mapping |
| `code_review.security_scan` | ~5,5 s | Regex-Katalog über alle Dateien |
| `code_search.references` | ~30 s (erster LSP-Kontakt) | `_wait_for_document_ready` |

`duplicates` ist der klare Kandidat für ein Cap/Abbruchkriterium.

## Offene Punkte

1. **LSP-UX:** `implementations`, `linked_editing` und `type_definition` melden
   „nichts vorhanden" als ❌-Error-Panel. Für einen Agenten liest sich das wie ein
   Defekt. Besser: `fmt_info` mit „keine Treffer".
2. **Kein Output-Cap:** `export` mit `kind=all` auf ein Projekt liefert mehrere MB.
   `op_meta` deklariert `max_tokens` pro Op, durchgesetzt wird es noch nicht
   (im Docstring als 0.6.36 geplant).
3. **Test-Schuld:** Mehrere Tests in `tests/test_test_coverage.py` mocken noch
   `code_references_tool` — sie sind grün, prüfen aber die entfernte Grenze.
   Die sechs Tests, die dadurch rot wurden, sind auf `_tests_find_references`
   bzw. die Bridge umgestellt.

## Regressionstests

`tests/test_regression_panel_json.py` (15 Tests):

- `TestFmtJsonContract` — lange Werte, verschachtelte Strukturen, keine ANSI-Codes
- `TestFmtResult` — `raw=True` → JSON, `raw=False` → Panel
- `TestIdentifierColumn` — `def`/`async def`/`class`/`return` in Python, `Widget`
- `TestRawContract` — `code_symbols_tool` raw/Panel, `_tests_find_symbol_name`, Grenze gemockt
- `TestOpMetaExamples` — jedes `op_meta`-Beispiel gegen das echte Handler-Schema
