from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lantopolog import ExportValidationError, parse_lantopolog_export
from app.models import LantopologImportInput
from tests.lantopolog_fixture import export_files

SWITCH_HEADER = 'N;IP;Model;"Serial number";"MAC address";"SNMP Version";Name;Location;Description\n'


def minimal_switches(*rows: str) -> str:
    return SWITCH_HEADER + "\n".join(rows) + "\n"


def test_full_export_uses_every_authoritative_source_and_builds_topology() -> None:
    topology = parse_lantopolog_export(export_files())

    assert len(topology.nodes) == 4
    assert len(topology.edges) == 3
    assert len(topology.interfaces) == 4
    assert len(topology.vlans) == 1
    assert topology.summary["switches"] == 2
    assert topology.summary["endpoints"] == 2
    assert topology.summary["connections"] == 3
    assert topology.summary["files_used"] == 6

    desktop = next(
        node for node in topology.nodes if node.mac_address == "00:11:22:33:44:55"
    )
    assert desktop.name == "DESKTOP-1"
    assert desktop.node_type == "workstation"
    assert desktop.metadata["Manufacturer"] == "Dell"
    assert desktop.metadata["OS Caption"] == "Windows 11"

    phone = next(
        node for node in topology.nodes if node.mac_address == "08:00:0F:E6:23:77"
    )
    assert phone.address == ""
    assert phone.node_type == "phone"
    assert any(
        edge.target_key == phone.key or edge.source_key == phone.key
        for edge in topology.edges
    )


def test_compact_endpoint_file_is_a_complete_fallback() -> None:
    topology = parse_lantopolog_export(export_files(include_wide_endpoints=False))

    assert topology.summary["endpoints"] == 2
    assert "complist2.csv" in topology.files_used
    assert all("Tmp/" not in path for path in topology.files_used)


def test_reverse_switch_rows_collapse_and_missing_port_rows_are_synthesized() -> None:
    topology = parse_lantopolog_export(export_files())

    switch_edges = [
        edge for edge in topology.edges if edge.edge_type == "infrastructure"
    ]
    assert len(switch_edges) == 1
    assert switch_edges[0].source_interface_key is not None
    assert switch_edges[0].target_interface_key == "switch:192.168.1.3:port:8"
    interface_keys = {interface.key for interface in topology.interfaces}
    assert "switch:192.168.1.3:port:8" in interface_keys
    assert "switch:192.168.1.2:port:5" in interface_keys
    assert "switch:192.168.1.2:port:6" in interface_keys


def test_repeated_real_endpoints_fan_out_through_one_deterministic_synthetic_switch() -> (
    None
):
    files = export_files()
    desktop_row = files["complist.csv"].splitlines()[1]
    extra_rows = [
        desktop_row.replace("001122334455", f"0011223344{suffix}")
        .replace("192.168.1.20", f"192.168.1.{address}")
        .replace("DESKTOP-1", f"DESKTOP-{suffix}")
        for suffix, address in (("56", 21), ("57", 22), ("58", 23), ("59", 24))
    ]
    files["complist.csv"] += "\n".join(extra_rows) + "\n"

    topology = parse_lantopolog_export(files)
    repeated = {
        node.key
        for node in topology.nodes
        if node.mac_address.startswith("00:11:22:33:44")
    }
    fanouts = [
        node
        for node in topology.nodes
        if node.metadata.get("Synthetic Role") == "shared-port-fanout"
    ]

    assert len(repeated) == 5
    assert len(fanouts) == 1
    fanout = fanouts[0]
    assert fanout.node_type == "switch"
    assert fanout.address == ""
    assert fanout.name.startswith("Inferred switch/bridge")
    assert fanout.metadata["Endpoint Count"] == "5"
    second_import = parse_lantopolog_export(files)
    second_fanout = next(
        node
        for node in second_import.nodes
        if node.metadata.get("Synthetic Role") == "shared-port-fanout"
    )
    assert fanout.key == second_fanout.key

    endpoint_edges = [edge for edge in topology.edges if edge.target_key in repeated]
    assert {edge.source_key for edge in endpoint_edges} == {fanout.key}
    assert {edge.label for edge in endpoint_edges} == {"Inferred downstream attachment"}
    assert {edge.metadata["Original Managed Port"] for edge in endpoint_edges} == {"5"}
    uplink = next(
        edge
        for edge in topology.edges
        if {edge.source_key, edge.target_key} == {"switch:192.168.1.2", fanout.key}
    )
    assert uplink.edge_type == "infrastructure"
    assert uplink.label == "5, Desk"
    assert uplink.source_interface_key == "switch:192.168.1.2:port:5"
    assert uplink.target_interface_key == f"{fanout.key}:port:uplink"
    interface_keys = {interface.key for interface in topology.interfaces}
    assert uplink.source_interface_key in interface_keys
    assert uplink.target_interface_key in interface_keys

    phone = next(node for node in topology.nodes if node.node_type == "phone")
    phone_edge = next(edge for edge in topology.edges if edge.target_key == phone.key)
    assert phone_edge.source_key == "switch:192.168.1.2"


