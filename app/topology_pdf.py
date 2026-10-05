from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

STAGE_WIDTH = 1800
STAGE_HEIGHT = 1100
NODE_SHAPES = {
    "icon": (124, 94),
    "card": (164, 72),
    "circle": (112, 112),
    "text": (170, 36),
}
CONTENT_PADDING = 80
HEADER_HEIGHT = 72
MIN_PAGE_WIDTH = 1000
MIN_PAGE_HEIGHT = 650
MAX_PAGE_SIDE = 7200

STATUS_COLORS = {
    "online": HexColor("#3ddc97"),
    "offline": HexColor("#f0524b"),
    "unknown": HexColor("#8ca0b5"),
}
VLAN_COLORS = (
    "#38bdf8", "#34d399", "#fbbf24", "#fb7185", "#a78bfa", "#22d3ee",
    "#f97316", "#4ade80", "#60a5fa", "#e879f9", "#facc15", "#2dd4bf",
)


@dataclass(frozen=True)
class TopologyBounds:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class PageGeometry:
    width: float
    height: float
    scale: float
    offset_x: float
    offset_y: float


@dataclass(frozen=True)
class PdfPalette:
    background: Color
    header: Color
    panel: Color
    panel_border: Color
    circle_panel: Color
    circle_border: Color
    primary_text: Color
    secondary_text: Color
    edge: Color
    icon_fill: Color
    icon_stroke: Color
    icon_copy: Color


PDF_PALETTES = {
    "dark": PdfPalette(
        background=HexColor("#091523"),
        header=HexColor("#0b1725"),
        panel=HexColor("#0f1d2d"),
        panel_border=HexColor("#31485f"),
        circle_panel=HexColor("#122d45"),
        circle_border=HexColor("#3e6588"),
        primary_text=HexColor("#f1f7fd"),
        secondary_text=HexColor("#9fb1c4"),
        edge=HexColor("#5b7896"),
        icon_fill=HexColor("#247fa7"),
        icon_stroke=HexColor("#9cddf5"),
        icon_copy=HexColor("#091523"),
    ),
    "light": PdfPalette(
        background=HexColor("#ffffff"),
        header=HexColor("#ffffff"),
        panel=HexColor("#ffffff"),
        panel_border=HexColor("#9eb1c3"),
        circle_panel=HexColor("#e2eff9"),
        circle_border=HexColor("#6b8eab"),
        primary_text=HexColor("#172536"),
        secondary_text=HexColor("#5c7187"),
        edge=HexColor("#6b8298"),
        icon_fill=HexColor("#2d88b2"),
        icon_stroke=HexColor("#195b7a"),
        icon_copy=HexColor("#ffffff"),
    ),
}


def pdf_palette(theme: str) -> PdfPalette:
    try:
        return PDF_PALETTES[theme]
    except KeyError as error:
        raise ValueError("Unsupported PDF theme") from error


def safe_pdf_text(value: object, *, maximum: int = 100) -> str:
    text = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in str(value or "")
    ).split()
    cleaned = " ".join(text)
    if len(cleaned) <= maximum:
        return cleaned
    return f"{cleaned[: max(0, maximum - 3)]}..."


def node_footprint(node: Any) -> tuple[int, int]:
    return NODE_SHAPES.get(str(getattr(node, "node_shape", "icon")), NODE_SHAPES["icon"])


def content_bounds(
    nodes: Sequence[Any],
    *,
    stage_width: float = STAGE_WIDTH,
    stage_height: float = STAGE_HEIGHT,
    node_width: float | None = None,
    node_height: float | None = None,
    padding: float = CONTENT_PADDING,
) -> TopologyBounds:
    if not nodes:
        raise ValueError("At least one device is required for a topology PDF")
    footprints = [
        (
            float(node.x) * stage_width,
            float(node.y) * stage_height,
            (node_width, node_height) if node_width is not None and node_height is not None
            else node_footprint(node),
        )
        for node in nodes
    ]
    left = min(x - width / 2 for x, _, (width, _) in footprints) - padding
    top = min(y - height / 2 for _, y, (_, height) in footprints) - padding
    right = max(x + width / 2 for x, _, (width, _) in footprints) + padding
    bottom = max(y + height / 2 for _, y, (_, height) in footprints) + padding
    return TopologyBounds(left, top, right - left, bottom - top)


