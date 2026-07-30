<p align="center">
  <img src="docs/assets/plating-social-preview.jpg" alt="plating banner" width="900">
</p>

<h1 align="center">plating</h1>

<p align="center">
  <img src="docs/assets/marks/plating-circle.svg" alt="" width="40" height="40">
</p>

<p align="center">
  <strong>Reproducible terminal demos and workflow diagrams for READMEs.</strong>
</p>

<p align="center">
  Build animated terminal demos from captured output or static workflow diagrams from JSON. Generated SVGs embed directly in GitHub with no browser runtime.
</p>

<p align="center">
  <a href="#install">Install</a> &middot; <a href="#use-it">Use it</a> &middot; <a href="https://github.com/escoffier-labs/brigade">Used by Brigade</a>
</p>

<p align="center">
  <img src="https://img.shields.io/pypi/v/plating-cli?style=for-the-badge&label=pypi" alt="PyPI version">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="MIT license">
</p>

## Install

```bash
pipx install plating-cli
npm install -g svg-term-cli   # SVG renderer plating shells out to
plating render examples/plating-demo.json
```

## What it does

| | Job | What you get |
|---|---|---|
| **Spec** | JSON steps + outputs | Commands stay honest; animation is synthesized |
| **Scan** | Refuse identity leaks | Home paths, username, hostname, private IPs |
| **Render** | Animated SVG embed | GitHub README and sites as a plain img |
| **Diagram** | Lay out a workflow from JSON | Matching static SVGs across repositories |
| **Verify** | Drift detection | Re-run specs when CLI output changes |

<p align="center">
  <img src="examples/plating-demo.svg" alt="plating rendering a demo spec into an SVG" width="760">
</p>

<p align="center"><em>That recording was made by plating itself.</em></p>


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

## Workflow diagrams

`plating workflow` renders a constrained JSON document into the shared Escoffier Labs workflow style. Authors name columns, nodes, and connections; connections are forward-only, running from an earlier column to a later column. Plating owns the canvas, spacing, typography, colors, arrows, and accessible SVG metadata.

```json
{
  "title": "Source to release",
  "eyebrow": "BUILD WORKFLOW",
  "description": "Tracked source passes through verification before release.",
  "columns": [
    {"title": "INPUT", "nodes": [{"id": "source", "label": "source"}]},
    {"title": "CHECK", "nodes": [{"id": "test", "label": "tests", "kind": "focus"}]},
    {"title": "OUTPUT", "nodes": [{"id": "release", "label": "release", "kind": "success"}]}
  ],
  "edges": [
    {"from": "source", "to": "test"},
    {"from": "test", "to": "release", "label": "pass"}
  ]
}
```

```bash
plating workflow examples/workflow.json
# plating: wrote examples/workflow.svg
```

Commit the JSON source and generated SVG together. Re-running the command with the same source produces the same bytes.

<p align="center">
  <img src="examples/workflow.svg" alt="Plating workflow: captured output and JSON pass through an identity scan and SVG renderer to produce a checked-in README image" width="900">
</p>

## Where each step's output comes from

In priority order:

| In the step | Output is |
|---|---|
| `"output": "..."` | the literal string |
| `"output_file": "path"` | a captured-output file (relative to the spec, confined under the spec directory) |
| `"run": true` (or `plating render --run`) | the live result of running `command` |

Live capture (`--run`) is convenient; committing captured output is what makes it reproducible in CI. Use `normalize` to rewrite a throwaway path into something clean:

```json
{ "normalize": [["/tmp/tmp.AbC123/demo", "~/my-repo"]] }
```

`normalize` rules are applied to the command **and** output shown in the recording. Live execution always runs the original, unnormalized command string or argv array, so a normalized display path never breaks the actual run.

### `output_file` confinement

`output_file` is resolved under the spec's directory. Absolute paths, `..` traversal, and symlink escapes are rejected with a `plating:` error before any artifact is written. A normal nested path inside the spec directory (e.g. `"captures/out.txt"`) works as expected.