def test_all_zero_port_never_creates_a_fanout_or_interface() -> None:
    files = export_files()
    desktop_row = files["complist.csv"].splitlines()[1]
    placeholder_rows = [
        desktop_row.replace("001122334455", "")
        .replace("192.168.1.20", "")
        .replace("DESKTOP-1", hostname)
        .replace("port 5", "port 00")
        .replace('"5, Desk"', '"00, unresolved"')
        for hostname in ("UNKNOWN-1", "UNKNOWN-2")
    ]
    real_zero_rows = [
        desktop_row.replace("001122334455", mac)
        .replace("192.168.1.20", address)
        .replace("DESKTOP-1", hostname)
        .replace("port 5", "port 00")
        .replace('"5, Desk"', '"00, unresolved"')
        for mac, address, hostname in (
            ("001122334466", "192.168.1.30", "REAL-ZERO-1"),
            ("001122334477", "192.168.1.31", "REAL-ZERO-2"),
        )
    ]
    files["complist.csv"] += "\n".join((*placeholder_rows, *real_zero_rows)) + "\n"

    topology = parse_lantopolog_export(files)
    placeholders = {
        node.key for node in topology.nodes if node.name in {"UNKNOWN-1", "UNKNOWN-2"}
    }
    real_zero_endpoints = {
        node.key
        for node in topology.nodes
        if node.name in {"REAL-ZERO-1", "REAL-ZERO-2"}
    }
    zero_port_endpoints = placeholders | real_zero_endpoints

    assert len(placeholders) == 2
    assert len(real_zero_endpoints) == 2
    assert not any(
        node.metadata.get("Synthetic Role") == "shared-port-fanout"
        and node.metadata.get("Managed Port") == "00"
        for node in topology.nodes
    )
    assert not any(interface.port == "00" for interface in topology.interfaces)
    assert all(
        edge.source_interface_key is None
        for edge in topology.edges
        if edge.target_key in zero_port_endpoints
    )
    assert {
        edge.source_key
        for edge in topology.edges
        if edge.target_key in zero_port_endpoints
    } == {"switch:192.168.1.2"}


def test_summary_reports_inferred_switches_separately() -> None:
    files = export_files()
    desktop_row = files["complist.csv"].splitlines()[1]
    files["complist.csv"] += (
        desktop_row.replace("001122334455", "001122334456")
        .replace("192.168.1.20", "192.168.1.21")
        .replace("DESKTOP-1", "DESKTOP-2")
        + "\n"
    )

    topology = parse_lantopolog_export(files)

    assert topology.summary["switches"] == 3
    assert topology.summary["inferred_switches"] == 1


def test_import_rejects_oversized_or_unrecognized_payloads() -> None:
    with pytest.raises(ExportValidationError, match="recognized"):
        parse_lantopolog_export({"passwords.txt": "secret"})

    with pytest.raises(ExportValidationError, match="too large"):
        parse_lantopolog_export({"sw_list.csv": "x" * (5 * 1024 * 1024 + 1)})


