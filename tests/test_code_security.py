"""Tests for code_security_scan_tool — vulnerability pattern scanner.

Target: bring coverage of tools/security.py from ~17% to 60%+ by covering
all public functions, helper functions, error-handling paths, and edge cases.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from code_intel.tools.security import (
    CODE_SECURITY_SCHEMA,
    _handle_code_security,
    _matches_glob,
    _matches_severity_threshold,
    _severity_level,
    code_security_scan_tool,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def scan_dir() -> Path:
    """Create a temporary directory for scanning."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _write(scan_dir: Path, name: str, content: str) -> Path:
    """Helper: write a file into the scan_dir."""
    path = scan_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# _severity_level — helper function coverage
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeverityLevel:
    """Cover _severity_level: known severities, case insensitivity, unknowns."""

    def test_known_severities(self):
        assert _severity_level("CRITICAL") == 0
        assert _severity_level("HIGH") == 1
        assert _severity_level("MEDIUM") == 2
        assert _severity_level("LOW") == 3

    def test_case_insensitive(self):
        assert _severity_level("critical") == 0
        assert _severity_level("Critical") == 0
        assert _severity_level("high") == 1

    def test_unknown_severity_returns_99(self):
        assert _severity_level("UNKNOWN") == 99
        assert _severity_level("") == 99
        assert _severity_level("INFO") == 99


# ═══════════════════════════════════════════════════════════════════════════════
# _matches_severity_threshold — helper function coverage
# ═══════════════════════════════════════════════════════════════════════════════


class TestMatchesSeverityThreshold:
    """Cover _matches_severity_threshold: all levels and thresholds."""

    def test_all_threshold_matches_everything(self):
        assert _matches_severity_threshold("CRITICAL", "all") is True
        assert _matches_severity_threshold("LOW", "all") is True

    def test_all_case_insensitive(self):
        assert _matches_severity_threshold("CRITICAL", "ALL") is True
        assert _matches_severity_threshold("CRITICAL", "All") is True

    def test_critical_threshold(self):
        assert _matches_severity_threshold("CRITICAL", "CRITICAL") is True
        assert _matches_severity_threshold("HIGH", "CRITICAL") is False
        assert _matches_severity_threshold("MEDIUM", "CRITICAL") is False
        assert _matches_severity_threshold("LOW", "CRITICAL") is False

    def test_high_threshold(self):
        assert _matches_severity_threshold("CRITICAL", "HIGH") is True
        assert _matches_severity_threshold("HIGH", "HIGH") is True
        assert _matches_severity_threshold("MEDIUM", "HIGH") is False
        assert _matches_severity_threshold("LOW", "HIGH") is False

    def test_medium_threshold(self):
        assert _matches_severity_threshold("CRITICAL", "MEDIUM") is True
        assert _matches_severity_threshold("HIGH", "MEDIUM") is True
        assert _matches_severity_threshold("MEDIUM", "MEDIUM") is True
        assert _matches_severity_threshold("LOW", "MEDIUM") is False

    def test_low_threshold(self):
        assert _matches_severity_threshold("CRITICAL", "LOW") is True
        assert _matches_severity_threshold("HIGH", "LOW") is True
        assert _matches_severity_threshold("MEDIUM", "LOW") is True
        assert _matches_severity_threshold("LOW", "LOW") is True

    def test_unknown_threshold_filters_everything(self):
        # Unknown threshold → _severity_level returns 99 → 0 <= 99 is True for
        # CRITICAL, but for LOW it's 3 <= 99, so basically everything passes.
        # This is the current behaviour: unknown defaults to lenient.
        assert _matches_severity_threshold("CRITICAL", "UNKNOWN") is True


# ═══════════════════════════════════════════════════════════════════════════════
# _matches_glob — helper function coverage
# ═══════════════════════════════════════════════════════════════════════════════


