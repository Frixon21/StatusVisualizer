from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.models import DeviceRecord, EdgeRecord
from app.topology_pdf import (
    content_bounds,
    node_footprint,
    pdf_palette,
    render_topology_pdf,
    safe_pdf_text,
)


def device(identifier: str, name: str, x: float, y: float, **changes) -> DeviceRecord:
    values = {
        "id": identifier,
        "name": name,
        "address": f"10.0.0.{len(identifier)}",
        "notes": "",
        "x": x,
        "y": y,
        "node_type": "workstation",
        "icon_type": "auto",
        "node_shape": "icon",
        "mac_address": "",
        "locked": True,
        "source": "local",
        "metadata": {},
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
        "liveness_state": "online",
        "liveness_checked_at": "2026-08-20T12:00:00Z",
        "liveness_latency_ms": 1.2,
    }
    return DeviceRecord(**{**values, **changes})


def edge(identifier: str, source_id: str, target_id: str, **changes) -> EdgeRecord:
    values = {
        "id": identifier,
        "source_id": source_id,
        "target_id": target_id,
        "label": "",
        "kind": "infrastructure",
        "source_interface": "",
        "target_interface": "",
        "evidence": "",
        "metadata": {},
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
    }
    return EdgeRecord(**{**values, **changes})


def snapshot(nodes: list[DeviceRecord], edges: list[EdgeRecord]) -> dict:
    return {"nodes": nodes, "edges": edges, "interfaces": [], "vlans": []}


def test_content_bounds_crop_to_nodes_even_outside_the_original_stage() -> None:
    bounds = content_bounds(
        [device("left", "Left", -0.2, 0.25), device("right", "Right", 1.5, 1.2)]
    )

    assert bounds.x == -502
    assert bounds.y == 148
    assert bounds.width == 3344
    assert bounds.height == 1299


def test_node_footprints_match_the_editor_shapes() -> None:
    assert node_footprint(device("icon", "Icon", 0.5, 0.5, node_shape="icon")) == (
        124,
        94,
    )
    assert node_footprint(device("card", "Card", 0.5, 0.5, node_shape="card")) == (
        164,
        72,
    )
    assert node_footprint(device("circle", "Circle", 0.5, 0.5, node_shape="circle")) == (
        112,
        112,
    )
    assert node_footprint(SimpleNamespace(node_shape="unknown")) == (
        124,
        94,
    )


def test_content_bounds_use_each_nodes_actual_shape() -> None:
    bounds = content_bounds(
        [
            device("icon", "Icon", 0.4, 0.5, node_shape="icon"),
            device("circle", "Circle", 0.6, 0.5, node_shape="circle"),
        ],
        padding=0,
    )

    assert bounds.x == 658
    assert bounds.y == 494
    assert bounds.width == 478
    assert bounds.height == 112


def test_icon_nodes_fit_the_saved_hierarchy_spacing_without_overlap() -> None:
    first = device("first", "First", 0.4, 0.5, node_shape="icon")
    second = device("second", "Second", 0.475, 0.5, node_shape="icon")
    first_width, _ = node_footprint(first)
    second_width, _ = node_footprint(second)

    first_right = first.x * 1800 + first_width / 2
    second_left = second.x * 1800 - second_width / 2

    assert second_left - first_right == 11


def test_rendered_topology_is_a_single_page_vector_diagram() -> None:
    nodes = [
        device("core", "Core router", 0.2, 0.2, node_type="router"),
        device("client", "Client workstation", 0.8, 0.7),
    ]
    pdf = render_topology_pdf(
        snapshot(nodes, [edge("uplink", "core", "client")]),
        [node.id for node in nodes],
        label_mode="both",
        show_vlans=False,
    )

    reader = PdfReader(BytesIO(pdf))
    assert pdf.startswith(b"%PDF-")
    assert len(reader.pages) == 1
    assert reader.metadata.title == "Network topology"
    assert tuple(float(value) for value in reader.pages[0].mediabox[2:]) != (792.0, 612.0)
    text = reader.pages[0].extract_text()
    assert "Network topology" in text
    assert "Core router" in text
    assert "Client workstation" in text
    assert "10.0.0.4" in text
    assert "10.0.0.6" in text

    resources = reader.pages[0]["/Resources"]
    xobjects = resources.get("/XObject", {})
    assert all(item.get_object().get("/Subtype") != "/Image" for item in xobjects.values())
    assert "/OpenAction" not in reader.trailer["/Root"]
    assert "/AA" not in reader.trailer["/Root"]
    assert "/Names" not in reader.trailer["/Root"]