def test_import_request_model_rejects_content_before_parser_work() -> None:
    with pytest.raises(ValidationError):
        LantopologImportInput(
            files=[
                {
                    "name": "sw_list.csv",
                    "path": "sw_list.csv",
                    "content": "x" * (5 * 1024 * 1024 + 1),
                }
            ]
        )

    with pytest.raises(ValidationError):
        LantopologImportInput(
            files=[
                {
                    "name": f"sw_list_{index}.csv",
                    "path": f"sw_list_{index}.csv",
                    "content": "x" * (1100 * 1024),
                }
                for index in range(20)
            ]
        )


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({}, "No recognized"),
        ({"../sw_list.csv": SWITCH_HEADER}, "invalid file path"),
        ({"./": SWITCH_HEADER}, "invalid file path"),
        ({"folder\x00/sw_list.csv": SWITCH_HEADER}, "invalid file path"),
        ({"sw_list.csv": object()}, "text data"),
        ({"complist.csv": "MAC;IP;HostName\n"}, "switch list is required"),
        (
            {f"file-{index}.txt": "" for index in range(65)},
            "too many files",
        ),
        (
            {f"chunk-{index}.txt": "x" * (4 * 1024 * 1024 + 1) for index in range(5)},
            "combined export is too large",
        ),
    ],
)
def test_import_validates_mapping_paths_content_and_aggregate_limits(
    files: dict[str, object], message: str
) -> None:
    with pytest.raises(ExportValidationError, match=message):
        parse_lantopolog_export(files)


def test_import_decodes_utf8_bom_and_cp1252_and_prefers_authoritative_paths() -> None:
    cp1252_switches = minimal_switches(
        "1;192.168.1.2;J9299A;SER-1;C09134866580;v2c;Café;Rack;Managed switch"
    ).encode("cp1252")
    files: dict[str, object] = {
        "./nested/SW_LIST.CSV": cp1252_switches,
        "Tmp/swlist.csv": minimal_switches("1;192.168.1.99;Demo;;;;Wrong;Lab;Fallback"),
        "complist2.csv": (
            b'\xef\xbb\xbf"Connected to";MAC;IP;HostName\n'
            b'"sw 192.168.1.2 port 1";001122334455;;DESKTOP-BOM\n'
        ),
    }

    topology = parse_lantopolog_export(files)

    assert {node.address for node in topology.nodes if node.address} == {"192.168.1.2"}
    assert (
        next(node for node in topology.nodes if node.address == "192.168.1.2").name
        == "Café"
    )
    assert topology.files_used == ("SW_LIST.CSV", "complist2.csv")


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"sw_list.csv": ""}, "empty"),
        ({"sw_list.csv": ";;\nvalue\n"}, "no recognizable columns"),
        ({"sw_list.csv": SWITCH_HEADER}, "invalid header or no devices"),
        (
            {"sw_list.csv": minimal_switches("1;not-an-ip;;;;;Bad;;;")},
            "no valid switch addresses",
        ),
        (
            {
                "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
                "complist.csv": "Domain;UserName\nLOCAL;alex\n",
            },
            "computer list.*invalid header",
        ),
        (
            {
                "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
                "port_list.csv": "",
            },
            "port_list.csv is empty",
        ),
        (
            {
                "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
                "port_list.csv": "IfIndex;Name\n1;uplink\n",
            },
            "port list has an invalid header",
        ),
        (
            {
                "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
                "vlan_list.csv": "Name;Switch\nDefault;192.168.1.2\n",
            },
            "VLAN list has an invalid header",
        ),
        (
            {
                "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
                "sw_conn.csv": "Name;Location;Address\nCore;Rack;192.168.1.2\n",
            },
            "connection list has an invalid header",
        ),
    ],
)
def test_recognized_csv_files_require_valid_headers_and_records(
    files: dict[str, str], message: str
) -> None:
    with pytest.raises(ExportValidationError, match=message):
        parse_lantopolog_export(files)