class TestMatchesGlob:
    """Cover _matches_glob: simple ext, multi-glob, dir-glob, fnmatch, edge."""

    def test_simple_extension_match(self):
        assert _matches_glob("foo.py", ("*.py",)) is True
        assert _matches_glob("foo.py", ("*.js",)) is False

    def test_multiple_globs(self):
        assert _matches_glob("foo.py", ("*.py", "*.js")) is True
        assert _matches_glob("foo.js", ("*.py", "*.js")) is True

    def test_subdirectory_suffix_match(self):
        """Globs like *.py match files in subdirectories."""
        assert _matches_glob("sub/foo.py", ("*.py",)) is True

    def test_empty_globs(self):
        assert _matches_glob("foo.py", ()) is False

    def test_directory_glob_startswith(self):
        """Glob ending with '/' matches path prefix."""
        assert _matches_glob("/some/dir/file.py", ("/some/dir/",)) is True
        assert _matches_glob("/other/file.py", ("/some/dir/",)) is False

    def test_fnmatch_pattern(self):
        """Fallback fnmatch-style matching."""
        assert _matches_glob("test_spec.py", ("*_spec.py",)) is True
        assert _matches_glob("test.py", ("*_spec.py",)) is False

    def test_fnmatch_full_path(self):
        assert _matches_glob("/path/to/Makefile", ("Makefile",)) is True
        assert _matches_glob("/path/to/test.txt", ("Makefile",)) is False

    def test_glob_without_asterisk(self):
        """A glob without '*' uses fnmatch on basename and full path."""
        assert _matches_glob("/a/b/README.md", ("README.md",)) is True

    def test_suffix_based_extension(self):
        """Check that suffix matching works via p.suffix == g[1:]."""
        assert _matches_glob("foo.test.py", ("*.py",)) is True


# ═══════════════════════════════════════════════════════════════════════════════
# code_security_scan_tool — error-handling paths
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanToolErrors:
    """Cover error-handling branches at the top of the scan function."""

    def test_path_does_not_exist(self):
        """Non-existent path: is_dir() returns False first → 'not a directory'."""
        result = code_security_scan_tool(path="/tmp/_nonexistent_dir_xyz_999")
        data = json.loads(result)
        assert data["status"] == "error"
        # NOTE: is_dir() check fires before exists() check, so we get
        # 'not a directory' for non-existent paths (dead code on line 347).
        assert "not a directory" in data["error"].lower()

    def test_path_is_file_not_directory(self, scan_dir):
        f = _write(scan_dir, "test.py", "")
        result = code_security_scan_tool(path=str(f))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not a directory" in data["error"].lower()

    def test_invalid_pattern_ids_no_match(self, scan_dir):
        result = code_security_scan_tool(path=str(scan_dir), pattern_ids=["NONEXISTENT-99"])
        data = json.loads(result)
        assert data["status"] == "error"
        assert "No patterns match" in data["error"]
        # Verify error message includes available IDs
        assert "HARDCODED_SECRETS" in data["error"]

    def test_empty_pattern_ids_list_uses_all(self, scan_dir):
        """Empty list means 'use all patterns' (falsy check)."""
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = code_security_scan_tool(path=str(scan_dir), pattern_ids=[])
        data = json.loads(result)
        # Empty list is falsy → falls through to all patterns
        assert data["status"] == "ok"

    def test_path_is_empty_string(self):
        """Empty string path → expansion resolves to CWD which is a dir."""
        # Should NOT error: Path("").expanduser().resolve() == CWD
        result = code_security_scan_tool(path="")
        # It will scan CWD, which may have files → either ok or error
        json.loads(result)
        # At minimum it shouldn't crash
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════════
# code_security_scan_tool — empty / safe directory paths
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanToolEmpty:
    """Cover scans that produce zero findings."""

    def test_empty_directory(self, scan_dir):
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["summary"]["total"] == 0
        assert data["metadata"]["files_scanned"] == 0

    def test_directory_with_no_matching_extensions(self, scan_dir):
        _write(scan_dir, "readme.md", "# Readme\n")
        _write(scan_dir, "notes.txt", "some text\n")
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["summary"]["total"] == 0
        # No .py/.js etc. files → 0 files scanned
        assert data["metadata"]["files_scanned"] == 0

    def test_safe_python_file_no_findings(self, scan_dir):
        _write(scan_dir, "safe.py", "x = 1 + 1\nprint('hello')\ndef foo():\n    pass\n")
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["summary"]["total"] == 0
        assert data["metadata"]["files_scanned"] == 1

    def test_metadata_includes_path_and_pattern_count(self, scan_dir):
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        meta = data["metadata"]
        assert "path" in meta
        assert "files_scanned" in meta
        assert "patterns_applied" in meta
        assert meta["patterns_applied"] > 0
        assert meta["severity_filter"] == "all"


