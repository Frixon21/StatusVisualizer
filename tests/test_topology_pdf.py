from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from app.models import DeviceRecord, EdgeRecord
from app.topology_pdf import (
    MAX_PAGE_SIDE,
    TopologyBounds,
    _draw_icon,
    _draw_node,
    _node_vlan_ids,
    _page_geometry,
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
    assert node_footprint(device("text", "Text", 0.5, 0.5, node_shape="text")) == (
        170,
        36,
    )
    assert node_footprint(SimpleNamespace(node_shape="unknown")) == (
        124,
        94,
    )


def test_text_node_draws_only_its_label_without_a_glyph_or_container(monkeypatch) -> None:
    class RecordingCanvas:
        def __init__(self) -> None:
            self.round_rects: list[tuple] = []
            self.rects: list[tuple] = []
            self.strings: list[str] = []

        def setFillColor(self, *_args) -> None:
            pass

        def setStrokeColor(self, *_args) -> None:
            pass

        def setLineWidth(self, *_args) -> None:
            pass

        def setFont(self, *_args) -> None:
            pass

        def drawString(self, _x, _y, text) -> None:
            self.strings.append(text)

        def drawCentredString(self, _x, _y, text) -> None:
            self.strings.append(text)

        def roundRect(self, *args, **_kwargs) -> None:
            self.round_rects.append(args)

        def rect(self, *args, **_kwargs) -> None:
            self.rects.append(args)

    def fail_if_icon_is_drawn(*_args, **_kwargs) -> None:
        raise AssertionError("text-only nodes must not draw a device glyph")

    monkeypatch.setattr("app.topology_pdf._draw_icon", fail_if_icon_is_drawn)
    canvas = RecordingCanvas()

    _draw_node(
        canvas,
        device("label", "PCI scope", 0.5, 0.5, node_shape="text"),
        100,
        100,
        1,
        "both",
        [],
        [],
        False,
        pdf_palette("dark"),
    )

    assert canvas.strings == ["PCI scope"]
    assert canvas.round_rects == []
    assert canvas.rects == []


def test_manual_metadata_vlans_join_existing_imported_vlan_sources() -> None:
    manual = device(
        "manual",
        "Manual server",
        0.5,
        0.5,
        metadata={"VLANs": "20, 10; 20", "Owner": "Operations"},
    )
    imported = device(
        "switch",
        "Imported switch",
        0.5,
        0.5,
        source="lantopolog",
        address="10.0.0.8",
        metadata={"PVID": "30"},
    )
    interfaces = [
        {"device_id": "manual", "vlan": "40"},
        {"device_id": "switch", "vlan": "50"},
    ]
    vlans = [{"switch_key": "switch:10.0.0.8", "vlan_id": "60"}]

    assert _node_vlan_ids(manual, interfaces, vlans) == ["10", "20", "40"]
    assert _node_vlan_ids(imported, interfaces, vlans) == ["30", "50", "60"]


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


def test_content_bounds_reject_empty_selection_and_support_fixed_footprints() -> None:
    with pytest.raises(ValueError, match="At least one device"):
        content_bounds([])

    bounds = content_bounds(
        [device("one", "One", 0.5, 0.5, node_shape="circle")],
        node_width=200,
        node_height=40,
        padding=10,
    )

    assert bounds == TopologyBounds(x=790, y=520, width=220, height=60)


def test_page_geometry_centers_small_content_and_scales_oversized_content() -> None:
    small = _page_geometry(TopologyBounds(0, 0, 100, 100))
    huge = _page_geometry(TopologyBounds(0, 0, 20_000, 10_000))

    assert (small.width, small.height, small.scale) == (1000, 650, 1.0)
    assert small.offset_x == 450
    assert small.offset_y == 239
    assert huge.width <= MAX_PAGE_SIDE
    assert huge.height <= MAX_PAGE_SIDE
    assert huge.scale < 1
    assert huge.offset_x >= 0
    assert huge.offset_y >= 0


@pytest.mark.parametrize(
    "icon_type",
    [
        "router",
        "switch",
        "access-point",
        "server",
        "workstation",
        "printer",
        "phone",
        "unsupported",
    ],
)
def test_each_device_icon_renders_as_vector_graphics(icon_type) -> None:
    output = BytesIO()
    canvas = Canvas(output, pagesize=(100, 100), invariant=1)

    _draw_icon(canvas, icon_type, 50, 50, 48, pdf_palette("dark"))
    canvas.showPage()
    canvas.save()

    pdf = output.getvalue()
    page = PdfReader(BytesIO(pdf)).pages[0]
    assert pdf.startswith(b"%PDF-")
    assert page.get_contents().get_data()
    assert not page["/Resources"].get("/XObject", {})


def test_all_node_shapes_render_with_vlan_stripes_and_label_modes() -> None:
    nodes = [
        device(
            "icon",
            "Icon node",
            0.15,
            0.2,
            node_shape="icon",
            icon_type="router",
            metadata={"VLANs": "10,20,30,40,50"},
        ),
        device(
            "card",
            "Card node",
            0.4,
            0.4,
            node_shape="card",
            icon_type="switch",
        ),
        device(
            "circle",
            "Circle node",
            0.65,
            0.6,
            node_shape="circle",
            icon_type="server",
        ),
        device("text", " ", 0.85, 0.8, node_shape="text", address="10.9.8.7"),
    ]

    pdf = render_topology_pdf(
        snapshot(
            nodes,
            [
                edge("manual", "icon", "card", kind="manual"),
                edge("imported", "card", "circle", kind="infrastructure"),
            ],
        ),
        [item.id for item in nodes],
        label_mode="both",
        show_vlans=True,
    )
    text = PdfReader(BytesIO(pdf)).pages[0].extract_text()

    assert "Icon node" in text
    assert "Card node" in text
    assert "Circle node" in text
    assert "10.9.8.7" in text


def test_render_rejects_missing_selected_device() -> None:
    with pytest.raises(ValueError, match="no longer exist"):
        render_topology_pdf(
            snapshot([device("present", "Present", 0.5, 0.5)], []),
            ["present", "missing"],
            label_mode="hostname",
            show_vlans=False,
        )