def test_switch_types_and_duplicate_records_use_the_richest_valid_row() -> None:
    files = {
        "sw_list.csv": minimal_switches(
            "1;192.168.1.2;;;;;Old;;;",
            "2;192.168.1.2;AP-515;SER-2;001122334455;v3;Lobby access point;Floor 1;wireless AP",
            "3;192.168.1.3;UDM-Pro;;;;Gateway;;;",
            "4;192.168.1.4;TrueNAS;;;;Storage;;;NAS",
            "5;192.168.1.5;Plain;;;;Core;;;",
            "6;invalid;Plain;;;;Ignored;;;",
        )
    }

    topology = parse_lantopolog_export(files)
    by_address = {node.address: node for node in topology.nodes}

    assert by_address["192.168.1.2"].name == "Lobby access point"
    assert by_address["192.168.1.2"].node_type == "access-point"
    assert by_address["192.168.1.2"].mac_address == "00:11:22:33:44:55"
    assert by_address["192.168.1.3"].node_type == "router"
    assert by_address["192.168.1.4"].node_type == "server"
    assert by_address["192.168.1.5"].node_type == "switch"


def test_endpoint_identity_type_inference_and_duplicate_metadata_merging() -> None:
    endpoints = (
        'MAC;IP;HostName;"MAC Lookup Vendor";Model;"OS Caption";Note\n'
        "001122334401;;mystery;;;;x\n"
        "001122334401;192.168.1.41;mystery;;;Windows Server 2025;a much longer note\n"
        "001122334402;;desk-phone;Acme;;;\n"
        "001122334403;;device;Brother Industries;;;\n"
        "001122334404;;NPI123;Hewlett Packard;;;\n"
        "001122334405;;hyperv-node;;;;\n"
        "001122334406;;plain;;;Windows Server 2025;\n"
        "001122334407;;plain;;;macOS 15;\n"
        ";192.168.1.48;remote-worker;;;;\n"
        ";;hostname-only;;;;\n"
        ";;;;;;\n"
    )

    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
            "complist.csv": endpoints,
        }
    )
    by_mac = {node.mac_address: node for node in topology.nodes if node.mac_address}

    assert by_mac["00:11:22:33:44:01"].node_type == "server"
    assert by_mac["00:11:22:33:44:01"].metadata["Note"] == "a much longer note"
    assert by_mac["00:11:22:33:44:02"].node_type == "phone"
    assert by_mac["00:11:22:33:44:03"].node_type == "printer"
    assert by_mac["00:11:22:33:44:04"].node_type == "printer"
    assert by_mac["00:11:22:33:44:05"].node_type == "server"
    assert by_mac["00:11:22:33:44:06"].node_type == "server"
    assert by_mac["00:11:22:33:44:07"].node_type == "workstation"
    assert any(node.key == "endpoint:ip:192.168.1.48" for node in topology.nodes)
    assert any(node.key.startswith("endpoint:name:") for node in topology.nodes)


def test_invalid_port_sections_are_ignored_and_valid_rows_replace_duplicates() -> None:
    port_list = (
        "Port;IfIndex;Name;Alias\n"
        "1;1;before-section;ignored\n"
        '"Not a switch section"\n'
        "2;2;invalid-section;ignored\n"
        '"Switch invalid-address Bad"\n'
        "3;3;invalid-address;ignored\n"
        '"Switch 192.168.1.2 Core"\n'
        "4;4;old-name;old\n"
        "4;44;new-name;new\n"
        ";;;\n"
    )

    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
            "port_list.csv": port_list,
        }
    )

    assert len(topology.interfaces) == 1
    assert topology.interfaces[0].port == "4"
    assert topology.interfaces[0].if_index == "44"
    assert topology.interfaces[0].name == "new-name"