def _page_geometry(bounds: TopologyBounds) -> PageGeometry:
    maximum_diagram_height = MAX_PAGE_SIDE - HEADER_HEIGHT
    scale = min(
        1.0,
        MAX_PAGE_SIDE / max(bounds.width, 1),
        maximum_diagram_height / max(bounds.height, 1),
    )
    diagram_width = bounds.width * scale
    diagram_height = bounds.height * scale
    page_width = min(MAX_PAGE_SIDE, max(MIN_PAGE_WIDTH, diagram_width))
    page_height = min(
        MAX_PAGE_SIDE,
        max(MIN_PAGE_HEIGHT, diagram_height + HEADER_HEIGHT),
    )
    available_height = page_height - HEADER_HEIGHT
    return PageGeometry(
        width=page_width,
        height=page_height,
        scale=scale,
        offset_x=(page_width - diagram_width) / 2,
        offset_y=(available_height - diagram_height) / 2,
    )


def _point(
    node: Any,
    bounds: TopologyBounds,
    geometry: PageGeometry,
) -> tuple[float, float]:
    world_x = float(node.x) * STAGE_WIDTH
    world_y = float(node.y) * STAGE_HEIGHT
    x = geometry.offset_x + (world_x - bounds.x) * geometry.scale
    y = geometry.offset_y + (bounds.y + bounds.height - world_y) * geometry.scale
    return x, y


def _fit_text(text: str, size: float, maximum_width: float) -> str:
    fitted = safe_pdf_text(text)
    while fitted and stringWidth(fitted, "Helvetica", size) > maximum_width:
        fitted = f"{fitted[:-4]}..." if fitted.endswith("...") else f"{fitted[:-1]}..."
    return fitted


def _display_label(node: Any, label_mode: str) -> tuple[str, str]:
    name = safe_pdf_text(node.name)
    address = safe_pdf_text(node.address)
    fallback = name or address or safe_pdf_text(node.node_type) or "Device"
    if getattr(node, "node_shape", "") == "text":
        return fallback, ""
    if label_mode == "hostname":
        return fallback, ""
    if label_mode == "ip":
        return address or fallback, ""
    primary = name or address or fallback
    return primary, address if address and address != primary else ""


def _vlan_ids_from_value(value: object) -> list[str]:
    ids = {item for item in re.findall(r"\d{1,4}", str(value or "")) if 1 <= int(item) <= 4094}
    return sorted(ids, key=lambda item: (int(item), item))


def _is_vlan_key(key: object) -> bool:
    normalized = re.sub(r"[_-]+", " ", str(key).lower()).strip()
    return normalized in {
        "vlan", "vlans", "vlan id", "pvid", "pvid vlan", "tagged vlan", "untagged vlan",
    }


def _node_vlan_ids(node: Any, interfaces: Sequence[dict], vlans: Sequence[dict]) -> list[str]:
    ids: set[str] = set()
    for key, value in (node.metadata or {}).items():
        if _is_vlan_key(key):
            ids.update(_vlan_ids_from_value(value))
    for interface in interfaces:
        if interface.get("device_id") == node.id:
            ids.update(_vlan_ids_from_value(interface.get("vlan")))
    if node.source == "lantopolog" and node.address:
        switch_key = f"switch:{node.address}"
        for vlan in vlans:
            if vlan.get("switch_key") == switch_key:
                ids.update(_vlan_ids_from_value(vlan.get("vlan_id")))
    return sorted(ids, key=lambda item: (int(item), item))