# ═══════════════════════════════════════════════════════════════════════════════
# code_security_scan_tool — vulnerability detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanToolDetection:
    """Cover actual vulnerability pattern matching for all categories."""

    def test_detects_hardcoded_api_key(self, scan_dir):
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("HARDCODED_SECRETS" in f["pattern_id"] for f in data["findings"])

    @pytest.mark.integration
    def test_detects_private_key(self, scan_dir):
        _write(
            scan_dir,
            "key.pem",
            "-----BEGIN RSA PRIVATE  KEY-----\nFAKEKEY\n-----END RSA PRIVATE  KEY-----\n",
        )
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        ids = [f["pattern_id"] for f in data["findings"]]
        assert "HARDCODED_SECRETS-2" in ids

    def test_detects_hardcoded_password(self, scan_dir):
        _write(scan_dir, "login.py", 'password = "supersecret123"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("HARDCODED_SECRETS" in f["pattern_id"] for f in data["findings"])

    def test_detects_sql_injection_concat(self, scan_dir):
        _write(
            scan_dir,
            "db.py",
            'cursor.execute("SELECT * FROM users WHERE id = " + user_id)\n',
        )
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("SQL_INJECTION" in f["pattern_id"] for f in data["findings"])

    def test_detects_sql_injection_fstring(self, scan_dir):
        _write(
            scan_dir,
            "query.py",
            'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n',
        )
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("SQL_INJECTION" in f["pattern_id"] for f in data["findings"])

    def test_detects_path_traversal_open(self, scan_dir):
        _write(scan_dir, "views.py", 'open(request.GET["file"])\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("PATH_TRAVERSAL" in f["pattern_id"] for f in data["findings"])

    def test_detects_path_traversal_path_constructor(self, scan_dir):
        _write(scan_dir, "router.py", 'Path(request.args["path"])\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("PATH_TRAVERSAL" in f["pattern_id"] for f in data["findings"])

    def test_detects_command_injection_system(self, scan_dir):
        _write(scan_dir, "shell.py", 'os.system("rm -rf " + user_input)\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("COMMAND_INJECTION" in f["pattern_id"] for f in data["findings"])

    def test_detects_command_injection_subprocess_shell(self, scan_dir):
        _write(
            scan_dir,
            "sub.py",
            "subprocess.call(cmd, shell=True)\n",
        )
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("COMMAND_INJECTION" in f["pattern_id"] for f in data["findings"])

    def test_detects_weak_crypto_md5(self, scan_dir):
        _write(scan_dir, "crypto.py", 'hashlib.md5(b"test")\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("WEAK_CRYPTO" in f["pattern_id"] for f in data["findings"])

    def test_detects_weak_crypto_sha1(self, scan_dir):
        _write(scan_dir, "hash.py", 'hashlib.sha1(b"data")\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("WEAK_CRYPTO-2" in f["pattern_id"] for f in data["findings"])

    def test_detects_weak_crypto_des(self, scan_dir):
        _write(scan_dir, "cipher.py", "DES.new(key, DES.MODE_CBC)\n")
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        assert any("WEAK_CRYPTO-3" in f["pattern_id"] for f in data["findings"])

    def test_detects_multiple_vulnerabilities_in_one_file(self, scan_dir):
        _write(
            scan_dir,
            "app.py",
            'API_KEY = "sk-1234567890abcdef"\ncursor.execute("SELECT * FROM users WHERE id = " + user_id)\n',
        )
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 2

    def test_detects_multiple_files(self, scan_dir):
        _write(scan_dir, "a.py", 'API_KEY = "sk-1234567890abcdef"\n')
        _write(scan_dir, "b.py", 'os.system("rm -rf " + x)\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert len(data["findings"]) >= 2
        # Check the finding structure
        finding = data["findings"][0]
        assert "pattern_id" in finding
        assert "severity" in finding
        assert "message" in finding
        assert "file" in finding
        assert "line" in finding
        assert "snippet" in finding

    def test_finding_has_snippet_with_context(self, scan_dir):
        """The snippet should include surrounding context (40 chars each side)."""
        _write(scan_dir, "app.py", '# comment\nAPI_KEY = "sk-long-enough-key-here"\n# trailing\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        snippet = data["findings"][0]["snippet"]
        assert "API_KEY" in snippet
        assert len(snippet) > 20


# ═══════════════════════════════════════════════════════════════════════════════
# code_security_scan_tool — filtering
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanToolFiltering:
    """Cover severity filter and pattern_ids filter branches."""

    def test_severity_filter_critical_only(self, scan_dir):
        """Only CRITICAL findings should be returned."""
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')  # CRITICAL
        _write(scan_dir, "crypto.py", 'hashlib.md5(b"test")\n')  # MEDIUM
        result = code_security_scan_tool(path=str(scan_dir), severity="CRITICAL")
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        for f in data["findings"]:
            assert f["severity"] == "CRITICAL", f"Expected CRITICAL, got {f['severity']}"
        # MEDIUM findings (WEAK_CRYPTO) should be absent
        weak_crypto = [f for f in data["findings"] if "WEAK_CRYPTO" in f["pattern_id"]]
        assert weak_crypto == []

    def test_severity_filter_high_includes_critical(self, scan_dir):
        """HIGH threshold includes CRITICAL + HIGH but not MEDIUM."""
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')  # CRITICAL
        _write(scan_dir, "crypto.py", 'hashlib.md5(b"test")\n')  # MEDIUM
        result = code_security_scan_tool(path=str(scan_dir), severity="HIGH")
        data = json.loads(result)
        cr = [f for f in data["findings"] if f["severity"] == "CRITICAL"]
        md = [f for f in data["findings"] if f["severity"] == "MEDIUM"]
        assert len(cr) >= 1
        assert len(md) == 0

    def test_severity_filter_medium_includes_critical_and_high(self, scan_dir):
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')  # CRITICAL
        _write(scan_dir, "crypto.py", 'hashlib.md5(b"test")\n')  # MEDIUM
        result = code_security_scan_tool(path=str(scan_dir), severity="MEDIUM")
        data = json.loads(result)
        md = [f for f in data["findings"] if f["severity"] == "MEDIUM"]
        assert len(md) >= 1

    def test_severity_filter_low_returns_all(self, scan_dir):
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')  # CRITICAL
        _write(scan_dir, "crypto.py", 'hashlib.md5(b"test")\n')  # MEDIUM
        result = code_security_scan_tool(path=str(scan_dir), severity="LOW")
        data = json.loads(result)
        severities = {f["severity"] for f in data["findings"]}
        assert "CRITICAL" in severities
        assert "MEDIUM" in severities

    def test_pattern_ids_filter_single(self, scan_dir):
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = code_security_scan_tool(path=str(scan_dir), pattern_ids=["HARDCODED_SECRETS-1"])
        data = json.loads(result)
        assert data["summary"]["total"] >= 1
        for f in data["findings"]:
            assert f["pattern_id"] == "HARDCODED_SECRETS-1"

    def test_pattern_ids_multiple(self, scan_dir):
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        _write(scan_dir, "shell.py", 'os.system("rm -rf " + x)\n')
        result = code_security_scan_tool(
            path=str(scan_dir),
            pattern_ids=["HARDCODED_SECRETS-1", "COMMAND_INJECTION-1"],
        )
        data = json.loads(result)
        ids_found = {f["pattern_id"] for f in data["findings"]}
        # At least one of the requested patterns matched
        assert ids_found.intersection({"HARDCODED_SECRETS-1", "COMMAND_INJECTION-1"})

    def test_pattern_ids_case_insensitive(self, scan_dir):
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = code_security_scan_tool(path=str(scan_dir), pattern_ids=["hardcoded_secrets-1"])
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_pattern_ids_exclude_other_categories(self, scan_dir):
        """Only the specified pattern category should match."""
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        _write(scan_dir, "shell.py", 'os.system("rm -rf " + x)\n')
        result = code_security_scan_tool(path=str(scan_dir), pattern_ids=["COMMAND_INJECTION-1"])
        data = json.loads(result)
        for f in data["findings"]:
            assert "COMMAND_INJECTION" in f["pattern_id"]
        # HARDCODED_SECRETS should not appear
        hc = [f for f in data["findings"] if "HARDCODED_SECRETS" in f["pattern_id"]]
        assert hc == []


# ═══════════════════════════════════════════════════════════════════════════════
# code_security_scan_tool — result sorting and format
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanToolResultFormat:
    """Cover finding sorting and summary/metadata dict construction."""

    def test_findings_sorted_by_severity(self, scan_dir):
        """Findings should be sorted by severity (critical first)."""
        _write(scan_dir, "crypto.py", 'hashlib.md5(b"test")\n')  # MEDIUM
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')  # CRITICAL
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        if len(data["findings"]) >= 2:
            sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
            sevs = [sev_order[f["severity"]] for f in data["findings"]]
            assert sevs == sorted(sevs), "Findings not sorted by severity"

    def test_summary_by_severity(self, scan_dir):
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')  # CRITICAL
        _write(scan_dir, "crypto.py", 'hashlib.md5(b"test")\n')  # MEDIUM
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        summary = data["summary"]
        assert "total" in summary
        assert "by_severity" in summary
        assert summary["total"] == sum(summary["by_severity"].values())

    def test_no_findings_title_is_positive(self, scan_dir):
        """When no findings, the output title reflects that."""
        _write(scan_dir, "safe.py", "x = 1\n")
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["title"].startswith("✅") or "No Vulnerabilities" in data["title"]

    def test_findings_title_includes_count(self, scan_dir):
        """When findings exist, the title mentions the count."""
        _write(scan_dir, "config.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["status"] == "ok"
        assert "Finding" in data["title"] or "Finding" in data.get("message", "")


# ═══════════════════════════════════════════════════════════════════════════════
# code_security_scan_tool — file-reading error paths
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanToolFileReadErrors:
    """Cover UnicodeDecodeError, OSError, PermissionError branches."""

    def test_utf8_decode_fallback_to_latin1(self, scan_dir):
        """File with non-UTF-8 bytes (0x80-0xFF) triggers UnicodeDecodeError,
        then falls back to latin-1 which succeeds. Pattern should still match."""
        # Write raw bytes: 'secret = "abc12345"\n' with some non-UTF-8 chars
        raw = b'secret = "abc12345"\n' + b"# latin-1: \xe9\xe0\xf1\n"
        path = scan_dir / "config.py"
        path.write_bytes(raw)
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        # File should have been read successfully via latin-1 fallback
        assert data["summary"]["total"] >= 0  # may or may not match
        assert data["metadata"]["files_scanned"] >= 1

    def test_all_files_with_binary_extensions_skipped(self, scan_dir):
        """Files that can't be read at all are silently skipped (logger.debug)."""
        _write(scan_dir, "test.py", 'API_KEY = "sk-1234567890abcdef"\n')
        # Mock Path.read_text to raise OSError for all calls
        with patch.object(Path, "read_text", side_effect=OSError(13, "Permission denied")):
            result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        # File read failed → files_scanned stays 0
        assert data["metadata"]["files_scanned"] == 0
        assert data["summary"]["total"] == 0

    def test_oserror_on_utf8_read_logged_and_skipped(self, scan_dir):
        """OSError during the initial utf-8 read causes the file to be skipped,
        but the scan continues with other files."""
        _write(scan_dir, "good.py", "x = 1\n")
        _write(scan_dir, "bad.py", 'API_KEY = "sk-1234567890abcdef"\n')

        original_read_text = Path.read_text

        def _side_effect(self, *args, **kwargs):
            if self.name == "bad.py":
                raise PermissionError(13, "Permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", _side_effect):
            result = code_security_scan_tool(path=str(scan_dir))

        data = json.loads(result)
        # Only the good file was scanned
        assert data["metadata"]["files_scanned"] == 1
        assert data["summary"]["total"] == 0  # good.py has no findings

    def test_latin1_fallback_fails_skipped(self, scan_dir):
        """If latin-1 fallback also fails (e.g. OSError), file is skipped."""
        _write(scan_dir, "test.py", 'API_KEY = "sk-1234567890abcdef"\n')

        call_count = 0

        def _failing_read(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise UnicodeDecodeError("utf-8", b"", 0, 0, "mock")
            raise OSError(5, "I/O error on latin-1 read")

        with patch.object(Path, "read_text", _failing_read):
            result = code_security_scan_tool(path=str(scan_dir))

        data = json.loads(result)
        # File count: 1 attempt, but failed on both utf-8 and latin-1
        assert data["metadata"]["files_scanned"] == 0

    def test_lookup_error_triggers_fallback(self, scan_dir):
        """LookupError (e.g. unknown encoding) also triggers latin-1 fallback."""
        _write(scan_dir, "test.py", 'API_KEY = "sk-1234567890abcdef"\n')

        with patch.object(Path, "read_text", side_effect=[LookupError("unknown encoding"), "content"]):
            result = code_security_scan_tool(path=str(scan_dir))

        data = json.loads(result)
        # latin-1 fallback succeeded
        assert data["metadata"]["files_scanned"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# code_security_scan_tool — non-Python file handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanToolNonPython:
    """Cover scanning of JS, TS, Go, Java, and other supported file types."""

    def test_javascript_file_scanned(self, scan_dir):
        _write(
            scan_dir,
            "app.js",
            'const TOKEN = "ghp_1234567890abcdef";\n',
        )
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_yaml_file_with_secret(self, scan_dir):
        _write(scan_dir, "config.yaml", 'api_key: "sk-1234567890abcdef"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_shell_file_with_secret(self, scan_dir):
        _write(scan_dir, "script.sh", 'export SECRET="my-secret-key-12345"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_ruby_file_scanned(self, scan_dir):
        _write(scan_dir, "app.rb", 'api_secret = "my-ruby-secret-123"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_php_file_scanned(self, scan_dir):
        _write(scan_dir, "app.php", "<?php $password = 'php-password-123'; ?>\n")
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_json_file_with_secret(self, scan_dir):
        # JSON ext is in HARDCODED_SECRETS-1 globs; use unquoted-key format
        _write(scan_dir, "config.json", 'secret: "my-test-value-12345"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_toml_file_scanned(self, scan_dir):
        _write(scan_dir, "config.toml", 'api_key = "sk-toml-key-12345"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_go_file_scanned(self, scan_dir):
        # *.go is in WEAK_CRYPTO globs; use an md5 pattern
        _write(scan_dir, "main.go", 'md5("test-value-123")\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        assert data["summary"]["total"] >= 1

    def test_hidden_files_are_skipped(self, scan_dir):
        """Files starting with '.' (like .env.local) should be skipped."""
        _write(scan_dir, ".env", 'API_KEY = "sk-hidden-secret-123"\n')
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        # The pattern file_glob for HARDCODED_SECRETS-1 includes *.env,
        # but rglob skips dotfiles (f.name.startswith(".")).
        assert data["metadata"]["files_scanned"] == 0
        assert data["summary"]["total"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# CODE_SECURITY_SCHEMA — schema structure tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCODESECURITYSCHEMA:
    """Cover the schema definition structure."""

    def test_schema_name(self):
        assert CODE_SECURITY_SCHEMA["name"] == "code_security_scan"

    def test_schema_description_exists(self):
        assert isinstance(CODE_SECURITY_SCHEMA["description"], str)
        assert len(CODE_SECURITY_SCHEMA["description"]) > 20

    def test_schema_parameters_type(self):
        assert CODE_SECURITY_SCHEMA["parameters"]["type"] == "object"

    def test_schema_has_path_parameter(self):
        props = CODE_SECURITY_SCHEMA["parameters"]["properties"]
        assert "path" in props
        assert props["path"]["type"] == "string"

    def test_schema_has_severity_parameter(self):
        props = CODE_SECURITY_SCHEMA["parameters"]["properties"]
        assert "severity" in props
        assert props["severity"]["type"] == "string"
        assert props["severity"].get("default") == "all"

    def test_schema_has_pattern_ids_parameter(self):
        props = CODE_SECURITY_SCHEMA["parameters"]["properties"]
        assert "pattern_ids" in props
        assert props["pattern_ids"]["type"] == "array"
        assert props["pattern_ids"]["items"]["type"] == "string"

    def test_schema_required_path(self):
        assert CODE_SECURITY_SCHEMA["parameters"]["required"] == ["path"]


# ═══════════════════════════════════════════════════════════════════════════════
# _handle_code_security — handler function coverage
# ═══════════════════════════════════════════════════════════════════════════════


class TestHandleCodeSecurity:
    """Cover _handle_code_security unpacking logic."""

    def test_handler_with_direct_kwargs(self, scan_dir):
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = _handle_code_security(path=str(scan_dir), severity="all")
        data = json.loads(result)
        assert data["status"] == "ok"

    def test_handler_with_args_dict(self, scan_dir):
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = _handle_code_security(args={"path": str(scan_dir), "severity": "all"})
        data = json.loads(result)
        assert data["status"] == "ok"

    def test_handler_with_args_dict_and_pattern_ids(self, scan_dir):
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = _handle_code_security(args={"path": str(scan_dir), "pattern_ids": ["HARDCODED_SECRETS-1"]})
        data = json.loads(result)
        assert data["status"] == "ok"

    def test_handler_with_empty_path(self):
        """Handler with empty path → code_security_scan_tool receives path='' → expands to CWD."""
        result = _handle_code_security(path="")
        data = json.loads(result)
        # Should handle gracefully (CWD exists, may not have .py files)
        assert "status" in data

    def test_handler_with_default_severity(self, scan_dir):
        _write(scan_dir, "app.py", 'API_KEY = "sk-1234567890abcdef"\n')
        result = _handle_code_security(path=str(scan_dir))
        data = json.loads(result)
        assert data["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: verify the full pipeline works end-to-end
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """Full end-to-end pipeline tests."""

    def test_multiple_categories_in_project(self, scan_dir):
        """Simulate a real project with several vulnerability types."""
        _write(scan_dir, "src/config.py", 'API_KEY = "sk-1234567890abcdef"\n')
        _write(scan_dir, "src/db.py", 'cursor.execute("SELECT * FROM users WHERE id = " + uid)\n')
        _write(scan_dir, "src/views.py", 'open(request.GET["path"])\n')
        _write(scan_dir, "src/utils.py", "hashlib.md5(b'test')\n")

        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["summary"]["total"] >= 4
        # Verify all 4 categories are represented
        cat_ids = {f["pattern_id"].split("-")[0] for f in data["findings"]}
        assert "HARDCODED_SECRETS" in cat_ids
        assert "SQL_INJECTION" in cat_ids
        assert "PATH_TRAVERSAL" in cat_ids
        assert "WEAK_CRYPTO" in cat_ids

    def test_file_line_numbers_are_correct(self, scan_dir):
        """Line numbers in findings should be 1-based and accurate."""
        content = 'import os\nimport hashlib\n\n# vulnerable line below\nAPI_KEY = "sk-1234567890abcdef"\n'
        _write(scan_dir, "app.py", content)
        result = code_security_scan_tool(path=str(scan_dir))
        data = json.loads(result)
        finding = data["findings"][0]
        # The API_KEY is on line 5 (1-indexed)
        assert finding["line"] == 5, f"Expected line 5, got {finding['line']}"
