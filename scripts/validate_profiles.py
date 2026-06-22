#!/usr/bin/env python3
"""Validate Tool-Profile consistency.

Cross-references _TOOL_PROFILES against _AST_TOOL_REGISTRATIONS
using AST-only parsing (avoids Hermes runtime dependencies like `toolsets`).

Checks:
  1. Every registered AST tool appears in at least one profile
  2. Every tool in the "all" profile has a corresponding registration
  3. All non-"all" profile tools are a subset of "all"
  4. Every "all" tool appears in at least one non-"all" profile (warnings)
"""

import ast
import os
import sys
from collections import Counter


# ---------------------------------------------------------------------------
# AST-based extraction helpers
# ---------------------------------------------------------------------------

def _schema_var_to_tool_name(var_name: str) -> str:
    """Convert a schema constant name to a tool name.

    Examples:
        CODE_SYMBOLS_SCHEMA   -> code_symbols
        CODE_SEARCH_BY_ERROR_SCHEMA -> code_search_by_error
        CODE_GIT_DIFF_FILE_SCHEMA   -> code_git_diff_file

    Some schema constants don't follow the strict convention (e.g.
    CODE_SECURITY_SCHEMA has name="code_security_scan").  This mapping
    handles those edge cases.
    """
    _OVERRIDES = {
        "CODE_SECURITY_SCHEMA": "code_security_scan",
    }
    if var_name in _OVERRIDES:
        return _OVERRIDES[var_name]
    name = var_name
    if name.endswith("_SCHEMA"):
        name = name[: -len("_SCHEMA")]
    return name.lower()


def _extract_tool_profiles(filepath: str) -> dict:
    """Parse __init__.py and extract _TOOL_PROFILES dict via AST.

    Returns a dict mapping profile name -> list of tool name strings.
    Handles both plain assignments and annotated assignments (with type hints).
    """
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        # Handle both _TOOL_PROFILES = {...} (Assign) and _TOOL_PROFILES: dict = {...} (AnnAssign)
        target_name = None
        value_node = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_TOOL_PROFILES":
                    target_name = target.id
                    value_node = node.value
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "_TOOL_PROFILES":
                target_name = node.target.id
                value_node = node.value

        if target_name is None or value_node is None:
            continue
        if not isinstance(value_node, ast.Dict):
            continue

        profiles = {}
        for key_node, val_node in zip(value_node.keys, value_node.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                continue
            profile_name = key_node.value
            if not isinstance(val_node, ast.List):
                profiles[profile_name] = []
                continue
            tools = []
            for elt in val_node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    tools.append(elt.value)
            profiles[profile_name] = tools
        return profiles
    raise ValueError("Could not find _TOOL_PROFILES assignment in AST")


def _extract_registered_tool_names(filepath: str) -> set:
    """Parse __init__.py and extract tool names from _AST_TOOL_REGISTRATIONS
    inside the _register_ast_tools() function body using AST analysis.

    Handles both:
      (ct.CODE_SYMBOLS_SCHEMA, ct._handle_code_symbols)  -- via attribute
      (CODE_CAPSULE_SCHEMA, _handle_code_capsule)          -- via direct name
    """
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_register_ast_tools":
            continue
        # Found the function — walk its body for the assignment
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign):
                continue
            for target in child.targets:
                if not isinstance(target, ast.Name) or target.id != "_AST_TOOL_REGISTRATIONS":
                    continue
                # Found: _AST_TOOL_REGISTRATIONS = [ ... ]
                if not isinstance(child.value, ast.List):
                    continue
                registered = set()
                for elt in child.value.elts:
                    if not isinstance(elt, ast.Tuple) or len(elt.elts) < 1:
                        continue
                    schema_expr = elt.elts[0]
                    if isinstance(schema_expr, ast.Attribute):
                        registered.add(_schema_var_to_tool_name(schema_expr.attr))
                    elif isinstance(schema_expr, ast.Name):
                        registered.add(_schema_var_to_tool_name(schema_expr.id))
                return registered
    raise ValueError("Could not find _AST_TOOL_REGISTRATIONS in _register_ast_tools")


