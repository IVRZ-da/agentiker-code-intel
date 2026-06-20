"""
sys.modules isolation for code_intel tests.

Prevents 26 collection errors caused by __init__.py importing:
  - hermes_cli.plugins (PluginContext, invoke_hook, has_hook)
  - toolsets (TOOLSETS, get_toolset, etc.)
  - tools.registry (registry.dispatch)
  - tools.delegate_tool (_build_child_system_prompt, DEFAULT_TOOLSETS, etc.)

Pattern from analysis-plugin conftest.py — injects mocks into sys.modules
BEFORE any code_intel module is imported, so the plugin code sees valid
mocked dependencies during import.
"""

import sys
import types
import os
from typing import Any

# ── sys.path fix: Plugin-Ordner muss als "code_intel" Package importierbar sein ──
# Das Plugin-Verzeichnis IST das code_intel Package (hat __init__.py im Root).
# Im CI-Container (docker://node-pytest) ist der Ordner als /repo gemountet,
# sodass "import code_intel" nicht funktioniert (Python sucht ein code_intel/
# Unterverzeichnis). Lösung: parent von __file__ (Plugin-Root) suchen und
# dessen PARENT in sys.path aufnehmen.
_conftest_dir = os.path.dirname(os.path.abspath(__file__))
_plugin_root = os.path.dirname(_conftest_dir)  # tests/.. = plugin root
_parent_dir = os.path.dirname(_plugin_root)    # parent of plugin root
_pkg_name = "code_intel"
if _parent_dir not in sys.path and os.path.isdir(os.path.join(_parent_dir, _pkg_name)):
    sys.path.insert(0, _parent_dir)


# ---------------------------------------------------------------------------
# hermes_cli.plugins Mock
# ---------------------------------------------------------------------------

class MockPluginContext:
    """Mock für PluginContext der Hermes Plugin-API.

    Registriert hooks, skills und tools in Dicts statt echter Hermes-API.
    """

    def __init__(self):
        self.hooks = {}
        self.skills = []
        self.tools = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_skill(self, name, path, description):
        self.skills.append({"name": name, "path": path, "description": description})

    def register_tool(self, name, toolset, schema, handler, description=None):
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "description": description,
        }

    def register_toolset(self, name, description, tools, exclusive=False):
        pass

    def get_tool(self, name):
        return self.tools.get(name)


def invoke_hook(*args, **kwargs) -> list:
    """Mock invoke_hook — gibt leere Liste zurück."""
    return []


def has_hook(*args, **kwargs) -> bool:
    """Mock has_hook — meldet keine Hooks."""
    return False


# Module-Baum für hermes_cli anlegen
_hermes_cli_pkg = types.ModuleType("hermes_cli")
_hermes_cli_pkg.__path__ = []
_plugins_mod = types.ModuleType("hermes_cli.plugins")
_plugins_mod.PluginContext = MockPluginContext
_plugins_mod.invoke_hook = invoke_hook
_plugins_mod.has_hook = has_hook
_hermes_cli_pkg.plugins = _plugins_mod
sys.modules["hermes_cli"] = _hermes_cli_pkg
sys.modules["hermes_cli.plugins"] = _plugins_mod


# ---------------------------------------------------------------------------
# toolsets Mock
# ---------------------------------------------------------------------------

# Minimaler TOOLSETS-Dict mit den Einträgen, die code_intel/__init__.py nutzt
_TOOLSETS_MOCK: dict = {
    "agentiker_code_intel": {
        "description": "Mock code intelligence toolset",
        "tools": [],
    },
}

def _get_toolset(name: str) -> list:
    return _TOOLSETS_MOCK.get(name, {}).get("tools", [])

def _resolve_toolset(name: str) -> list:
    return _TOOLSETS_MOCK.get(name, {}).get("tools", [])

def _get_all_toolsets() -> dict:
    return dict(_TOOLSETS_MOCK)

_toolsets_mod = types.ModuleType("toolsets")
_toolsets_mod.TOOLSETS = _TOOLSETS_MOCK
_toolsets_mod._HERMES_CORE_TOOLS = []
_toolsets_mod.get_toolset = _get_toolset
_toolsets_mod.resolve_toolset = _resolve_toolset
_toolsets_mod.get_all_toolsets = _get_all_toolsets
sys.modules["toolsets"] = _toolsets_mod


# ---------------------------------------------------------------------------
# tools.registry Mock
# ---------------------------------------------------------------------------

class MockEntry:
    """Mock für einen Registry-Eintrag."""

    def __init__(self, schema=None, handler=None):
        self.schema = schema or {"description": ""}
        self.handler = handler