def _draw_icon(
    canvas: Canvas,
    icon_type: str,
    x: float,
    y: float,
    size: float,
    palette: PdfPalette,
) -> None:
    """Draw the same 64x64 device glyphs used by topologyIcon() in the editor."""
    icon_type = icon_type if icon_type in {
        "router", "switch", "access-point", "server", "workstation", "printer", "phone",
    } else "other"
    canvas.saveState()
    canvas.translate(x - size / 2, y + size / 2)
    canvas.scale(size / 64, -size / 64)
    canvas.setStrokeColor(palette.icon_stroke)
    canvas.setFillColor(palette.icon_fill)
    canvas.setLineWidth(2)
    canvas.setLineJoin(1)
    canvas.setLineCap(1)

    if icon_type == "router":
        canvas.ellipse(8, 12, 56, 32, fill=1, stroke=1)
        body = canvas.beginPath()
        body.moveTo(8, 22)
        body.lineTo(8, 36)
        body.curveTo(8, 42, 19, 46, 32, 46)
        body.curveTo(45, 46, 56, 42, 56, 36)
        body.lineTo(56, 22)
        canvas.drawPath(body, fill=1, stroke=1)
        for points in (
            ((20, 20), (29, 20), (25, 16)),
            ((44, 24), (35, 24), (39, 28)),
            ((32, 13), (32, 22), (36, 18)),
            ((28, 35), (28, 26), (24, 30)),
        ):
            path = canvas.beginPath()
            path.moveTo(*points[0])
            path.lineTo(*points[1])
            path.lineTo(*points[2])
            canvas.drawPath(path, fill=0, stroke=1)
    elif icon_type == "switch":
        top = canvas.beginPath()
        top.moveTo(7, 20)
        top.lineTo(17, 11)
        top.lineTo(57, 11)
        top.lineTo(47, 20)
        top.close()
        canvas.drawPath(top, fill=1, stroke=1)
        canvas.roundRect(7, 20, 40, 24, 3, fill=1, stroke=1)
        side = canvas.beginPath()
        side.moveTo(47, 20)
        side.lineTo(57, 11)
        side.lineTo(57, 34)
        side.lineTo(47, 44)
        side.close()
        canvas.drawPath(side, fill=1, stroke=1)
        for row in (29, 36):
            for start, end in ((14, 19), (23, 28), (32, 37), (41, 44)):
                canvas.line(start, row, end, row)
    elif icon_type == "access-point":
        base = canvas.beginPath()
        base.moveTo(22, 45)
        base.lineTo(42, 45)
        base.lineTo(38, 37)
        base.lineTo(26, 37)
        base.close()
        canvas.drawPath(base, fill=1, stroke=1)
        canvas.roundRect(27, 25, 10, 14, 3, fill=1, stroke=1)
        canvas.arc(20, 17, 44, 41, 25, 130)
        canvas.arc(13, 8, 51, 46, 25, 130)
    elif icon_type == "server":
        body = canvas.beginPath()
        body.moveTo(16, 5)
        body.lineTo(46, 5)
        body.lineTo(53, 12)
        body.lineTo(53, 54)
        body.lineTo(16, 54)
        body.close()
        canvas.drawPath(body, fill=1, stroke=1)
        canvas.line(46, 5, 46, 14)
        canvas.line(46, 14, 53, 14)
        for row in (18, 29, 40):
            canvas.line(23, row, 44, row)
        canvas.setFillColor(STATUS_COLORS["online"])
        canvas.circle(24, 48, 2, fill=1, stroke=0)
        canvas.circle(31, 48, 2, fill=1, stroke=0)
    elif icon_type == "workstation":
        canvas.roundRect(10, 8, 44, 33, 4, fill=1, stroke=1)
        stand = canvas.beginPath()
        stand.moveTo(26, 42)
        stand.lineTo(38, 42)
        stand.lineTo(41, 50)
        stand.lineTo(23, 50)
        stand.close()
        canvas.drawPath(stand, fill=1, stroke=1)
        canvas.line(18, 52, 46, 52)
        canvas.rect(16, 14, 32, 21, fill=0, stroke=1)
    elif icon_type == "printer":
        canvas.rect(18, 7, 28, 15, fill=1, stroke=1)
        canvas.roundRect(10, 20, 44, 25, 5, fill=1, stroke=1)
        canvas.rect(18, 36, 28, 20, fill=1, stroke=1)
        canvas.line(23, 42, 41, 42)
        canvas.line(23, 48, 37, 48)
        canvas.setFillColor(STATUS_COLORS["online"])
        canvas.circle(47, 28, 2, fill=1, stroke=0)
    elif icon_type == "phone":
        body = canvas.beginPath()
        body.moveTo(20, 7)
        body.lineTo(44, 7)
        body.lineTo(49, 56)
        body.lineTo(15, 56)
        body.close()
        canvas.drawPath(body, fill=1, stroke=1)
        canvas.roundRect(22, 14, 20, 27, 2, fill=0, stroke=1)
        canvas.setFillColor(STATUS_COLORS["online"])
        canvas.circle(32, 49, 3, fill=1, stroke=0)
    else:
        body = canvas.beginPath()
        body.moveTo(32, 6)
        body.lineTo(55, 19)
        body.lineTo(55, 45)
        body.lineTo(32, 58)
        body.lineTo(9, 45)
        body.lineTo(9, 19)
        body.close()
        canvas.drawPath(body, fill=1, stroke=1)
        canvas.line(9, 19, 32, 33)
        canvas.line(32, 33, 55, 19)
        canvas.line(32, 33, 32, 58)
    canvas.restoreState()


