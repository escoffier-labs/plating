import json
import xml.etree.ElementTree as ET

import pytest

from plating.cli import main
from plating.workflow import (
    WorkflowError,
    compute_workflow_layout,
    render_workflow,
    route_workflow_edges,
)


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


@pytest.mark.parametrize(
    ("drop", "path"),
    [
        ("title", "title"),
        ("eyebrow", "eyebrow"),
        ("description", "description"),
    ],
)
def test_render_workflow_requires_top_level_text(drop, path):
    data = _spec()
    data[drop] = "   "

    with pytest.raises(WorkflowError) as exc_info:
        render_workflow(data)

    assert path in str(exc_info.value)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda data: data["columns"][0].update(title="   "), "columns[0].title"),
        (lambda data: data["columns"][0]["nodes"][0].update(label=""), "columns[0].nodes[0].label"),
        (lambda data: data["columns"][0]["nodes"][0].update(id=""), "columns[0].nodes[0].id"),
    ],
)
def test_render_workflow_requires_nested_text(mutate, path):
    data = _spec()
    mutate(data)

    with pytest.raises(WorkflowError) as exc_info:
        render_workflow(data)

    assert path in str(exc_info.value)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("neon", "kind must be one of"),
        (["default"], "kind must be a string"),
        (42, "kind must be a string"),
        (None, "kind must be a string"),
    ],
)
def test_render_workflow_rejects_invalid_kind(kind, message):
    data = _spec()
    data["columns"][0]["nodes"][0]["kind"] = kind

    with pytest.raises(WorkflowError, match=message):
        render_workflow(data)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda data: data["columns"][0]["nodes"][0].update(id=" source"), "columns[0].nodes[0].id"),
        (lambda data: data["columns"][0]["nodes"][0].update(id="source "), "columns[0].nodes[0].id"),
        (lambda data: data["columns"][0]["nodes"][0].update(id=" source "), "columns[0].nodes[0].id"),
        (lambda data: data["edges"].append({"from": " check", "to": "release"}), "edges[2].from"),
        (lambda data: data["edges"].append({"from": "check", "to": " release"}), "edges[2].to"),
        (lambda data: data["edges"].append({"from": "check ", "to": "release"}), "edges[2].from"),
    ],
)
def test_render_workflow_rejects_whitespace_identifiers(mutate, path):
    data = _spec()
    mutate(data)

    with pytest.raises(WorkflowError) as exc_info:
        render_workflow(data)

    assert path in str(exc_info.value)


def test_render_workflow_rejects_whitespace_edge_endpoints_before_lookup():
    """Whitespace endpoints must fail validation, not KeyError during positions lookup."""
    data = _spec()
    data["edges"].append({"from": "source", "to": "release "})

    with pytest.raises(WorkflowError) as exc_info:
        render_workflow(data)

    assert "edges[2].to" in str(exc_info.value)


def test_render_workflow_rejects_backward_edge():
    """Edges must flow forward from an earlier column to a later column."""
    data = _spec()
    data["edges"].append({"from": "release", "to": "source"})

    with pytest.raises(WorkflowError) as exc_info:
        render_workflow(data)

    message = str(exc_info.value)
    assert "release -> source" in message
    assert "forward" in message


def test_render_workflow_rejects_same_column_edge():
    """Edges must advance to a later column; same-column edges are not allowed."""
    data = _spec()
    data["columns"][0]["nodes"].append({"id": "extra", "label": "extra"})
    data["edges"].append({"from": "source", "to": "extra"})

    with pytest.raises(WorkflowError) as exc_info:
        render_workflow(data)

    message = str(exc_info.value)
    assert "source -> extra" in message
    assert "forward" in message


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda data: data.update(title="bad\x00title"), "title"),
        (lambda data: data.update(description="line\x0bbreak"), "description"),
        (lambda data: data.update(eyebrow="eye\x1f brow"), "eyebrow"),
        (
            lambda data: data["columns"][0]["nodes"][0].update(label="lab\x02el"),
            "columns[0].nodes[0].label",
        ),
        (lambda data: data["edges"][1].update(label="pas\x07s"), "edges[1].label"),
        (lambda data: data["context"].update(body="body\x03text"), "context.body"),
    ],
)
def test_render_workflow_rejects_xml_forbidden_control_characters(mutate, field):
    data = _spec()
    mutate(data)

    with pytest.raises(WorkflowError) as exc_info:
        render_workflow(data)

    message = str(exc_info.value)
    assert field in message
    assert "forbidden XML 1.0 control character" in message