class MockRegistry:
    """Mock für Tool-Registry mit dispatch-Funktion."""

    def __init__(self):
        self.entries: dict[str, MockEntry] = {}

    def get_entry(self, name):
        return self.entries.get(name)

    def register(self, name, **kw):
        self.entries[name] = MockEntry(kw.get("schema", {"description": ""}))

    def dispatch(self, name, args) -> str:
        import json
        return json.dumps({"tool": name, "status": "mocked", "args": args})

    def register_hook(self, name, handler):
        pass

    def has_hook(self, name):
        return False


_tools_pkg = types.ModuleType("tools")
_tools_reg_mod = types.ModuleType("tools.registry")
_tools_reg_mod.registry = MockRegistry()
_tools_reg_mod.dispatch = lambda n, a: __import__("json").dumps(
    {"tool": n, "status": "mocked", "args": a}
)
_tools_pkg.registry = _tools_reg_mod
sys.modules["tools"] = _tools_pkg
sys.modules["tools.registry"] = _tools_reg_mod


# ---------------------------------------------------------------------------
# tools.delegate_tool Mock
# ---------------------------------------------------------------------------

_delegate_mod = types.ModuleType("tools.delegate_tool")
_delegate_mod.DEFAULT_TOOLSETS = ["terminal", "file", "agentiker_code_intel"]
_delegate_mod._build_child_system_prompt = lambda *a, **kw: "base prompt"
_delegate_mod._build_child_agent = lambda *a, **kw: "child agent"
_delegate_mod._SUBAGENT_TOOLSETS = ["terminal", "file", "agentiker_code_intel"]
_delegate_mod._TOOLSET_LIST_STR = "'terminal', 'file', 'agentiker_code_intel'"
_delegate_mod._EXCLUDED_TOOLSET_NAMES = set()
_delegate_mod.DELEGATE_BLOCKED_TOOLS = set()
_delegate_mod.DELEGATE_TASK_SCHEMA = {
    "parameters": {
        "properties": {
            "toolsets": {
                "description": "Toolsets to enable",
                "type": "array",
                "items": {"type": "string"},
            },
            "tasks": {
                "type": "array",
                "items": {
                    "properties": {
                        "toolsets": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    }
                },
            },
        }
    }
}
sys.modules["tools.delegate_tool"] = _delegate_mod

# ---------------------------------------------------------------------------
# _fmt Mock — damit Tool-Handler JSON statt rich-Panels zurückgeben
# ---------------------------------------------------------------------------
import json
_fmt_mock = types.ModuleType("_fmt")
_fmt_mock.fmt_ok = lambda d, **kw: json.dumps({"status": "ok", **d}, ensure_ascii=False)
_fmt_mock.fmt_err = lambda m, **kw: json.dumps({"status": "error", "error": m})
_fmt_mock.fmt_info = lambda m, **kw: json.dumps({"info": m}, ensure_ascii=False)
_fmt_mock.fmt_warn = lambda m, **kw: json.dumps({"warn": m}, ensure_ascii=False)
_fmt_mock.fmt_tree = lambda d, **kw: json.dumps({"tree": d}, ensure_ascii=False)
_fmt_mock.fmt_code = lambda code, lang="python", **kw: json.dumps({"code": code, "lang": lang}, ensure_ascii=False)
_fmt_mock.fmt_json = lambda d, **kw: json.dumps(d, ensure_ascii=False)
sys.modules["_fmt"] = _fmt_mock
sys.modules["code_intel._fmt"] = _fmt_mock


# ---------------------------------------------------------------------------
# Per-Test Isolation: Cache zwischen Tests leeren
# ---------------------------------------------------------------------------

def pytest_runtest_setup(item):
    """Before each test: clear cached code_intel submodules.

    Removes reloaded instances of code_intel.* from sys.modules so that
    fresh imports in each test get the mocked deps. Does NOT purge
    code_intel.__init__ itself — some tests use importlib.reload() on it.
    """
    _KEEP = {
        "tools", "tools.registry", "toolsets",
        "hermes_cli", "hermes_cli.plugins",
        "tools.delegate_tool",
        "code_intel",           # the package entry itself
        "code_intel.__init__",  # needed by importlib.reload() tests
        "_fmt",                 # _fmt Mock für JSON statt rich-Panels
        "code_intel._fmt",      # _fmt Mock (Package-qualified)
    }
    for k in list(sys.modules.keys()):
        # Purge code_intel submodules (not the package itself) and any
        # dynamically-loaded code_intel.* artifacts from prev tests
        if k.startswith("code_intel.") and k not in _KEEP:
            del sys.modules[k]
        # Purge non-keep tools/hermes_cli entries that may have been
        # re-imported with stale state
        if k.startswith(("tools.", "hermes_cli.")):
            if k not in _KEEP:
                del sys.modules[k]
