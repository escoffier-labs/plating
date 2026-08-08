import pytest

from plating.scan import default_patterns, prompt_patterns, scan


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


@pytest.mark.parametrize(
    "address",
    [
        ".".join(["127", "0", "0", "1"]),
        ".".join(["169", "254", "10", "20"]),
        ".".join(["0", "0", "0", "0"]),
        "::1",
        "fe80::1",
        "fc00::1",
    ],
)
def test_flags_special_use_ip_addresses(address):
    findings = scan(f"connect to {address} now")

    assert ("private-ip", address) in findings


@pytest.mark.parametrize(
    "address",
    [
        ".".join(["127", "0", "0", "1"]),
        ".".join(["10", "0", "0", "5"]),
        ".".join(["192", "168", "50", "12"]),
        ".".join(["172", "16", "2", "9"]),
    ],
)
def test_flags_private_or_loopback_ipv4_with_port(address):
    findings = scan(f"service listening on {address}:8199")

    assert ("private-ip", address) in findings


@pytest.mark.parametrize(
    ("text", "reported"),
    [
        ("connect to [::1]:8199", "::1"),
        ("connect to [fe80::1%eth0]:8199", "fe80::1%eth0"),
        ("connect to fe80::1%en0 now", "fe80::1%en0"),
    ],
)
def test_flags_useful_ipv6_forms_without_malformed_reporting(text, reported):
    findings = scan(text)

    assert ("private-ip", reported) in findings
    assert not any(value.endswith("%e") for name, value in findings
                   if name == "private-ip")


def test_invalid_private_looking_ip_is_not_flagged():
    invalid_address = ".".join(["10", "999", "999", "999"])
    findings = scan(f"connect to {invalid_address} now")

    assert not any(value == invalid_address for name, value in findings
                   if name == "private-ip")


@pytest.mark.parametrize(
    "address",
    [
        ".".join(["192", "0", "2", "1"]),
        ".".join(["198", "51", "100", "7"]),
        ".".join(["203", "0", "113", "8"]),
        "2001:db8::1",
    ],
)
def test_documentation_ip_ranges_are_not_flagged(address):
    findings = scan(f"example address {address}")

    assert not any(value == address for name, value in findings
                   if name == "private-ip")


def test_default_patterns_warn_when_identity_patterns_are_skipped(monkeypatch):
    monkeypatch.setattr("plating.scan.getpass.getuser", lambda: "ab")
    monkeypatch.setattr("plating.scan.socket.gethostname", lambda: "")

    with pytest.warns(RuntimeWarning) as records:
        patterns = default_patterns()

    assert not any(name == "current-username" for name, _ in patterns)
    assert not any(name == "current-hostname" for name, _ in patterns)
    messages = [str(record.message) for record in records]
    assert any("username" in message and "too short" in message
               for message in messages)
    assert any("hostname" in message and "empty" in message
               for message in messages)


@pytest.mark.parametrize(
    ("target", "label"),
    [
        ("plating.scan.getpass.getuser", "username"),
        ("plating.scan.socket.gethostname", "hostname"),
    ],
)
def test_default_patterns_warn_when_identity_lookup_raises(monkeypatch, target, label):
    def fail():
        raise OSError("identity unavailable")

    monkeypatch.setattr(target, fail)

    with pytest.warns(RuntimeWarning) as records:
        patterns = default_patterns()

    assert not any(name == f"current-{label}" for name, _ in patterns)
    messages = [str(record.message) for record in records]
    assert any(
        f"skipping current-{label} leak pattern" in message
        and "identity unavailable" in message
        for message in messages
    )


def test_prompt_patterns_flag_foreign_fqdn_identity():
    prompt = "alice@build.remote.example.com:~/repo$ "

    findings = scan(prompt, extra=prompt_patterns())

    assert ("prompt-user-host", "alice@build.remote.example.com") in findings


def test_clean_text_has_no_findings():
    assert scan("~/my-repo is fine, nothing to see") == []


def test_extra_pattern_is_honored():
    findings = scan("token=sk-abc", extra=[("fake-token", r"sk-[a-z]+")])
    assert ("fake-token", "sk-abc") in findings
