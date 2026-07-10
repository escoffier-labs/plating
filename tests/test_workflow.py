import json
import xml.etree.ElementTree as ET

import pytest

from plating.cli import main
from plating.workflow import WorkflowError, render_workflow


def _spec():
    return {
        "title": "Build < ship",
        "eyebrow": "THE WORKFLOW",
        "description": "Source & checks become a release.",
        "meta": "deterministic SVG",
        "columns": [
            {
                "title": "INPUT",
                "nodes": [
                    {"id": "source", "label": "source", "detail": "tracked files"}
                ],
            },
            {
                "title": "BUILD",
                "nodes": [
                    {
                        "id": "check",
                        "label": "verify",
                        "detail": "tests + scan",
                        "badge": "gate",
                    }
                ],
            },
            {
                "title": "OUTPUT",
                "nodes": [
                    {"id": "release", "label": "release", "detail": "signed artifact"}
                ],
            },
        ],
        "edges": [
            {"from": "source", "to": "check"},
            {"from": "check", "to": "release", "label": "pass"},
        ],
        "context": {
            "title": "RECEIPT",
            "body": "command + exit code + artifact digest",
            "detail": "checked before publish",
        },
    }


def test_render_workflow_is_accessible_escaped_and_deterministic():
    first = render_workflow(_spec())
    second = render_workflow(_spec())

    assert first == second
    ET.fromstring(first)
    assert '<title id="workflow-title">Build &lt; ship</title>' in first
    assert '<desc id="workflow-desc">Source &amp; checks become a release.</desc>' in first
    assert "source" in first
    assert "verify" in first
    assert "release" in first
    assert ">pass<" in first
    assert "command + exit code + artifact digest" in first
    for color in (
        "#0d1014",
        "#11161c",
        "#0f1318",
        "#dde3ea",
        "#9aa4b2",
        "#7d8590",
        "#e0a45c",
        "#1e242c",
        "#2a323d",
    ):
        assert color in first
    assert "#182338" not in first
    assert "#4F86FF" not in first


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data["edges"].append({"from": "missing", "to": "check"}),
            "unknown node",
        ),
        (
            lambda data: data["columns"][1]["nodes"].append(
                {"id": "source", "label": "duplicate"}
            ),
            "duplicate node id",
        ),
        (lambda data: data["columns"][0].update(nodes=[]), "at least one node"),
    ],
)
def test_render_workflow_rejects_invalid_specs(mutate, message):
    data = _spec()
    mutate(data)

    with pytest.raises(WorkflowError, match=message):
        render_workflow(data)


def test_workflow_command_writes_svg_beside_spec(tmp_path, capsys):
    source = tmp_path / "pipeline.json"
    source.write_text(json.dumps(_spec()))

    assert main(["workflow", str(source)]) == 0

    output = tmp_path / "pipeline.svg"
    assert output.exists()
    assert output.read_text() == render_workflow(_spec())
    assert capsys.readouterr().out == f"plating: wrote {output}\n"
