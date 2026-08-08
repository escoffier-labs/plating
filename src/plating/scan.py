"""Best-effort identity/leak scan for terminal-demo content.

Catches the most common things that leak into a recording: home-directory
paths, the machine's current username and hostname, private IPs, and a few
narrow secret shapes (``API_TOKEN=...`` assignments and PEM private-key
headers via :func:`scan_secrets`). It is a guardrail, not a secrets scanner;
pair it with a real scanner for anything sensitive.
"""
from __future__ import annotations

import getpass
import ipaddress
import re
import socket
import warnings


_IPV4_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<addr>(?:\d{1,3}\.){3}\d{1,3})"
    r"(?::\d{1,5})?"
    r"(?![A-Za-z0-9_.-])"
)
_IPV6_BRACKETED_CANDIDATE = re.compile(
    r"\[(?P<addr>[A-Fa-f0-9:.]+(?:%[A-Za-z0-9_.-]+)?)\](?::\d{1,5})?"
)
_IPV6_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_.:%-])"
    r"(?P<addr>[A-Fa-f0-9:.]*:[A-Fa-f0-9:.]*(?:%[A-Za-z0-9_.-]+)?)"
    r"(?![A-Za-z0-9_.:%-])"
)
_IPV4_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_IPV6_PRIVATE_NETWORKS = (ipaddress.ip_network("fc00::/7"),)


def _warn_skipped_identity(label: str, reason: str) -> None:
    warnings.warn(
        f"skipping current-{label} leak pattern: {reason}",
        RuntimeWarning,
        stacklevel=3,
    )


def _add_identity_pattern(
    patterns: list[tuple[str, str]], label: str, value: str
) -> None:
    if not value:
        _warn_skipped_identity(label, "identity is empty")
        return
    if len(value) < 3:
        _warn_skipped_identity(label, "identity is too short")
        return
    patterns.append((f"current-{label}", re.escape(value)))


def default_patterns() -> list[tuple[str, str]]:
    patterns: list[tuple[str, str]] = [
        ("home-path-linux", r"/home/[A-Za-z0-9._-]+"),
        ("home-path-macos", r"/Users/[A-Za-z0-9._-]+"),
    ]
    try:
        user = getpass.getuser()
        _add_identity_pattern(patterns, "username", user)
    except Exception as exc:
        _warn_skipped_identity("username", str(exc) or exc.__class__.__name__)
    try:
        host = socket.gethostname().split(".")[0]
        _add_identity_pattern(patterns, "hostname", host)
    except Exception as exc:
        _warn_skipped_identity("hostname", str(exc) or exc.__class__.__name__)
    return patterns


def prompt_patterns() -> list[tuple[str, str]]:
    """Narrowly prompt-specific leak patterns.

    These are meant to be applied to the raw prompt/cwd configuration text, not
    to the rendered cast (the cast header always embeds ``SHELL=/bin/bash``,
    which a broad "absolute path" rule would false-positive on). They catch an
    identity-bearing prompt for a different user/host and a non-home absolute
    cwd before any artifact is written. The user@host rule intentionally
    requires a shell-prompt delimiter after the host and is not a general email
    detector; a literal email address in prompt text can still match.
    """
    return [
        # user@host identity baked into a prompt, including foreign FQDN hosts
        # such as ``alice@build.remote.example.com:~$ ``.
        ("prompt-user-host",
         r"[A-Za-z][A-Za-z0-9._-]*@[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
         r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*(?=[:#$\s])"),
        # An absolute path that is not under /home/ or /Users/ (e.g. ``/etc/secret``).
        # This runs only against prompt/cwd content, never the cast header.
        ("prompt-non-home-absolute",
         r"(?<![A-Za-z0-9._-])/(?!(?:home|Users)(?:/|$))[A-Za-z0-9._/-]+"),
    ]


def _append_ip_finding(
    findings: list[tuple[str, str]], seen: set[str], candidate: str
) -> None:
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return
    private_networks = (
        _IPV4_PRIVATE_NETWORKS if address.version == 4 else _IPV6_PRIVATE_NETWORKS
    )
    sensitive = (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or any(address in network for network in private_networks)
    )
    if sensitive and candidate not in seen:
        seen.add(candidate)
        findings.append(("private-ip", candidate))


def _scan_ip_addresses(text: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern in (
        _IPV4_CANDIDATE,
        _IPV6_BRACKETED_CANDIDATE,
        _IPV6_CANDIDATE,
    ):
        for match in pattern.finditer(text):
            _append_ip_finding(findings, seen, match.group("addr"))
    return findings


def scan(text: str, extra: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Return a de-duplicated list of (rule_name, matched_text) findings."""
    patterns = default_patterns()
    patterns.extend(extra or [])
    findings: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for finding in _scan_ip_addresses(text):
        seen.add(finding)
        findings.append(finding)
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