def test_unattached_and_invalidly_attached_endpoints_remain_visible_without_edges() -> (
    None
):
    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
            "complist.csv": (
                'MAC;IP;HostName;"Connected to"\n'
                "001122334455;;UNATTACHED;\n"
                '001122334466;;BAD-TARGET;"sw invalid port 4"\n'
                '001122334477;;BAD-FORMAT;"switch 192.168.1.2 port 5"\n'
            ),
        }
    )

    endpoints = [node for node in topology.nodes if node.key.startswith("endpoint:")]
    assert len(endpoints) == 3
    assert not topology.edges
    assert all(node.y >= 0.7 for node in endpoints)


def test_connections_resolve_names_skip_invalid_and_synthesize_missing_switches() -> (
    None
):
    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches(
                "1;192.168.1.2;;;;;Core;;;",
                "2;192.168.1.3;;;;;Remote;;;",
            ),
            "sw_conn.csv": (
                "Name;Location;IP;ifName;-;ifName;IP;Name;Location\n"
                "Core;Rack;invalid;1;-;2;invalid;Remote;Office\n"
                "Core;Rack;192.168.1.2;3;-;4;192.168.1.99;Unknown;Lab\n"
                "Unknown;;;5;-;6;;Missing;\n"
                "Core;Rack;192.168.1.2;7;-;8;192.168.1.2;Core;Rack\n"
            ),
        }
    )

    assert len(topology.edges) == 2
    synthetic = next(node for node in topology.nodes if node.address == "192.168.1.99")
    assert synthetic.metadata["Synthesized From"] == "connection data"
    assert {interface.port for interface in topology.interfaces} == {"1", "2", "3", "4"}


def test_duplicate_switch_names_are_not_used_to_resolve_connections() -> None:
    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches(
                "1;192.168.1.2;;;;;Core;;;",
                "2;192.168.1.3;;;;;CORE;;;",
            ),
            "sw_conn.csv": (
                "Name;Location;IP;ifName;-;ifName;IP;Name;Location\n"
                "Core;;invalid;1;-;2;invalid;Other;\n"
            ),
        }
    )

    assert not topology.edges


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        (
            '<!DOCTYPE root [<!ENTITY x "bad">]><mxGraphModel/>',
            "unsupported XML declarations",
        ),
        ("<mxGraphModel><root>", "not valid XML"),
    ],
)
def test_topology_xml_rejects_unsafe_or_malformed_documents(
    xml: str, message: str
) -> None:
    with pytest.raises(ExportValidationError, match=message):
        parse_lantopolog_export(
            {
                "sw_list.csv": minimal_switches("1;192.168.1.2;;;;;Core;;;"),
                "top_map.xml": xml,
            }
        )


def test_xml_layout_ignores_bad_geometry_and_normalizes_page_positions() -> None:
    xml = (
        '<mxGraphModel pageWidth="400" pageHeight="bad"><root>'
        '<mxCell id="a" value="192.168.1.2" vertex="1" parent="1">'
        '<mxGeometry x="20" y="40" width="10" height="10"/></mxCell>'
        '<mxCell id="b" value="192.168.1.3" vertex="1" parent="1">'
        '<mxGeometry x="bad" y="50"/></mxCell>'
        '<mxCell id="c" value="not an address" vertex="1" parent="1">'
        '<mxGeometry x="50" y="50"/></mxCell>'
        "</root></mxGraphModel>"
    )

    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches(
                "1;192.168.1.2;;;;;Core;;;",
                "2;192.168.1.3;;;;;Remote;;;",
            ),
            "top_map.xml": xml,
        }
    )
    by_address = {node.address: node for node in topology.nodes}

    assert (by_address["192.168.1.2"].x, by_address["192.168.1.2"].y) == (0.666667, 0.8)
    assert by_address["192.168.1.3"].y == 0.12


