"""Render constrained workflow specifications as deterministic SVGs."""
from __future__ import annotations

from xml.sax.saxutils import escape


class WorkflowError(ValueError):
    """Raised when a workflow specification cannot be rendered."""


_KINDS = {"default", "accent", "focus", "success", "muted"}

# Canonical dark-ledger tokens from escoffier-fleet-kit/DESIGN.md.
BG = "#0d1014"
PANEL = "#11161c"
PANEL_2 = "#0f1318"
TEXT = "#dde3ea"
MUTED = "#9aa4b2"
DIM = "#7d8590"
ACCENT = "#e0a45c"
HAIRLINE = "#1e242c"
HAIRLINE_STRONG = "#2a323d"

# XML 1.0 forbids most C0 control codes; only tab, newline, and carriage
# return are legal. Anything else would either fail to parse or silently
# corrupt the rendered SVG.
_XML_FORBIDDEN_CONTROL = {chr(code) for code in range(0x20)} - {"\t", "\n", "\r"}


def _required_text(value, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{path} must be a non-empty string")
    return value.strip()


def _required_identifier(value, path: str) -> str:
    """Validate an identifier used as a positions key or edge endpoint.

    Identifiers must not carry leading or trailing whitespace; otherwise the
    stripped value used during validation would diverge from the unstripped
    value used during rendering and trigger a KeyError in positions lookup.
    """
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"{path} must be a non-empty string")
    if value != value.strip():
        raise WorkflowError(f"{path} must not have leading or trailing whitespace")
    return value


def _reject_control_characters(text: str, path: str) -> None:
    for char in text:
        if char in _XML_FORBIDDEN_CONTROL:
            raise WorkflowError(
                f"{path} contains a forbidden XML 1.0 control character: "
                f"U+{ord(char):04X}"
            )


def validate_workflow(data: dict) -> None:
    if not isinstance(data, dict):
        raise WorkflowError("workflow must be a JSON object")

    for key in ("title", "eyebrow", "description"):
        _required_text(data.get(key), key)

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
            node_id = _required_identifier(node.get("id"), f"{path}.id")
            _required_text(node.get("label"), f"{path}.label")
            if node_id in node_ids:
                raise WorkflowError(f"duplicate node id: {node_id}")
            node_ids.add(node_id)
            kind = node.get("kind", "default")
            if not isinstance(kind, str):
                raise WorkflowError(f"{path}.kind must be a string")
            if kind not in _KINDS:
                raise WorkflowError(f"{path}.kind must be one of {sorted(_KINDS)}")

    edges = data.get("edges", [])
    if not isinstance(edges, list):
        raise WorkflowError("edges must be a list")
    for edge_index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise WorkflowError(f"edges[{edge_index}] must be an object")
        source = _required_identifier(edge.get("from"), f"edges[{edge_index}].from")
        target = _required_identifier(edge.get("to"), f"edges[{edge_index}].to")
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


def _text(value, path: str = "rendered text") -> str:
    text = str(value)
    _reject_control_characters(text, path)
    return escape(text, {'"': "&quot;"})


