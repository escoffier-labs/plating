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
    and getattr(os, "supports_dir_fd", None) is not None
    and os.open in os.supports_dir_fd
)
_WINDOWS_COMMAND_PATH = re.compile(r'(?:^|[\s"])(?:[A-Za-z]:\\|\\\\)')


class SpecError(ValueError):
    """Raised when a demo spec cannot be safely resolved or executed."""


class _StableBase:
    """Hold an open spec-base directory fd for confined relative lookups."""

    __slots__ = ("_fd")

    def __init__(self, fd: int) -> None:
        self._fd = fd

    @property
    def fd(self) -> int:
        return self._fd

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class _StableCwd:
    """Hold an optional open directory fd used as a stable subprocess cwd."""

    __slots__ = ("cwd", "_fd", "_owns_fd")

    def __init__(self, cwd: str, fd: int | None, *, owns_fd: bool = True) -> None:
        self.cwd = cwd
        self._fd = fd
        self._owns_fd = owns_fd

    def close(self) -> None:
        if self._owns_fd and self._fd is not None:
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
        except TypeError as exc:
            if current_fd != dir_fd:
                os.close(current_fd)
            raise SpecError(
                f"cannot open {label} {part!r}: {exc}"
            ) from None
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


def _open_spec_base(base: Path) -> _StableBase:
    try:
        fd = os.open(str(base), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError as exc:
        raise SpecError(
            f"cannot open spec base: {_os_error_reason(exc)}"
        ) from None
    return _StableBase(fd)


def _read_confined_text_at(
    stable_base: _StableBase,
    relative: str,
    parts: tuple[str, ...],
    *,
    label: str,
) -> str:
    file_fd: int | None = None
    try:
        file_fd = _open_at(stable_base.fd, parts, label=label, leaf_is_dir=False)
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


def _read_confined_text(stable_base: _StableBase, relative: str, *, label: str) -> str:
    parts = _path_parts(relative, label=label)

    if not _SUPPORTS_POSIX_DIRFD:
        raise SpecError(
            f"cannot read {label} {relative!r} safely on this platform")
    return _read_confined_text_at(stable_base, relative, parts, label=label)


def _cwd_from_dir_fd(
    dir_fd: int,
    *,
    fallback_path: str,
    require_descriptor: bool = False,
    owns_fd: bool = False,
) -> _StableCwd:
    proc_path = _proc_fd_path(dir_fd)
    if proc_path is None:
        if owns_fd:
            os.close(dir_fd)
        if require_descriptor:
            raise SpecError(
                "spec-declared cwd cannot be passed through a stable directory "
                "descriptor on this platform")
        return _StableCwd(fallback_path, None, owns_fd=False)
    return _StableCwd(proc_path, dir_fd if owns_fd else None, owns_fd=owns_fd)


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
    return _cwd_from_dir_fd(
        fd,
        fallback_path=str(resolved),
        require_descriptor=require_descriptor,
        owns_fd=True,
    )


def _open_confined_cwd(stable_base: _StableBase, relative: str) -> _StableCwd:
    parts = _path_parts(relative, label="cwd")

    if not _SUPPORTS_POSIX_DIRFD:
        raise SpecError(
            "spec-declared cwd cannot be opened with a stable directory "
            "descriptor on this platform")

    cwd_fd: int | None = None
    try:
        cwd_fd = _open_at(stable_base.fd, parts, label="cwd", leaf_is_dir=True)
        owned_fd = cwd_fd
        cwd_fd = None
        return _cwd_from_dir_fd(
            owned_fd,
            fallback_path=relative,
            require_descriptor=True,
            owns_fd=True,
        )
    except SpecError:
        if cwd_fd is not None:
            os.close(cwd_fd)
        raise
    except OSError as exc:
        if cwd_fd is not None:
            os.close(cwd_fd)
        raise SpecError(
            f"cannot open cwd {relative!r}: {_os_error_reason(exc)}") from None


def _prepare_run_cwd(
    data,
    stable_base: _StableBase | None,
    base: Path,
    *,
    cli_cwd,
) -> _StableCwd:
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
        if stable_base is not None:
            return _cwd_from_dir_fd(
                stable_base.fd,
                fallback_path=str(base),
                require_descriptor=True,
                owns_fd=False,
            )
        raise SpecError(
            "default spec-base cwd cannot be opened with a stable directory "
            "descriptor on this platform")
    if stable_base is None:
        raise SpecError("internal error: missing spec base for confined cwd")
    return _open_confined_cwd(stable_base, declared)


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


def _step_is_captured(raw: dict) -> bool:
    return "output" in raw or "output_file" in raw


def _step_is_live(raw: dict, *, run: bool) -> bool:
    return not _step_is_captured(raw) and bool(raw.get("run") or run)


def _step_needs_output_file(raw: dict) -> bool:
    return "output_file" in raw and "output" not in raw


def _needs_stable_base(
    steps: list,
    *,
    cli_cwd,
    needs_live: bool,
) -> bool:
    if any(
        isinstance(raw, dict) and _step_needs_output_file(raw)
        for raw in steps
    ):
        return True
    if needs_live and cli_cwd is None:
        return True
    return False


def resolve_steps(data, base_dir, *, run=False, cwd=None, timeout=None) -> list[Step]:
    rules = data.get("normalize", [])
    base = Path(base_dir)
    steps_raw = data["steps"]
    needs_live = any(
        _step_is_live(raw, run=run)
        for raw in steps_raw
        if isinstance(raw, dict)
    )
    needs_base_fd = _needs_stable_base(
        steps_raw, cli_cwd=cwd, needs_live=needs_live,
    )

    stable_base: _StableBase | None = None
    if needs_base_fd:
        if not _SUPPORTS_POSIX_DIRFD:
            if any(
                isinstance(raw, dict) and _step_needs_output_file(raw)
                for raw in steps_raw
            ):
                raise SpecError(
                    "cannot read output_file safely on this platform")
            if data.get("cwd") is not None:
                raise SpecError(
                    "spec-declared cwd cannot be opened with a stable directory "
                    "descriptor on this platform")
            if needs_live and cwd is None:
                raise SpecError(
                    "default spec-base cwd cannot be opened with a stable directory "
                    "descriptor on this platform")
        else:
            stable_base = _open_spec_base(base)

    run_timeout = _resolve_timeout(data, cli_timeout=timeout) if needs_live else None
    env = _build_env() if needs_live else None
    stable_cwd = (
        _prepare_run_cwd(data, stable_base, base, cli_cwd=cwd)
        if needs_live else None
    )
    steps: list[Step] = []
    try:
        for raw in steps_raw:
            command = raw["command"]
            display_command = _command_display(command, label="command")
            if "output" in raw:
                output = raw["output"]
            elif "output_file" in raw:
                if stable_base is None:
                    raise SpecError("internal error: missing spec base for output_file")
                output = _read_confined_text(
                    stable_base, raw["output_file"], label="output_file")
            elif _step_is_live(raw, run=run):
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
        if stable_base is not None:
            stable_base.close()
    return steps