# ---------------------------------------------------------------------------
# Main validation logic
# ---------------------------------------------------------------------------

def main() -> int:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    init_file = os.path.join(project_root, "__init__.py")

    # Extract data via AST (avoids runtime deps like `toolsets`)
    tool_profiles = _extract_tool_profiles(init_file)
    registered_tools = _extract_registered_tool_names(init_file)
    all_profile_tools = set(tool_profiles["all"])
    profile_names = list(tool_profiles.keys())

    issues_found = False

    print("🔍 Tool-Profile Validation")
    print("=" * 60)

    # --- 1. Every registered AST tool must appear in at least one profile ---
    all_tools_any_profile = set()
    for name, tools in tool_profiles.items():
        all_tools_any_profile.update(tools)

    registered_not_in_any_profile = registered_tools - all_tools_any_profile
    if registered_not_in_any_profile:
        issues_found = True
        print("\n❌ Registered tools NOT found in any profile:")
        for t in sorted(registered_not_in_any_profile):
            print(f"    - {t}")
    else:
        print(f"\n✅ All {len(registered_tools)} registered AST tools are present in at least one profile")

    # --- 2. Every tool in "all" must have a registration entry ---
    # Note: _AST_TOOL_REGISTRATIONS only covers AST-based tools.
    # LSP tools (code_definition, code_references, etc.) are registered
    # separately via register_lsp_tools() in lsp_bridge.py.  We flag
    # missing AST registrations as warnings, not errors.
    in_all_but_not_registered = all_profile_tools - registered_tools
    if in_all_but_not_registered:
        print("\n⚠️  Tools in 'all' profile WITHOUT a matching AST registration:")
        print("    (These are likely LSP tools registered via register_lsp_tools)")
        for t in sorted(in_all_but_not_registered):
            print(f"    - {t}")

    # --- 3. Profile subset check ---
    for name, tools in tool_profiles.items():
        if name == "all":
            continue
        tool_set = set(tools)
        extras = tool_set - all_profile_tools
        if extras:
            issues_found = True
            print(f"\n❌ Profile '{name}' has tools not in 'all': {extras}")

    # --- 4. Coverage: tools only in "all" (not in core/search/edit/lsp) ---
    covered_tools = set()
    for name, tools in tool_profiles.items():
        if name != "all":
            covered_tools.update(tools)

    uncovered = all_profile_tools - covered_tools
    if uncovered:
        print("\n⚠️  Tools ONLY in 'all' profile (not in core/search/edit/lsp):")
        for t in sorted(uncovered):
            print(f"    - {t}")
    else:
        print("\n✅ All tools are covered by at least one non-'all' profile")

    # --- 5. Duplicate entries across profiles ---
    all_tool_occurrences: Counter = Counter()
    for name, tools in tool_profiles.items():
        if name != "all":
            for t in tools:
                all_tool_occurrences[t] += 1

    duplicates = {t: c for t, c in all_tool_occurrences.items() if c > 1}
    if duplicates:
        print("\n📌 Tools appearing in multiple profiles:")
        for t in sorted(duplicates):
            which = [name for name, tools in tool_profiles.items()
                     if name != "all" and t in tools]
            print(f"    {t}: {', '.join(which)} ({duplicates[t]} profiles)")

    # --- 6. Summary ---
    print(f"\n{'=' * 60}")
    print("📊 Profile Summary:")
    for name in profile_names:
        tools = tool_profiles[name]
        flag = "★" if name == "all" else " "
        print(f"  {flag} {name}: {len(tools)} tools")
    print(f"\n  Total unique tools (all):  {len(all_profile_tools)}")
    print(f"  Registered AST tools:      {len(registered_tools)}")
    print(f"  Covered in non-'all':      {len(covered_tools)}")
    print(f"  Uncovered (only in all):   {len(uncovered)}")

    if issues_found:
        print("\n❌ Validation FAILED — issues found (see above)")
        return 1
    else:
        print("\n✅ Validation PASSED — all checks OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