On POSIX systems with `O_DIRECTORY` and `O_NOFOLLOW`, paths are opened component-by-component through directory descriptors so symlink swaps after validation are rejected. **Windows and other platforms without that capability cannot use `output_file`**. Use a literal `"output"` value instead. Literal backslashes in POSIX filenames are preserved and are not treated as path separators.

## Sanitization

Before rendering, plating scans the recording for `/home/...` and `/Users/...` paths, the machine's current username and hostname, private IPs, and a few narrow secret shapes (`SOMETHING_TOKEN=...` / `SOMETHING_API_KEY=...` / `SOMETHING_SECRET=...` / `SOMETHING_PASSWORD=...` assignments and PEM `-----BEGIN ... PRIVATE KEY-----` headers). Secret findings are redacted so the value is never echoed back. If the scan finds one it refuses to render and tells you how to fix it with a `normalize` rule or an explicit `--allow-leaks` override.

This scan is best-effort and dependency-free. It catches common shapes that leak into a recording. It is **not** a secrets scanner, so use a dedicated scanner for sensitive material.

```bash
plating scan some-recording.cast
```

## Live runs (`--run`)

When a step runs live, plating parses the command into an argv list and runs it with `shell=False`. Consequences:

- **Use argv arrays for non-trivial commands.** A `command` string is split with `shlex` (POSIX rules). On Windows, strings containing backslashes are rejected. POSIX also rejects ambiguous drive and UNC forms such as `C:\...` and `\\...`. Use an explicit JSON argv array for quoted arguments, empty strings, and backslash paths.
- **No shell operators.** Pipes (`|`), redirects (`>`), `&&`, `;`, backticks, and `$()` substitutions are not supported in live capture. Use a real script for anything that needs a shell.
- **Empty or malformed commands are rejected** (e.g. an unterminated quote).
- **`cwd` is confined on POSIX.** A spec-declared `cwd` is resolved relative to and confined within the spec directory using stable directory descriptors. **Windows and other platforms without `O_DIRECTORY` + `O_NOFOLLOW` reject spec-declared `cwd`** before subprocess start. The CLI `--cwd` argument may point anywhere on disk (not confined to the spec directory), but must exist and be a directory.
- **Environment is an explicit allowlist.** Live runs inherit only `PATH`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TERM`, `COLORTERM`, `NO_COLOR`, and (when present) the Windows launch variables `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PATHEXT`. `HOME`, cloud-provider, CI, SSH, and token variables are **not** inherited.
- **Timeout is enforced.** Each live step gets a positive finite timeout, default 30 seconds. Set it with the spec's `run_timeout` key or the CLI `--timeout` flag (CLI wins). A timeout is reported as a `plating:` error, not a traceback.

## Options

**Spec keys:** `title`, `width`, `height`, `padding`, `window` (macOS chrome, on by default), `prompt`, `prompt_color`, the timing knobs (`type_speed`, `line_delay`, `command_pause`, ... see `src/plating/cast.py`), `normalize`, `scan_patterns`, `cwd`, `run_timeout`.

**CLI:**

```
plating render <spec> [--run] [--cwd DIR] [--timeout SECONDS] [--out-dir DIR] [--png MS] [--allow-leaks]
plating scan <file>
plating workflow <spec> [--out FILE]
```

`--png MS` writes a static PNG of the frame at MS milliseconds (via headless Chrome), handy for a quick eyeball before you commit the SVG. `--timeout SECONDS` overrides the spec's `run_timeout` for live runs.

## A real example

`examples/brigade-quickstart.json` rebuilds the quickstart recording used in the [Brigade](https://github.com/escoffier-labs/brigade) README from its real, captured output:

```bash
plating render examples/brigade-quickstart.json
```

## License

MIT. See [LICENSE](LICENSE).
