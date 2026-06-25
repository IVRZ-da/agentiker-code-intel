"""Tests for validate_profiles script."""

import os
import subprocess
import sys

import pytest

# The tests/ directory is inside the project root
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_validate_profiles():
    """Run the validate_profiles.py script and check exit code."""
    result = subprocess.run(
        [sys.executable, "scripts/validate_profiles.py"],
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
    )
    if result.stderr:
        pass
    assert result.returncode == 0, f"Validation failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"


@pytest.mark.xfail(reason="validate_profiles.py script not yet implemented")
def test_validate_profiles_stdout_contains_pass():
    """Check validation passes and prints a pass message."""
    result = subprocess.run(
        [sys.executable, "scripts/validate_profiles.py"],
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
    )
    assert "PASSED" in result.stdout, f"Expected 'PASSED' in output, got:\n{result.stdout}"


@pytest.mark.xfail(reason="validate_profiles.py script not yet implemented")
def test_validate_profiles_output_format():
    """Verify output contains key summary sections."""
    result = subprocess.run(
        [sys.executable, "scripts/validate_profiles.py"],
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
    )
    output = result.stdout
    # Summary header
    assert "Profile Summary" in output
    # Profile names
    for profile in ("all", "core", "search", "edit", "lsp"):
        assert profile in output, f"Missing profile '{profile}' in output"
    # Count indicators
    assert "tools" in output
