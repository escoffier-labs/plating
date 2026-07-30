"""Load a demo spec (JSON) and resolve each step's output.

A step's output comes from one of, in priority order:
  - "output":      a literal string in the spec
  - "output_file": a path (relative to the spec) to a captured-output file
  - live run:      execute "command" and capture stdout+stderr (step "run": true,
                   or the global --run flag)

``normalize`` rules ([from, to] pairs) are applied to the displayed ``command``
and to the resolved ``output`` so a throwaway temp path can be shown as a clean
``~/...`` in the recording. Live execution always uses the original,
unnormalized command string or argv array.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path, PurePosixPath

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

_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_SUPPORTS_POSIX_DIRFD = (
    os.name != "nt"
    and hasattr(os, "open")
    and bool(_O_DIRECTORY)
    and bool(_O_NOFOLLOW)
)
_WINDOWS_COMMAND_PATH = re.compile(r'(?:^|[\s"])(?:[A-Za-z]:\\|\\\\)')


class SpecError(ValueError):
    """Raised when a demo spec cannot be safely resolved or executed."""


class _StableCwd:
    """Hold an optional open directory fd used as a stable subprocess cwd."""

    __slots__ = ("cwd", "_fd")

    def __init__(self, cwd: str, fd: int | None) -> None:
        self.cwd = cwd
        self._fd = fd

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


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


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except RuntimeError as exc:
        raise SpecError(f"cannot resolve path: {exc}") from None


def _path_parts(value: str, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise SpecError(f"{label} must be a non-empty string")
    if "\x00" in value:
        raise SpecError(f"{label} must not contain a NUL character")
    candidate = PurePosixPath(value)
    if candidate.is_absolute():
        raise SpecError(
            f"{label} must be relative to the spec base, got absolute path {value!r}")
    if ".." in candidate.parts:
        raise SpecError(f"{label} must not contain '..' traversal")
    if not candidate.parts:
        raise SpecError(f"{label} must be a non-empty string")
    return candidate.parts


def _confine_path(base: Path, value: str, *, label: str) -> Path:
    """Resolve ``value`` under ``base`` and reject escapes.

    Rejects absolute paths, ``..`` traversal, and symlink targets that resolve
    outside the canonical base directory. Returns the resolved, confined path.
    """
    _path_parts(value, label=label)
    candidate = Path(value)
    if candidate.is_absolute():
        raise SpecError(
            f"{label} must be relative to the spec base, got absolute path {value!r}")
    resolved = _safe_resolve(base / value)
    canonical_base = _safe_resolve(base)
    try:
        resolved.relative_to(canonical_base)
    except ValueError:
        raise SpecError(
            f"{label} {value!r} resolves outside the spec base directory") from None
    return resolved


def _proc_fd_path(fd: int) -> str | None:
    for prefix in (f"/proc/self/fd/{fd}", f"/dev/fd/{fd}"):
        if os.path.isdir(prefix):
            return prefix
    return None


def _os_error_reason(exc: OSError) -> str:
    return exc.strerror or exc.__class__.__name__


def _open_at(
    dir_fd: int,
    parts: tuple[str, ...],
    *,
    label: str,
    leaf_is_dir: bool,
) -> int:
    current_fd = dir_fd
    for index, part in enumerate(parts):
        is_leaf = index == len(parts) - 1
        flags = os.O_RDONLY
        if not is_leaf or leaf_is_dir:
            flags |= _O_DIRECTORY
        if _O_NOFOLLOW:
            flags |= _O_NOFOLLOW
        try:
            next_fd = os.open(part, flags, dir_fd=current_fd)
        except OSError as exc:
            if current_fd != dir_fd:
                os.close(current_fd)
            raise SpecError(
                f"cannot open {label} {part!r}: {_os_error_reason(exc)}"
            ) from None
        if current_fd != dir_fd:
            os.close(current_fd)
        current_fd = next_fd
    return current_fd


def _read_confined_text_posix(
    base: Path,
    relative: str,
    parts: tuple[str, ...],
    *,
    label: str,
) -> str:
    base_fd: int | None = None
    file_fd: int | None = None
    try:
        try:
            base_fd = os.open(str(_safe_resolve(base)), os.O_RDONLY | _O_DIRECTORY)
        except OSError as exc:
            raise SpecError(
                f"cannot open spec base for {label}: {_os_error_reason(exc)}"
            ) from None
        file_fd = _open_at(base_fd, parts, label=label, leaf_is_dir=False)
        st = os.fstat(file_fd)
        if stat.S_ISDIR(st.st_mode):
            raise SpecError(f"{label} {relative!r} is a directory")
        with os.fdopen(file_fd, "r", encoding="utf-8", closefd=True) as handle:
            file_fd = None
            return handle.read()
    except SpecError:
        raise
    except OSError as exc:
        raise SpecError(
            f"cannot read {label} {relative!r}: {_os_error_reason(exc)}") from None
    except UnicodeDecodeError as exc:
        raise SpecError(
            f"cannot decode {label} {relative!r} as UTF-8: {exc}") from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if base_fd is not None:
            os.close(base_fd)


def _read_confined_text(base: Path, relative: str, *, label: str) -> str:
    parts = _path_parts(relative, label=label)
    _confine_path(base, relative, label=label)

    if not _SUPPORTS_POSIX_DIRFD:
        raise SpecError(
            f"cannot read {label} {relative!r} safely on this platform")
    return _read_confined_text_posix(base, relative, parts, label=label)


def _open_stable_cwd(path: Path, *, require_descriptor: bool = False) -> _StableCwd:
    resolved = _safe_resolve(path)
    _validate_cwd(resolved)
    if not _SUPPORTS_POSIX_DIRFD:
        if require_descriptor:
            raise SpecError(
                "spec-declared cwd cannot be opened with a stable directory "
                "descriptor on this platform")
        return _StableCwd(str(resolved), None)
    try:
        fd = os.open(str(resolved), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError as exc:
        raise SpecError(
            f"cwd cannot be opened safely: {_os_error_reason(exc)}") from None
    proc_path = _proc_fd_path(fd)
    if proc_path is None:
        os.close(fd)
        if require_descriptor:
            raise SpecError(
                "spec-declared cwd cannot be passed through a stable directory "
                "descriptor on this platform")
        return _StableCwd(str(resolved), None)
    return _StableCwd(proc_path, fd)


def _open_confined_cwd(base: Path, relative: str) -> _StableCwd:
    parts = _path_parts(relative, label="cwd")
    _confine_path(base, relative, label="cwd")

    if not _SUPPORTS_POSIX_DIRFD:
        raise SpecError(
            "spec-declared cwd cannot be opened with a stable directory "
            "descriptor on this platform")

    base_fd: int | None = None
    cwd_fd: int | None = None
    proc_path: str | None = None
    try:
        try:
            base_fd = os.open(str(_safe_resolve(base)), os.O_RDONLY | _O_DIRECTORY)
        except OSError as exc:
            raise SpecError(
                f"cannot open spec base for cwd: {_os_error_reason(exc)}"
            ) from None
        cwd_fd = _open_at(base_fd, parts, label="cwd", leaf_is_dir=True)
        proc_path = _proc_fd_path(cwd_fd)
        if proc_path is None:
            raise SpecError(
                "spec-declared cwd cannot be passed through a stable directory "
                "descriptor on this platform")
    except SpecError:
        if cwd_fd is not None:
            os.close(cwd_fd)
        raise
    except OSError as exc:
        if cwd_fd is not None:
            os.close(cwd_fd)
        raise SpecError(
            f"cannot open cwd {relative!r}: {_os_error_reason(exc)}") from None
    finally:
        if base_fd is not None:
            os.close(base_fd)

    return _StableCwd(proc_path, cwd_fd)


def _prepare_run_cwd(data, base: Path, *, cli_cwd) -> _StableCwd:
    """Return a cwd handle for live runs.

    Spec-declared ``cwd`` values must be passed through an open directory
    descriptor (``/proc/self/fd`` or ``/dev/fd``). CLI ``--cwd`` may use an
    ordinary path string because it is explicitly allowed outside confinement.
    """
    if cli_cwd is not None:
        cwd = Path(cli_cwd)
        if not cwd.is_absolute():
            cwd = Path.cwd() / cwd
        return _open_stable_cwd(cwd, require_descriptor=False)
    declared = data.get("cwd")
    if declared is None:
        return _open_stable_cwd(base, require_descriptor=False)
    return _open_confined_cwd(base, declared)


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


def _command_display(command, *, label: str = "command") -> str:
    if isinstance(command, list):
        for index, arg in enumerate(command):
            if not isinstance(arg, str):
                raise SpecError(f"{label} argv[{index}] must be a string")
        return " ".join(shlex.quote(arg) for arg in command)
    if not isinstance(command, str):
        raise SpecError(f"{label} must be a string or argv array")
    return command


def _looks_like_windows_command_string(command: str) -> bool:
    if os.name == "nt":
        return "\\" in command
    return bool(_WINDOWS_COMMAND_PATH.search(command))


def _parse_command(command, *, label: str) -> list[str]:
    if isinstance(command, list):
        if not command:
            raise SpecError(f"{label} argv must be a non-empty array")
        argv: list[str] = []
        for index, word in enumerate(command):
            if not isinstance(word, str):
                raise SpecError(f"{label} argv[{index}] must be a string")
            if "\x00" in word:
                raise SpecError(f"{label} argv must not contain a NUL character")
            argv.append(word)
        return argv
    if not isinstance(command, str):
        raise SpecError(f"{label} must be a string or argv array")
    if not command.strip():
        raise SpecError(f"{label} must be a non-empty command")
    if _looks_like_windows_command_string(command):
        raise SpecError(
            f"{label} contains Windows-style backslashes; "
            "use an explicit argv array")
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
    run_timeout = _resolve_timeout(data, cli_timeout=timeout) if will_run else None
    env = _build_env() if will_run else None
    stable_cwd = _prepare_run_cwd(data, base, cli_cwd=cwd) if will_run else None
    steps: list[Step] = []
    try:
        for raw in data["steps"]:
            command = raw["command"]
            display_command = _command_display(command, label="command")
            if "output" in raw:
                output = raw["output"]
            elif "output_file" in raw:
                output = _read_confined_text(
                    base, raw["output_file"], label="output_file")
            elif raw.get("run") or run:
                argv = _parse_command(command, label="command")
                if stable_cwd is None:
                    raise SpecError("internal error: missing cwd for live run")
                try:
                    proc = subprocess.run(
                        argv,
                        shell=False,
                        cwd=stable_cwd.cwd,
                        env=env,
                        timeout=run_timeout,
                        capture_output=True,
                        text=True,
                    )
                except subprocess.TimeoutExpired:
                    raise SpecError(
                        f"live command timeout after {run_timeout:g}s") from None
                except OSError as exc:
                    raise SpecError(
                        f"failed to start live command: {_os_error_reason(exc)}"
                    ) from None
                output = proc.stdout + proc.stderr
            else:
                output = ""
            steps.append(Step(
                command=_normalize(display_command, rules),
                output=_normalize(output, rules),
            ))
    finally:
        if stable_cwd is not None:
            stable_cwd.close()
    return steps
