"""Load a demo spec (JSON) and resolve each step's output.

A step's output comes from one of, in priority order:
  - "output":      a literal string in the spec
  - "output_file": a path (relative to the spec) to a captured-output file
  - live run:      execute "command" and capture stdout+stderr (step "run": true,
                   or the global --run flag)

``normalize`` rules ([from, to] pairs) are applied to the displayed ``command``
and to the resolved ``output`` so a throwaway temp path can be shown as a clean
``~/...`` in the recording. Live execution always uses the original,
unnormalized command string.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from .cast import CastOptions, Step

_OPTION_KEYS = (
    "width", "height", "prompt", "prompt_color", "type_speed", "line_delay",
    "first_line_delay", "command_pause", "after_output_pause", "prompt_pause",
    "final_hold",
)

# Default live-run timeout in seconds.
DEFAULT_RUN_TIMEOUT = 30

# Environment variables inherited by live-run subprocesses. This is an explicit
# allowlist: HOME, cloud-provider, CI, SSH, and token variables are NOT passed.
# Only the locale/terminal/PATH basics needed for a deterministic capture are
# kept, plus the Windows launch variables (resolved only when present).
_ENV_ALLOWLIST = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "NO_COLOR",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
)


class SpecError(ValueError):
    """Raised when a demo spec cannot be safely resolved or executed."""


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


def _confine_path(base: Path, value: str, *, label: str) -> Path:
    """Resolve ``value`` under ``base`` and reject escapes.

    Rejects absolute paths, ``..`` traversal, and symlink targets that resolve
    outside the canonical base directory. Returns the resolved, confined path.
    """
    if not isinstance(value, str) or not value:
        raise SpecError(f"{label} must be a non-empty string")
    if "\x00" in value:
        raise SpecError(f"{label} must not contain a NUL character")
    candidate = Path(value)
    if candidate.is_absolute():
        raise SpecError(f"{label} must be relative to the spec base, got absolute path {value!r}")
    resolved = (base / value).resolve()
    canonical_base = base.resolve()
    try:
        resolved.relative_to(canonical_base)
    except ValueError:
        raise SpecError(
            f"{label} {value!r} resolves outside the spec base directory") from None
    return resolved


def _build_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for name in _ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def _validate_cwd(path: Path) -> Path:
    if not path.exists():
        raise SpecError(f"cwd does not exist: {path}")
    if not path.is_dir():
        raise SpecError(f"cwd is not a directory: {path}")
    return path


def _resolve_cwd(data, base: Path, *, cli_cwd) -> Path:
    """Resolve and validate the working directory for live runs.

    ``cli_cwd`` (the CLI ``--cwd`` argument) may point outside the spec base
    after canonical resolution, but must exist and be a directory. A spec-
    declared ``cwd`` is resolved relative to and confined within the spec base.
    """
    if cli_cwd is not None:
        cwd = Path(cli_cwd)
        if not cwd.is_absolute():
            cwd = Path.cwd() / cwd
        return _validate_cwd(cwd.resolve())
    declared = data.get("cwd")
    if declared is None:
        return _validate_cwd(base.resolve())
    return _validate_cwd(_confine_path(base, declared, label="cwd"))


def _resolve_timeout(data, *, cli_timeout) -> float:
    """Return a positive finite timeout. CLI ``--timeout`` overrides the spec."""
    raw = cli_timeout if cli_timeout is not None else data.get("run_timeout", DEFAULT_RUN_TIMEOUT)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        raise SpecError(f"timeout must be a positive number, got {raw!r}") from None
    if not (timeout > 0) or timeout != timeout or timeout in (float("inf"), float("-inf")):
        raise SpecError(f"timeout must be a positive finite number, got {raw!r}")
    return timeout


def _parse_command(command, *, label: str) -> list[str]:
    if not isinstance(command, str):
        raise SpecError(f"{label} must be a string")
    if not command.strip():
        raise SpecError(f"{label} must be a non-empty command")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise SpecError(f"{label} is not a valid command: {exc}") from None
    if not argv:
        raise SpecError(f"{label} must contain at least one command word")
    return argv


def resolve_steps(data, base_dir, *, run=False, cwd=None, timeout=None) -> list[Step]:
    rules = data.get("normalize", [])
    base = Path(base_dir)
    will_run = run or any(raw.get("run") for raw in data.get("steps", []) if isinstance(raw, dict))
    run_cwd = _resolve_cwd(data, base, cli_cwd=cwd) if will_run else None
    run_timeout = _resolve_timeout(data, cli_timeout=timeout) if will_run else None
    env = _build_env() if will_run else None
    steps: list[Step] = []
    for raw in data["steps"]:
        command = raw["command"]
        if "output" in raw:
            output = raw["output"]
        elif "output_file" in raw:
            path = _confine_path(base, raw["output_file"], label="output_file")
            try:
                output = path.read_text()
            except OSError as exc:
                reason = exc.strerror or exc.__class__.__name__
                raise SpecError(
                    f"cannot read output_file {raw['output_file']!r}: {reason}"
                ) from None
        elif raw.get("run") or run:
            argv = _parse_command(command, label="command")
            try:
                proc = subprocess.run(argv, shell=False, cwd=str(run_cwd),
                                      env=env, timeout=run_timeout,
                                      capture_output=True, text=True)
            except subprocess.TimeoutExpired:
                raise SpecError(
                    f"live command timeout after {run_timeout:g}s") from None
            except OSError as exc:
                reason = exc.strerror or exc.__class__.__name__
                raise SpecError(f"failed to start live command: {reason}") from None
            output = proc.stdout + proc.stderr
        else:
            output = ""
        steps.append(Step(
            command=_normalize(command, rules),
            output=_normalize(output, rules),
        ))
    return steps
