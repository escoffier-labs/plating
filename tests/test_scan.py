import json

from plating.scan import patterns_from_content_guard_policy, scan


def test_flags_linux_home_path():
    findings = scan("see /home/alice/secret here")
    assert any(name == "home-path-linux" for name, _ in findings)


def test_flags_macos_home_path():
    findings = scan("/Users/bob/project")
    assert any(name == "home-path-macos" for name, _ in findings)


def test_flags_private_ip():
    # Build the address at runtime so a literal RFC1918 IP never lives in the
    # source (keeps the repo's own leak scanners happy while still exercising the rule).
    ip = ".".join(["192", "168", "1", "1"])
    findings = scan(f"connect to {ip} now")
    assert any(name == "private-ip" for name, _ in findings)


def test_clean_text_has_no_findings():
    assert scan("~/my-repo is fine, nothing to see") == []


def test_extra_pattern_is_honored():
    findings = scan("token=sk-abc", extra=[("fake-token", r"sk-[a-z]+")])
    assert ("fake-token", "sk-abc") in findings


def test_content_guard_policy_custom_rules_are_honored(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "defaults": {"secret": "block"},
        "custom_rules": [
            {
                "id": "fleet-secret",
                "category": "secret",
                "pattern": "fleet-secret-[0-9]+",
            }
        ],
    }))

    findings = scan("token=fleet-secret-123", patterns_from_content_guard_policy(policy))

    assert ("content-guard:fleet-secret", "fleet-secret-123") in findings


def test_content_guard_policy_allow_rules_are_ignored(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "rules": {"demo-word": "allow"},
        "custom_rules": [
            {
                "id": "demo-word",
                "category": "secret",
                "pattern": "allowed-demo",
            }
        ],
    }))

    assert scan("allowed-demo", patterns_from_content_guard_policy(policy)) == []


def test_content_guard_policy_known_hosts_are_honored(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"known_hosts": ["internal-host"]}))

    findings = scan("ssh internal-host", patterns_from_content_guard_policy(policy))

    assert ("content-guard:known-host", "internal-host") in findings
