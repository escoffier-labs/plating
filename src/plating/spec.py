"""Load a demo spec (JSON) and resolve each step's output.

A step's output comes from one of, in priority order:
  - "output":      a literal string in the spec
  - "output_file": a path (relative to the spec) to a captured-output file
  - live run:      execute "command" and capture stdout+stderr (step "run": true,
                   or the global --run flag)

`normalize` rules ([from, to] pairs) are applied to every resolved output, so a
throwaway temp path can be shown as a clean `~/...` in the recording.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .cast import CastOptions, Step

_OPTION_KEYS = (
    "width", "height", "prompt", "prompt_color", "type_speed", "line_delay",
    "first_line_delay", "command_pause", "after_output_pause", "prompt_pause",
    "final_hold",
)


def load_spec(path):
    path = Path(path)
    return json.loads(path.read_text()), path.parent


def options_from_spec(data) -> CastOptions:
    opts = CastOptions()
    for key in _OPTION_KEYS:
        if key in data:
            setattr(opts, key, data[key])
    return opts


def _normalize(text: str, rules) -> str:
    for frm, to in rules or []:
        text = text.replace(frm, to)
    return text


def resolve_steps(data, base_dir, *, run=False, cwd=None) -> list[Step]:
    rules = data.get("normalize", [])
    cwd = cwd or data.get("cwd")
    steps: list[Step] = []
    for raw in data["steps"]:
        command = raw["command"]
        if "output" in raw:
            output = raw["output"]
        elif "output_file" in raw:
            output = (Path(base_dir) / raw["output_file"]).read_text()
        elif raw.get("run") or run:
            proc = subprocess.run(command, shell=True, cwd=cwd,
                                  capture_output=True, text=True)
            output = proc.stdout + proc.stderr
        else:
            output = ""
        steps.append(Step(command=command, output=_normalize(output, rules)))
    return steps
