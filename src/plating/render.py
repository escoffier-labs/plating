"""Render an asciicast to an animated SVG via svg-term-cli.

svg-term-cli is an external Node tool (https://github.com/marionebl/svg-term-cli).
Install once with: npm install -g svg-term-cli

The animated SVG it produces embeds in GitHub READMEs and on web pages as a
plain <img>, with no runtime JavaScript.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
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
    svg_path = Path(svg_path)
    svg_path.unlink(missing_ok=True)
    cmd = [_svg_term_bin(), "--in", str(cast_path), "--out", str(svg_path),
           "--width", str(width), "--height", str(height), "--padding", str(padding)]
    if window:
        cmd.append("--window")
    if at is not None:
        cmd += ["--at", str(at)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        svg_path.unlink(missing_ok=True)
        raise RenderError(f"svg-term failed: {result.stderr.strip() or result.stdout.strip()}")
    return svg_path


_SVG_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_SVG_UNIT_PX = {
    "px": 1,
    "in": 96,
    "cm": 96 / 2.54,
    "mm": 96 / 25.4,
    "q": 96 / 101.6,
    "pt": 96 / 72,
    "pc": 16,
}


def _svg_length(value: str | None) -> float | None:
    """Convert absolute CSS lengths to pixels at 96px per inch."""
    if value is None:
        return None
    match = re.fullmatch(
        rf"({_SVG_NUMBER})(px|in|cm|mm|q|pt|pc)?", value.strip().lower()
    )
    if match is None:
        return None
    length = float(match[1]) * _SVG_UNIT_PX[match[2] or "px"]
    return length if math.isfinite(length) and length > 0 else None


def _svg_window_size(svg_path) -> str:
    """Use root dimensions in CSS pixels; keep the legacy size if unknown."""
    fallback = "960,820"
    try:
        root = ET.parse(svg_path).getroot()
    except (OSError, ET.ParseError):
        return fallback
    if root.tag not in ("svg", "{http://www.w3.org/2000/svg}svg"):
        return fallback
    width = _svg_length(root.get("width"))
    height = _svg_length(root.get("height"))
    if width is None or height is None:
        parts = re.split(r"[\s,]+", root.get("viewBox", "").strip())
        if len(parts) != 4 or not all(re.fullmatch(_SVG_NUMBER, p) for p in parts):
            return fallback
        x, y, vb_width, vb_height = map(float, parts)
        if not all(math.isfinite(v) for v in (x, y, vb_width, vb_height)):
            return fallback
        if vb_width <= 0 or vb_height <= 0:
            return fallback
        if width is None and height is None:
            width, height = vb_width, vb_height
        elif width is None:
            width = height * (vb_width / vb_height)
        else:
            height = width * (vb_height / vb_width)
    # Chrome's window dimensions are positive signed integers. Do not pass
    # non-finite or overflowing values from malformed input to its CLI.
    if not all(math.isfinite(v) and 0 < v <= 2**31 - 1 for v in (width, height)):
        return fallback
    return f"{math.ceil(width)},{math.ceil(height)}"


def render_png(svg_path, png_path, *, scale=2, window_size=None) -> Path:
    """Rasterize an SVG using its dimensions, or an explicit CSS window size.

    Absolute root sizes take precedence over viewBox dimensions. Fractional
    sizes round up; unusable dimensions retain the legacy 960x820 viewport.
    Device scale affects PNG resolution, not the inferred CSS viewport.
    """
    png_path = Path(png_path)
    png_path.unlink(missing_ok=True)
    chrome = next((shutil.which(n) for n in
                   ("google-chrome", "chromium", "chromium-browser", "chrome")
                   if shutil.which(n)), None)
    if not chrome:
        raise RenderError("no Chrome/Chromium found for PNG preview")
    if window_size is None:
        window_size = _svg_window_size(svg_path)
    cmd = [chrome, "--headless=new", "--hide-scrollbars",
           f"--force-device-scale-factor={scale}", f"--window-size={window_size}",
           f"--screenshot={png_path}", f"file://{Path(svg_path).resolve()}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        png_path.unlink(missing_ok=True)
        detail = result.stderr.strip() or result.stdout.strip()
        if not detail:
            detail = f"exit code {result.returncode}"
        raise RenderError(f"chrome screenshot failed: {detail}")
    if not png_path.exists():
        png_path.unlink(missing_ok=True)
        raise RenderError("chrome screenshot produced no file")
    return png_path
