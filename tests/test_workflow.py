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
