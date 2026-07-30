"""Best-effort identity/leak scan for terminal-demo content.

Catches the most common things that leak into a recording: home-directory
paths, the machine's current username and hostname, private IPs, and a few
narrow secret shapes (``API_TOKEN=...`` assignments and PEM private-key
headers via :func:`scan_secrets`). It is a guardrail, not a secrets scanner;
pair it with a real scanner for anything sensitive.
"""
from __future__ import annotations

import getpass
import re
import socket


def default_patterns() -> list[tuple[str, str]]:
    patterns: list[tuple[str, str]] = [
        ("home-path-linux", r"/home/[A-Za-z0-9._-]+"),
        ("home-path-macos", r"/Users/[A-Za-z0-9._-]+"),
        ("private-ip", r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}(?:\.\d{1,3}){1,2}\b"),
    ]
    try:
        user = getpass.getuser()
        if user and len(user) >= 3:
            patterns.append(("current-username", re.escape(user)))
    except Exception:
        pass
    try:
        host = socket.gethostname().split(".")[0]
        if host and len(host) >= 3:
            patterns.append(("current-hostname", re.escape(host)))
    except Exception:
        pass
    return patterns


def prompt_patterns() -> list[tuple[str, str]]:
    """Narrowly prompt-specific leak patterns.

    These are meant to be applied to the raw prompt/cwd configuration text, not
    to the rendered cast (the cast header always embeds ``SHELL=/bin/bash``,
    which a broad "absolute path" rule would false-positive on). They catch an
    identity-bearing prompt for a different user/host and a non-home absolute
    cwd before any artifact is written.
    """
    return [
        # user@host identity baked into a prompt (e.g. ``alice@example-host:~$ ``).
        ("prompt-user-host", r"[A-Za-z][A-Za-z0-9._-]*@[A-Za-z][A-Za-z0-9._-]+"),
        # An absolute path that is not under /home/ or /Users/ (e.g. ``/etc/secret``).
        # This runs only against prompt/cwd content, never the cast header.
        ("prompt-non-home-absolute",
         r"(?<![A-Za-z0-9._-])/(?!(?:home|Users)(?:/|$))[A-Za-z0-9._/-]+"),
    ]


def scan(text: str, extra: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Return a de-duplicated list of (rule_name, matched_text) findings."""
    patterns = default_patterns()
    patterns.extend(extra or [])
    findings: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, pattern in patterns:
        for match in re.finditer(pattern, text):
            key = (name, match.group(0))
            if key not in seen:
                seen.add(key)
                findings.append(key)
    return findings


# Narrow, dependency-free secret-assignment patterns. These are a guardrail, not
# a real secrets scanner: they flag the common shapes that leak into a recording
# (an ``API_TOKEN=...`` assignment or a PEM private-key header). Findings are
# redacted so the matched value never appears in the report. Pair this with a
# dedicated scanner for anything sensitive.
_SECRET_PATTERNS: list[tuple[str, str]] = [
    # ``SOMETHING_TOKEN=value``, ``SOMETHING_API_KEY=value``,
    # ``SOMETHING_SECRET=value``, ``SOMETHING_PASSWORD=value`` assignments.
    ("secret-assignment",
     r"\b[A-Z][A-Z0-9_]*(?:TOKEN|API_KEY|SECRET|PASSWORD)=[^\s;'\"]+"),
    # PEM private-key header (RSA/EC/DSA/OpenSSH/PGP/ENCRYPTED).
    ("secret-private-key",
     r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"),
]


def secret_patterns() -> list[tuple[str, str]]:
    """Return the (name, regex) secret-assignment patterns."""
    return list(_SECRET_PATTERNS)


def _redact_secret(name: str, match: str) -> str:
    if name == "secret-private-key":
        return match
    head, _, _ = match.partition("=")
    return f"{head}=<redacted>"


def scan_secrets(text: str) -> list[tuple[str, str]]:
    """Scan ``text`` for common secret assignments and PEM private-key headers.

    Findings are returned as ``(rule_name, redacted_text)`` so the actual secret
    value is never echoed back. This is best-effort and dependency-free; use a
    dedicated scanner for sensitive material.
    """
    findings: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, pattern in secret_patterns():
        for match in re.finditer(pattern, text):
            redacted = _redact_secret(name, match.group(0))
            key = (name, redacted)
            if key not in seen:
                seen.add(key)
                findings.append(key)
    return findings
