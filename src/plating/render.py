"""Render an asciicast to an animated SVG via svg-term-cli.

svg-term-cli is an external Node tool (https://github.com/marionebl/svg-term-cli).
Install once with: npm install -g svg-term-cli

The animated SVG it produces embeds in GitHub READMEs and on web pages as a
plain <img>, with no runtime JavaScript.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class RenderError(RuntimeError):
    pass


def _svg_term_bin() -> str:
    path = shutil.which("svg-term")
    if not path:
        raise RenderError("svg-term not found. Install it with: npm install -g svg-term-cli")
    return path


def render_svg(cast_path, svg_path, *, width=84, height=30, padding=14,
               window=True, at=None) -> Path:
    cmd = [_svg_term_bin(), "--in", str(cast_path), "--out", str(svg_path),
           "--width", str(width), "--height", str(height), "--padding", str(padding)]
    if window:
        cmd.append("--window")
    if at is not None:
        cmd += ["--at", str(at)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(f"svg-term failed: {result.stderr.strip() or result.stdout.strip()}")
    return Path(svg_path)


def render_png(svg_path, png_path, *, scale=2, window_size="960,820") -> Path:
    """Rasterize a static SVG frame to PNG via headless Chrome (for previews)."""
    chrome = next((shutil.which(n) for n in
                   ("google-chrome", "chromium", "chromium-browser", "chrome")
                   if shutil.which(n)), None)
    if not chrome:
        raise RenderError("no Chrome/Chromium found for PNG preview")
    cmd = [chrome, "--headless=new", "--no-sandbox", "--hide-scrollbars",
           f"--force-device-scale-factor={scale}", f"--window-size={window_size}",
           f"--screenshot={png_path}", f"file://{Path(svg_path).resolve()}"]
    subprocess.run(cmd, capture_output=True, text=True)
    if not Path(png_path).exists():
        raise RenderError("chrome screenshot produced no file")
    return Path(png_path)
