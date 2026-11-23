"""Regression: register_namespaces zählt Erfolge, nicht Versuche (F19).

``ctx.register_tool`` liefert ``None``, wenn ein global belegter Toolname ohne
``override=True`` überschrieben werden sollte — das Tool ist dann nicht
verfügbar. Der Zähler stieg trotzdem und die Statuszeile meldete „7
registriert", während keins der sieben Tools auffindbar war.
"""

from __future__ import annotations

from typing import Any

from code_intel.namespaces import GROUPS, register_namespaces


def _catalog(groups: int = 2) -> dict:
    """Katalog mit zwei Ops je Gruppe — Format wie im echten Plugin."""
    catalog: dict[str, dict[str, Any]] = {}
    for group, cfg in list(GROUPS.items())[:groups]:
        for op in list(cfg["ops"])[:2]:
            catalog[f"code_{op}"] = {
                "handler": lambda args: "{}",
                "schema": {"parameters": {"properties": {}, "required": []}},
                "description": f"op {op}",
            }
    return catalog


EXPECTED_SPECS = 2


class _Ctx:
    def __init__(self, results: list[Any]):
        self.results = results
        self.calls: list[str] = []

    def register_tool(self, *, name: str, **kwargs: Any) -> Any:
        self.calls.append(name)
        return self.results.pop(0) if self.results else object()


class _RaisingCtx(_Ctx):
    def register_tool(self, *, name: str, **kwargs: Any) -> Any:
        self.calls.append(name)
        raise RuntimeError("kaputt")


def test_counts_only_successful_registrations():
    ctx = _Ctx(results=[None, None])
    assert register_namespaces(ctx, _catalog()) == 0
    assert len(ctx.calls) == EXPECTED_SPECS


def test_partial_success_is_counted_exactly():
    ctx = _Ctx(results=[None, object()])
    assert register_namespaces(ctx, _catalog()) == 1


def test_all_success():
    ctx = _Ctx(results=[object(), object()])
    assert register_namespaces(ctx, _catalog()) == EXPECTED_SPECS


def test_exception_does_not_block_others():
    ctx = _RaisingCtx(results=[])
    assert register_namespaces(ctx, _catalog()) == 0
    assert len(ctx.calls) == EXPECTED_SPECS