def test_render_workflow_allows_xml_legal_control_characters():
    data = _spec()
    data["context"]["body"] = "line one\nline two\tindented\rdone"

    svg = render_workflow(data)

    assert "line one\nline two\tindented\rdone" in svg
    ET.fromstring(svg)


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


def _write_spec(tmp_path, data):
    source = tmp_path / "pipeline.json"
    source.write_text(json.dumps(data))
    return source


def test_cli_invalid_spec_returns_exit_code_without_traceback(tmp_path, capsys):
    data = _spec()
    data["columns"][0]["nodes"][0]["id"] = " source"
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "columns[0].nodes[0].id" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_invalid_kind_returns_exit_code_without_traceback(tmp_path, capsys):
    data = _spec()
    data["columns"][0]["nodes"][0]["kind"] = ["default"]
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "kind must be a string" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_whitespace_edge_endpoint_returns_exit_code_without_traceback(tmp_path, capsys):
    data = _spec()
    data["edges"].append({"from": "check", "to": "release "})
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "edges[2].to" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_xml_control_character_returns_exit_code_without_traceback(tmp_path, capsys):
    data = _spec()
    data["title"] = "bad\x00title"
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "forbidden XML 1.0 control character" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_workflow_leak_scan_prevents_svg_write_and_directory_creation(
    tmp_path, capsys
):
    data = _spec()
    ip = ".".join(["192", "168", "1", "1"])
    data["description"] = f"deploy to {ip} internal host"
    source = _write_spec(tmp_path, data)
    out_dir = tmp_path / "nested" / "deep"
    output = out_dir / "pipeline.svg"

    code = main(["workflow", str(source), "--out", str(output)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private-ip" in captured.err
    assert "Traceback" not in captured.err
    assert not output.exists()
    assert not out_dir.exists()


def test_cli_workflow_leak_scan_checks_raw_input_when_xml_escaping_hides_it(
    tmp_path, capsys
):
    data = _spec()
    data["columns"][0]["nodes"][0]["detail"] = 'leak "release-secret" token'
    data["scan_patterns"] = [["release-token", r'"release-secret"']]
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "release-token" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_workflow_leak_scan_clean_spec_with_quoted_secret_pattern_writes_svg(
    tmp_path, capsys
):
    data = _spec()
    data["scan_patterns"] = [["release-token", r'"release-secret"']]
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 0
    captured = capsys.readouterr()
    output = tmp_path / "pipeline.svg"
    assert output.exists()
    assert output.read_text() == render_workflow(_spec())
    assert captured.out == f"plating: wrote {output}\n"
    assert captured.err == ""


def test_cli_workflow_leak_scan_catches_renderer_uppercased_eyebrow(
    tmp_path, capsys
):
    data = _spec()
    data["eyebrow"] = "secret-value-eyebrow"
    data["scan_patterns"] = [["upper-secret", r"SECRET-VALUE-EYEBROW"]]
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "upper-secret" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_workflow_leak_scan_deduplicates_input_and_svg_findings(
    tmp_path, capsys
):
    data = _spec()
    ip = ".".join(["10", "0", "0", "1"])
    data["description"] = f"deploy to {ip}"
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.err.count("private-ip") == 1
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_workflow_write_oserror_returns_two_without_traceback(tmp_path, capsys):
    data = _spec()
    source = _write_spec(tmp_path, data)
    output = tmp_path / "pipeline.svg"
    output.mkdir()

    code = main(["workflow", str(source), "--out", str(output)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_cli_workflow_malformed_scan_pattern_returns_two_without_traceback(
    tmp_path, capsys
):
    data = _spec()
    data["scan_patterns"] = [["bad-pattern", "["]]
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert "bad-pattern" in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_workflow_malformed_scan_pattern_shape_returns_two_without_traceback(
    tmp_path, capsys
):
    data = _spec()
    data["scan_patterns"] = [["missing-regex-only"]]
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert "scan_patterns" in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


def test_cli_workflow_rejects_unsupported_scan_policy_without_traceback(
    tmp_path, capsys
):
    data = _spec()
    data["scan_policy"] = "policies/public-repo.json"
    source = _write_spec(tmp_path, data)

    code = main(["workflow", str(source)])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert "scan_policy" in captured.err
    assert "scan_patterns" in captured.err
    assert not (tmp_path / "pipeline.svg").exists()


# --- Issue #23: edge geometry matrix (helpers; contract stays forward-only) ---

_NS = {"svg": "http://www.w3.org/2000/svg"}


def _boxes(positions):
    return {
        node_id: (x, y, x + w, y + h)
        for node_id, (x, y, w, h) in positions.items()
    }


def _segment_hits_box(x1, y1, x2, y2, left, top, right, bottom, *, endpoint_ok=False):
    """Return True if open segment (x1,y1)-(x2,y2) intersects the box interior."""
    # Reject if either endpoint lies strictly inside the box.
    for px, py in ((x1, y1), (x2, y2)):
        if left < px < right and top < py < bottom:
            return True
    # Liang-Barsky style clip against expanded-open box (edges count as exterior).
    dx = x2 - x1
    dy = y2 - y1
    p = (-dx, dx, -dy, dy)
    q = (x1 - left, right - x1, y1 - top, bottom - y1)
    u1, u2 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0:
            if qi < 0:
                return False
            continue
        t = qi / pi
        if pi < 0:
            if t > u2:
                return False
            if t > u1:
                u1 = t
        else:
            if t < u1:
                return False
            if t < u2:
                u2 = t
    if u1 >= u2:
        return False
    # Touching only at u=0 or u=1 (endpoint on boundary) is allowed when endpoint_ok.
    if endpoint_ok and ((u1 == 0.0 and u2 == 0.0) or (u1 == 1.0 and u2 == 1.0)):
        return False
    # Intersection with interior of parametric range.
    mid = (u1 + u2) / 2
    mx = x1 + mid * dx
    my = y1 + mid * dy
    return left < mx < right and top < my < bottom


def _assert_route_avoids_nodes(points, positions, source_id, target_id):
    boxes = _boxes(positions)
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        for node_id, (left, top, right, bottom) in boxes.items():
            if node_id in (source_id, target_id):
                # Endpoints may sit on the source/target anchor; other segments
                # must still stay outside the node interior.
                if i == 0 and node_id == source_id:
                    continue
                if i == len(points) - 2 and node_id == target_id:
                    continue
            assert not _segment_hits_box(
                x1, y1, x2, y2, left, top, right, bottom
            ), f"segment {i} crosses node {node_id}"


def _layout_columns(columns):
    data = {
        "title": "Geometry",
        "eyebrow": "TEST",
        "description": "geometry fixture",
        "columns": columns,
        "edges": [],
    }
    return compute_workflow_layout(data)


def test_geometry_forward_edge_unchanged_byte_for_byte():
    """Simple forward edges keep the historical straight-line SVG bytes."""
    first = render_workflow(_spec())
    second = render_workflow(_spec())
    assert first == second
    root = ET.fromstring(first)
    edges = root.findall('.//svg:line[@class="workflow-edge"]', _NS)
    assert len(edges) == 2
    assert edges[0].attrib["x1"] == "324.0"
    assert edges[0].attrib["y1"] == "271.0"
    assert edges[0].attrib["x2"] == "354.0"
    assert edges[0].attrib["y2"] == "271.0"
    assert edges[1].attrib["x1"] == "606.0"
    assert edges[1].attrib["y1"] == "271.0"
    assert edges[1].attrib["x2"] == "636.0"
    assert edges[1].attrib["y2"] == "271.0"
    assert root.findall('.//svg:polyline[@class="workflow-edge"]', _NS) == []
    assert root.findall('.//svg:path[@class="workflow-edge"]', _NS) == []


def test_geometry_backward_edge_uses_left_to_right_exterior_lane():
    columns = [
        {"title": "A", "nodes": [{"id": "left", "label": "left"}]},
        {"title": "B", "nodes": [{"id": "right", "label": "right"}]},
    ]
    positions, node_columns = _layout_columns(columns)
    routes = route_workflow_edges(
        positions,
        node_columns,
        [{"from": "right", "to": "left"}],
    )
    assert len(routes) == 1
    points = routes[0].points
    src = positions["right"]
    tgt = positions["left"]
    assert points[0] == pytest.approx((src[0], src[1] + src[3] / 2))
    assert points[-1] == pytest.approx((tgt[0] + tgt[2], tgt[1] + tgt[3] / 2))
    assert len(points) >= 4
    _assert_route_avoids_nodes(points, positions, "right", "left")


def test_geometry_same_column_uses_vertical_anchors_by_center():
    columns = [
        {
            "title": "A",
            "nodes": [
                {"id": "top", "label": "top"},
                {"id": "bottom", "label": "bottom"},
            ],
        },
        {"title": "B", "nodes": [{"id": "other", "label": "other"}]},
    ]
    positions, node_columns = _layout_columns(columns)
    down = route_workflow_edges(
        positions, node_columns, [{"from": "top", "to": "bottom"}]
    )[0].points
    up = route_workflow_edges(
        positions, node_columns, [{"from": "bottom", "to": "top"}]
    )[0].points
    top = positions["top"]
    bottom = positions["bottom"]
    assert down[0] == pytest.approx((top[0] + top[2] / 2, top[1] + top[3]))
    assert down[-1] == pytest.approx((bottom[0] + bottom[2] / 2, bottom[1]))
    assert up[0] == pytest.approx((bottom[0] + bottom[2] / 2, bottom[1]))
    assert up[-1] == pytest.approx((top[0] + top[2] / 2, top[1] + top[3]))
    _assert_route_avoids_nodes(down, positions, "top", "bottom")
    _assert_route_avoids_nodes(up, positions, "bottom", "top")


def test_geometry_same_column_center_tie_breaks_by_node_id():
    # Force equal centers by feeding synthetic boxes with identical cy.
    positions = {
        "a-node": (100.0, 200.0, 80.0, 40.0),
        "b-node": (100.0, 200.0, 80.0, 40.0),
    }
    node_columns = {"a-node": 0, "b-node": 0}
    # Identical centers: lower id leaves via bottom when id orders a < b? 
    # Stable rule: when cy equal, the lexicographically smaller id is treated
    # as "above" so it exits bottom toward the other.
    route = route_workflow_edges(
        positions, node_columns, [{"from": "a-node", "to": "b-node"}]
    )[0].points
    assert route[0] == pytest.approx((140.0, 240.0))  # bottom-center of a
    assert route[-1] == pytest.approx((140.0, 200.0))  # top-center of b


def test_geometry_parallel_edges_get_stable_lane_offsets():
    columns = [
        {"title": "A", "nodes": [{"id": "left", "label": "left"}]},
        {"title": "B", "nodes": [{"id": "right", "label": "right"}]},
    ]
    positions, node_columns = _layout_columns(columns)
    edges = [
        {"from": "right", "to": "left", "label": "one"},
        {"from": "right", "to": "left", "label": "two"},
    ]
    first = route_workflow_edges(positions, node_columns, edges)
    second = route_workflow_edges(positions, node_columns, edges)
    assert first[0].points == second[0].points
    assert first[1].points == second[1].points
    assert first[0].points != first[1].points
    _assert_route_avoids_nodes(first[0].points, positions, "right", "left")
    _assert_route_avoids_nodes(first[1].points, positions, "right", "left")


def test_geometry_two_node_cycle_pair_uses_distinct_lanes():
    columns = [
        {"title": "A", "nodes": [{"id": "left", "label": "left"}]},
        {"title": "B", "nodes": [{"id": "right", "label": "right"}]},
    ]
    positions, node_columns = _layout_columns(columns)
    edges = [
        {"from": "left", "to": "right"},
        {"from": "right", "to": "left"},
    ]
    routes = route_workflow_edges(positions, node_columns, edges)
    assert routes[0].points != routes[1].points
    # Forward stays right-center -> left-center endpoints.
    src = positions["left"]
    tgt = positions["right"]
    assert routes[0].points[0] == pytest.approx((src[0] + src[2], src[1] + src[3] / 2))
    assert routes[0].points[-1] == pytest.approx((tgt[0], tgt[1] + tgt[3] / 2))
    assert routes[1].points[0] == pytest.approx((tgt[0], tgt[1] + tgt[3] / 2))
    assert routes[1].points[-1] == pytest.approx((src[0] + src[2], src[1] + src[3] / 2))
    for route, frm, to in (
        (routes[0], "left", "right"),
        (routes[1], "right", "left"),
    ):
        _assert_route_avoids_nodes(route.points, positions, frm, to)


def test_geometry_same_column_routes_around_intervening_node():
    columns = [
        {
            "title": "A",
            "nodes": [
                {"id": "top", "label": "top"},
                {"id": "mid", "label": "mid"},
                {"id": "bottom", "label": "bottom"},
            ],
        },
        {"title": "B", "nodes": [{"id": "other", "label": "other"}]},
    ]
    positions, node_columns = _layout_columns(columns)
    points = route_workflow_edges(
        positions, node_columns, [{"from": "top", "to": "bottom"}]
    )[0].points
    assert len(points) >= 4
    _assert_route_avoids_nodes(points, positions, "top", "bottom")
    # Must not be a direct vertical through mid.
    mid = positions["mid"]
    mid_box = (mid[0], mid[1], mid[0] + mid[2], mid[1] + mid[3])
    for i in range(len(points) - 1):
        assert not _segment_hits_box(
            points[i][0],
            points[i][1],
            points[i + 1][0],
            points[i + 1][1],
            *mid_box,
        )


def test_geometry_impossible_route_raises_explicit_diagnostic():
    # Overlapping boxes leave no exterior clearance for a backward stub.
    positions = {
        "a": (0.0, 0.0, 960.0, 540.0),
        "b": (0.0, 0.0, 960.0, 540.0),
    }
    node_columns = {"a": 1, "b": 0}
    with pytest.raises(WorkflowError, match="edge geometry"):
        route_workflow_edges(positions, node_columns, [{"from": "a", "to": "b"}])


def test_geometry_routes_are_deterministic():
    columns = [
        {
            "title": "A",
            "nodes": [
                {"id": "a1", "label": "a1"},
                {"id": "a2", "label": "a2"},
            ],
        },
        {"title": "B", "nodes": [{"id": "b1", "label": "b1"}]},
        {"title": "C", "nodes": [{"id": "c1", "label": "c1"}]},
    ]
    positions, node_columns = _layout_columns(columns)
    edges = [
        {"from": "a1", "to": "b1"},
        {"from": "c1", "to": "a1"},
        {"from": "a1", "to": "a2"},
        {"from": "b1", "to": "c1"},
        {"from": "c1", "to": "b1"},
    ]
    assert route_workflow_edges(positions, node_columns, edges) == route_workflow_edges(
        positions, node_columns, edges
    )


# --- Repair regressions for issue #23 draft PR defects ---

HISTORICAL_FORWARD_CROSSING_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540" role="img" aria-labelledby="workflow-title workflow-desc">\n  <title id="workflow-title">Forward cross</title>\n  <desc id="workflow-desc">valid forward spanning columns</desc>\n  <defs>\n    <linearGradient id="workflow-bg" x1="0" y1="0" x2="1" y2="1">\n      <stop offset="0" stop-color="#0d1014"/>\n      <stop offset="1" stop-color="#0f1318"/>\n    </linearGradient>\n    <linearGradient id="workflow-card" x1="0" y1="0" x2="0" y2="1">\n      <stop offset="0" stop-color="#11161c"/>\n      <stop offset="1" stop-color="#0f1318"/>\n    </linearGradient>\n    <filter id="workflow-shadow" x="-10%" y="-10%" width="120%" height="130%">\n      <feDropShadow dx="0" dy="14" stdDeviation="16" flood-color="#000000" flood-opacity="0.38"/>\n    </filter>\n    <marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">\n      <path d="M0,0 L8,4 L0,8 Z" fill="#e0a45c"/>\n    </marker>\n  </defs>\n  <rect width="960" height="540" fill="url(#workflow-bg)"/>\n  <circle cx="872" cy="52" r="220" fill="#e0a45c" opacity="0.07"/>\n  <circle cx="62" cy="506" r="180" fill="#e0a45c" opacity="0.04"/>\n  <rect x="36" y="28" width="888" height="484" rx="20" fill="url(#workflow-card)" stroke="#2a323d" filter="url(#workflow-shadow)"/>\n  <rect x="36" y="28" width="5" height="484" rx="2.5" fill="#e0a45c"/>\n  <text x="72" y="67" fill="#e0a45c" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="11" font-weight="600" letter-spacing="2.4">TEST</text>\n  <text x="72" y="96" fill="#dde3ea" font-family="Inter, ui-sans-serif, sans-serif" font-size="21" font-weight="700" letter-spacing="-0.6">Forward cross</text>\n  <text x="72" y="119" fill="#9aa4b2" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="11">valid forward spanning columns</text>\n  <line x1="72" y1="139" x2="888" y2="139" stroke="#1e242c"/>\n  <text x="72.0" y="164" fill="#e0a45c" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10" font-weight="600" letter-spacing="1.7">A</text>\n  <text x="354.0" y="164" fill="#e0a45c" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10" font-weight="600" letter-spacing="1.7">B</text>\n  <text x="636.0" y="164" fill="#e0a45c" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10" font-weight="600" letter-spacing="1.7">C</text>\n  <line class="workflow-edge" x1="324.0" y1="312.0" x2="636.0" y2="312.0" stroke="#e0a45c" stroke-width="1.5" opacity="0.9" marker-end="url(#workflow-arrow)"/>\n  <rect x="72.0" y="196.0" width="252.0" height="68" rx="12" fill="#11161c" stroke="#2a323d"/>\n  <text class="workflow-node-label" x="88.0" y="225.0" fill="#dde3ea" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="14" font-weight="600">n0_0</text>\n  <rect x="72.0" y="278.0" width="252.0" height="68" rx="12" fill="#11161c" stroke="#2a323d"/>\n  <text class="workflow-node-label" x="88.0" y="307.0" fill="#dde3ea" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="14" font-weight="600">n0_1</text>\n  <rect x="354.0" y="196.0" width="252.0" height="68" rx="12" fill="#11161c" stroke="#2a323d"/>\n  <text class="workflow-node-label" x="370.0" y="225.0" fill="#dde3ea" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="14" font-weight="600">n1_0</text>\n  <rect x="354.0" y="278.0" width="252.0" height="68" rx="12" fill="#11161c" stroke="#2a323d"/>\n  <text class="workflow-node-label" x="370.0" y="307.0" fill="#dde3ea" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="14" font-weight="600">n1_1</text>\n  <rect x="636.0" y="196.0" width="252.0" height="68" rx="12" fill="#11161c" stroke="#2a323d"/>\n  <text class="workflow-node-label" x="652.0" y="225.0" fill="#dde3ea" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="14" font-weight="600">n2_0</text>\n  <rect x="636.0" y="278.0" width="252.0" height="68" rx="12" fill="#11161c" stroke="#2a323d"/>\n  <text class="workflow-node-label" x="652.0" y="307.0" fill="#dde3ea" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="14" font-weight="600">n2_1</text>\n</svg>\n'


def _forward_crossing_spec():
    return {
        "title": "Forward cross",
        "eyebrow": "TEST",
        "description": "valid forward spanning columns",
        "columns": [
            {
                "title": "A",
                "nodes": [
                    {"id": "n0_0", "label": "n0_0"},
                    {"id": "n0_1", "label": "n0_1"},
                ],
            },
            {
                "title": "B",
                "nodes": [
                    {"id": "n1_0", "label": "n1_0"},
                    {"id": "n1_1", "label": "n1_1"},
                ],
            },
            {
                "title": "C",
                "nodes": [
                    {"id": "n2_0", "label": "n2_0"},
                    {"id": "n2_1", "label": "n2_1"},
                ],
            },
        ],
        "edges": [{"from": "n0_1", "to": "n2_1"}],
    }


def test_geometry_forward_spanning_preserves_historical_svg_bytes():
    """Already-valid forward edges keep base 067718c SVG bytes, including spans."""
    svg = render_workflow(_forward_crossing_spec())
    assert svg == HISTORICAL_FORWARD_CROSSING_SVG
    assert (
        '<line class="workflow-edge" x1="324.0" y1="312.0" '
        'x2="636.0" y2="312.0"'
    ) in svg
    assert '<polyline class="workflow-edge"' not in svg


def test_geometry_same_column_parallel_and_cycle_do_not_overlap():
    """Parallel and reverse-cycle same-column routes keep anchors but not paths."""
    positions = {
        "a": (100.0, 100.0, 80.0, 40.0),
        "b": (100.0, 200.0, 80.0, 40.0),
    }
    node_columns = {"a": 0, "b": 0}

    parallel = route_workflow_edges(
        positions,
        node_columns,
        [{"from": "a", "to": "b"}, {"from": "a", "to": "b"}],
    )
    assert parallel[0].points[0] == pytest.approx((140.0, 140.0))
    assert parallel[0].points[-1] == pytest.approx((140.0, 200.0))
    assert parallel[1].points[0] == pytest.approx((140.0, 140.0))
    assert parallel[1].points[-1] == pytest.approx((140.0, 200.0))
    assert parallel[0].points != parallel[1].points
    _assert_route_avoids_nodes(parallel[0].points, positions, "a", "b")
    _assert_route_avoids_nodes(parallel[1].points, positions, "a", "b")

    cycle = route_workflow_edges(
        positions,
        node_columns,
        [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    )
    assert cycle[0].points[0] == pytest.approx((140.0, 140.0))
    assert cycle[0].points[-1] == pytest.approx((140.0, 200.0))
    assert cycle[1].points[0] == pytest.approx((140.0, 200.0))
    assert cycle[1].points[-1] == pytest.approx((140.0, 140.0))
    # Geometric non-overlap: not the same polyline and not the exact reverse.
    assert cycle[0].points != cycle[1].points
    assert cycle[1].points != tuple(reversed(cycle[0].points))
    _assert_route_avoids_nodes(cycle[0].points, positions, "a", "b")
    _assert_route_avoids_nodes(cycle[1].points, positions, "b", "a")


def test_geometry_missing_edge_endpoint_raises_workflow_error():
    positions = {
        "a": (100.0, 100.0, 80.0, 40.0),
        "b": (100.0, 200.0, 80.0, 40.0),
    }
    node_columns = {"a": 0, "b": 0}
    with pytest.raises(WorkflowError, match=r"edges\[0\]\.to") as exc_info:
        route_workflow_edges(positions, node_columns, [{"from": "a"}])
    assert "KeyError" not in type(exc_info.value).__name__