def _draw_label(
    canvas: Canvas,
    primary: str,
    secondary: str,
    *,
    x: float,
    y: float,
    width: float,
    scale: float,
    centered: bool,
    palette: PdfPalette,
) -> None:
    primary_size = max(1.5, 10 * scale)
    secondary_size = max(1.2, 8 * scale)
    primary = _fit_text(primary, primary_size, width)
    secondary = _fit_text(secondary, secondary_size, width)
    canvas.setFillColor(palette.primary_text)
    canvas.setFont("Helvetica-Bold", primary_size)
    if centered:
        canvas.drawCentredString(x, y + (4 if secondary else 0) * scale, primary)
    else:
        canvas.drawString(x, y + (4 if secondary else 0) * scale, primary)
    if secondary:
        canvas.setFillColor(palette.secondary_text)
        canvas.setFont("Helvetica", secondary_size)
        if centered:
            canvas.drawCentredString(x, y - 9 * scale, secondary)
        else:
            canvas.drawString(x, y - 9 * scale, secondary)


def _draw_node(
    canvas: Canvas,
    node: Any,
    x: float,
    y: float,
    scale: float,
    label_mode: str,
    interfaces: Sequence[dict],
    vlans: Sequence[dict],
    show_vlans: bool,
    palette: PdfPalette,
) -> None:
    shape = str(node.node_shape) if str(node.node_shape) in NODE_SHAPES else "icon"
    raw_width, raw_height = NODE_SHAPES[shape]
    width = raw_width * scale
    height = raw_height * scale
    if shape == "card":
        canvas.setFillColor(palette.panel)
        canvas.setStrokeColor(palette.panel_border)
        canvas.setLineWidth(max(0.7, 1.1 * scale))
        canvas.roundRect(
            x - width / 2, y - height / 2, width, height, max(3, 10 * scale),
            fill=1, stroke=1,
        )
    elif shape == "circle":
        canvas.setFillColor(palette.circle_panel)
        canvas.setStrokeColor(palette.circle_border)
        canvas.setLineWidth(max(0.8, 2 * scale))
        canvas.circle(x, y, width / 2, fill=1, stroke=1)
    icon_type = node.icon_type if node.icon_type != "auto" else node.node_type
    primary, secondary = _display_label(node, label_mode)
    if shape == "icon":
        icon_size = 58 * scale
        icon_y = y + 14 * scale
        _draw_icon(canvas, icon_type, x, icon_y, icon_size, palette)
        copy_height = (28 if secondary else 18) * scale
        canvas.setFillColor(palette.icon_copy)
        canvas.roundRect(
            x - 60 * scale, y - 43 * scale, 120 * scale, copy_height,
            max(2, 6 * scale), fill=1, stroke=0,
        )
        _draw_label(
            canvas, primary, secondary, x=x, y=y - 34 * scale,
            width=106 * scale, scale=scale, centered=True, palette=palette,
        )
    elif shape == "circle":
        icon_size = 46 * scale
        icon_y = y + 17 * scale
        _draw_icon(canvas, icon_type, x, icon_y, icon_size, palette)
        _draw_label(
            canvas, primary, secondary, x=x, y=y - 30 * scale,
            width=84 * scale, scale=scale, centered=True, palette=palette,
        )
    elif shape == "text":
        _draw_label(
            canvas, primary, secondary, x=x, y=y - 3 * scale,
            width=width - 16 * scale, scale=scale, centered=True, palette=palette,
        )
    else:
        icon_size = 42 * scale
        icon_x = x - width / 2 + 31 * scale
        _draw_icon(canvas, icon_type, icon_x, y, icon_size, palette)
        _draw_label(
            canvas, primary, secondary, x=x - width / 2 + 61 * scale, y=y - 1 * scale,
            width=width - 70 * scale, scale=scale, centered=False, palette=palette,
        )

    if show_vlans:
        vlan_ids = _node_vlan_ids(node, interfaces, vlans)
        shown = vlan_ids[:4]
        if shown:
            stripe_width = max(8 * scale, width - 24 * scale)
            segment_width = stripe_width / len(shown)
            stripe_y = y - height / 2 - max(1, 3 * scale)
            for index, vlan_id in enumerate(shown):
                canvas.setFillColor(HexColor(VLAN_COLORS[int(vlan_id) % len(VLAN_COLORS)]))
                canvas.rect(
                    x - stripe_width / 2 + index * segment_width,
                    stripe_y,
                    segment_width,
                    max(1.2, 4 * scale),
                    fill=1,
                    stroke=0,
                )


