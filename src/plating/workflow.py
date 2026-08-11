"""Render constrained workflow specifications as deterministic SVGs."""
from __future__ import annotations

from dataclasses import dataclass
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

# Layout and edge-routing constants. Forward right-to-left anchors stay fixed;
# exterior lanes are used for backward and detoured geometries (#23).
_LAYOUT_LEFT = 72.0
_LAYOUT_RIGHT = 888.0
_LAYOUT_GUTTER = 30.0
_NODE_HEIGHT = 68
_NODE_GAP = 14
_NODE_TOP = 176
_NODE_BAND = 190
_EDGE_STUB = 12.0
_LANE_GAP = 8.0
_EXTERIOR_TOP = 150.0
_EXTERIOR_BOTTOM = 382.0
_CANVAS_WIDTH = 960.0
_CANVAS_HEIGHT = 540.0

# XML 1.0 forbids most C0 control codes; only tab, newline, and carriage
# return are legal. Anything else would either fail to parse or silently
# corrupt the rendered SVG.
_XML_FORBIDDEN_CONTROL = {chr(code) for code in range(0x20)} - {"\t", "\n", "\r"}


@dataclass(frozen=True)
class EdgeRoute:
    """Deterministic polyline for one workflow edge."""

    points: tuple[tuple[float, float], ...]


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
    node_columns: dict[str, int] = {}
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
            node_columns[node_id] = column_index
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
        if node_columns[target] <= node_columns[source]:
            raise WorkflowError(
                f"edge {source} -> {target} must flow forward "
                f"from an earlier column to a later column"
            )

    context = data.get("context")
    if context is not None:
        if not isinstance(context, dict):
            raise WorkflowError("context must be an object")
        _required_text(context.get("title"), "context.title")
        _required_text(context.get("body"), "context.body")


def compute_workflow_layout(
    data: dict,
) -> tuple[dict[str, tuple[float, float, float, float]], dict[str, int]]:
    """Return ``(positions, node_columns)`` for a validated workflow document.

    ``positions`` maps node id to ``(x, y, width, height)``. Geometry helpers use
    this layout so edge routing can be tested without opening the forward-only
    workflow contract.
    """
    validate_workflow(data)
    columns = data["columns"]
    column_width = (
        _LAYOUT_RIGHT - _LAYOUT_LEFT - _LAYOUT_GUTTER * (len(columns) - 1)
    ) / len(columns)
    positions: dict[str, tuple[float, float, float, float]] = {}
    node_columns: dict[str, int] = {}
    for column_index, column in enumerate(columns):
        x = _LAYOUT_LEFT + column_index * (column_width + _LAYOUT_GUTTER)
        nodes = column["nodes"]
        block_height = len(nodes) * _NODE_HEIGHT + (len(nodes) - 1) * _NODE_GAP
        y = _NODE_TOP + (_NODE_BAND - block_height) / 2
        for node in nodes:
            node_id = node["id"]
            positions[node_id] = (x, y, column_width, _NODE_HEIGHT)
            node_columns[node_id] = column_index
            y += _NODE_HEIGHT + _NODE_GAP
    return positions, node_columns


