import json
import subprocess
import sys
from pathlib import Path

import pytest


def _write_spec(tmp_path: Path, title: str) -> Path:
    spec = {
        "title": title,
        "width": 40,
        "height": 4,
        "steps": [{"command": "echo hi", "output": "hi\n"}],
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    return path


def _run(spec: Path, out_dir: Path):
    return subprocess.run(
        [sys.executable, "-m", "plating.cli", "render", str(spec), "--out-dir", str(out_dir)],
        capture_output=True, text=True,
    )


def test_normal_title_keeps_stem(tmp_path):
    spec = _write_spec(tmp_path, "plating-demo")
    out = tmp_path / "out"
    result = _run(spec, out)
    assert result.returncode == 0, result.stderr
    assert (out / "plating-demo.cast").exists()
    assert (out / "plating-demo.svg").exists()


@pytest.mark.parametrize(
    "title",
    [
        "../x",
        "..\\x",
        "/etc/passwd",
        "a/b",
        "a\\b",
        "..",
        "./x",
        "C:",
        "D:",
        "C:foo",
        "D:folder",
        "bad\x00title",
    ],
)
def test_hostile_titles_rejected(tmp_path, title):
    spec = _write_spec(tmp_path, title)
    out = tmp_path / "out"
    result = _run(spec, out)
    assert result.returncode == 2, f"{title!r} should be rejected; stderr={result.stderr!r}"
    assert "title" in result.stderr.lower()
    # nothing escaped the out-dir
    assert not list(out.glob("**/*")) if out.exists() else True
    # specifically no file written outside out_dir
    parent = tmp_path / "x"
    assert not parent.exists()


def test_traversal_never_escapes_outdir(tmp_path):
    spec = _write_spec(tmp_path, "../../escape")
    out = tmp_path / "out"
    out.mkdir()
    result = _run(spec, out)
    assert result.returncode == 2
    # nothing was written above tmp_path
    assert not (tmp_path.parent / "escape.cast").exists()
