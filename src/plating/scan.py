"""Best-effort identity/leak scan for terminal-demo content.

Catches the most common things that leak into a recording: home-directory
paths, the machine's current username and hostname, and private IPs. It is a
guardrail, not a secrets scanner; pair it with a real scanner for anything
sensitive.
"""
from __future__ import annotations

import getpass
import json
import re
import socket
from pathlib import Path
from typing import Any


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


def patterns_from_content_guard_policy(path: str | Path) -> list[tuple[str, str]]:
    """Return Plating scan patterns from a Content Guard policy JSON file."""
    policy_path = Path(path)
    raw = json.loads(policy_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("content-guard policy root must be an object")

    defaults = _action_map(raw.get("defaults"), "defaults")
    rules = _action_map(raw.get("rules"), "rules")
    patterns: list[tuple[str, str]] = []

    known_hosts = raw.get("known_hosts") or []
    if not isinstance(known_hosts, list):
        raise ValueError("known_hosts must be a list")
    if known_hosts and rules.get("known-host", "block") != "allow":
        escaped_hosts = []
        for index, host in enumerate(known_hosts):
            if not isinstance(host, str) or not host.strip():
                raise ValueError(f"known_hosts[{index}] must be a non-empty string")
            escaped_hosts.append(re.escape(host.strip()))
        patterns.append(("content-guard:known-host", r"\b(?:" + "|".join(escaped_hosts) + r")\b"))

    custom_rules = raw.get("custom_rules") or []
    if not isinstance(custom_rules, list):
        raise ValueError("custom_rules must be a list")
    for index, item in enumerate(custom_rules):
        if not isinstance(item, dict):
            raise ValueError(f"custom_rules[{index}] must be an object")
        rule_id = str(item.get("id") or "")
        category = str(item.get("category") or "")
        pattern = str(item.get("pattern") or "")
        if not rule_id:
            raise ValueError(f"custom_rules[{index}] missing required field 'id'")
        if not category:
            raise ValueError(f"custom_rules[{index}] missing required field 'category'")
        if not pattern:
            raise ValueError(f"custom_rules[{index}] missing required field 'pattern'")
        action = rules.get(rule_id) or defaults.get(category, "warn")
        if action == "allow":
            continue
        flagged_pattern = _with_inline_flags(pattern, item.get("flags") or [], index)
        re.compile(flagged_pattern)
        patterns.append((f"content-guard:{rule_id}", flagged_pattern))
    return patterns


def _action_map(raw: Any, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    actions: dict[str, str] = {}
    for key, value in raw.items():
        if value not in {"allow", "warn", "redact", "block"}:
            raise ValueError(f"{where}.{key} must be allow, warn, redact, or block")
        actions[str(key)] = str(value)
    return actions


def _with_inline_flags(pattern: str, raw_flags: Any, index: int) -> str:
    if not isinstance(raw_flags, list):
        raise ValueError(f"custom_rules[{index}].flags must be a list")
    flag_codes = []
    for flag in raw_flags:
        if flag == "ignorecase":
            flag_codes.append("i")
        elif flag == "multiline":
            flag_codes.append("m")
        elif flag == "dotall":
            flag_codes.append("s")
        else:
            raise ValueError(f"custom_rules[{index}].flags has unsupported flag {flag!r}")
    if not flag_codes:
        return pattern
    return f"(?{''.join(flag_codes)}:{pattern})"
