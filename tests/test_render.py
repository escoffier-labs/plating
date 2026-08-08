import subprocess

from plating.render import RenderError, render_png


def test_render_png_removes_stale_output_and_surfaces_chrome_stderr(
    tmp_path, monkeypatch
):
    svg = tmp_path / "frame.svg"
    svg.write_text("<svg></svg>")
    png = tmp_path / "frame.png"
    png.write_text("stale")

    monkeypatch.setattr("plating.render.shutil.which", lambda name: "/bin/chrome")

    def fake_run(cmd, capture_output, text):
        assert "--no-sandbox" not in cmd
        assert not png.exists()
        return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="chrome failed")

    monkeypatch.setattr("plating.render.subprocess.run", fake_run)

    try:
        render_png(svg, png)
    except RenderError as exc:
        assert "chrome failed" in str(exc)
    else:
        raise AssertionError("expected RenderError")
    assert not png.exists()


def test_render_png_requires_new_output_after_success(tmp_path, monkeypatch):
    svg = tmp_path / "frame.svg"
    svg.write_text("<svg></svg>")
    png = tmp_path / "frame.png"

    monkeypatch.setattr("plating.render.shutil.which", lambda name: "/bin/chrome")
    monkeypatch.setattr(
        "plating.render.subprocess.run",
        lambda cmd, capture_output, text: subprocess.CompletedProcess(
            cmd, 0, stdout="", stderr=""
        ),
    )

    try:
        render_png(svg, png)
    except RenderError as exc:
        assert "produced no file" in str(exc)
    else:
        raise AssertionError("expected RenderError")