def _box_sides(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = box
    return x, y, x + w, y + h


def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


def _segment_crosses_box(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> bool:
    """Return True when the open segment intersects the open box interior."""
    for px, py in ((x1, y1), (x2, y2)):
        if left < px < right and top < py < bottom:
            return True
    dx = x2 - x1
    dy = y2 - y1
    p = (-dx, dx, -dy, dy)
    q = (x1 - left, right - x1, y1 - top, bottom - y1)
    u1, u2 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0:
            if qi <= 0:
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
    # Sample the clipped interval; boundary-only contact is not an interior hit.
    for u in (u1, (u1 + u2) / 2, u2):
        if u <= 0.0 or u >= 1.0:
            continue
        mx = x1 + u * dx
        my = y1 + u * dy
        if left < mx < right and top < my < bottom:
            return True
    return False


def _route_hits_nodes(
    points: tuple[tuple[float, float], ...],
    positions: dict[str, tuple[float, float, float, float]],
    source_id: str,
    target_id: str,
) -> bool:
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        for node_id, box in positions.items():
            if node_id == source_id and index == 0:
                continue
            if node_id == target_id and index == len(points) - 2:
                continue
            left, top, right, bottom = _box_sides(box)
            if _segment_crosses_box(x1, y1, x2, y2, left, top, right, bottom):
                return True
    return False


def _assert_route_clear(
    points: tuple[tuple[float, float], ...],
    positions: dict[str, tuple[float, float, float, float]],
    source_id: str,
    target_id: str,
) -> None:
    if len(points) < 2:
        raise WorkflowError(
            f"edge geometry for {source_id} -> {target_id} produced no path"
        )
    for x, y in points:
        if not (0.0 <= x <= _CANVAS_WIDTH and 0.0 <= y <= _CANVAS_HEIGHT):
            raise WorkflowError(
                f"edge geometry for {source_id} -> {target_id} leaves the canvas"
            )
    if _route_hits_nodes(points, positions, source_id, target_id):
        raise WorkflowError(
            f"edge geometry for {source_id} -> {target_id} crosses a node box"
        )


def _lane_y(lane_index: int, *, prefer_top: bool) -> float:
    if prefer_top:
        return _EXTERIOR_TOP - lane_index * _LANE_GAP
    return _EXTERIOR_BOTTOM + lane_index * _LANE_GAP


def _forward_route(
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
    lane_index: int,
    lane_count: int,
) -> tuple[tuple[float, float], ...]:
    x1 = source[0] + source[2]
    y1 = source[1] + source[3] / 2
    x2 = target[0]
    y2 = target[1] + target[3] / 2
    if lane_count <= 1:
        return ((x1, y1), (x2, y2))
    offset = (lane_index - (lane_count - 1) / 2) * _LANE_GAP
    if offset == 0:
        return ((x1, y1), (x2, y2))
    mid_y = (y1 + y2) / 2 + offset
    return (
        (x1, y1),
        (x1 + _EDGE_STUB, y1),
        (x1 + _EDGE_STUB, mid_y),
        (x2 - _EDGE_STUB, mid_y),
        (x2 - _EDGE_STUB, y2),
        (x2, y2),
    )


def _backward_route(
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
    lane_index: int,
    *,
    prefer_top: bool,
) -> tuple[tuple[float, float], ...]:
    x1 = source[0]
    y1 = source[1] + source[3] / 2
    x2 = target[0] + target[2]
    y2 = target[1] + target[3] / 2
    exit_x = x1 - _EDGE_STUB
    enter_x = x2 + _EDGE_STUB
    lane = _lane_y(lane_index, prefer_top=prefer_top)
    return (
        (x1, y1),
        (exit_x, y1),
        (exit_x, lane),
        (enter_x, lane),
        (enter_x, y2),
        (x2, y2),
    )


def _same_column_source_is_above(
    source_id: str,
    target_id: str,
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
) -> bool:
    _, source_cy = _center(source)
    _, target_cy = _center(target)
    if source_cy < target_cy:
        return True
    if source_cy > target_cy:
        return False
    return source_id < target_id


def _same_column_intervening(
    positions: dict[str, tuple[float, float, float, float]],
    node_columns: dict[str, int],
    source_id: str,
    target_id: str,
) -> bool:
    column = node_columns[source_id]
    _, source_cy = _center(positions[source_id])
    _, target_cy = _center(positions[target_id])
    lo, hi = sorted((source_cy, target_cy))
    for node_id, box in positions.items():
        if node_id in (source_id, target_id):
            continue
        if node_columns[node_id] != column:
            continue
        _, cy = _center(box)
        if lo < cy < hi:
            return True
    return False


def _same_column_route(
    source_id: str,
    target_id: str,
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
    positions: dict[str, tuple[float, float, float, float]],
    node_columns: dict[str, int],
    lane_index: int,
) -> tuple[tuple[float, float], ...]:
    above = _same_column_source_is_above(source_id, target_id, source, target)
    if above:
        start = (source[0] + source[2] / 2, source[1] + source[3])
        end = (target[0] + target[2] / 2, target[1])
    else:
        start = (source[0] + source[2] / 2, source[1])
        end = (target[0] + target[2] / 2, target[1] + target[3])
    if not _same_column_intervening(positions, node_columns, source_id, target_id):
        direct = (start, end)
        if not _route_hits_nodes(direct, positions, source_id, target_id):
            return direct
    # Detour through a deterministic exterior side lane beside the column.
    side_x = source[0] - _EDGE_STUB - lane_index * _LANE_GAP
    if side_x < 0:
        side_x = source[0] + source[2] + _EDGE_STUB + lane_index * _LANE_GAP
    return (start, (side_x, start[1]), (side_x, end[1]), end)


def _assign_lane_indices(edges: list[dict]) -> list[int]:
    """Stable parallel-edge lane indices keyed by (from, to) appearance order."""
    groups: dict[tuple[str, str], list[int]] = {}
    for index, edge in enumerate(edges):
        key = (edge["from"], edge["to"])
        groups.setdefault(key, []).append(index)
    lanes = [0] * len(edges)
    for indexes in groups.values():
        for lane, edge_index in enumerate(indexes):
            lanes[edge_index] = lane
    return lanes


def _lane_counts(edges: list[dict]) -> list[int]:
    counts_by_key: dict[tuple[str, str], int] = {}
    for edge in edges:
        key = (edge["from"], edge["to"])
        counts_by_key[key] = counts_by_key.get(key, 0) + 1
    return [counts_by_key[(edge["from"], edge["to"])] for edge in edges]


def route_workflow_edges(
    positions: dict[str, tuple[float, float, float, float]],
    node_columns: dict[str, int],
    edges: list[dict],
) -> list[EdgeRoute]:
    """Route edges with collision-free anchors and deterministic exterior lanes.

    Forward edges keep right-center → left-center straight lines when a single
    clear path exists. Backward edges leave the source left-center and enter the
    target right-center via exterior lanes. Same-column edges use top/bottom
    centers ordered by node center with a stable id tie-break.
    """
    if not isinstance(edges, list):
        raise WorkflowError("edges must be a list")
    lanes = _assign_lane_indices(edges)
    counts = _lane_counts(edges)
    routes: list[EdgeRoute] = []
    for edge_index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise WorkflowError(f"edges[{edge_index}] must be an object")
        source_id = edge["from"]
        target_id = edge["to"]
        if source_id not in positions or target_id not in positions:
            raise WorkflowError(
                f"edge {source_id} -> {target_id} references an unknown node"
            )
        source = positions[source_id]
        target = positions[target_id]
        source_col = node_columns[source_id]
        target_col = node_columns[target_id]
        lane_index = lanes[edge_index]
        lane_count = counts[edge_index]
        if source_col < target_col:
            points = _forward_route(source, target, lane_index, lane_count)
            if _route_hits_nodes(points, positions, source_id, target_id):
                # Detour via exterior lane while preserving endpoint anchors.
                prefer_top = _center(source)[1] <= _NODE_TOP + _NODE_BAND / 2
                x1 = source[0] + source[2]
                y1 = source[1] + source[3] / 2
                x2 = target[0]
                y2 = target[1] + target[3] / 2
                exit_x = x1 + _EDGE_STUB
                enter_x = x2 - _EDGE_STUB
                lane = _lane_y(lane_index, prefer_top=prefer_top)
                points = (
                    (x1, y1),
                    (exit_x, y1),
                    (exit_x, lane),
                    (enter_x, lane),
                    (enter_x, y2),
                    (x2, y2),
                )
        elif source_col > target_col:
            prefer_top = _center(source)[1] <= _NODE_TOP + _NODE_BAND / 2
            # Cycle companions: offset backward lanes so they never share the
            # forward straight path's y.
            cycle_boost = 0
            reverse_key = (target_id, source_id)
            if any(
                other.get("from") == reverse_key[0] and other.get("to") == reverse_key[1]
                for other in edges
            ):
                cycle_boost = 1
            points = _backward_route(
                source,
                target,
                lane_index + cycle_boost,
                prefer_top=prefer_top,
            )
            if _route_hits_nodes(points, positions, source_id, target_id):
                points = _backward_route(
                    source,
                    target,
                    lane_index + cycle_boost,
                    prefer_top=not prefer_top,
                )
        else:
            points = _same_column_route(
                source_id,
                target_id,
                source,
                target,
                positions,
                node_columns,
                lane_index,
            )
        _assert_route_clear(points, positions, source_id, target_id)
        routes.append(EdgeRoute(points=points))
    return routes


def _text(value, path: str = "rendered text") -> str:
    text = str(value)
    _reject_control_characters(text, path)
    return escape(text, {'"': "&quot;"})


def _format_point(value: float) -> str:
    return f"{value:.1f}"


def _edge_svg(points: tuple[tuple[float, float], ...]) -> str:
    if len(points) == 2:
        (x1, y1), (x2, y2) = points
        return (
            f'  <line class="workflow-edge" x1="{_format_point(x1)}" '
            f'y1="{_format_point(y1)}" x2="{_format_point(x2)}" '
            f'y2="{_format_point(y2)}" stroke="{ACCENT}" stroke-width="1.5" '
            f'opacity="0.9" marker-end="url(#workflow-arrow)"/>'
        )
    point_str = " ".join(
        f"{_format_point(x)},{_format_point(y)}" for x, y in points
    )
    return (
        f'  <polyline class="workflow-edge" fill="none" points="{point_str}" '
        f'stroke="{ACCENT}" stroke-width="1.5" opacity="0.9" '
        f'marker-end="url(#workflow-arrow)"/>'
    )


def render_workflow(data: dict) -> str:
    """Validate *data* and return a complete SVG document."""
    positions, node_columns = compute_workflow_layout(data)
    columns = data["columns"]

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

    left = _LAYOUT_LEFT
    gutter = _LAYOUT_GUTTER
    column_width = (
        _LAYOUT_RIGHT - _LAYOUT_LEFT - _LAYOUT_GUTTER * (len(columns) - 1)
    ) / len(columns)
    for column_index, column in enumerate(columns):
        x = left + column_index * (column_width + gutter)
        out.append(
            f'  <text x="{x:.1f}" y="164" fill="{ACCENT}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="10" font-weight="600" letter-spacing="1.7">{_text(column["title"].upper(), f"columns[{column_index}].title")}</text>'
        )

    edges = data.get("edges", [])
    routes = route_workflow_edges(positions, node_columns, edges)
    for edge_index, (edge, route) in enumerate(zip(edges, routes)):
        out.append(_edge_svg(route.points))
        if edge.get("label"):
            xs = [point[0] for point in route.points]
            ys = [point[1] for point in route.points]
            label_x = (min(xs) + max(xs)) / 2
            label_y = (min(ys) + max(ys)) / 2 - 8
            # Preserve historical label placement for two-point forward edges.
            if len(route.points) == 2:
                (x1, y1), (x2, y2) = route.points
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
