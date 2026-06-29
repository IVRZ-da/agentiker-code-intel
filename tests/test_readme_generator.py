"""Tests for the README auto-generator — validates that tools are detected correctly."""

from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def test_generator_finds_tools():
    """The generator should find all tools via Python import from _profiles.py."""
    import sys
    sys.path.insert(0, str(PLUGIN_DIR.parent))
    from scripts.generate_readme import CodeIntelReadmeGenerator

    gen = CodeIntelReadmeGenerator(PLUGIN_DIR)
    tools = gen.get_tools()

    # Currently broken — shows 0 tools (regression test)
    # Expected: 70 tools
    assert len(tools) > 0, (
        f"Generator found {len(tools)} tools — expected > 0! "
        "The _TOOL_PROFILES regex in generate_readme.py is broken."
    )
    assert len(tools) >= 70, (
        f"Generator found {len(tools)} tools — expected >= 70. "
        "Some tools are missing from the generated list."
    )
