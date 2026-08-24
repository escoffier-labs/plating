# Memory Handoff

## Type
decision

## Title
Escoffier Labs workflow diagrams use Plating JSON sources

## Summary
Plating owns the shared workflow-diagram layout for Escoffier Labs repositories. Consuming repos commit a constrained JSON source and its generated SVG, which prevents hand-positioned assets from drifting while keeping GitHub rendering dependency-free.

## Durable facts
- Run `plating workflow SPEC [--out PATH]` to produce a 960 by 540 accessible SVG.
- The v1 JSON contract supports 2 to 4 columns, up to 3 nodes per column, explicit edges, and an optional context band.
- Colors are fixed to the canonical dark-ledger tokens in `escoffier-fleet-kit/DESIGN.md`; consuming specs cannot override the fleet palette.
- The renderer owns coordinates, typography, spacing, node styles, connectors, and SVG accessibility metadata.
- GraphTrail is the first consumer, with sources under `docs/assets/workflows/`.

## Evidence
- files changed: `src/plating/workflow.py`, `tests/test_workflow.py`, `examples/workflow.json`, `README.md`
- commands run: `PYTHONPATH=src python3 -m plating.cli workflow examples/workflow.json`; `/usr/bin/env PYTHONPATH=src pytest -q`
- design: `docs/specs/2026-07-10-workflow-renderer.md`

## Recommended memory action
create-card

## Target card
escoffier-workflow-diagrams.md

## Suggested card content
---
title: Escoffier Labs workflow diagrams
tags: [escoffier-labs, documentation, svg, plating]
---

Use Plating for matching workflow diagrams across Escoffier Labs repositories. Store the content in a constrained JSON spec and commit the generated SVG beside the README assets. Run `plating workflow SPEC --out PATH`. Plating owns the 960 by 540 layout, canonical dark-ledger colors from `escoffier-fleet-kit/DESIGN.md`, typography, node styling, connectors, and accessible `<title>` and `<desc>` elements. Consuming specs supply content, not colors. Add layout primitives only when a second real repository cannot fit the existing columns, edges, and context-band contract.
