"""Render constrained workflow specifications as deterministic SVGs."""
from __future__ import annotations

import re
from xml.sax.saxutils import escape


class WorkflowError(ValueError):
    """Raised when a workflow specification cannot be rendered."""


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_KINDS = {"default", "accent", "focus", "success", "muted"}


def _required_text(value, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{path} must be a non-empty string")
    return value.strip()


def validate_workflow(data: dict) -> None:
    if not isinstance(data, dict):
        raise WorkflowError("workflow must be a JSON object")

    for key in ("title", "eyebrow", "description"):
        _required_text(data.get(key), key)

    accent = data.get("accent", "#E0A45C")
    if not isinstance(accent, str) or not _HEX_COLOR.fullmatch(accent):
        raise WorkflowError("accent must be a six-digit hex color such as #E0A45C")

    columns = data.get("columns")
    if not isinstance(columns, list) or not 2 <= len(columns) <= 4:
        raise WorkflowError("columns must contain between 2 and 4 columns")

    node_ids: set[str] = set()
    for column_index, column in enumerate(columns):
        if not isinstance(column, dict):
            raise WorkflowError(f"columns[{column_index}] must be an object")
        _required_text(column.get("title"), f"columns[{column_index}].title")
        nodes = column.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise WorkflowError(f"columns[{column_index}] must contain at least one node")
        if len(nodes) > 3:
            raise WorkflowError(f"columns[{column_index}] supports at most 3 nodes")
        for node_index, node in enumerate(nodes):
            path = f"columns[{column_index}].nodes[{node_index}]"
            if not isinstance(node, dict):
                raise WorkflowError(f"{path} must be an object")
            node_id = _required_text(node.get("id"), f"{path}.id")
            _required_text(node.get("label"), f"{path}.label")
            if node_id in node_ids:
                raise WorkflowError(f"duplicate node id: {node_id}")
            node_ids.add(node_id)
            kind = node.get("kind", "default")
            if kind not in _KINDS:
                raise WorkflowError(f"{path}.kind must be one of {sorted(_KINDS)}")

    edges = data.get("edges", [])
    if not isinstance(edges, list):
        raise WorkflowError("edges must be a list")
    for edge_index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise WorkflowError(f"edges[{edge_index}] must be an object")
        source = _required_text(edge.get("from"), f"edges[{edge_index}].from")
        target = _required_text(edge.get("to"), f"edges[{edge_index}].to")
        if source not in node_ids or target not in node_ids:
            raise WorkflowError(
                f"edge {source} -> {target} references an unknown node"
            )

    context = data.get("context")
    if context is not None:
        if not isinstance(context, dict):
            raise WorkflowError("context must be an object")
        _required_text(context.get("title"), "context.title")
        _required_text(context.get("body"), "context.body")


def _text(value) -> str:
    return escape(str(value), {'"': "&quot;"})


def render_workflow(data: dict) -> str:
    """Validate *data* and return a complete SVG document."""
    validate_workflow(data)

    accent = data.get("accent", "#E0A45C").upper()
    columns = data["columns"]
    left = 72
    right = 888
    gutter = 30
    column_width = (right - left - gutter * (len(columns) - 1)) / len(columns)
    node_height = 68
    node_gap = 14
    node_top = 176
    positions: dict[str, tuple[float, float, float, float]] = {}

    for column_index, column in enumerate(columns):
        x = left + column_index * (column_width + gutter)
        nodes = column["nodes"]
        block_height = len(nodes) * node_height + (len(nodes) - 1) * node_gap
        y = node_top + (190 - block_height) / 2
        for node in nodes:
            positions[node["id"]] = (x, y, column_width, node_height)
            y += node_height + node_gap

    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" '
        'viewBox="0 0 960 540" role="img" aria-labelledby="workflow-title workflow-desc">',
        f"  <title id=\"workflow-title\">{_text(data['title'])}</title>",
        f"  <desc id=\"workflow-desc\">{_text(data['description'])}</desc>",
        "  <defs>",
        '    <linearGradient id="workflow-bg" x1="0" y1="0" x2="1" y2="1">',
        '      <stop offset="0" stop-color="#182338"/>',
        '      <stop offset="1" stop-color="#101722"/>',
        "    </linearGradient>",
        '    <linearGradient id="workflow-card" x1="0" y1="0" x2="0" y2="1">',
        '      <stop offset="0" stop-color="#171B22"/>',
        '      <stop offset="1" stop-color="#0E1319"/>',
        "    </linearGradient>",
        '    <filter id="workflow-shadow" x="-10%" y="-10%" width="120%" height="130%">',
        '      <feDropShadow dx="0" dy="14" stdDeviation="16" flood-color="#000000" flood-opacity="0.38"/>',
        "    </filter>",
        '    <marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">',
        f'      <path d="M0,0 L8,4 L0,8 Z" fill="{accent}"/>',
        "    </marker>",
        "  </defs>",
        '  <rect width="960" height="540" fill="url(#workflow-bg)"/>',
        f'  <circle cx="872" cy="52" r="220" fill="{accent}" opacity="0.10"/>',
        f'  <circle cx="62" cy="506" r="180" fill="{accent}" opacity="0.06"/>',
        '  <rect x="36" y="28" width="888" height="484" rx="20" fill="url(#workflow-card)" stroke="#2B3543" filter="url(#workflow-shadow)"/>',
        f'  <rect x="36" y="28" width="5" height="484" rx="2.5" fill="{accent}"/>',
        f'  <text x="72" y="67" fill="{accent}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="11" font-weight="700" letter-spacing="2.4">{_text(data["eyebrow"].upper())}</text>',
        f'  <text x="72" y="96" fill="#F4F7FB" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="21" font-weight="700">{_text(data["title"])}</text>',
        f'  <text x="72" y="119" fill="#8190A5" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="11">{_text(data["description"])}</text>',
    ]
    if data.get("meta"):
        out.append(
            f'  <text x="888" y="67" text-anchor="end" fill="#738198" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10">{_text(data["meta"])}</text>'
        )
    out.append('  <line x1="72" y1="139" x2="888" y2="139" stroke="#2A3340"/>')

    for column_index, column in enumerate(columns):
        x = left + column_index * (column_width + gutter)
        out.append(
            f'  <text x="{x:.1f}" y="164" fill="#91A0B5" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10" font-weight="700" letter-spacing="1.7">{_text(column["title"].upper())}</text>'
        )

    for edge in data.get("edges", []):
        source = positions[edge["from"]]
        target = positions[edge["to"]]
        x1 = source[0] + source[2]
        y1 = source[1] + source[3] / 2
        x2 = target[0]
        y2 = target[1] + target[3] / 2
        bend = max(18, (x2 - x1) / 2)
        out.append(
            f'  <path d="M{x1:.1f},{y1:.1f} C{x1 + bend:.1f},{y1:.1f} {x2 - bend:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" fill="none" stroke="{accent}" stroke-width="1.6" opacity="0.86" marker-end="url(#workflow-arrow)"/>'
        )
        if edge.get("label"):
            label_x = (x1 + x2) / 2
            label_y = (y1 + y2) / 2 - 8
            out.append(
                f'  <text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="middle" fill="#AAB6C7" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="9">{_text(edge["label"])}</text>'
            )

    kind_colors = {
        "default": ("#141B24", "#465469"),
        "accent": ("#1C2025", accent),
        "focus": ("#201A13", accent),
        "success": ("#13231D", "#42C77A"),
        "muted": ("#11161D", "#303B4A"),
    }
    for column in columns:
        for node in column["nodes"]:
            x, y, width, height = positions[node["id"]]
            fill, stroke = kind_colors[node.get("kind", "default")]
            out.append(
                f'  <rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height}" rx="12" fill="{fill}" stroke="{stroke}"/>'
            )
            out.append(
                f'  <text x="{x + 16:.1f}" y="{y + 29:.1f}" fill="#F3F6FA" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="14" font-weight="700">{_text(node["label"])}</text>'
            )
            if node.get("detail"):
                out.append(
                    f'  <text x="{x + 16:.1f}" y="{y + 49:.1f}" fill="#7F8DA2" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10">{_text(node["detail"])}</text>'
                )
            if node.get("badge"):
                badge = str(node["badge"])
                badge_width = max(42, len(badge) * 7 + 16)
                out.append(
                    f'  <rect x="{x + width - badge_width - 12:.1f}" y="{y + 12:.1f}" width="{badge_width}" height="20" rx="10" fill="#0E141C" stroke="{accent}"/>'
                )
                out.append(
                    f'  <text x="{x + width - badge_width / 2 - 12:.1f}" y="{y + 26:.1f}" text-anchor="middle" fill="{accent}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="9" font-weight="700">{_text(badge)}</text>'
                )

    context = data.get("context")
    if context:
        out.extend(
            [
                '  <line x1="72" y1="398" x2="888" y2="398" stroke="#2A3340"/>',
                f'  <text x="72" y="427" fill="{accent}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10" font-weight="700" letter-spacing="1.6">{_text(context["title"].upper())}</text>',
                f'  <text x="72" y="455" fill="#E6EBF2" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="13" font-weight="700">{_text(context["body"])}</text>',
            ]
        )
        if context.get("detail"):
            out.append(
                f'  <text x="72" y="478" fill="#758399" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10">{_text(context["detail"])}</text>'
            )

    out.append("</svg>")
    return "\n".join(out) + "\n"