def test_pdf_omits_liveness_markers_and_ping_legend() -> None:
    nodes = [
        device("online", "Online device", 0.2, 0.3, icon_type="other"),
        device(
            "offline",
            "Offline device",
            0.5,
            0.5,
            icon_type="other",
            liveness_state="offline",
        ),
        device(
            "unknown",
            "Unchecked device",
            0.8,
            0.7,
            icon_type="other",
            liveness_state="unknown",
        ),
    ]

    pdf = render_topology_pdf(
        snapshot(nodes, []),
        [node.id for node in nodes],
        label_mode="hostname",
        show_vlans=False,
    )
    page = PdfReader(BytesIO(pdf)).pages[0]
    text = page.extract_text()
    drawing = page.get_contents().get_data()

    assert "No ping reply" not in text
    assert "Not checked" not in text
    assert "visible devices - vector diagram" not in text
    assert b".239216 .862745 .592157 rg" not in drawing
    assert b".941176 .321569 .294118 rg" not in drawing
    assert b".54902 .627451 .709804 rg" not in drawing


def test_pdf_theme_changes_the_vector_palette_without_changing_content() -> None:
    topology = snapshot([device("client", "Client workstation", 0.5, 0.5)], [])

    dark = render_topology_pdf(
        topology,
        ["client"],
        label_mode="both",
        show_vlans=False,
        theme="dark",
    )
    light = render_topology_pdf(
        topology,
        ["client"],
        label_mode="both",
        show_vlans=False,
        theme="light",
    )

    assert dark != light
    assert "Client workstation" in PdfReader(BytesIO(dark)).pages[0].extract_text()
    assert "Client workstation" in PdfReader(BytesIO(light)).pages[0].extract_text()
    assert pdf_palette("dark").background != pdf_palette("light").background
    assert pdf_palette("dark").primary_text != pdf_palette("light").primary_text
    with pytest.raises(ValueError, match="PDF theme"):
        pdf_palette("sepia")


def test_light_pdf_uses_a_pure_white_print_background() -> None:
    palette = pdf_palette("light")
    pdf = render_topology_pdf(
        snapshot([device("client", "Client workstation", 0.5, 0.5)], []),
        ["client"],
        label_mode="hostname",
        show_vlans=False,
        theme="light",
    )
    drawing = PdfReader(BytesIO(pdf)).pages[0].get_contents().get_data()

    assert (palette.background.red, palette.background.green, palette.background.blue) == (
        1.0,
        1.0,
        1.0,
    )
    assert b"1 1 1 rg\nn 0 0" in drawing


def test_render_filters_hidden_nodes_and_their_connections() -> None:
    nodes = [
        device("visible", "Visible device", 0.3, 0.4),
        device("hidden", "Hidden device", 0.7, 0.6),
    ]
    pdf = render_topology_pdf(
        snapshot(nodes, [edge("link", "visible", "hidden")]),
        ["visible"],
        label_mode="hostname",
        show_vlans=False,
    )

    text = PdfReader(BytesIO(pdf)).pages[0].extract_text()
    assert "Visible device" in text
    assert "Hidden device" not in text


def test_pdf_text_is_plain_sanitized_and_bounded() -> None:
    assert safe_pdf_text("  Name\x00 with\ncontrols   ") == "Name with controls"
    assert safe_pdf_text("x" * 200, maximum=20) == "xxxxxxxxxxxxxxxxx..."

    unsafe_name = "<b>Device</b>\x00\nName"
    pdf = render_topology_pdf(
        snapshot([device("safe", unsafe_name, 0.5, 0.5)], []),
        ["safe"],
        label_mode="hostname",
        show_vlans=False,
    )
    text = PdfReader(BytesIO(pdf)).pages[0].extract_text()
    assert "<b>Device</b> Name" in text


def test_extreme_workspace_is_scaled_below_pdf_page_limits() -> None:
    nodes = [
        device("minimum", "Minimum", -100, -100),
        device("maximum", "Maximum", 101, 101),
    ]
    pdf = render_topology_pdf(
        snapshot(nodes, []),
        [node.id for node in nodes],
        label_mode="hostname",
        show_vlans=False,
    )
    page = PdfReader(BytesIO(pdf)).pages[0]
    assert max(float(page.mediabox.width), float(page.mediabox.height)) <= 7200
