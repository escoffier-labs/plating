<p align="center">
  <img src="docs/assets/plating-social-preview.jpg" alt="plating banner" width="900">
</p>

<h1 align="center">plating</h1>

<p align="center"><strong>Reproducible, sanitized terminal-demo SVGs for READMEs and websites.</strong></p>

<p align="center">
  <img src="https://shieldcn.dev/github/actions/workflow/status/escoffier-labs/plating/ci.yml.svg?branch=main&label=ci" alt="CI status">
  <img src="https://shieldcn.dev/pypi/plating-cli.svg" alt="PyPI version">
  <img src="https://shieldcn.dev/badge/python-3.10%2B-blue.svg?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://shieldcn.dev/badge/license-MIT-green.svg" alt="MIT license">
</p>

<p align="center">
  <img src="examples/plating-demo.svg" alt="plating rendering a demo spec into an SVG" width="640">
</p>

<p align="center"><em>That recording was made by plating: <code>plating render examples/plating-demo.json</code>.</em></p>

## What it does

You want a clean terminal recording at the top of your README, the kind that types a command and shows real output in a tidy macOS-style window. Recording one by hand means fighting a screen recorder, leaking your home path and hostname into the frames, and redoing it whenever the output changes. plating turns a small JSON spec into that SVG, every time, from real command output, with a leak scan in front of it.

- **Reproducible.** Commit the spec and its captured output. Regenerate the exact SVG with one command.
- **Sanitized.** A built-in leak scan refuses to render if a home path, username, hostname, or private IP slips into the frame.
- **Honest.** Commands and their output are verbatim. Only the typing animation and a throwaway-path to `~` rewrite are synthesized.
- **Portable.** The animated SVG embeds in GitHub READMEs and on web pages as a plain `<img>`, with no runtime JavaScript.

## Install

```bash
pipx install plating-cli
npm install -g svg-term-cli   # the SVG renderer plating shells out to
```

## Use it

Write a spec, `quickstart.json`:

```json
{
  "title": "quickstart",
  "width": 84,
  "steps": [
    { "command": "mytool --version", "output": "mytool 1.2.3\n" },
    { "command": "mytool build", "output_file": "build-output.txt" }
  ]
}
```

Render it:

```bash
plating render quickstart.json
# wrote quickstart.svg   (and quickstart.cast, the reproducible source)
```

Then embed `quickstart.svg` in your README or drop it into a site.

## A real example

`examples/brigade-quickstart.json` rebuilds the quickstart recording used in the [Brigade](https://github.com/escoffier-labs/brigade) README from its real, captured output:

```bash
plating render examples/brigade-quickstart.json
```

## Where each step's output comes from

In priority order:

| In the step | Output is |
|---|---|
| `"output": "..."` | the literal string |
| `"output_file": "path"` | a captured-output file (relative to the spec) |
| `"run": true` (or `plating render --run`) | the live result of running `command` |

Live capture (`--run`) is convenient. Committing captured output is what makes it reproducible in CI. Use `normalize` to rewrite a throwaway path into something clean:

```json
{ "normalize": [["/tmp/tmp.AbC123/demo", "~/my-repo"]] }
```

## Sanitization

Before rendering, plating scans the recording for Unix and macOS home paths, the machine's current username and hostname, and private IPs. If it finds one it refuses to render and tells you how to fix it (add a `normalize` rule, or pass `--allow-leaks`). You can scan any file on its own:

```bash
plating scan some-recording.cast
```

## Why not a screen recorder or svg-term by hand?

A screen recorder can capture a terminal demo, but it leaves you fighting the recording, redoing it when output changes, and checking every frame for home paths and hostnames.

Calling `svg-term-cli` directly is still useful. plating shells out to it, then adds the JSON spec, captured output, reproducible `.cast` source, and leak scan before rendering.

## What plating is not

plating is not a screen recorder. It renders an SVG from a JSON spec and literal, captured-file, or live command output.

plating is not a silent sanitizer. If the scan finds a home path, username, hostname, or private IP, rendering stops until you add a `normalize` rule or explicitly pass `--allow-leaks`.

plating is not a guarantee that live commands will stay reproducible. Live capture with `--run` is convenient. Committing captured output is what makes the demo reproducible in CI.

## Options

**Spec keys:** `title`, `width`, `height`, `padding`, `window` (macOS chrome, on by default), `prompt`, `prompt_color`, the timing knobs (`type_speed`, `line_delay`, `command_pause`, ... see `src/plating/cast.py`), `normalize`, `scan_patterns`, `cwd`.

**CLI:**

```
plating render <spec> [--run] [--cwd DIR] [--out-dir DIR] [--png MS] [--allow-leaks]
plating scan <file>
```

`--png MS` writes a static PNG of the frame at MS milliseconds (via headless Chrome), handy for a quick eyeball before you commit the SVG.

## License

MIT. See [LICENSE](LICENSE).
