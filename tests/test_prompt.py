import json
import subprocess
import sys
from pathlib import Path

import pytest

from plating.cast import PROMPT_COLOR_ALLOWLIST, CastOptions, Step, build_cast
from plating.scan import prompt_patterns, scan


def _run_spec(tmp_path: Path, spec: dict) -> subprocess.CompletedProcess:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    return subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec_path),
         "--out-dir", str(out)],
        capture_output=True, text=True,
    )


def test_prompt_patterns_flag_user_host_identity():
    findings = scan("alice@example-host:~$ ", extra=prompt_patterns())
    assert any(name == "prompt-user-host" for name, _ in findings)


@pytest.mark.parametrize("path", ["/etc/secret", "/opt/private", "/srv/app"])
def test_prompt_patterns_flag_non_home_absolute_cwd(path):
    findings = scan(path, extra=prompt_patterns())
    assert ("prompt-non-home-absolute", path) in findings


def test_prompt_patterns_do_not_flag_default_prompt():
    assert scan("$ ", extra=prompt_patterns()) == []


def test_prompt_patterns_do_not_flag_bin_bash_in_cast_header():
    # The cast header always embeds SHELL=/bin/bash; the cast scan (default
    # patterns) must not flag it, and the prompt-specific patterns are never
    # applied to the cast scan, so /bin/bash cannot false-positive there.
    cast = build_cast([Step("ls", "")], CastOptions())
    findings = scan(cast)
    assert not any("/bin/bash" in value or "/bash" in value for _, value in findings)


def test_prompt_color_allowlist_accepts_default():
    cast = build_cast([Step("ls", "")], CastOptions(prompt_color="1;32"))
    events = [json.loads(line) for line in cast.splitlines()[1:]]
    blob = "".join(event[2] for event in events)
    assert "\x1b[1;32m$ \x1b[0m" in blob


def test_prompt_color_allowlist_accepts_disabled():
    cast = build_cast([Step("ls", "")], CastOptions(prompt_color=""))
    events = [json.loads(line) for line in cast.splitlines()[1:]]
    blob = "".join(event[2] for event in events)
    assert "\x1b[" not in blob
    assert "$ " in blob


def test_prompt_color_rejects_osc_injection():
    hostile = "31m\x1b]0;owned\x07"
    with pytest.raises(ValueError, match="prompt_color"):
        build_cast([Step("ls", "")], CastOptions(prompt_color=hostile))


def test_prompt_color_rejects_unknown_value():
    with pytest.raises(ValueError, match="prompt_color"):
        build_cast([Step("ls", "")], CastOptions(prompt_color="99"))


@pytest.mark.parametrize("value", [[], {}])
def test_prompt_color_rejects_non_string(value):
    with pytest.raises(ValueError, match="prompt_color must be a string"):
        build_cast([Step("ls", "")], CastOptions(prompt_color=value))


def test_prompt_color_allowlist_is_explicit():
    assert "1;32" in PROMPT_COLOR_ALLOWLIST
    assert "" in PROMPT_COLOR_ALLOWLIST


_BASE = {"title": "demo", "width": 40, "height": 4,
         "steps": [{"command": "echo hi", "output": "hi\n"}]}


def test_cli_rejects_identity_prompt_before_writing(tmp_path):
    spec = dict(_BASE)
    spec["prompt"] = "alice@example-host:~$ "
    result = _run_spec(tmp_path, spec)
    assert result.returncode == 2, result.stderr
    assert "prompt-user-host" in result.stderr
    assert not (tmp_path / "out" / "demo.cast").exists()


def test_cli_rejects_non_home_absolute_cwd_before_writing(tmp_path):
    spec = dict(_BASE)
    spec["cwd"] = "/etc/secret"
    result = _run_spec(tmp_path, spec)
    assert result.returncode == 2, result.stderr
    assert "prompt-non-home-absolute" in result.stderr
    assert not (tmp_path / "out" / "demo.cast").exists()


def test_cli_rejects_prompt_color_injection_before_writing(tmp_path):
    spec = dict(_BASE)
    spec["prompt_color"] = "31m\x1b]0;owned\x07"
    result = _run_spec(tmp_path, spec)
    assert result.returncode == 2, result.stderr
    assert "prompt_color" in result.stderr
    assert not (tmp_path / "out" / "demo.cast").exists()


def test_cli_rejects_non_string_prompt_color_before_writing(tmp_path):
    spec = dict(_BASE)
    spec["prompt_color"] = ["1;32"]
    result = _run_spec(tmp_path, spec)
    assert result.returncode == 2, result.stderr
    assert "prompt_color must be a string" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out" / "demo.cast").exists()


def test_cli_accepts_normal_spec(tmp_path):
    result = _run_spec(tmp_path, _BASE)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "demo.cast").exists()
