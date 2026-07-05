"""plating: reproducible, sanitized terminal-demo SVGs for READMEs and websites."""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from . import __version__
from .cast import build_cast
from .render import RenderError, render_png, render_svg
from .scan import patterns_from_content_guard_policy, scan
from .spec import load_spec, options_from_spec, resolve_live_steps, resolve_steps


def _scan_extra(data, base: Path, policy_arg: str | None) -> list[tuple[str, str]]:
    extra = [(name, pat) for name, pat in (data.get("scan_patterns") or [])]
    policy = policy_arg or data.get("scan_policy")
    if policy:
        policy_path = Path(policy)
        if not policy_path.is_absolute():
            policy_path = base / policy_path
        extra.extend(patterns_from_content_guard_policy(policy_path))
    return extra


def _leak_scan(cast_text: str, extra: list[tuple[str, str]], *, allow_leaks: bool, cast_name: str) -> int:
    findings = scan(cast_text, extra)
    if findings:
        print("plating: leak scan found identity in the recording:", file=sys.stderr)
        for name, value in findings:
            print(f"  - {name}: {value}", file=sys.stderr)
        if not allow_leaks:
            print("plating: refusing to render. Add `normalize` rules to the spec, "
                  "or pass --allow-leaks to override.", file=sys.stderr)
            return 2
    else:
        print(f"plating: leak scan clean ({cast_name})")
    return 0


def _render(args) -> int:
    data, base = load_spec(args.spec)
    title = data.get("title", Path(args.spec).stem)
    out_dir = Path(args.out_dir) if args.out_dir else base
    out_dir.mkdir(parents=True, exist_ok=True)

    steps = resolve_steps(data, base, run=args.run, cwd=args.cwd)
    opts = options_from_spec(data)
    cast_text = build_cast(steps, opts)

    cast_path = out_dir / f"{title}.cast"
    cast_path.write_text(cast_text)

    scan_status = _leak_scan(
        cast_text,
        _scan_extra(data, base, args.scan_policy),
        allow_leaks=args.allow_leaks,
        cast_name=cast_path.name,
    )
    if scan_status:
        return scan_status

    padding = data.get("padding", 14)
    window = data.get("window", True)
    svg_path = out_dir / f"{title}.svg"
    try:
        render_svg(cast_path, svg_path, width=opts.width, height=opts.height,
                   padding=padding, window=window)
    except RenderError as exc:
        print(f"plating: {exc}", file=sys.stderr)
        return 1
    print(f"plating: wrote {svg_path}")

    if args.png is not None:
        frame = out_dir / f"{title}.frame.svg"
        render_svg(cast_path, frame, width=opts.width, height=opts.height,
                   padding=padding, window=window, at=args.png)
        try:
            png_path = render_png(frame, out_dir / f"{title}.png")
            print(f"plating: wrote {png_path}")
        except RenderError as exc:
            print(f"plating: png preview skipped: {exc}", file=sys.stderr)
        finally:
            frame.unlink(missing_ok=True)
    return 0


def _verify(args) -> int:
    data, base = load_spec(args.spec)
    title = data.get("title", Path(args.spec).stem)
    expected = resolve_steps(data, base, run=False, cwd=args.cwd)
    actual = resolve_live_steps(data, base, cwd=args.cwd)
    drifted = []
    for index, (want, got) in enumerate(zip(expected, actual), start=1):
        if want.command != got.command or want.output != got.output:
            drifted.append((index, want, got))

    if drifted:
        print(f"plating: demo drift found in {args.spec}", file=sys.stderr)
        for index, want, got in drifted:
            print(f"  - step {index}: {want.command}", file=sys.stderr)
            diff = difflib.unified_diff(
                want.output.splitlines(),
                got.output.splitlines(),
                fromfile="recorded",
                tofile="live",
                lineterm="",
            )
            for line in diff:
                print(f"    {line}", file=sys.stderr)
        return 1

    cast_text = build_cast(actual, options_from_spec(data))
    scan_status = _leak_scan(
        cast_text,
        _scan_extra(data, base, args.scan_policy),
        allow_leaks=args.allow_leaks,
        cast_name=f"{title}.cast",
    )
    if scan_status:
        return scan_status
    print(f"plating: verify clean ({Path(args.spec).name})")
    return 0


def _scan(args) -> int:
    extra = patterns_from_content_guard_policy(args.policy) if args.policy else []
    findings = scan(Path(args.file).read_text(), extra)
    if findings:
        for name, value in findings:
            print(f"{name}: {value}")
        return 2
    print("clean")
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
    render.add_argument("--out-dir", help="output directory (default: the spec's directory)")
    render.add_argument("--png", type=int, metavar="MS",
                        help="also write a static PNG preview of the frame at MS milliseconds")
    render.add_argument("--allow-leaks", action="store_true",
                        help="render even if the leak scan finds something")
    render.add_argument("--scan-policy",
                        help="Content Guard policy JSON to add to the leak scan")
    render.set_defaults(func=_render)

    verify = sub.add_parser("verify", help="rerun a demo spec and fail if captured output drifted")
    verify.add_argument("spec", help="path to a JSON demo spec")
    verify.add_argument("--cwd", help="working directory for live steps")
    verify.add_argument("--allow-leaks", action="store_true",
                        help="pass even if the leak scan finds something")
    verify.add_argument("--scan-policy",
                        help="Content Guard policy JSON to add to the leak scan")
    verify.set_defaults(func=_verify)

    scan_cmd = sub.add_parser("scan", help="leak-scan a file for identity and paths")
    scan_cmd.add_argument("file")
    scan_cmd.add_argument("--policy",
                          help="Content Guard policy JSON to add to the leak scan")
    scan_cmd.set_defaults(func=_scan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
