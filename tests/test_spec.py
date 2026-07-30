"""Regression tests for spec execution hardening (issues #9, #10, #12).

These tests are intentionally focused: each one pins a single security behavior
in ``plating.spec.resolve_steps`` and the surrounding scan/CLI surfaces.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from plating.cast import Step
from plating.scan import scan, scan_secrets, secret_patterns
from plating.spec import SpecError, resolve_steps


# ---------------------------------------------------------------------------
# Issue #9: normalize applies to the displayed command AND output, but live
# execution still uses the original unnormalized command string.
# ---------------------------------------------------------------------------

def test_normalize_applies_to_displayed_command():
    data = {
        "normalize": [["/tmp/tmp.AbC123/demo", "~/my-repo"]],
        "steps": [{"command": "cd /tmp/tmp.AbC123/demo && ls", "output": "file\n"}],
    }
    steps = resolve_steps(data, Path("/tmp"))
    assert steps[0].command == "cd ~/my-repo && ls"
    assert steps[0].output == "file\n"


def test_normalize_applies_to_output():
    data = {
        "normalize": [["/tmp/tmp.AbC123/demo", "~/my-repo"]],
        "steps": [{"command": "ls", "output": "drwxr-xr-x /tmp/tmp.AbC123/demo\n"}],
    }
    steps = resolve_steps(data, Path("/tmp"))
    assert steps[0].output == "drwxr-xr-x ~/my-repo\n"


def test_live_run_uses_unnormalized_command(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = "ran\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)

    target = tmp_path / "real-dir"
    target.mkdir()
    data = {
        "normalize": [[str(target), "~/my-repo"]],
        "steps": [{"command": f"ls {target}", "run": True}],
    }
    steps = resolve_steps(data, tmp_path)
    # Displayed command is normalized.
    assert steps[0].command == "ls ~/my-repo"
    # But the executed command preserved the original path as an argv list.
    assert captured["cmd"] == ["ls", str(target)]


# ---------------------------------------------------------------------------
# Issue #10: output_file path confinement inside the canonical spec base.
# ---------------------------------------------------------------------------

def test_output_file_normal_nested_path_works(tmp_path):
    nested = tmp_path / "captures"
    nested.mkdir()
    (nested / "out.txt").write_text("hello\n")
    data = {"steps": [{"command": "x", "output_file": "captures/out.txt"}]}
    steps = resolve_steps(data, tmp_path)
    assert steps[0].output == "hello\n"


def test_output_file_rejects_absolute_path(tmp_path):
    data = {"steps": [{"command": "x", "output_file": "/etc/passwd"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


def test_output_file_rejects_traversal(tmp_path):
    data = {"steps": [{"command": "x", "output_file": "../escape.txt"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


def test_output_file_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-target.txt"
    outside.write_text("secret\n")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)
    data = {"steps": [{"command": "x", "output_file": "link.txt"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


def test_output_file_missing_raises_spec_error(tmp_path):
    data = {"steps": [{"command": "x", "output_file": "missing.txt"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


def test_output_file_directory_raises_spec_error(tmp_path):
    directory = tmp_path / "capture"
    directory.mkdir()
    data = {"steps": [{"command": "x", "output_file": "capture"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


# ---------------------------------------------------------------------------
# Issue #10: secret-bearing content scan.
# ---------------------------------------------------------------------------

def test_secret_patterns_flag_api_token_assignment():
    findings = scan_secrets("export API_TOKEN=fake-example-value")
    assert any(name.startswith("secret-") for name, _ in findings)


def test_secret_patterns_flag_private_key_header():
    text = "-----BEGIN " + "RSA PRIVATE KEY-----"
    findings = scan_secrets(text)
    assert any("private-key" in name for name, _ in findings)


def test_secret_findings_redact_the_value():
    findings = scan_secrets("export API_TOKEN=fake-example-value")
    for name, value in findings:
        assert "fake-example-value" not in value


def test_scan_secret_marker_in_output_file_refuses_render(tmp_path):
    capture = tmp_path / "leaked.txt"
    capture.write_text("config: API_TOKEN=fake-example-value\n")
    spec = {
        "title": "demo",
        "width": 40,
        "height": 4,
        "steps": [{"command": "cat config", "output_file": "leaked.txt"}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec_path),
         "--out-dir", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr
    assert not (out / "demo.cast").exists()


# ---------------------------------------------------------------------------
# Issue #12: shell=False, argv parsing, reject empty/malformed commands.
# ---------------------------------------------------------------------------

def test_live_run_uses_shell_false_argv(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = "ok\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell")
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"steps": [{"command": "echo hello world", "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["cmd"] == ["echo", "hello", "world"]
    assert captured["shell"] is False


def test_live_run_rejects_empty_command(tmp_path):
    data = {"steps": [{"command": "   ", "run": True}]}
    with pytest.raises(SpecError, match="command"):
        resolve_steps(data, tmp_path)


def test_live_run_rejects_malformed_command(tmp_path):
    data = {"steps": [{"command": "echo 'unterminated", "run": True}]}
    with pytest.raises(SpecError, match="command"):
        resolve_steps(data, tmp_path)


# ---------------------------------------------------------------------------
# Issue #12: cwd resolution and confinement.
# ---------------------------------------------------------------------------

def test_spec_cwd_confined_inside_base(tmp_path, monkeypatch):
    sub = tmp_path / "work"
    sub.mkdir()
    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"cwd": "work", "steps": [{"command": "pwd", "run": True}]}
    resolve_steps(data, tmp_path)
    assert Path(captured["cwd"]).resolve() == sub.resolve()


def test_spec_cwd_rejects_traversal(tmp_path):
    data = {"cwd": "../escape", "steps": [{"command": "pwd", "run": True}]}
    with pytest.raises(SpecError, match="cwd"):
        resolve_steps(data, tmp_path)


def test_spec_cwd_missing_directory_rejected_before_run(tmp_path, monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"cwd": "missing", "steps": [{"command": "pwd", "run": True}]}
    with pytest.raises(SpecError, match="cwd"):
        resolve_steps(data, tmp_path)
    assert called is False


def test_spec_cwd_file_rejected_before_run(tmp_path, monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    (tmp_path / "not-a-directory").write_text("file")
    data = {
        "cwd": "not-a-directory",
        "steps": [{"command": "pwd", "run": True}],
    }
    with pytest.raises(SpecError, match="cwd"):
        resolve_steps(data, tmp_path)
    assert called is False


def test_cli_cwd_may_be_outside_base_when_it_exists(tmp_path):
    outside = tmp_path.parent / "external-cwd"
    outside.mkdir()
    try:
        spec = {
            "title": "demo",
            "width": 40,
            "height": 4,
            "steps": [{"command": "pwd", "run": True, "output": "ran\n"}],
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec))
        out = tmp_path / "out"
        result = subprocess.run(
            [sys.executable, "-m", "plating.cli", "render", str(spec_path),
             "--run", "--cwd", str(outside), "--out-dir", str(out),
             "--allow-leaks"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    finally:
        outside.rmdir()


def test_cli_cwd_missing_directory_rejected(tmp_path):
    spec = {
        "title": "demo",
        "width": 40,
        "height": 4,
        "steps": [{"command": "pwd", "run": True, "output": "ran\n"}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec_path),
         "--run", "--cwd", str(tmp_path / "nope"), "--out-dir", str(out),
         "--allow-leaks"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "cwd" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert not (out / "demo.cast").exists()


# ---------------------------------------------------------------------------
# Issue #12: environment allowlist.
# ---------------------------------------------------------------------------

def test_live_run_env_allowlist_excludes_home_and_tokens(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    monkeypatch.setenv("HOME", "/home/sneaky")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    data = {"steps": [{"command": "env", "run": True}]}
    resolve_steps(data, tmp_path)
    env = captured["env"]
    assert "HOME" not in env
    assert "GITHUB_TOKEN" not in env
    assert "PATH" in env


def test_live_run_env_allowlist_includes_no_color(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    monkeypatch.setenv("NO_COLOR", "1")
    data = {"steps": [{"command": "ls", "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["env"].get("NO_COLOR") == "1"


# ---------------------------------------------------------------------------
# Issue #12: timeout enforcement.
# ---------------------------------------------------------------------------

def test_live_run_default_timeout_is_30s(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"steps": [{"command": "ls", "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["timeout"] == 30


def test_live_run_spec_run_timeout_honored(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"run_timeout": 5, "steps": [{"command": "ls", "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["timeout"] == 5


def test_live_run_cli_timeout_overrides_spec(tmp_path):
    spec = {
        "title": "demo",
        "run_timeout": 5,
        "width": 40,
        "height": 4,
        "steps": [{"command": "true", "run": True, "output": "ok\n"}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec_path),
         "--run", "--timeout", "8", "--out-dir", str(out), "--allow-leaks"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_live_run_invalid_timeout_rejected(tmp_path):
    data = {"run_timeout": 0, "steps": [{"command": "ls", "run": True}]}
    with pytest.raises(SpecError, match="timeout"):
        resolve_steps(data, tmp_path)


def test_live_run_negative_timeout_rejected(tmp_path):
    data = {"run_timeout": -1, "steps": [{"command": "ls", "run": True}]}
    with pytest.raises(SpecError, match="timeout"):
        resolve_steps(data, tmp_path)


def test_live_run_timeout_expired_reports_domain_error(tmp_path, monkeypatch):
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"run_timeout": 1, "steps": [{"command": "sleep 5", "run": True}]}
    with pytest.raises(SpecError, match="timeout"):
        resolve_steps(data, tmp_path)


# ---------------------------------------------------------------------------
# Issue #12: CLI error handling returns exit code 2 with plating: prefix.
# ---------------------------------------------------------------------------

def test_cli_render_error_exit_code_2_with_prefix(tmp_path):
    spec = {
        "title": "demo",
        "width": 40,
        "height": 4,
        "steps": [{"command": "   ", "run": True}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec_path),
         "--run", "--out-dir", str(out), "--allow-leaks"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("plating:")
    assert "Traceback" not in result.stderr
    assert not (out / "demo.cast").exists()


def test_cli_render_osserror_exit_code_2(tmp_path):
    spec = {
        "title": "demo",
        "width": 40,
        "height": 4,
        "steps": [{"command": "definitely-not-a-real-binary-xyz", "run": True}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec_path),
         "--run", "--out-dir", str(out), "--allow-leaks"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("plating:")
    assert "Traceback" not in result.stderr
    assert not (out / "demo.cast").exists()