def _draw_edge(
    canvas: Canvas,
    source: tuple[float, float],
    target: tuple[float, float],
    scale: float,
    *,
    inferred: bool,
    palette: PdfPalette,
) -> None:
    source_x, source_y = source
    target_x, target_y = target
    middle_y = (source_y + target_y) / 2
    canvas.setStrokeColor(palette.edge)
    canvas.setLineWidth(max(0.65, 1.5 * scale))
    canvas.setDash(6 * scale, 5 * scale) if inferred else canvas.setDash()
    path = canvas.beginPath()
    path.moveTo(source_x, source_y)
    path.lineTo(source_x, middle_y)
    path.lineTo(target_x, middle_y)
    path.lineTo(target_x, target_y)
    canvas.drawPath(path, fill=0, stroke=1)
    canvas.setDash()


def render_topology_pdf(
    snapshot: dict[str, Any],
    visible_node_ids: Sequence[str],
    *,
    label_mode: str,
    show_vlans: bool,
    theme: str = "dark",
) -> bytes:
    palette = pdf_palette(theme)
    selected_ids = set(visible_node_ids)
    nodes = [node for node in snapshot["nodes"] if node.id in selected_ids]
    if len(nodes) != len(selected_ids):
        raise ValueError("One or more selected devices no longer exist")
    bounds = content_bounds(nodes)
    geometry = _page_geometry(bounds)
    positions = {node.id: _point(node, bounds, geometry) for node in nodes}
    edges = [
        edge for edge in snapshot["edges"]
        if edge.source_id in positions and edge.target_id in positions
    ]

    output = BytesIO()
    canvas = Canvas(
        output,
        pagesize=(geometry.width, geometry.height),
        pageCompression=1,
        invariant=1,
    )
    canvas.setTitle("Network topology")
    canvas.setAuthor("Status Visualizer")
    canvas.setCreator("Status Visualizer vector PDF exporter")
    canvas.setFillColor(palette.background)
    canvas.rect(0, 0, geometry.width, geometry.height, fill=1, stroke=0)

    canvas.setFillColor(palette.header)
    canvas.rect(
        0,
        geometry.height - HEADER_HEIGHT,
        geometry.width,
        HEADER_HEIGHT,
        fill=1,
        stroke=0,
    )
    canvas.setFillColor(palette.primary_text)
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(24, geometry.height - 43, "Network topology")
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    canvas.setFillColor(palette.secondary_text)
    canvas.setFont("Helvetica", 9)
    noun = "device" if len(nodes) == 1 else "devices"
    canvas.drawRightString(
        geometry.width - 24,
        geometry.height - 40,
        f"{len(nodes)} {noun} - exported {exported_at}",
    )

    for item in edges:
        _draw_edge(
            canvas,
            positions[item.source_id],
            positions[item.target_id],
            geometry.scale,
            inferred=item.kind == "manual",
            palette=palette,
        )
    for node in nodes:
        x, y = positions[node.id]
        _draw_node(
            canvas,
            node,
            x,
            y,
            geometry.scale,
            label_mode,
            snapshot.get("interfaces", []),
            snapshot.get("vlans", []),
            show_vlans,
            palette,
        )

    canvas.showPage()
    canvas.save()
    return output.getvalue()
