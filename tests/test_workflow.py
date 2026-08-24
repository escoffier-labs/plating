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


def test_badge_uses_eyebrow_row_instead_of_overlapping_label():
    data = _spec()
    data["columns"][1]["nodes"][0].update(
        label="graphtrail sync", badge="incremental"
    )

    root = ET.fromstring(render_workflow(data))
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    badge = root.find('.//svg:text[@class="workflow-badge"]', namespace)
    label = next(
        element
        for element in root.findall(
            './/svg:text[@class="workflow-node-label"]', namespace
        )
        if element.text == "graphtrail sync"
    )

    assert badge is not None
    assert label is not None
    assert badge.attrib["x"] == label.attrib["x"]
    assert float(badge.attrib["y"]) < float(label.attrib["y"])
    assert root.find('.//svg:rect[@class="workflow-badge"]', namespace) is None


def test_edges_use_straight_fleet_connectors():
    root = ET.fromstring(render_workflow(_spec()))
    namespace = {"svg": "http://www.w3.org/2000/svg"}

    edges = root.findall('.//svg:line[@class="workflow-edge"]', namespace)
    curved_edges = root.findall('.//svg:path[@class="workflow-edge"]', namespace)

    assert len(edges) == 2
    assert curved_edges == []


def _node_rects(root):
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    return sorted(
        (float(e.attrib["x"]), float(e.attrib["y"]), float(e.attrib["width"]))
        for e in root.findall(".//svg:rect", namespace)
        if all(k in e.attrib for k in ("x", "y", "width", "height"))
        and float(e.attrib["width"]) < 400
        and float(e.attrib["height"]) == 68
    )


def _path_points(path):
    return [
        tuple(float(v) for v in pair.split(","))
        for pair in path.attrib["d"].replace("M ", "").split(" L ")
    ]


def test_backward_edge_routes_around_nodes():
    data = _spec()
    data["edges"] = [{"from": "check", "to": "source", "label": "feedback"}]

    root = ET.fromstring(render_workflow(data))
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    path = root.find('.//svg:path[@class="workflow-edge"]', namespace)

    assert path is not None
    points = _path_points(path)
    rects = _node_rects(root)
    # Anchors sit exactly on node edges (exits a left edge, enters a right edge).
    assert any(
        pytest.approx(x, abs=0.2) == points[0][0] for x, _, _ in rects
    )
    assert any(
        pytest.approx(x + w, abs=0.2) == points[-1][0] for x, _, w in rects
    )
    # Vertical segment lives in the gutter left of the source column.
    back_x = points[1][0]
    assert back_x < points[0][0]
    assert all(point[0] == pytest.approx(back_x, abs=0.05) for point in points[1:3])


def test_same_column_edge_uses_gutter_bypass():
    data = _spec()
    data["columns"][0]["nodes"].append({"id": "cache", "label": "cache"})
    data["edges"] = [{"from": "source", "to": "cache", "label": "warm"}]

    root = ET.fromstring(render_workflow(data))
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    path = root.find('.//svg:path[@class="workflow-edge"]', namespace)

    assert path is not None
    points = _path_points(path)
    xs = [point[0] for point in points]
    # Both anchors sit on the same node edge; bypass stays to the right.
    assert xs[0] == xs[-1]
    assert max(xs) > xs[0]
    assert len(points) == 4


def test_mixed_diagram_edges_stay_in_distinct_lanes():
    data = _spec()
    data["columns"][0]["nodes"].append({"id": "cache", "label": "cache"})
    data["edges"] = [
        {"from": "source", "to": "check"},
        {"from": "check", "to": "release"},
        {"from": "check", "to": "source"},
        {"from": "source", "to": "cache"},
    ]

    svg = render_workflow(data)
    root = ET.fromstring(svg)
    namespace = {"svg": "http://www.w3.org/2000/svg"}

    paths = root.findall('.//svg:path[@class="workflow-edge"]', namespace)
    lines = root.findall('.//svg:line[@class="workflow-edge"]', namespace)
    assert len(paths) == 2
    assert len(lines) == 2

    lanes = []
    for path in paths:
        points = _path_points(path)
        lanes.append(round(points[1][0], 1))
    assert lanes[0] != lanes[1]
    ET.fromstring(svg)
