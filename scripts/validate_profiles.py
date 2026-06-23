#!/usr/bin/env python3
"""Validate tool profiles in __init__.py.

Cross-references _TOOL_PROFILES against _AST_TOOL_REGISTRATIONS
using AST-only parsing (avoids Hermes runtime dependencies like `toolsets`).

Checks:
 1. Every registered AST tool appears in at least one profile
 2. Every tool in the "all" profile has a corresponding registration
 3. Sub-profile tools are subsets of "all"
 4. Coverage: tools only in "all" (missing from specific profiles)
 5. Duplicate entries across profiles
"""
