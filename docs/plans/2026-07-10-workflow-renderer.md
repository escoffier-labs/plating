# Workflow renderer implementation plan

**Goal:** Add a dependency-free `plating workflow` command that turns a constrained JSON workflow spec into a deterministic, accessible SVG.

**Architecture:** `workflow.py` validates the JSON document, computes a fixed column layout, and renders SVG with `xml.sax.saxutils.escape`. `cli.py` handles paths and error reporting. GraphTrail owns its content specs and generated assets.

**Key technology:** Python 3.10 standard library, JSON, SVG 1.1, pytest.

Execute each task in order and tick every checkbox after its command produces the expected result.

## File map

- `src/plating/workflow.py`: workflow validation, layout, and SVG rendering.
- `src/plating/cli.py`: `workflow` subcommand and file IO.
- `tests/test_workflow.py`: renderer and CLI contract tests.
- `README.md`: workflow command documentation and example.
- `examples/workflow.json`: runnable example source.
- `examples/workflow.svg`: generated example output.
- `docs/specs/2026-07-10-workflow-renderer.md`: approved design.
- `docs/plans/2026-07-10-workflow-renderer.md`: this execution record.

### Task 1: Renderer contract

**Files:**
- Create: `tests/test_workflow.py`
- Create: `src/plating/workflow.py`

- [x] Write tests that call `render_workflow` with a 3-column document, parse the SVG as XML, assert its title, description, nodes, edge label, escaped content, and byte-identical second render.
- [x] Write tests that assert `WorkflowError` for an edge referencing an unknown ID, a duplicate node ID, and an empty column.
- [x] Run `/usr/bin/env PYTHONPATH=src pytest -q tests/test_workflow.py`; expect collection to fail because `plating.workflow` does not exist.
- [x] Implement `WorkflowError`, `validate_workflow`, and `render_workflow`. Use a fixed 960 by 540 canvas, equal-width columns, stacked nodes, cubic connectors, and one optional bottom context band.
- [x] Re-run `/usr/bin/env PYTHONPATH=src pytest -q tests/test_workflow.py`; expect all renderer tests to pass.
- [x] Commit with `feat: add deterministic workflow SVG renderer`.

### Task 2: CLI command

**Files:**
- Modify: `src/plating/cli.py`
- Modify: `tests/test_workflow.py`

- [x] Add a CLI test that writes a source JSON file, calls `main(["workflow", source])`, and asserts the default sibling SVG and success message.
- [x] Run the focused test; expect failure because `workflow` is not a registered command.
- [x] Add `_workflow`, register `plating workflow SPEC --out PATH`, catch JSON and workflow validation errors, and return exit code 2 without a traceback.
- [x] Re-run the focused test and full workflow test file; expect all tests to pass.
- [x] Commit with `feat: expose workflow renderer in CLI`.

### Task 3: Plating example and docs

**Files:**
- Create: `examples/workflow.json`
- Create: `examples/workflow.svg`
- Modify: `README.md`

- [x] Add a concrete 3-stage example spec using only the documented contract.
- [x] Render it with `/usr/bin/env PYTHONPATH=src python3 -m plating.cli workflow examples/workflow.json`; expect `examples/workflow.svg`.
- [x] Add the workflow use case, CLI syntax, JSON example, and embedded SVG to the README.
- [x] Run the prose leak/style scan and XML parse check; expect no findings and valid XML.
- [x] Run the full Plating test suite through Brigade; expect all tests to pass.
- [x] Commit with `docs: document reusable workflow diagrams`.

### Task 4: GraphTrail pilot

**Files in the consuming `graphtrail` repository:**
- Create: `docs/assets/workflows/quickstart.json`
- Create: `docs/assets/workflows/relationships.json`
- Replace: `docs/assets/graphtrail-context.svg`
- Replace: `docs/assets/graph-relationships.svg`
- Modify: `README.md`

- [x] Add the 2 GraphTrail workflow specs using the Plating contract.
- [x] Render both SVGs with the Plating feature branch and parse them as XML.
- [x] Re-render both into temporary files and compare bytes with the committed outputs; expect no differences.
- [x] Update README alt text and captions to match the generated diagrams.
- [x] Run the prose leak/style scan; expect no new findings. The existing documented Code Search fallback remains intentional.
- [x] Run `cargo test --all-features` through Brigade; expect all tests to pass.
- [x] Commit with `docs: standardize workflow diagrams with plating`.