def render_workflow(data: dict) -> str:
    """Validate *data* and return a complete SVG document."""
    validate_workflow(data)

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
        f"  <title id=\"workflow-title\">{_text(data['title'], 'title')}</title>",
        f"  <desc id=\"workflow-desc\">{_text(data['description'], 'description')}</desc>",
        "  <defs>",
        '    <linearGradient id="workflow-bg" x1="0" y1="0" x2="1" y2="1">',
        f'      <stop offset="0" stop-color="{BG}"/>',
        f'      <stop offset="1" stop-color="{PANEL_2}"/>',
        "    </linearGradient>",
        '    <linearGradient id="workflow-card" x1="0" y1="0" x2="0" y2="1">',
        f'      <stop offset="0" stop-color="{PANEL}"/>',
        f'      <stop offset="1" stop-color="{PANEL_2}"/>',
        "    </linearGradient>",
        '    <filter id="workflow-shadow" x="-10%" y="-10%" width="120%" height="130%">',
        '      <feDropShadow dx="0" dy="14" stdDeviation="16" flood-color="#000000" flood-opacity="0.38"/>',
        "    </filter>",
        '    <marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">',
        f'      <path d="M0,0 L8,4 L0,8 Z" fill="{ACCENT}"/>',
        "    </marker>",
        "  </defs>",
        '  <rect width="960" height="540" fill="url(#workflow-bg)"/>',
        f'  <circle cx="872" cy="52" r="220" fill="{ACCENT}" opacity="0.07"/>',
        f'  <circle cx="62" cy="506" r="180" fill="{ACCENT}" opacity="0.04"/>',
        f'  <rect x="36" y="28" width="888" height="484" rx="20" fill="url(#workflow-card)" stroke="{HAIRLINE_STRONG}" filter="url(#workflow-shadow)"/>',
        f'  <rect x="36" y="28" width="5" height="484" rx="2.5" fill="{ACCENT}"/>',
        f'  <text x="72" y="67" fill="{ACCENT}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="11" font-weight="600" letter-spacing="2.4">{_text(data["eyebrow"].upper(), "eyebrow")}</text>',
        f'  <text x="72" y="96" fill="{TEXT}" font-family="Inter, ui-sans-serif, sans-serif" font-size="21" font-weight="700" letter-spacing="-0.6">{_text(data["title"], "title")}</text>',
        f'  <text x="72" y="119" fill="{MUTED}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="11">{_text(data["description"], "description")}</text>',
    ]
    if data.get("meta"):
        out.append(
            f'  <text x="888" y="67" text-anchor="end" fill="{DIM}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10">{_text(data["meta"], "meta")}</text>'
        )
    out.append(f'  <line x1="72" y1="139" x2="888" y2="139" stroke="{HAIRLINE}"/>')

    for column_index, column in enumerate(columns):
        x = left + column_index * (column_width + gutter)
        out.append(
            f'  <text x="{x:.1f}" y="164" fill="{ACCENT}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10" font-weight="600" letter-spacing="1.7">{_text(column["title"].upper(), f"columns[{column_index}].title")}</text>'
        )

    for edge_index, edge in enumerate(data.get("edges", [])):
        source = positions[edge["from"]]
        target = positions[edge["to"]]
        x1 = source[0] + source[2]
        y1 = source[1] + source[3] / 2
        x2 = target[0]
        y2 = target[1] + target[3] / 2
        out.append(
            f'  <line class="workflow-edge" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{ACCENT}" stroke-width="1.5" opacity="0.9" marker-end="url(#workflow-arrow)"/>'
        )
        if edge.get("label"):
            label_x = (x1 + x2) / 2
            label_y = (y1 + y2) / 2 - 8
            out.append(
                f'  <text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="middle" fill="{DIM}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="9">{_text(edge["label"], f"edges[{edge_index}].label")}</text>'
            )

    kind_colors = {
        "default": (PANEL, HAIRLINE_STRONG),
        "accent": (PANEL, ACCENT),
        "focus": (PANEL_2, ACCENT),
        "success": (PANEL, HAIRLINE_STRONG),
        "muted": (PANEL_2, HAIRLINE),
    }
    for column_index, column in enumerate(columns):
        for node_index, node in enumerate(column["nodes"]):
            x, y, width, height = positions[node["id"]]
            fill, stroke = kind_colors[node.get("kind", "default")]
            node_path = f"columns[{column_index}].nodes[{node_index}]"
            out.append(
                f'  <rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height}" rx="12" fill="{fill}" stroke="{stroke}"/>'
            )
            label_y = y + (37 if node.get("badge") else 29)
            out.append(
                f'  <text class="workflow-node-label" x="{x + 16:.1f}" y="{label_y:.1f}" fill="{TEXT}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="14" font-weight="600">{_text(node["label"], f"{node_path}.label")}</text>'
            )
            if node.get("detail"):
                detail_y = y + (56 if node.get("badge") else 49)
                out.append(
                    f'  <text x="{x + 16:.1f}" y="{detail_y:.1f}" fill="{MUTED}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10">{_text(node["detail"], f"{node_path}.detail")}</text>'
                )
            if node.get("badge"):
                badge = str(node["badge"])
                out.append(
                    f'  <text class="workflow-badge" x="{x + 16:.1f}" y="{y + 17:.1f}" fill="{ACCENT}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="8" font-weight="600" letter-spacing="1.2">{_text(badge.upper(), f"{node_path}.badge")}</text>'
                )

    context = data.get("context")
    if context:
        out.extend(
            [
                f'  <line x1="72" y1="398" x2="888" y2="398" stroke="{HAIRLINE}"/>',
                f'  <text x="72" y="427" fill="{ACCENT}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10" font-weight="600" letter-spacing="1.6">{_text(context["title"].upper(), "context.title")}</text>',
                f'  <text x="72" y="455" fill="{TEXT}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="13" font-weight="600">{_text(context["body"], "context.body")}</text>',
            ]
        )
        if context.get("detail"):
            out.append(
                f'  <text x="72" y="478" fill="{DIM}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10">{_text(context["detail"], "context.detail")}</text>'
            )

    out.append("</svg>")
    return "\n".join(out) + "\n"
