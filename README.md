<p align="center">
  <img src="docs/assets/plating-social-preview.jpg" alt="plating banner" width="900">
</p>

<h1 align="center">plating</h1>

<p align="center">
  <img src="docs/assets/marks/plating-circle.svg" alt="" width="40" height="40">
</p>

<p align="center">
  <strong>README terminal demos without recording leaks.</strong>
</p>

<p align="center">
  Turn a small JSON spec plus captured command output into a reproducible, sanitized animated SVG. Leak scan refuses home paths and hostnames. No runtime JavaScript in the embed.
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

## Where each step's output comes from

In priority order:

| In the step | Output is |
|---|---|
| `"output": "..."` | the literal string |
| `"output_file": "path"` | a captured-output file (relative to the spec) |
| `"run": true` (or `plating render --run`) | the live result of running `command` |

Live capture (`--run`) is convenient; committing captured output is what makes it reproducible in CI. Use `normalize` to rewrite a throwaway path into something clean:

```json
{ "normalize": [["/tmp/tmp.AbC123/demo", "~/my-repo"]] }
```

## Sanitization

Before rendering, plating scans the recording for `/home/...` and `/Users/...` paths, the machine's current username and hostname, and private IPs. If it finds one it refuses to render and tells you how to fix it (add a `normalize` rule, or pass `--allow-leaks`). You can also point the scan at a Content Guard policy JSON so demo recordings share the same fleet denylist:

```bash
plating scan some-recording.cast
plating scan some-recording.cast --policy ../content-guard/policies/public-repo.json
plating render quickstart.json --scan-policy ../content-guard/policies/public-repo.json
plating verify quickstart.json --scan-policy ../content-guard/policies/public-repo.json
```

## Options

**Spec keys:** `title`, `width`, `height`, `padding`, `window` (macOS chrome, on by default), `prompt`, `prompt_color`, the timing knobs (`type_speed`, `line_delay`, `command_pause`, ... see `src/plating/cast.py`), `normalize`, `scan_patterns`, `scan_policy`, `cwd`.

**CLI:**

```
plating render <spec> [--run] [--cwd DIR] [--out-dir DIR] [--png MS] [--allow-leaks] [--scan-policy FILE]
plating verify <spec> [--cwd DIR] [--allow-leaks] [--scan-policy FILE]
plating scan <file> [--policy FILE]
```

`--png MS` writes a static PNG of the frame at MS milliseconds (via headless Chrome), handy for a quick eyeball before you commit the SVG.

## A real example

`examples/brigade-quickstart.json` rebuilds the quickstart recording used in the [Brigade](https://github.com/escoffier-labs/brigade) README from its real, captured output:

```bash
plating render examples/brigade-quickstart.json
```

## License

MIT. See [LICENSE](LICENSE).
