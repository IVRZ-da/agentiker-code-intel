"""sys.modules isolation for test_plugin_init_registry.py

The tests in test_plugin_init_registry.py patch sys.modules to mock
internal imports inside register(). Without isolation, mocked entries
leak between tests when the full suite runs.

This conftest removes ALL code_intel/tools/lsp_bridge entries from
sys.modules BEFORE each test in this directory, so they are freshly
imported with the test's mocks in place.
"""

import sys


def pytest_runtest_setup(item):
    """Before each test: purge cached modules that registry tests mock."""
    if "test_plugin_init_registry" in str(item.fspath):
        _KEY_PREFIXES = ("code_intel", "tools", "lsp_bridge")
        for k in list(sys.modules.keys()):
            if any(k.startswith(p) or k == p for p in _KEY_PREFIXES):
                del sys.modules[k]
