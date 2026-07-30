"""plating: reproducible, sanitized terminal-demo SVGs for READMEs and websites."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import __version__
from .cast import build_cast
from .render import RenderError, render_png, render_svg
from .scan import prompt_patterns, scan, scan_secrets
from .spec import SpecError, load_spec, options_from_spec, resolve_steps
from .workflow import WorkflowError, render_workflow


class UnsafeTitleError(ValueError):
    """Raised when a spec title cannot be used as a confined filename stem."""


def _validate_stem(title: str) -> str:
    """Return ``title`` unchanged when it is a safe single-segment filename stem.

    Rejects any title that contains a path separator (either slash direction),
    an absolute/path-like form, or a traversal (``.``/``..``) segment. The
    display title value is preserved; we only refuse titles that would escape
    the output directory when used as a filename stem.
    """
    if not isinstance(title, str) or not title:
        raise UnsafeTitleError("title must be a non-empty filename stem")
    if "\x00" in title:
        raise UnsafeTitleError("title must not contain a NUL character")
    if "/" in title or "\\" in title:
        raise UnsafeTitleError(
            f"title {title!r} must not contain '/' or '\\' (would split a path)")
    # Treat as posix and windows to catch absolute/path-like forms on either OS.
    windows_path = PureWindowsPath(title)
    if windows_path.drive or windows_path.anchor:
        raise UnsafeTitleError(
            f"title {title!r} must not contain a Windows drive or anchor")
    for pure in (PurePosixPath(title), windows_path):
        if pure.is_absolute() or len(pure.parts) != 1:
            raise UnsafeTitleError(
                f"title {title!r} must be a single path segment, not a path")
    if title in (".", "..") or title.startswith(".") and title.lstrip(".") == "":
        raise UnsafeTitleError(f"title {title!r} is a traversal segment")
    return title


def _render(args) -> int:
    data, base = load_spec(args.spec)
    title = data.get("title", Path(args.spec).stem)
    try:
        stem = _validate_stem(title)
    except UnsafeTitleError as exc:
        print(f"plating: unsafe title: {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else base
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        steps = resolve_steps(data, base, run=args.run, cwd=args.cwd,
                              timeout=args.timeout)
    except SpecError as exc:
        print(f"plating: {exc}", file=sys.stderr)
        return 2
    opts = options_from_spec(data)
    try:
        cast_text = build_cast(steps, opts)
    except ValueError as exc:
        print(f"plating: {exc}", file=sys.stderr)
        return 2

    cast_path = out_dir / f"{stem}.cast"

    # Scan the raw prompt configuration and cwd for identity/path leaks before
    # any artifact is written. These narrowly prompt-specific patterns are
    # applied only here (not to the cast scan), so the always-present cast
    # header ``SHELL=/bin/bash`` cannot false-positive.
    prompt_ctx = opts.prompt or ""
    cwd = args.cwd or data.get("cwd") or ""
    if cwd:
        prompt_ctx = f"{prompt_ctx}\n{cwd}"
    extra = [(name, pat) for name, pat in (data.get("scan_patterns") or [])]
    findings = scan(cast_text, extra)
    findings.extend(scan_secrets(cast_text))
    findings.extend(scan(prompt_ctx, prompt_patterns()))
    if findings:
        print("plating: leak scan found sensitive content in the recording:",
              file=sys.stderr)
        for name, value in findings:
            print(f"  - {name}: {value}", file=sys.stderr)
        if not args.allow_leaks:
            print("plating: refusing to render. Add `normalize` rules to the spec, "
                  "or pass --allow-leaks to override.", file=sys.stderr)
            return 2
    else:
        print(f"plating: leak scan clean ({cast_path.name})")

    cast_path.write_text(cast_text)

    padding = data.get("padding", 14)
    window = data.get("window", True)
    svg_path = out_dir / f"{stem}.svg"
    try:
        render_svg(cast_path, svg_path, width=opts.width, height=opts.height,
                   padding=padding, window=window)
    except RenderError as exc:
        print(f"plating: {exc}", file=sys.stderr)
        return 1
    print(f"plating: wrote {svg_path}")

    if args.png is not None:
        frame = out_dir / f"{stem}.frame.svg"
        render_svg(cast_path, frame, width=opts.width, height=opts.height,
                   padding=padding, window=window, at=args.png)
        try:
            png_path = render_png(frame, out_dir / f"{stem}.png")
            print(f"plating: wrote {png_path}")
        except RenderError as exc:
            print(f"plating: png preview skipped: {exc}", file=sys.stderr)
        finally:
            frame.unlink(missing_ok=True)
    return 0


def _scan(args) -> int:
    text = Path(args.file).read_text()
    findings = scan(text)
    findings.extend(scan_secrets(text))
    if findings:
        for name, value in findings:
            print(f"{name}: {value}")
        return 2
    print("clean")
    return 0


def _collect_string_values(value, out: list[str]) -> None:
    """Append every string reachable from *value*, recursing into dicts/lists."""
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "scan_patterns":
                continue
            _collect_string_values(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_string_values(item, out)


def _validate_scan_patterns(extra: list[tuple[str, str]]) -> None:
    """Reject malformed user-supplied regular expressions."""
    for name, pattern in extra:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise WorkflowError(
                f"scan_patterns entry {name!r} is not a valid regex: {exc}"
            ) from exc


def _scan_workflow(data: dict, svg: str) -> list[tuple[str, str]]:
    """Scan parsed input values and the rendered SVG, de-duplicating findings.

    Input values are scanned as the actual parsed strings (not flattened through
    ``json.dumps``), so a quoted secret survives even though JSON serialization
    would add backslashes and SVG escaping would turn quotes into ``&quot;``.
    The ``scan_patterns`` subtree is excluded so definitions cannot self-match.
    """
    extra = [(name, pat) for name, pat in (data.get("scan_patterns") or [])]
    _validate_scan_patterns(extra)
    findings: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    values: list[str] = []
    _collect_string_values(data, values)
    for text in values:
        for finding in scan(text, extra):
            if finding not in seen:
                seen.add(finding)
                findings.append(finding)
    for finding in scan(svg, extra):
        if finding not in seen:
            seen.add(finding)
            findings.append(finding)
    return findings


def _workflow(args) -> int:
    source = Path(args.spec)
    output = Path(args.out) if args.out else source.with_suffix(".svg")
    try:
        data, _ = load_spec(source)
        svg = render_workflow(data)
        findings = _scan_workflow(data, svg)
        if findings:
            print("plating: leak scan found identity in the workflow:",
                  file=sys.stderr)
            for name, value in findings:
                print(f"  - {name}: {value}", file=sys.stderr)
            return 2
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(svg)
    except (OSError, json.JSONDecodeError, WorkflowError) as exc:
        print(f"plating: {exc}", file=sys.stderr)
        return 2
    print(f"plating: wrote {output}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="plating",
        description="Reproducible, sanitized terminal-demo SVGs for READMEs and websites.")
    parser.add_argument("--version", action="version", version=f"plating {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    render = sub.add_parser("render", help="render a demo spec to an animated SVG")
    render.add_argument("spec", help="path to a JSON demo spec")
    render.add_argument("--run", action="store_true",
                        help="execute each step's command live and capture its output")
    render.add_argument("--cwd", help="working directory for live (--run) steps")
    render.add_argument("--timeout", type=float, metavar="SECONDS",
                        help="per-step live-run timeout in seconds (default: 30; "
                             "overrides the spec's run_timeout)")
    render.add_argument("--out-dir", help="output directory (default: the spec's directory)")
    render.add_argument("--png", type=int, metavar="MS",
                        help="also write a static PNG preview of the frame at MS milliseconds")
    render.add_argument("--allow-leaks", action="store_true",
                        help="render even if the leak scan finds something")
    render.set_defaults(func=_render)

    scan_cmd = sub.add_parser("scan", help="leak-scan a file for identity and paths")
    scan_cmd.add_argument("file")
    scan_cmd.set_defaults(func=_scan)

    workflow = sub.add_parser(
        "workflow", help="render a JSON workflow spec to a static SVG"
    )
    workflow.add_argument("spec", help="path to a JSON workflow spec")
    workflow.add_argument("--out", help="output SVG path (default: beside the spec)")
    workflow.set_defaults(func=_workflow)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
