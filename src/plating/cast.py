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


def _prompt_seq(opts: CastOptions) -> str:
    if opts.prompt_color:
        return f"\x1b[{opts.prompt_color}m{opts.prompt}\x1b[0m"
    return opts.prompt


def build_cast(steps, opts: CastOptions | None = None) -> str:
    """Return an asciicast v2 document (header line + one JSON event per line)."""
    opts = opts or CastOptions()
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
