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


_SVG_OPEN_TAG = re.compile(r"(<svg\b[^>]*>)", re.IGNORECASE)
_LEAK_OVERRIDE_METADATA = re.compile(
    r'<metadata\s+id="plating-leak-override"[^>]*>.*?</metadata>\s*',
    re.IGNORECASE | re.DOTALL,
)


class LeakOverrideAnnotationError(ValueError):
    """Raised when a required leak-override marker cannot be written."""


def _dedupe_findings(
    findings: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Return findings with first-seen (name, value) pairs preserved."""
    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        if finding in seen:
            continue
        seen.add(finding)
        unique.append(finding)
    return unique


def _extend_unique_findings(
    destination: list[tuple[str, str]],
    additions: list[tuple[str, str]],
) -> None:
    """Append findings that are not already present in ``destination``."""
    seen = set(destination)
    for finding in additions:
        if finding in seen:
            continue
        seen.add(finding)
        destination.append(finding)


def _leak_override_stdout_line(findings: list[tuple[str, str]]) -> str:
    count = len(findings)
    noun = "finding" if count == 1 else "findings"
    return f"plating: leak override allowed ({count} {noun})"


def _leak_override_metadata(findings: list[tuple[str, str]]) -> str:
    count = len(findings)
    return (
        f'<metadata id="plating-leak-override">'
        f"plating-leak-override allowed=true finding-count={count}"
        f"</metadata>"
    )


def _annotate_svg_leak_override(svg_text: str, findings: list[tuple[str, str]]) -> str:
    """Insert or replace a deterministic leak-override ``<metadata>`` block.

    Records only override state and finding count. Rule names and matched values
    are never copied into the artifact (``scan_patterns`` names are user-controlled).
    """
    if not findings:
        return svg_text
    cleaned = _LEAK_OVERRIDE_METADATA.sub("", svg_text)
    match = _SVG_OPEN_TAG.search(cleaned)
    if not match:
        raise LeakOverrideAnnotationError(
            "cannot annotate leak override: SVG root element not found"
        )
    meta = _leak_override_metadata(findings)
    insert_at = match.end()
    return f"{cleaned[:insert_at]}{meta}{cleaned[insert_at:]}"


def _write_svg_leak_override(svg_path: Path, findings: list[tuple[str, str]]) -> None:
    svg_text = svg_path.read_text()
    annotated = _annotate_svg_leak_override(svg_text, findings)
    if annotated != svg_text:
        svg_path.write_text(annotated)


def _publish_leak_override(
    svg_path: Path, findings: list[tuple[str, str]],
) -> None:
    """Write one SVG marker and print one stdout line for the final finding set."""
    unique = _dedupe_findings(findings)
    if not unique:
        return
    _write_svg_leak_override(svg_path, unique)
    print(_leak_override_stdout_line(unique))


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
    svg_path = out_dir / f"{stem}.svg"
    png_path = out_dir / f"{stem}.png"
    frame = out_dir / f"{stem}.frame.svg"

    for artifact in (cast_path, svg_path, png_path, frame):
        artifact.unlink(missing_ok=True)

    # Scan the raw prompt configuration and cwd for identity/path leaks before
    # any artifact is written. These narrowly prompt-specific patterns are
    # applied only here (not to the cast scan), so the always-present cast
    # header ``SHELL=/bin/bash`` cannot false-positive.
    prompt_ctx = opts.prompt or ""
    cwd = args.cwd or data.get("cwd") or ""
    if cwd:
        prompt_ctx = f"{prompt_ctx}\n{cwd}"
    try:
        extra = _load_scan_patterns(data)
    except WorkflowError as exc:
        print(f"plating: {exc}", file=sys.stderr)
        return 2
    findings = scan(cast_text, extra)
    findings.extend(scan_secrets(cast_text))
    findings.extend(scan(prompt_ctx, prompt_patterns()))
    allowed_findings: list[tuple[str, str]] = []
    if findings:
        print("plating: leak scan found sensitive content in the recording:",
              file=sys.stderr)
        for name, value in findings:
            print(f"  - {name}: {value}", file=sys.stderr)
        if not args.allow_leaks:
            print("plating: refusing to render. Add `normalize` rules to the spec, "
                  "or pass --allow-leaks to override.", file=sys.stderr)
            return 2
        _extend_unique_findings(allowed_findings, findings)
    else:
        print(f"plating: leak scan clean ({cast_path.name})")

    out_dir.mkdir(parents=True, exist_ok=True)
    cast_path.write_text(cast_text)

    padding = data.get("padding", 14)
    window = data.get("window", True)
    try:
        render_svg(cast_path, svg_path, width=opts.width, height=opts.height,
                   padding=padding, window=window)
    except RenderError as exc:
        svg_path.unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)
        print(f"plating: {exc}", file=sys.stderr)
        return 1
    try:
        svg_text = svg_path.read_text()
    except OSError as exc:
        svg_path.unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)
        print(f"plating: {exc}", file=sys.stderr)
        return 2
    svg_findings = scan(svg_text, extra)
    svg_findings.extend(scan_secrets(svg_text))
    if svg_findings:
        print("plating: leak scan found sensitive content in the rendered SVG:",
              file=sys.stderr)
        for name, value in svg_findings:
            print(f"  - {name}: {value}", file=sys.stderr)
        if not args.allow_leaks:
            svg_path.unlink(missing_ok=True)
            png_path.unlink(missing_ok=True)
            print("plating: refusing to render. Add `normalize` rules to the spec, "
                  "or pass --allow-leaks to override.", file=sys.stderr)
            return 2
        _extend_unique_findings(allowed_findings, svg_findings)
    print(f"plating: wrote {svg_path}")

    png_failed = False
    if args.png is not None:
        try:
            render_svg(cast_path, frame, width=opts.width, height=opts.height,
                       padding=padding, window=window, at=args.png)
        except RenderError as exc:
            frame.unlink(missing_ok=True)
            png_path.unlink(missing_ok=True)
            print(f"plating: {exc}", file=sys.stderr)
            return 1
        try:
            frame_text = frame.read_text()
        except OSError as exc:
            frame.unlink(missing_ok=True)
            png_path.unlink(missing_ok=True)
            print(f"plating: {exc}", file=sys.stderr)
            return 2
        frame_findings = scan(frame_text, extra)
        frame_findings.extend(scan_secrets(frame_text))
        if frame_findings:
            print("plating: leak scan found sensitive content in the rendered SVG:",
                  file=sys.stderr)
            for name, value in frame_findings:
                print(f"  - {name}: {value}", file=sys.stderr)
            if not args.allow_leaks:
                frame.unlink(missing_ok=True)
                png_path.unlink(missing_ok=True)
                print("plating: refusing to render. Add `normalize` rules to the spec, "
                      "or pass --allow-leaks to override.", file=sys.stderr)
                return 2
            _extend_unique_findings(allowed_findings, frame_findings)
        try:
            rendered_png_path = render_png(frame, png_path)
            print(f"plating: wrote {rendered_png_path}")
        except RenderError as exc:
            png_path.unlink(missing_ok=True)
            print(f"plating: {exc}", file=sys.stderr)
            png_failed = True
        finally:
            frame.unlink(missing_ok=True)

    if allowed_findings:
        try:
            _publish_leak_override(svg_path, allowed_findings)
        except (OSError, LeakOverrideAnnotationError) as exc:
            print(f"plating: {exc}", file=sys.stderr)
            return 2
    return 1 if png_failed else 0


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


def _load_scan_patterns(data: dict) -> list[tuple[str, str]]:
    """Return validated ``scan_patterns`` pairs, or raise ``WorkflowError``.

    Rejects the unsupported ``scan_policy`` key (documented historically but
    never implemented), bad list/pair shapes, non-string entries, and regexes
    that fail to compile. Callers must run this before any scan or write.
    """
    if "scan_policy" in data:
        raise WorkflowError(
            "scan_policy is not supported; use scan_patterns "
            "[[name, regex], ...] instead"
        )
    raw = data.get("scan_patterns", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise WorkflowError(
            "scan_patterns must be a list of [name, regex] pairs"
        )
    extra: list[tuple[str, str]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise WorkflowError(
                f"scan_patterns[{index}] must be a [name, regex] pair"
            )
        name, pattern = entry
        if not isinstance(name, str) or not isinstance(pattern, str):
            raise WorkflowError(
                f"scan_patterns[{index}] name and regex must be strings"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise WorkflowError(
                f"scan_patterns entry {name!r} is not a valid regex: {exc}"
            ) from exc
        extra.append((name, pattern))
    return extra


def _scan_workflow(data: dict, svg: str) -> list[tuple[str, str]]:
    """Scan parsed input values and the rendered SVG, de-duplicating findings.

    Input values are scanned as the actual parsed strings (not flattened through
    ``json.dumps``), so a quoted secret survives even though JSON serialization
    would add backslashes and SVG escaping would turn quotes into ``&quot;``.
    The ``scan_patterns`` subtree is excluded so definitions cannot self-match.
    """
    extra = _load_scan_patterns(data)
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
