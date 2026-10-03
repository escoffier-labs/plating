import subprocess

import pytest

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


@pytest.fixture
def screenshot_command(tmp_path, monkeypatch):
    svg = tmp_path / "frame.svg"
    png = tmp_path / "frame.png"
    commands = []
    monkeypatch.setattr("plating.render.shutil.which", lambda name: "/bin/chrome")

    def fake_run(cmd, capture_output, text):
        commands.append(cmd)
        png.write_bytes(b"new screenshot")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("plating.render.subprocess.run", fake_run)
    return svg, png, commands


@pytest.mark.parametrize(
    "svg_text, expected",
    [
        ('<svg width="240" height="160"/>', "240,160"),
        ('<svg width="1440" height="1080"/>', "1440,1080"),
        # svg-term emits fractional unitless lengths and nested SVG elements.
        ('<svg xmlns="http://www.w3.org/2000/svg" width="808" height="261.68">'
         '<svg width="740" height="173.68" viewBox="0 0 74 17.368"/></svg>',
         "808,262"),
        ('<svg width=" 320.1px " height="200.5px"/>', "321,201"),
        ('<svg width="1e3" height="5e2"/>', "1000,500"),
        ('<svg width="960" height="540" viewBox="0 0 1920 1080"/>', "960,540"),
        ('<svg viewBox="10 -20 1200 900"/>', "1200,900"),
        ('<svg width="100%" height="100%" viewBox="0,0,640.2,480.1"/>', "641,481"),
        ('<svg width="800" viewBox="0 0 400 300"/>', "800,600"),
        ('<svg height="600" viewBox="0 0 400 300"/>', "800,600"),
    ],
)
def test_render_png_uses_root_svg_dimensions(screenshot_command, svg_text, expected):
    svg, png, commands = screenshot_command
    svg.write_text(svg_text)

    assert render_png(svg, png, scale=3) == png

    assert f"--window-size={expected}" in commands[0]
    assert "--force-device-scale-factor=3" in commands[0]
    assert "--no-sandbox" not in commands[0]


@pytest.mark.parametrize(
    "svg_text",
    [
        "<svg/>",
        "<svg",
        '<html width="320" height="200"/>',
        '<svg width="100%" height="auto"/>',
        '<svg width="0" height="200"/>',
        '<svg width="-1" height="200"/>',
        '<svg width="NaN" height="200"/>',
        '<svg width="inf" height="200"/>',
        '<svg width="1e309" height="200"/>',
        '<svg width="999999999999999999999" height="200"/>',
        '<svg width="300; --no-sandbox" height="200"/>',
        '<svg viewBox="0 0 -320 200"/>',
        '<svg viewBox="0 0 320"/>',
        '<svg viewBox="nan 0 320 200"/>',
        '<!DOCTYPE svg [<!ENTITY size SYSTEM "file:///does-not-exist">]>'
        '<svg width="&size;" height="200"/>',
    ],
)
def test_render_png_falls_back_for_unusable_dimensions(screenshot_command, svg_text):
    svg, png, commands = screenshot_command
    svg.write_text(svg_text)

    render_png(svg, png)

    assert "--window-size=960,820" in commands[0]


def test_render_png_preserves_explicit_window_override(screenshot_command):
    svg, png, commands = screenshot_command
    # Explicit callers did not need a readable SVG before viewport inference.
    assert not svg.exists()

    render_png(svg, png, window_size="1234,567", scale=1)

    assert "--window-size=1234,567" in commands[0]
    assert "--force-device-scale-factor=1" in commands[0]


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