def test_xml_link_swaps_ports_to_match_interfaces_and_merges_exact_edge() -> None:
    files = {
        "sw_list.csv": minimal_switches(
            "1;192.168.1.2;;;;;Left;;;",
            "2;192.168.1.3;;;;;Right;;;",
        ),
        "port_list.csv": (
            "Port;IfIndex;Name\n"
            '"Switch 192.168.1.2 Left"\n'
            "8;8;8\n"
            '"Switch 192.168.1.3 Right"\n'
            "1;1;1\n"
        ),
        "sw_conn.csv": (
            "Name;Location;IP;ifName;-;ifName;IP;Name;Location\n"
            "Left;;192.168.1.2;8;-;1;192.168.1.3;Right;\n"
        ),
        "top_map.xml": (
            "<mxGraphModel><root>"
            '<mxCell id="a" value="192.168.1.2" vertex="1" parent="1"><mxGeometry/></mxCell>'
            '<mxCell id="b" value="192.168.1.3" vertex="1" parent="1"><mxGeometry/></mxCell>'
            '<mxCell id="edge" edge="1" source="a" target="b"/>'
            '<mxCell id="label" value="1-8" vertex="1" parent="edge"/>'
            "</root></mxGraphModel>"
        ),
    }

    topology = parse_lantopolog_export(files)

    assert len(topology.edges) == 1
    assert topology.edges[0].label == "8 - 1"
    assert topology.edges[0].metadata["XML Cell"] == "edge"


def test_xml_link_merges_compatible_reversed_labels_without_duplicate_edge() -> None:
    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches(
                "1;192.168.1.2;;;;;Left;;;",
                "2;192.168.1.3;;;;;Right;;;",
            ),
            "sw_conn.csv": (
                "Name;Location;IP;ifName;-;ifName;IP;Name;Location\n"
                "Left;;192.168.1.2;1;-;8;192.168.1.3;Right;\n"
            ),
            "top_map.xml": (
                "<mxGraphModel><root>"
                '<mxCell id="a" value="192.168.1.2" vertex="1" parent="1"><mxGeometry/></mxCell>'
                '<mxCell id="b" value="192.168.1.3" vertex="1" parent="1"><mxGeometry/></mxCell>'
                '<mxCell id="edge" edge="1" source="a" target="b"/>'
                '<mxCell id="label" value="8-1" vertex="1" parent="edge"/>'
                "</root></mxGraphModel>"
            ),
        }
    )

    assert len(topology.edges) == 1
    assert topology.edges[0].label == "1 - 8"
    assert topology.edges[0].metadata["XML Label"] == "8-1"


def test_xml_link_replaces_placeholder_connection_and_adds_map_only_switch() -> None:
    topology = parse_lantopolog_export(
        {
            "sw_list.csv": minimal_switches(
                "1;192.168.1.2;;;;;Left;;;",
                "2;192.168.1.3;;;;;Right;;;",
            ),
            "sw_conn.csv": (
                "Name;Location;IP;ifName;-;ifName;IP;Name;Location\n"
                "Left;;192.168.1.2;demo;-;xx;192.168.1.3;Right;\n"
            ),
            "top_map.xml": (
                "<mxGraphModel><root>"
                '<mxCell id="a" value="192.168.1.2" vertex="1" parent="1"><mxGeometry/></mxCell>'
                '<mxCell id="b" value="192.168.1.3" vertex="1" parent="1"><mxGeometry/></mxCell>'
                '<mxCell id="c" value="192.168.1.99" vertex="1" parent="1"><mxGeometry/></mxCell>'
                '<mxCell id="ab" edge="1" source="a" target="b"/>'
                '<mxCell id="ab-label" value="2-3" vertex="1" parent="ab"/>'
                '<mxCell id="bc" edge="1" source="b" target="c"/>'
                '<mxCell id="bc-label" value="uplink" vertex="1" parent="bc"/>'
                '<mxCell id="self" edge="1" source="a" target="a"/>'
                "</root></mxGraphModel>"
            ),
        }
    )

    assert {edge.label for edge in topology.edges} == {"2 - 3", "uplink"}
    map_switch = next(node for node in topology.nodes if node.address == "192.168.1.99")
    assert map_switch.metadata["Synthesized From"] == "topology map"
    assert {interface.port for interface in topology.interfaces} == {"2", "3", "uplink"}
