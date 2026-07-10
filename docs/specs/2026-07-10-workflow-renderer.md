# Plating workflow renderer

## Goal

Add a deterministic workflow-diagram mode to Plating, then use GraphTrail as the first consumer. A repository will commit a small JSON source file and the generated SVG so GitHub can render the image without Plating installed.

## Visual contract

Workflow diagrams use a 960 by 540 canvas, a dark graphite card, a narrow project-color rail, monospace labels, restrained accent colors, rounded nodes, orthogonal connectors, and a bottom context band. The renderer owns spacing and typography. A project spec supplies content and one accent color.

Every SVG includes a `<title>` and `<desc>`. Text is escaped, colors are validated as six-digit hex values, and links between unknown node IDs are rejected.

## JSON contract

The first version accepts:

- `title`, `eyebrow`, `description`, and optional `meta`
- optional `accent`, defaulting to Plating amber
- `columns`, each with a `title` and one or more nodes
- nodes with `id`, `label`, and optional `detail`, `kind`, or `badge`
- optional `edges` with `from`, `to`, and optional `label`
- optional `context` with `title`, `body`, and optional `detail`

The CLI command is `plating workflow SPEC [--out PATH]`. With no output path it writes an SVG beside the source using the JSON filename stem.

## GraphTrail pilot

GraphTrail will keep 2 JSON sources under `docs/assets/workflows/`:

1. `quickstart.json`: source files flow through `init` and incremental `sync` into SQLite, then CLI and MCP queries return graph answers and context packs.
2. `relationships.json`: callers flow into a focus symbol, then out to callees, with impact and context represented explicitly.

The generated assets replace the terminal recording and the hand-written relationship diagram in the README. The surrounding captions stay short and describe the actual flow shown.

## Non-goals

- Arbitrary node coordinates, free-form SVG fragments, icons, animation, Mermaid import, YAML, and raster export
- A general graph-layout engine
- Automatic discovery of workflow content from repository source
- Fleet-wide migration in this change

## Verification

- Unit tests cover deterministic output, escaping, invalid edges, invalid colors, and CLI output paths.
- Both GraphTrail specs render twice with byte-identical output.
- Generated SVGs parse as XML and include their accessible title and description.
- GraphTrail's existing test suite remains green.

## Growth trigger

Add another layout primitive only after a second Escoffier Labs repository has a diagram that cannot be represented by columns, edges, and the context band.
