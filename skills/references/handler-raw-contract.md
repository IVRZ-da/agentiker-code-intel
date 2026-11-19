# Der `_raw`-Vertrag: Panel für Menschen, JSON für Aufrufer

Die code_intel-Tools geben standardmaessig ein Rich-Panel zurueck (`_fmt.fmt_ok`).
Das ist für die Anzeige richtig und für jeden programmatischen Aufrufer unbrauchbar:
`json.loads` scheitert an den Box-Zeichen, und wer die Exception schluckt, sieht ein
leeres Ergebnis statt eines Fehlers.

## Wer liefert JSON?

`_fmt.fmt_result(data, raw=...)` weiche: `raw=True` → `fmt_json` (reines
`json.dumps`), sonst Panel. Durchgereicht ueber den Parameter `_raw: bool = False`:

| Tool-Funktion | Datei |
|---|---|
| `code_symbols_tool` | `tools/symbols_extractor.py` |
| `code_diagnostics_tool` | `lsp/tools_core.py` |
| `code_references_tool` | `lsp/tools_core.py` |
| `code_tests_for_symbol_tool` | `tools/test_coverage.py` |

Die zugehoerigen `_handle_code_*`-Wrapper muessen `_raw=bool(args.get("_raw", False))`
setzen, sonst endet der Vertrag eine Ebene zu frueh.

## Diagnose: welcher Handler ist wirklich registriert?

Dateinamen raten fuehrt in die Irre — `_handle_code_symbols` existiert doppelt
(`tools/symbols.py` und `tools/symbols_extractor.py`). Der Katalog weiss, welche
Variante zaehlt:

```python
import inspect, sys
sys.path.insert(0, "/home/jo/.hermes/plugins")
from code_intel import namespaces as ci_ns
from code_intel import _register_ast_tools
from code_intel.lsp_bridge import register_lsp_tools

catalog = ci_ns.build_catalog(_register_ast_tools, register_lsp_tools)
for name in ("code_symbols", "code_diagnostics", "code_references",
             "code_tests_for_symbol", "code_unused_finder"):
    handler = (catalog.get(name) or {}).get("handler")
    if handler is None:
        print(name, "-> kein Handler"); continue
    src = inspect.getsource(handler)
    print(f"{name:<24} {inspect.getsourcefile(handler).split('code_intel/')[-1]}"
          f":{inspect.getsourcelines(handler)[1]}  raw={'_raw' in src}")
```

Nach dem Patch dieselbe Ausgabe erneut pruefen — `raw=True` bei den vier Handlern.
Werkzeuge ohne `_raw` (`code_unused_finder`, `code_security_scan`, `code_complexity`,
`code_hot_paths`) liefern ohnehin JSON; dort ist `raw=False` normal.

## Verhalten verifizieren, nicht Formatierung

```python
spec = [s for s in ci_ns.build_tool_specs(catalog) if s["name"] == "code_search"][0]
out = spec["handler"]({"op": "symbols", "path": "/pfad/datei.py", "_raw": True})
import json; data = json.loads(out)          # muss klappen
print(data["symbol_count"], len(data["symbols"]))
```

Verzeichnis-Scan liefert eine andere Form als eine Einzeldatei:
`{"results": [{"path": ..., "symbols": [...]}]}` statt `{"symbols": [...]}`.
Aufrufer muessen beide Formen kennen, sonst melden sie für Verzeichnisse 0 Symbole,
obwohl der Scan 200 gefunden hat.

## Gegenstelle: Scout

`scout/analysis/tools/base.py::_call_tool` setzt `_raw=True`, wenn der Aufrufer es nicht
explizit setzt, und wiederholt den Dispatch ohne `_raw`, sobald die Antwort
`unexpected keyword argument '_raw'` enthaelt. Damit funktionieren beide Tool-Generationen
ohne Fallunterscheidung an jeder Aufrufstelle.

Ein Dispatch-Ergebnis in 0–1 ms mit `status: ok` und leeren Feldern ist immer ein
Fehler: entweder das Tool wurde nicht gefunden (`Unknown tool: ...`) oder die Datenform
wurde nicht erkannt. Beides ist an der Laufzeit erkennbar, bevor jemand die Felder liest.
