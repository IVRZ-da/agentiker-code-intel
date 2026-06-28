"""conftest.py — Shared test infrastructure for code_intel plugin tests.

Provides:
- _fmt mock that returns plain JSON (not rich panels) for test assertions
- tools.registry mock for tests that import the Hermes registry
- hermes_cli mock for __init__.py register() function
- Test isolation via _KEEP list (pytest_runtest_setup)
"""

from __future__ import annotations

import json
import os
import sys
import types
import warnings
from typing import Any
from unittest.mock import MagicMock

import pytest

# Force DEBUG logging for test environments — setup_logger reads this at module import time
os.environ["CODE_INTEL_LOG_LEVEL"] = "DEBUG"

# ─── DeprecationWarning-Filter (importlib __package__ != __spec__.parent) ─
warnings.filterwarnings("ignore", message=".*__package__.*")

# ═══════════════════════════════════════════════════════════════════════════
# _fmt Mock — returns plain JSON instead of rich panels
# ═══════════════════════════════════════════════════════════════════════════

_fmt_mod = types.ModuleType("code_intel._fmt")


def _fmt_ok(data: Any = None, msg: str | None = None, title: str | None = None) -> str:
    result: dict[str, Any] = {"status": "ok"}
    if isinstance(data, dict):
        result.update(data)
    if msg:
        result["message"] = msg
    if title:
        result["title"] = title
    return json.dumps(result, ensure_ascii=False)


def _fmt_err(msg: str, details: Any = None, title: str | None = None) -> str:
    result: dict[str, Any] = {"status": "error", "error": msg, "message": msg}
    if details:
        result["details"] = details
    if title:
        result["title"] = title
    return json.dumps(result, ensure_ascii=False)


def _fmt_info(msg: str, data: Any = None) -> str:
    return json.dumps({"status": "info", "message": msg, **(data or {})}, ensure_ascii=False)


_fmt_mod.fmt_ok = _fmt_ok
_fmt_mod.fmt_err = _fmt_err
_fmt_mod.fmt_info = _fmt_info
_fmt_mod.fmt_json = lambda data: json.dumps(data, ensure_ascii=False, indent=2, default=str)
_fmt_mod.fmt_table = lambda *a, **kw: ""
_fmt_mod.fmt_code = lambda code, lang="", **kw: f"```{lang}\n{code}\n```"
_fmt_mod.fmt_markdown = lambda text: text.strip()
_fmt_mod.fmt_warn = lambda msg, data=None: json.dumps(
    {"status": "warning", "message": msg, **(data or {})}, ensure_ascii=False
)
_fmt_mod.fmt_tree = lambda *a, **kw: str(a) if a else ""
_fmt_mod.fmt_panel = lambda *a, **kw: str(a) if a else ""
_fmt_mod.fmt_compact = lambda data, msg="", title="": json.dumps({**data, **({"message": msg} if msg else {})}, ensure_ascii=False, indent=2)
_fmt_mod._strip_ansi = lambda text, **kw: text
_fmt_mod.STYLE_TITLE = "bold cyan"
_fmt_mod.STYLE_OK = "green"

sys.modules["code_intel._fmt"] = _fmt_mod
sys.modules["_fmt"] = _fmt_mod  # backward compat for direct imports

# ═══════════════════════════════════════════════════════════════════════════
# Hermes Module Mocks
# ═══════════════════════════════════════════════════════════════════════════

# --- hermes_cli ---
_hermes = types.ModuleType("hermes_cli")
_hermes.plugins = types.ModuleType("hermes_cli.plugins")
_hermes.plugins.PluginContext = type(
    "MockPluginContext",
    (),
    {
        "register_tool": lambda *a, **kw: None,
        "register_hook": lambda *a, **kw: None,
        "register_skill": lambda *a, **kw: None,
        "register_command": lambda *a, **kw: None,
    },
)
sys.modules["hermes_cli"] = _hermes
sys.modules["hermes_cli.plugins"] = _hermes.plugins

# --- tools package (for lazy imports in lsp/tools_extra.py, tools/symbols.py) ---
_tools = types.ModuleType("tools")
_tools.registry = types.ModuleType("tools.registry")


class _RegistryMock:
    """Functional registry mock that stores tool registrations."""

    def __init__(self):
        self._entries: dict[str, dict] = {}

    def get_entry(self, name: str) -> Any:
        return self._entries.get(name)

    def register(self, name: str, toolset: str = "", schema: dict | None = None, handler=None, **kwargs) -> None:
        entry = {
            "name": name,
            "toolset": toolset,
            "schema": schema or {},
            "handler": handler or (lambda a: json.dumps({"status": "ok"})),
        }
        # Support both dict-style and object-style access
        self._entries[name] = type("RegistryEntry", (), entry)()

    def deregister(self, name: str) -> None:
        self._entries.pop(name, None)

    def dispatch(self, name: str, args: dict | None = None, **kwargs) -> str:
        entry = self.get_entry(name)
        if entry and entry.handler:
            return entry.handler(args or {})
        return json.dumps({"status": "error", "error": "not found"})

    def get_all_tool_names(self) -> list[str]:
        return list(self._entries.keys())

    def get_toolset_for_tool(self, name: str) -> str | None:
        entry = self.get_entry(name)
        return getattr(entry, "toolset", None) if entry else None


_tools.registry.registry = _RegistryMock()
sys.modules["tools"] = _tools
sys.modules["tools.registry"] = _tools.registry

