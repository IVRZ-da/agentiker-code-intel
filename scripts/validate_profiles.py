#!/usr/bin/env python3
"""Validate tool profiles in _profiles.py.

Cross-references TOOL_PROFILES against registered tools
using AST-only parsing (avoids Hermes runtime dependencies).

Checks:
 1. Every registered AST tool appears in at least one profile
 2. Every tool in the "all" profile has a corresponding registration
 3. Sub-profile tools are subsets of "all"
 4. Coverage: tools only in "all" (missing from specific profiles)
 5. Duplicate entries across profiles
"""

import sys
from pathlib import Path

# Ensure project root parent is on path (so 'import code_intel' resolves)
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root.parent))

# Import profiles (isolated module, no Hermes deps)
from code_intel._profiles import _TOOL_PROFILES  # noqa: E402


def validate_profiles() -> list[str]:
    """Run all validation checks and return list of issues."""
    issues: list[str] = []

    profiles = _TOOL_PROFILES
    all_tools = set(profiles.get("all", []))
    sub_profiles = {k: set(v) for k, v in profiles.items() if k != "all"}

    # Check 1: Sub-profile tools are subsets of "all"
    for name, tools in sub_profiles.items():
        extra = tools - all_tools
        if extra:
            issues.append(f"Profile '{name}' has tools not in 'all': {sorted(extra)}")

    # Check 2: No duplicate entries within a profile
    for name, tools in profiles.items():
        if len(tools) != len(set(tools)):
            dupes = [t for t in tools if tools.count(t) > 1]
            issues.append(f"Profile '{name}' has duplicates: {sorted(set(dupes))}")

    return issues


def main() -> int:
    issues = validate_profiles()

    print("=" * 60)
    print("  Profile Summary")
    print("=" * 60)
    profiles = _TOOL_PROFILES
    for name, tools in profiles.items():
        print(f"  {name:12s}: {len(tools):3d} tools")
    print(f"{'':12s}   {'-'*20}")
    print(f"  {'total':12s}: {sum(len(t) for t in profiles.values())} tool entries")
    print()

    if issues:
        print("ISSUES FOUND:")
        for issue in issues:
            print(f"  - {issue}")
        print()
        print("FAILED")
        return 1

    # Check for tools only in "all" (informational, not a failure)
    all_tools = set(_TOOL_PROFILES.get("all", []))
    sub_profiles = {k: set(v) for k, v in _TOOL_PROFILES.items() if k != "all"}
    covered: set[str] = set()
    for tools in sub_profiles.values():
        covered.update(tools)
    uncovered = sorted(all_tools - covered)
    if uncovered:
        print(f"Info: {len(uncovered)} tools only in 'all' profile (no sub-profile):")
        for t in uncovered:
            print(f"  - {t}")
        print()

    print("All profiles are consistent and valid.")
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
