"""Build an asciicast v2 from (command, output) steps with a typed-command effect.

The content (commands and their output) is verbatim; only the timing and the
typing animation are synthesized. This module is pure and dependency-free so it
is trivial to test.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class Step:
    """One terminal step: a command that is typed out, then its output."""
    command: str
    output: str = ""


@dataclass
class CastOptions:
    width: int = 84
    height: int = 30
    prompt: str = "$ "
    prompt_color: str = "1;32"   # ANSI SGR for the prompt (bold green); "" disables
    type_speed: float = 0.04     # seconds per typed character
    line_delay: float = 0.06     # seconds between streamed output lines
    first_line_delay: float = 0.30
    command_pause: float = 0.45  # after a command is typed, before its output
    after_output_pause: float = 1.0
    prompt_pause: float = 0.6    # before each prompt appears
    final_hold: float = 2.2      # hold on the final frame before the loop restarts


# Explicit allowlist of safe, supported SGR parameter strings for ``prompt_color``.
# The empty string disables coloring. Anything not listed here is refused before
# it can enter an escape sequence, so a hostile value like ``"31m\x1b]0;...\x07"``
# (an SGR followed by an OSC payload) cannot inject terminal controls.
PROMPT_COLOR_ALLOWLIST: frozenset[str] = frozenset({
    "",       # disabled
    "0",      # reset
    "1",      # bold
    "2",      # dim
    "3",      # italic
    "4",      # underline
    "7",      # reverse
    "30", "31", "32", "33", "34", "35", "36", "37",
    "1;30", "1;31", "1;32", "1;33", "1;34", "1;35", "1;36", "1;37",
    "90", "91", "92", "93", "94", "95", "96", "97",
})


def _validate_prompt_color(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"prompt_color must be a string, got {type(value).__name__}")
    if value not in PROMPT_COLOR_ALLOWLIST:
        raise ValueError(
            f"prompt_color {value!r} is not in the safe SGR allowlist; "
            "use one of the supported values or '' to disable coloring")
    return value


def _prompt_seq(opts: CastOptions) -> str:
    if opts.prompt_color:
        return f"\x1b[{opts.prompt_color}m{opts.prompt}\x1b[0m"
    return opts.prompt


def build_cast(steps, opts: CastOptions | None = None) -> str:
    """Return an asciicast v2 document (header line + one JSON event per line)."""
    opts = opts or CastOptions()
    _validate_prompt_color(opts.prompt_color)
    events: list = []
    clock = 0.0

    def emit(data: str, dt: float = 0.0) -> None:
        nonlocal clock
        clock += dt
        events.append([round(clock, 3), "o", data])

    prompt = _prompt_seq(opts)
    for step in steps:
        emit(prompt, opts.prompt_pause)
        for ch in step.command:
            emit(ch, opts.type_speed)
        emit("\r\n", opts.command_pause)
        out = step.output.rstrip("\n")
        if out:
            for i, line in enumerate(out.split("\n")):
                emit(line + "\r\n", opts.first_line_delay if i == 0 else opts.line_delay)
        clock += opts.after_output_pause
    emit("", opts.final_hold)

    header = {
        "version": 2,
        "width": opts.width,
        "height": opts.height,
        "timestamp": 0,
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
    }
    lines = [json.dumps(header)]
    lines.extend(json.dumps(event) for event in events)
    return "\n".join(lines) + "\n"
