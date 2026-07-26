"""plating: reproducible, sanitized terminal-demo SVGs for READMEs and websites."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .cast import CastOptions, Step, build_cast  # noqa: F401

try:
    __version__ = version("plating-cli")
except PackageNotFoundError:  # pragma: no cover - dev fallback when not installed
    __version__ = "0.0.0"