# ═══════════════════════════════════════════════════════════════════════════
# Mock Registry (for tests that directly test registry registration)
# ═══════════════════════════════════════════════════════════════════════════


class MockRegistry:
    """Minimal registry mock for tests."""

    def __init__(self):
        self._entries: dict[str, MagicMock] = {}

    def get_entry(self, name: str) -> MagicMock | None:
        return self._entries.get(name)

    def register(self, name: str, *args, **kwargs) -> None:
        entry = MagicMock()
        entry.name = name
        entry.handler = MagicMock(return_value=json.dumps({"status": "ok"}))
        self._entries[name] = entry

    def deregister(self, name: str) -> None:
        self._entries.pop(name, None)

    def dispatch(self, name: str, args: dict = None, **kwargs) -> Any:
        entry = self.get_entry(name)
        if entry and entry.handler:
            return entry.handler(args or {})
        return None

    def get_all_tool_names(self) -> list[str]:
        return list(self._entries.keys())


@pytest.fixture
def mock_registry() -> MockRegistry:
    """Fixture that provides a clean MockRegistry per test."""
    return MockRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# Test Isolation — _KEEP List
# ═══════════════════════════════════════════════════════════════════════════

# Modules that must NOT be removed between tests (monkeypatch targets)
_KEEP: set[str] = {
    "code_intel",
    "code_intel.code_tools",
    "code_intel._fmt",
    "code_intel._logging",
    "code_intel._import_graph",
    "code_intel.lsp_bridge",
    "code_intel.lsp",
    "code_intel.lsp.bridge",
    "code_intel.lsp.bridge.pool",
    "code_intel.lsp.bridge.server",
    "code_intel.lsp.tools_core",
    "code_intel.lsp.tools_extra",
    "code_intel.lsp.tools_handler",
    "code_intel.lsp.extra.actions",
    "code_intel.lsp.extra.completion",
    "code_intel.lsp.extra.definition",
    "code_intel.lsp.extra.signatures",
    "code_intel.lsp.extra.tokens",
    "code_intel.lsp.extra.registration",
    "code_intel.tools",
    "code_intel.tools.base",
    "code_intel.tools.symbols",
    "code_intel.tools.symbols_extractor",
    "code_intel.tools.analysis",
    "code_intel.tools.search",
    "code_intel.tools.edit",
    "code_intel.tools.impact",
    "code_intel.tools.export",
    "code_intel.tools.cache",
    "code_intel.tools.language",
    "code_intel.tools.complexity",
    "code_intel.tools.unused",
    "code_intel.tools.diagram",
    "code_intel.tools.duplicates_extractor",
    "code_intel.tools.explain_extractor",
    "code_intel.tools.git",
    "code_intel.tools.custom",
    "code_intel.tools.migration",
    "code_intel.tools.diff_analysis",
    "code_intel.tools.timeline",
    "code_intel.tools.knowledge_graph",
    "code_intel.tools.review_assistant",
}


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Clean up code_intel submodules between tests (except _KEEP list).

    This prevents test-to-test interference from cached module state.
    Note: xdist workers do NOT run this hook reliably — use
    _xdist_isolation_reset fixture instead for xdist-safe cleanup.
    """
    for key in list(sys.modules.keys()):
        if key.startswith("code_intel.") and key not in _KEEP:
            del sys.modules[key]


# ═══════════════════════════════════════════════════════════════════════════
# xdist Isolation — Auto-Reset Fixture (works in parallel workers)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _xdist_isolation_reset() -> None:
    """Reset plugin global state between tests for xdist worker isolation.

    xdist runs each test worker in a separate subprocess. The
    pytest_runtest_setup hook runs in the main process only — it is NOT
    propagated to xdist workers. This autouse fixture runs IN each worker
    and resets global state that would otherwise leak between tests:

    - _SYMBOL_CACHE / clear_symbol_cache()
    - LSP Manager bridges (shuts down stale servers)
    - _LANGUAGE_PARSERS cache
    - hooks._pre_llm_call_cache
    - _PERSIST_DIR in tools/cache.py

    Without this, tests that pass in isolation fail in the full suite
    because one test's cached state affects another test's assertions.
    """
    try:
        from code_intel.tools.base import _LANGUAGE_PARSERS, _SYMBOL_CACHE, clear_symbol_cache
        from code_intel.tools.cache import _PERSIST_DIR as _cache_persist_dir
    except ImportError:
        _LANGUAGE_PARSERS = {}
        _SYMBOL_CACHE = {}
        _cache_persist_dir = {}

        def clear_symbol_cache() -> None:
            _SYMBOL_CACHE.clear()

    clear_symbol_cache()
    _LANGUAGE_PARSERS.clear()

    # Reset cache module persist dir (used in _import_graph tests)
    if isinstance(_cache_persist_dir, dict):
        _cache_persist_dir.clear()

    # Shut down LSP bridges so next test starts fresh
    try:
        from code_intel.lsp.bridge import get_lsp_manager

        mgr = get_lsp_manager()
        mgr.shutdown_all()
    except Exception:
        pass  # LSP manager not initialized yet — noop

    # Clear hook cache
    try:
        from code_intel.hooks import _pre_llm_call_cache

        _pre_llm_call_cache.clear()
    except Exception:
        pass  # Module not imported yet

    yield

    # Post-test cleanup (ensures state doesn't leak into next xdist cycle)
    clear_symbol_cache()
    try:
        mgr.shutdown_all()  # type: ignore[possibly-undefined]
    except Exception:
        pass
