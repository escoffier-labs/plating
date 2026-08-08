"""Mocked integration tests for ``plating.cli._render`` (issue #19).

Each test calls ``main(["render", ...])`` with ``render_svg`` / ``render_png`` /
``subprocess.run`` patched so the full CLI render path is exercised without
external render binaries. Existing tests in ``test_spec.py``, ``test_paths.py``,
and ``test_prompt.py`` already cover several of these behaviors via subprocess
or unit-level helpers; the cases here pin ``_render`` wiring that was still
uncovered at origin/main after issue #16 merged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from plating.cli import main
from plating.render import RenderError


def _write_clean_svg(cast_path, svg_path, **kwargs):
    Path(svg_path).write_text("<svg></svg>")
    return Path(svg_path)


def _base_spec(**overrides) -> dict:
    spec = {
        "title": "demo",
        "width": 40,
        "height": 4,
        "padding": 20,
        "window": False,
        "steps": [{"command": "echo hi", "output": "hi\n"}],
    }
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------
# Issue #19: successful render output through _render.
# ---------------------------------------------------------------------------

def test_cli_render_success_mocked_writes_cast_and_svg(
    tmp_path, monkeypatch, capsys,
):
    captured: dict = {}

    def tracking_render_svg(cast_path, svg_path, **kwargs):
        captured["render_kwargs"] = kwargs
        return _write_clean_svg(cast_path, svg_path, **kwargs)

    monkeypatch.setattr("plating.cli.render_svg", tracking_render_svg)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_base_spec()))
    out = tmp_path / "out"

    code = main(["render", str(spec_path), "--out-dir", str(out)])

    assert code == 0
    captured_io = capsys.readouterr()
    assert captured_io.out == (
        "plating: leak scan clean (demo.cast)\n"
        f"plating: wrote {out / 'demo.svg'}\n"
    )
    assert captured_io.err == ""
    assert (out / "demo.cast").exists()
    assert (out / "demo.svg").exists()
    assert captured["render_kwargs"] == {
        "width": 40,
        "height": 4,
        "padding": 20,
        "window": False,
    }


# ---------------------------------------------------------------------------
# Issue #19: --png behavior through _render.
# ---------------------------------------------------------------------------

def test_cli_render_png_success_mocked_invokes_frame_render_and_cleans_up(
    tmp_path, monkeypatch, capsys,
):
    calls: list[dict] = []

    def tracking_render_svg(cast_path, svg_path, **kwargs):
        calls.append({"path": Path(svg_path), "kwargs": dict(kwargs)})
        Path(svg_path).write_text("<svg></svg>")
        return Path(svg_path)

    def tracking_render_png(svg_path, png_path):
        Path(png_path).write_text("png-bytes")
        return Path(png_path)

    monkeypatch.setattr("plating.cli.render_svg", tracking_render_svg)
    monkeypatch.setattr("plating.cli.render_png", tracking_render_png)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_base_spec()))
    out = tmp_path / "out"

    code = main(["render", str(spec_path), "--out-dir", str(out), "--png", "250"])

    assert code == 0
    captured = capsys.readouterr()
    assert "plating: leak scan clean (demo.cast)" in captured.out
    assert f"plating: wrote {out / 'demo.svg'}" in captured.out
    assert f"plating: wrote {out / 'demo.png'}" in captured.out
    assert len(calls) == 2
    assert calls[0]["path"] == out / "demo.svg"
    assert calls[0]["kwargs"].get("at") is None
    assert calls[1]["path"] == out / "demo.frame.svg"
    assert calls[1]["kwargs"]["at"] == 250
    assert (out / "demo.png").read_text() == "png-bytes"
    assert not (out / "demo.frame.svg").exists()


# ---------------------------------------------------------------------------
# Issue #19: leak refusal with no accepted artifact.
# ---------------------------------------------------------------------------

def test_cli_render_cast_leak_refuses_before_renderer_runs(
    tmp_path, monkeypatch, capsys,
):
    def fail_render_svg(*args, **kwargs):
        raise AssertionError("renderer must not run after cast leak scan fails")

    monkeypatch.setattr("plating.cli.render_svg", fail_render_svg)
    spec = _base_spec(
        steps=[{
            "command": "cat config",
            "output": "API_TOKEN=fake-example-value\n",
        }],
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    out.mkdir()
    for suffix in ("cast", "svg", "png"):
        (out / f"demo.{suffix}").write_text("stale")

    code = main(["render", str(spec_path), "--out-dir", str(out), "--png", "100"])

    assert code == 2
    captured = capsys.readouterr()
    assert "secret-assignment" in captured.err
    assert "refusing to render" in captured.err
    assert "Traceback" not in captured.err
    assert not (out / "demo.cast").exists()
    assert not (out / "demo.svg").exists()
    assert not (out / "demo.png").exists()


def test_cli_render_rejects_frame_leak_before_publishing_png(
    tmp_path, monkeypatch, capsys,
):
    calls = 0

    def render_svg_with_frame_leak(cast_path, svg_path, **kwargs):
        nonlocal calls
        calls += 1
        if kwargs.get("at") == 100:
            Path(svg_path).write_text("<svg>FRAME-LEAK</svg>")
        else:
            Path(svg_path).write_text("<svg></svg>")
        return Path(svg_path)

    def fail_render_png(*args, **kwargs):
        raise AssertionError("PNG rendering must not run after frame scan failure")

    monkeypatch.setattr("plating.cli.render_svg", render_svg_with_frame_leak)
    monkeypatch.setattr("plating.cli.render_png", fail_render_png)
    spec = _base_spec(
        scan_patterns=[["frame-leak", "FRAME-LEAK"]],
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"
    out.mkdir()
    (out / "demo.png").write_text("stale")

    code = main(["render", str(spec_path), "--out-dir", str(out), "--png", "100"])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out.startswith("plating: leak scan clean (demo.cast)\n")
    assert f"plating: wrote {out / 'demo.svg'}" in captured.out
    assert "frame-leak" in captured.err
    assert "refusing to render" in captured.err
    assert calls == 2
    assert (out / "demo.svg").exists()
    assert (out / "demo.cast").exists()
    assert not (out / "demo.frame.svg").exists()
    assert not (out / "demo.png").exists()


def test_cli_render_rejects_cli_cwd_prompt_leak_before_writing(
    tmp_path, monkeypatch, capsys,
):
    def fail_render_svg(*args, **kwargs):
        raise AssertionError("renderer must not run after prompt cwd scan fails")

    monkeypatch.setattr("plating.cli.render_svg", fail_render_svg)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_base_spec()))
    out = tmp_path / "out"

    code = main([
        "render", str(spec_path),
        "--cwd", "/etc/secret",
        "--out-dir", str(out),
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert "prompt-non-home-absolute" in captured.err
    assert "refusing to render" in captured.err
    assert not (out / "demo.cast").exists()


# ---------------------------------------------------------------------------
# Issue #19: renderer failure surfaced as documented CLI result (exit 1).
# ---------------------------------------------------------------------------

def test_cli_render_renderer_failure_returns_exit_code_one(
    tmp_path, monkeypatch, capsys,
):
    def fail_render_svg(cast_path, svg_path, **kwargs):
        Path(svg_path).write_text("<svg>partial</svg>")
        raise RenderError("svg-term failed: boom")

    monkeypatch.setattr("plating.cli.render_svg", fail_render_svg)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_base_spec()))
    out = tmp_path / "out"

    code = main(["render", str(spec_path), "--out-dir", str(out)])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == "plating: leak scan clean (demo.cast)\n"
    assert captured.err.startswith("plating: svg-term failed: boom")
    assert "Traceback" not in captured.err
    assert (out / "demo.cast").exists()
    assert not (out / "demo.svg").exists()


# ---------------------------------------------------------------------------
# Issue #19: invalid input and spec resolution through _render.
# ---------------------------------------------------------------------------

def test_cli_render_spec_resolution_error_skips_renderer(
    tmp_path, monkeypatch, capsys,
):
    def fail_render_svg(*args, **kwargs):
        raise AssertionError("renderer must not run after spec resolution fails")

    monkeypatch.setattr("plating.cli.render_svg", fail_render_svg)
    spec = _base_spec(
        steps=[{"command": "x", "output_file": "../escape.txt"}],
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"

    code = main(["render", str(spec_path), "--out-dir", str(out)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("plating:")
    assert "output_file" in captured.err
    assert "Traceback" not in captured.err
    assert not (out / "demo.cast").exists()


# ---------------------------------------------------------------------------
# Issue #19: --run / spec-resolution path through _render.
# ---------------------------------------------------------------------------

def test_cli_render_run_mocked_captures_live_output(
    tmp_path, monkeypatch, capsys,
):
    class FakeProc:
        stdout = "live-output\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        assert kwargs.get("shell") is False
        assert cmd == [sys.executable, "-c", "print('live-output')"]
        return FakeProc()

    monkeypatch.setattr("plating.spec.subprocess.run", fake_run)
    monkeypatch.setattr("plating.cli.render_svg", _write_clean_svg)
    spec = _base_spec(
        steps=[{
            "command": [sys.executable, "-c", "print('live-output')"],
            "run": True,
        }],
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"

    code = main(["render", str(spec_path), "--run", "--out-dir", str(out)])

    assert code == 0
    captured = capsys.readouterr()
    assert captured.out.endswith(f"plating: wrote {out / 'demo.svg'}\n")
    cast = (out / "demo.cast").read_text()
    assert "live-output" in cast


def test_cli_render_run_spec_error_surfaces_before_artifacts(
    tmp_path, monkeypatch, capsys,
):
    def fail_render_svg(*args, **kwargs):
        raise AssertionError("renderer must not run after --run spec resolution fails")

    monkeypatch.setattr("plating.cli.render_svg", fail_render_svg)
    spec = _base_spec(
        steps=[{"command": "   ", "run": True}],
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out"

    code = main([
        "render", str(spec_path),
        "--run", "--out-dir", str(out),
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("plating:")
    assert "Traceback" not in captured.err
    assert not (out / "demo.cast").exists()
