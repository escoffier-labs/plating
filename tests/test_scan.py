from plating.scan import scan


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
