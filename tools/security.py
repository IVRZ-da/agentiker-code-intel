#!/usr/bin/env python3
"""tools/security.py — Built-in vulnerability pattern scanner.

Provides code_security_scan_tool, CODE_SECURITY_SCHEMA, and
_handle_code_security for scanning projects against known vulnerability
patterns without external dependencies (no semgrep needed).

Vulnerability categories:
  - HARDCODED_SECRETS: API keys, passwords, tokens in source
  - SQL_INJECTION: raw SQL concatenation with unsanitized input
  - PATH_TRAVERSAL: unsanitized file paths from user input
  - COMMAND_INJECTION: shell commands with unsanitized input
  - WEAK_CRYPTO: MD5, SHA1, weak encryption usage
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# Vulnerability pattern definitions
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

_VULNERABILITY_PATTERNS: List[Dict[str, Any]] = [
    # ── HARDCODED_SECRETS ──────────────────────────────────────────────
    {
        "id": "HARDCODED_SECRETS-1",
        "severity": "CRITICAL",
        "message": "Hardcoded API key, secret, or password found. Keys and secrets "
        "should be stored in environment variables or a secret manager.",
        "pattern": re.compile(
            r"""(?x)
            (?:api[_-]?key|apikey|secret|password|passwd|pwd|token|auth_token|access_key)
            \s*[:=]\s*
            ['\"](?![${\s])   # not a variable reference
            [^'\"\n]{8,}       # at least 8 chars
            ['\"]
            """,
            re.IGNORECASE,
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.env",
                       "*.json", "*.yaml", "*.yml", "*.sh", "*.conf",
                       "*.ini", "*.cfg", "*.toml", "*.rb", "*.php"),
    },
    {
        "id": "HARDCODED_SECRETS-2",
        "severity": "CRITICAL",
        "message": "Embedded private key detected. Private keys must never be "
        "committed to version control.",
        "pattern": re.compile(
            r"-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH|PGP)\s+PRIVATE\s+KEY-----",
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.pem", "*.key", "*.cer",
                       "*.p12", "*.pfx", "*.sh"),
    },
    {
        "id": "HARDCODED_SECRETS-3",
        "severity": "HIGH",
        "message": "Hardcoded credential string found (password-like variable).",
        "pattern": re.compile(
            r"""(?x)
            (?:password|passwd|pwd|secret|credential)\s*=
            \s*['\"](?![${\s])
            [^'\"\n]{4,}
            ['\"]
            """,
            re.IGNORECASE,
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.env", "*.rb", "*.php"),
    },

    # ── SQL_INJECTION ──────────────────────────────────────────────────
    {
        "id": "SQL_INJECTION-1",
        "severity": "CRITICAL",
        "message": "Raw SQL query built with string concatenation or "
        "interpolation using f-strings. Use parameterized queries instead.",
        "pattern": re.compile(
            r"""(?x)
            (?:cursor|execute|exec|query|session\.execute|db\.execute|conn\.execute)
            \s*\(
            \s*
            (?:f['\"]|['\"])
            .*?(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE)
            .*?\+
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py",),
    },
    {
        "id": "SQL_INJECTION-2",
        "severity": "HIGH",
        "message": "Potential SQL injection via f-string or format() in "
        "database query. Use parameterized queries to prevent injection.",
        "pattern": re.compile(
            r"""(?x)
            (?:cursor|execute|exec|query)
            \s*\(
            \s*
            f['\"]
            .*?(?:SELECT|INSERT|UPDATE|DELETE)
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py", "*.js", "*.ts"),
    },
    {
        "id": "SQL_INJECTION-3",
        "severity": "HIGH",
        "message": "Raw SQL query string concatenation detected. Possible "
        "SQL injection vulnerability.",
        "pattern": re.compile(
            r"""(?x)
            (?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|DROP\s+TABLE)
            .{0,200}?
            ['\"]\s*\+\s*[a-zA-Z_]
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.go", "*.java", "*.rb", "*.php"),
    },

    # ── PATH_TRAVERSAL ─────────────────────────────────────────────────
    {
        "id": "PATH_TRAVERSAL-1",
        "severity": "HIGH",
        "message": "User-controlled input used in file path without "
        "sanitization. Risk of path traversal vulnerability.",
        "pattern": re.compile(
            r"""(?x)
            (?:open|read_text|read_bytes|write_text|write_bytes)
            \s*\(
            .*?
            (?:request\.(?:GET|POST|args|form|files|cookies|headers)
            |request\[|request\.get|request\.json
            |input\(|sys\.argv|os\.environ)
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py",),
    },
    {
        "id": "PATH_TRAVERSAL-2",
        "severity": "HIGH",
        "message": "Path construction using user input without validation. "
        "Could allow directory traversal attacks.",
        "pattern": re.compile(
            r"""(?x)
            Path\(
            .*?
            (?:request|input\(|sys\.argv|parse\.args|args\[)
            \s*\)
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py",),
    },
    {
        "id": "PATH_TRAVERSAL-3",
        "severity": "MEDIUM",
        "message": "Path.join or file read with potentially unsanitized input.",
        "pattern": re.compile(
            r"""(?x)
            (?:join|os\.path\.join|Path|readFile|readFileSync|fs\.readFile)
            .{0,50}?
            (?:req\.|request\.|params\.|query\.|body\.|input|argv)
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.go", "*.java"),
    },

    # ── COMMAND_INJECTION ──────────────────────────────────────────────
    {
        "id": "COMMAND_INJECTION-1",
        "severity": "CRITICAL",
        "message": "Shell command execution with string concatenation. "
        "Unsantized user input can lead to command injection.",
        "pattern": re.compile(
            r"""(?x)
            (?:os\.system|os\.popen|subprocess\.(?:call|Popen|run|check_call|check_output))
            \s*\(
            .*?\+
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py",),
    },
    {
        "id": "COMMAND_INJECTION-2",
        "severity": "CRITICAL",
        "message": "Shell=True in subprocess call with dynamic input. "
        "This can allow arbitrary command execution.",
        "pattern": re.compile(
            r"""(?x)
            subprocess\.(?:call|Popen|run|check_call|check_output)
            \s*\(
            .{0,200}?
            shell\s*=\s*True
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py",),
    },
    {
        "id": "COMMAND_INJECTION-3",
        "severity": "CRITICAL",
        "message": "Child process execution via template literal or "
        "string interpolation with user input. Risk of command injection.",
        "pattern": re.compile(
            r"""(?x)
            (?:exec|execSync|execFile|spawn|fork)\s*\(\s*
            [`'\"]
            .*?\$\{
            """,
            re.IGNORECASE,
        ),
        "file_glob": ("*.js", "*.ts", "*.jsx", "*.tsx"),
    },
    {
        "id": "COMMAND_INJECTION-4",
        "severity": "HIGH",
        "message": "Shell execution with potentially unsanitized input.",
        "pattern": re.compile(
            r"""(?x)
            (?:exec|eval|Runtime\.getRuntime\(\)\.exec|ProcessBuilder)
            .{0,100}?
            (?:request|input|userInput|params|query|body)
            """,
            re.IGNORECASE | re.DOTALL,
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.java", "*.go", "*.rb", "*.php"),
    },

    # ── WEAK_CRYPTO ────────────────────────────────────────────────────
    {
        "id": "WEAK_CRYPTO-1",
        "severity": "MEDIUM",
        "message": "Use of MD5 hash function. MD5 is cryptographically "
        "broken and should not be used for security purposes.",
        "pattern": re.compile(
            r"hashlib\.md5\(|md5\s*\(|MD5\s*\(|MessageDigest\.getInstance\(\"MD5\"",
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.java", "*.go"),
    },
    {
        "id": "WEAK_CRYPTO-2",
        "severity": "MEDIUM",
        "message": "Use of SHA-1 hash function. SHA-1 is deprecated and "
        "should be replaced with SHA-256 or SHA-3.",
        "pattern": re.compile(
            r"hashlib\.sha1\(|sha1\s*\(|SHA1\s*\(|MessageDigest\.getInstance\(\"SHA-1\"",
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.java", "*.go"),
    },
    {
        "id": "WEAK_CRYPTO-3",
        "severity": "LOW",
        "message": "Use of weak encryption algorithm (DES, RC2, or "
        "Blowfish). Use AES or ChaCha20 instead.",
        "pattern": re.compile(
            r"""(?x)
            (?:DES|DES3|RC2|Blowfish|ARC2|ARC4|RC4)
            (?:\.new|/CBC|/ECB|/OFB)
            |Cipher\.(?:DES|DES3|RC2|Blowfish)
            """,
            re.IGNORECASE,
        ),
        "file_glob": ("*.py", "*.js", "*.ts", "*.java", "*.go"),
    },
]


# ---------------------------------------------------------------------------
# Severity filter helpers
# ---------------------------------------------------------------------------

def _severity_level(sev: str) -> int:
    """Convert a severity string to a numeric level (lower = more severe)."""
    return _SEVERITY_ORDER.get(sev.upper(), 99)


def _matches_severity_threshold(check_sev: str, threshold: str) -> bool:
    """Return True if check_sev is at or above the threshold severity."""
    if threshold.lower() == "all":
        return True
    return _severity_level(check_sev) <= _severity_level(threshold)


def _matches_glob(filepath: str, globs: tuple) -> bool:
    """Check if a filepath's extension matches one of the glob patterns."""
    p = Path(filepath)
    for g in globs:
        if g.startswith("*."):
            if p.suffix == g[1:] or p.name.endswith(g[1:]):
                return True
        elif g.endswith("/"):
            if str(p).startswith(g):
                return True
        else:
            # Try fnmatch-style matching
            from fnmatch import fnmatch
            if fnmatch(p.name, g) or fnmatch(str(p), g):
                return True
    return False


# ---------------------------------------------------------------------------
# Excluded directories (system/hidden/build artifacts)
# ---------------------------------------------------------------------------

_IGNORED_DIRS: set = {
    ".git", ".svn", ".hg",
    "__pycache__", "node_modules", "bower_components",
    ".venv", "venv", ".env",
    ".next", "dist", "build", "target",
    ".hermes", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vscode",
}


# ---------------------------------------------------------------------------
# Core scanning logic
# ---------------------------------------------------------------------------

def code_security_scan_tool(
    path: str,
    severity: str = "all",
    pattern_ids: Optional[List[str]] = None,
) -> str:
    """Scan a directory recursively for security vulnerabilities.

    Uses built-in regex-based vulnerability patterns (no external scanner
    like semgrep required). Results are grouped by severity level.

    Args:
        path: Absolute path to the project directory to scan.
        severity: Minimum severity threshold to report. One of:
                  "all" (default), "CRITICAL", "HIGH", "MEDIUM", "LOW".
        pattern_ids: Optional list of specific pattern IDs to scan for.
                     If None (default), all patterns are used.

    Returns:
        Formatted result with a "findings" list grouped by severity,
        a "summary" with counts, and the scan metadata.
    """
    scan_path = Path(path).expanduser().resolve()
    if not scan_path.exists():
        return fmt_err(f"Path does not exist: {scan_path}")

    if not scan_path.is_dir():
        return fmt_err(f"Path is not a directory: {scan_path}")

    # Filter patterns by ID if specified
    patterns = _VULNERABILITY_PATTERNS
    if pattern_ids:
        pid_set = set(p.upper() for p in pattern_ids)
        patterns = [p for p in patterns if p["id"].upper() in pid_set]
        if not patterns:
            available = sorted(set(p["id"] for p in _VULNERABILITY_PATTERNS))
            return fmt_err(
                f"No patterns match the given pattern_ids: {pattern_ids}\n"
                f"Available pattern IDs: {available}"
            )

    # Collect all matching files, excluding system/hidden directories
    matching_files: set = set()
    glob_extensions: set = set()
    for pat in patterns:
        for g in pat["file_glob"]:
            if g.startswith("*."):
                glob_extensions.add(g.replace("*", ""))
            glob_extensions.add(g)

    for ext in sorted(glob_extensions):
        if ext.startswith("."):
            for f in scan_path.rglob(f"*{ext}"):
                # Skip files in ignored directories
                in_ignored = False
                for parent in f.parents:
                    if parent == scan_path:
                        break
                    if parent.name in _IGNORED_DIRS:
                        in_ignored = True
                        break
                if in_ignored:
                    continue
                # Allow all files — _matches_glob filters by extension per pattern
                if f.is_file():
                    matching_files.add(f)

    # Run each pattern against each matching file
    findings: List[Dict[str, Any]] = []
    files_scanned = 0

    for fpath in sorted(matching_files):
        try:
            # Try UTF-8 first, fall back to latin-1 for binary-looking files
            content = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, LookupError):
            try:
                content = fpath.read_text(encoding="latin-1")
            except Exception as e:
                logger.debug("_scan_code: latin-1 fallback failed: %s", e)
                continue
        except (OSError, PermissionError) as e:
            logger.debug("_scan_code: OSError reading file: %s", e)
            continue

        files_scanned += 1

        for pat in patterns:
            if not _matches_glob(str(fpath), pat["file_glob"]):
                continue
            if not _matches_severity_threshold(pat["severity"], severity):
                continue

            for match in pat["pattern"].finditer(content):
                # Get context lines around the match
                line_num = content[: match.start()].count("\n") + 1
                start = max(0, match.start() - 40)
                end = min(len(content), match.end() + 40)
                snippet = content[start:end].replace("\n", " ").strip()

                findings.append({
                    "pattern_id": pat["id"],
                    "severity": pat["severity"],
                    "message": pat["message"],
                    "file": str(fpath),
                    "line": line_num,
                    "snippet": snippet,
                })

    # Sort: severity (critical first), then file, then line
    findings.sort(
        key=lambda f: (_severity_level(f["severity"]), f["file"], f["line"])
    )

    # Build summary
    summary: Dict[str, int] = {}
    for f in findings:
        sev = f["severity"]
        summary[sev] = summary.get(sev, 0) + 1

    result: Dict[str, Any] = {
        "findings": findings,
        "summary": {
            "total": len(findings),
            "by_severity": summary,
        },
        "metadata": {
            "path": str(scan_path),
            "files_scanned": files_scanned,
            "patterns_applied": len(patterns),
            "severity_filter": severity,
        },
    }

    if not findings:
        return fmt_ok(result, title="✅ Security Scan — No Vulnerabilities Found")

    return fmt_ok(result, title=f"🔒 Security Scan — {len(findings)} Finding(s)")


# ---------------------------------------------------------------------------
# Schema for tool registration
# ---------------------------------------------------------------------------

CODE_SECURITY_SCHEMA = {
    "name": "code_security_scan",
    "description": (
        "Scan a project directory for security vulnerabilities using "
        "built-in regex pattern matching (no semgrep dependency). "
        "Detects hardcoded secrets, SQL injection, path traversal, "
        "command injection, and weak cryptography usage. "
        "Results are grouped by severity with file locations and snippets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the project directory to scan",
            },
            "severity": {
                "type": "string",
                "description": (
                    "Minimum severity threshold. One of: 'all' (default), "
                    "'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'. "
                    "Filters results to only include findings at or above "
                    "this severity level."
                ),
                "default": "all",
            },
            "pattern_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of specific vulnerability pattern IDs to "
                    "scan for (e.g. ['HARDCODED_SECRETS-1', 'SQL_INJECTION-1']). "
                    "If omitted, all patterns are used."
                ),
            },
        },
        "required": ["path"],
    },
}


def _handle_code_security(args: dict = None, **kwargs) -> str:
    """Handler for code_security_scan tool dispatch."""
    args = args or kwargs.get("args", kwargs)
    return code_security_scan_tool(
        path=args.get("path", ""),
        severity=args.get("severity", "all"),
        pattern_ids=args.get("pattern_ids"),
    )


__all__ = [
    "code_security_scan_tool",
    "_handle_code_security",
    "CODE_SECURITY_SCHEMA",
]
