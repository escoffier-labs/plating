"""Best-effort identity/leak scan for terminal-demo content.

Catches the most common things that leak into a recording: home-directory
paths, the machine's current username and hostname, and private IPs. It is a
guardrail, not a secrets scanner; pair it with a real scanner for anything
sensitive.
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
