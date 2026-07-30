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

import plating.spec as spec_module
from plating.cast import Step
from plating.cli import main
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


def test_output_file_rejects_post_validation_symlink_swap(tmp_path, monkeypatch):
    """Replacing the leaf with an outside symlink after validation is rejected."""
    if not spec_module._SUPPORTS_POSIX_DIRFD:
        pytest.skip("requires POSIX dirfd support")

    real = tmp_path / "out.txt"
    real.write_text("safe\n")
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret\n")

    original_open_at = spec_module._open_at

    def hooked_open_at(dir_fd, parts, *, label, leaf_is_dir):
        if label == "output_file" and not leaf_is_dir:
            leaf = tmp_path.joinpath(*parts)
            if leaf.exists() or leaf.is_symlink():
                leaf.unlink()
            leaf.symlink_to(outside)
        return original_open_at(dir_fd, parts, label=label, leaf_is_dir=leaf_is_dir)

    monkeypatch.setattr(spec_module, "_open_at", hooked_open_at)
    data = {"steps": [{"command": "x", "output_file": "out.txt"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


def test_output_file_directory_raises_spec_error(tmp_path):
    directory = tmp_path / "capture"
    directory.mkdir()
    data = {"steps": [{"command": "x", "output_file": "capture"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


def test_output_file_rejects_symlink_leaf(tmp_path):
    """Leaf symlinks are rejected at open time to close output_file TOCTOU."""
    target = tmp_path / "real.txt"
    target.write_text("content\n")
    link = tmp_path / "out.txt"
    link.symlink_to(target)
    data = {"steps": [{"command": "x", "output_file": "out.txt"}]}
    with pytest.raises(SpecError, match="output_file"):
        resolve_steps(data, tmp_path)


def test_symlink_loop_in_base_raises_spec_error(tmp_path):
    loop_a = tmp_path / "loop-a"
    loop_b = tmp_path / "loop-b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    data = {"steps": [{"command": "x", "output_file": "loop-a"}]}
    with pytest.raises(SpecError, match="output_file|resolve|symlink"):
        resolve_steps(data, tmp_path)


def test_spec_cwd_symlink_loop_raises_spec_error(tmp_path):
    loop_a = tmp_path / "loop-a"
    loop_b = tmp_path / "loop-b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    data = {"cwd": "loop-a", "steps": [{"command": "pwd", "run": True}]}
    with pytest.raises(SpecError, match="cwd|resolve|symlink"):
        resolve_steps(data, tmp_path)


def test_cli_symlink_loop_exit_2_without_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr("plating.cli.render_svg", lambda *args, **kwargs: None)
    loop_a = tmp_path / "loop-a"
    loop_b = tmp_path / "loop-b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    spec = {
        "title": "demo",
        "width": 40,
        "height": 4,
        "steps": [{"command": "x", "output_file": "loop-a"}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec_path),
         "--out-dir", str(out), "--allow-leaks"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("plating:")
    assert "Traceback" not in result.stderr
    assert not (out / "demo.cast").exists()


def test_output_file_base_open_oserror_wrapped_as_spec_error(tmp_path, monkeypatch):
    if not spec_module._SUPPORTS_POSIX_DIRFD:
        pytest.skip("requires POSIX dirfd support")

    def boom(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(spec_module.os, "open", boom)
    (tmp_path / "out.txt").write_text("hello\n")
    data = {"steps": [{"command": "x", "output_file": "out.txt"}]}
    with pytest.raises(SpecError, match="output_file|spec base"):
        resolve_steps(data, tmp_path)


def test_output_file_rejected_without_stable_open_capability(tmp_path, monkeypatch):
    (tmp_path / "out.txt").write_text("hello\n")
    monkeypatch.setattr(spec_module, "_SUPPORTS_POSIX_DIRFD", False)
    data = {"steps": [{"command": "x", "output_file": "out.txt"}]}
    with pytest.raises(SpecError, match="output_file.*safely on this platform"):
        resolve_steps(data, tmp_path)


def test_output_file_literal_backslash_filename(tmp_path):
    if not spec_module._SUPPORTS_POSIX_DIRFD:
        pytest.skip("requires POSIX dirfd support")
    name = "foo\\bar.txt"
    (tmp_path / name).write_text("literal\n")
    data = {"steps": [{"command": "x", "output_file": name}]}
    steps = resolve_steps(data, tmp_path)
    assert steps[0].output == "literal\n"


def test_output_file_windows_style_prefix_filename(tmp_path):
    if not spec_module._SUPPORTS_POSIX_DIRFD:
        pytest.skip("requires POSIX dirfd support")
    name = r"C:\not-a-drive.txt"
    (tmp_path / name).write_text("literal\n")
    data = {"steps": [{"command": "x", "output_file": name}]}
    steps = resolve_steps(data, tmp_path)
    assert steps[0].output == "literal\n"


def test_open_at_closes_intermediate_fds_on_failure_without_closing_root(
    tmp_path, monkeypatch,
):
    if not spec_module._SUPPORTS_POSIX_DIRFD:
        pytest.skip("requires POSIX dirfd support")

    nested = tmp_path / "nested"
    nested.mkdir()
    root_fd = os.open(str(tmp_path), os.O_RDONLY | spec_module._O_DIRECTORY)
    opened: list[int] = []
    closed: list[int] = []
    real_open = spec_module.os.open

    def tracking_open(path, flags, *, dir_fd=-1):
        fd = real_open(path, flags, dir_fd=dir_fd)
        if dir_fd == root_fd:
            opened.append(fd)
        return fd

    real_close = spec_module.os.close

    def tracking_close(fd):
        if fd != root_fd:
            closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(spec_module.os, "open", tracking_open)
    monkeypatch.setattr(spec_module.os, "close", tracking_close)

    with pytest.raises(SpecError, match="output_file"):
        spec_module._open_at(
            root_fd,
            ("nested", "missing.txt"),
            label="output_file",
            leaf_is_dir=False,
        )

    assert len(opened) == 1
    assert closed == opened
    assert root_fd not in closed
    os.close(root_fd)


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


def test_live_run_argv_array_passthrough(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = "ok\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"steps": [{"command": ["echo", "hello world"], "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["cmd"] == ["echo", "hello world"]


def test_live_run_argv_array_windows_backslash_path(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    win_path = r"C:\Users\demo\file.txt"
    data = {"steps": [{"command": [win_path], "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["cmd"] == [win_path]


def test_live_run_argv_array_preserves_quoted_argument_content(tmp_path, monkeypatch):
    import shlex

    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    argv = ["printf", "%s\n", 'say "hello"']
    data = {"steps": [{"command": argv, "run": True}]}
    steps = resolve_steps(data, tmp_path)
    assert captured["cmd"] == argv
    assert steps[0].command == " ".join(shlex.quote(arg) for arg in argv)


def test_live_run_argv_array_rejects_empty(tmp_path):
    data = {"steps": [{"command": [], "run": True}]}
    with pytest.raises(SpecError, match="command"):
        resolve_steps(data, tmp_path)


def test_live_run_argv_array_rejects_non_string(tmp_path):
    data = {"steps": [{"command": ["echo", 42], "run": True}]}
    with pytest.raises(SpecError, match="command"):
        resolve_steps(data, tmp_path)


def test_live_run_argv_array_allows_empty_string_argument(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = "ok\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"steps": [{"command": ["echo", ""], "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["cmd"] == ["echo", ""]


def test_live_run_rejects_windows_backslash_command_string(tmp_path):
    data = {"steps": [{"command": r"type C:\Users\demo\file.txt", "run": True}]}
    with pytest.raises(SpecError, match="backslashes|argv array"):
        resolve_steps(data, tmp_path)


def test_live_run_rejects_quoted_windows_drive_command_string(tmp_path):
    data = {"steps": [{"command": r'"C:\Users\demo\file.txt"', "run": True}]}
    with pytest.raises(SpecError, match="backslashes|argv array"):
        resolve_steps(data, tmp_path)


def test_live_run_rejects_any_backslash_command_string_on_win32(monkeypatch):
    monkeypatch.setattr(spec_module.os, "name", "nt")
    with pytest.raises(SpecError, match="backslashes|argv array"):
        spec_module._parse_command(r"dir sub\folder", label="command")


def test_live_run_allows_relative_backslash_command_string_on_posix(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeProc:
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"steps": [{"command": r"cat sub\\folder", "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["cmd"] == ["cat", r"sub\folder"]


def test_spec_cwd_rejected_without_stable_open_capability(tmp_path, monkeypatch):
    sub = tmp_path / "work"
    sub.mkdir()
    monkeypatch.setattr(spec_module, "_SUPPORTS_POSIX_DIRFD", False)
    data = {"cwd": "work", "steps": [{"command": "pwd", "run": True}]}
    with pytest.raises(SpecError, match="stable directory descriptor"):
        resolve_steps(data, tmp_path)


def test_spec_cwd_real_process_boundary(tmp_path):
    sub = tmp_path / "work"
    sub.mkdir()
    marker = sub / "marker.txt"
    marker.write_text("here\n")
    data = {
        "cwd": "work",
        "steps": [{
            "command": [sys.executable, "-c",
                        "import pathlib; print(pathlib.Path('marker.txt').read_text(), end='')"],
            "run": True,
        }],
    }
    steps = resolve_steps(data, tmp_path)
    assert steps[0].output == "here\n"


def test_spec_cwd_rejects_when_stable_descriptor_unavailable(tmp_path, monkeypatch):
    if not spec_module._SUPPORTS_POSIX_DIRFD:
        pytest.skip("requires POSIX dirfd support")
    sub = tmp_path / "work"
    sub.mkdir()
    monkeypatch.setattr(spec_module, "_proc_fd_path", lambda fd: None)
    data = {"cwd": "work", "steps": [{"command": "pwd", "run": True}]}
    with pytest.raises(SpecError, match="stable directory descriptor"):
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
        cwd = kwargs.get("cwd")
        captured["cwd"] = cwd
        if cwd.startswith("/proc/") or cwd.startswith("/dev/fd/"):
            captured["resolved"] = Path(os.readlink(cwd)).resolve()
        else:
            captured["resolved"] = Path(cwd).resolve()
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    data = {"cwd": "work", "steps": [{"command": "pwd", "run": True}]}
    resolve_steps(data, tmp_path)
    assert captured["resolved"] == sub.resolve()


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


def test_cli_cwd_may_be_outside_base_when_it_exists(tmp_path, monkeypatch):
    outside = tmp_path.parent / "external-cwd"
    outside.mkdir()
    monkeypatch.setattr("plating.cli.render_svg", lambda *args, **kwargs: None)
    try:
        spec = {
            "title": "demo",
            "width": 40,
            "height": 4,
            "steps": [{
                "command": [sys.executable, "-c",
                            "import os; print(os.getcwd(), end='')"],
                "run": True,
            }],
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec))
        out = tmp_path / "out"
        code = main([
            "render", str(spec_path),
            "--run", "--cwd", str(outside), "--out-dir", str(out),
            "--allow-leaks",
        ])
        assert code == 0
        cast = (out / "demo.cast").read_text()
        assert str(outside.resolve()) in cast
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


def test_live_run_cli_timeout_overrides_spec(tmp_path, monkeypatch):
    monkeypatch.setattr("plating.cli.render_svg", lambda *args, **kwargs: None)
    spec = {
        "title": "demo",
        "run_timeout": 0.5,
        "width": 40,
        "height": 4,
        "steps": [{
            "command": [sys.executable, "-c", "import time; time.sleep(1.0)"],
            "run": True,
        }],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    code = main([
        "render", str(spec_path),
        "--run", "--timeout", "8", "--out-dir", str(out), "--allow-leaks",
    ])
    assert code == 0
    assert (out / "demo.cast").exists()


def test_live_run_cli_timeout_expired_at_process_boundary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("plating.cli.render_svg", lambda *args, **kwargs: None)
    spec = {
        "title": "demo",
        "width": 40,
        "height": 4,
        "steps": [{
            "command": [sys.executable, "-c", "import time; time.sleep(30)"],
            "run": True,
        }],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    code = main([
        "render", str(spec_path),
        "--run", "--timeout", "0.2", "--out-dir", str(out), "--allow-leaks",
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "timeout" in captured.err.lower()
    assert "Traceback" not in captured.err
    assert not (out / "demo.cast").exists()


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
