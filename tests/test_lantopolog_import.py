from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lantopolog import ExportValidationError, parse_lantopolog_export
from app.models import LantopologImportInput
from tests.lantopolog_fixture import export_files


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

    desktop = next(node for node in topology.nodes if node.mac_address == "00:11:22:33:44:55")
    assert desktop.name == "DESKTOP-1"
    assert desktop.node_type == "workstation"
    assert desktop.metadata["Manufacturer"] == "Dell"
    assert desktop.metadata["OS Caption"] == "Windows 11"

    phone = next(node for node in topology.nodes if node.mac_address == "08:00:0F:E6:23:77")
    assert phone.address == ""
    assert phone.node_type == "phone"
    assert any(edge.target_key == phone.key or edge.source_key == phone.key for edge in topology.edges)


def test_compact_endpoint_file_is_a_complete_fallback() -> None:
    topology = parse_lantopolog_export(export_files(include_wide_endpoints=False))

    assert topology.summary["endpoints"] == 2
    assert "complist2.csv" in topology.files_used
    assert all("Tmp/" not in path for path in topology.files_used)


def test_reverse_switch_rows_collapse_and_missing_port_rows_are_synthesized() -> None:
    topology = parse_lantopolog_export(export_files())

    switch_edges = [edge for edge in topology.edges if edge.edge_type == "infrastructure"]
    assert len(switch_edges) == 1
    assert switch_edges[0].source_interface_key is not None
    assert switch_edges[0].target_interface_key == "switch:192.168.1.3:port:8"
    interface_keys = {interface.key for interface in topology.interfaces}
    assert "switch:192.168.1.3:port:8" in interface_keys
    assert "switch:192.168.1.2:port:5" in interface_keys
    assert "switch:192.168.1.2:port:6" in interface_keys


def test_repeated_real_endpoints_fan_out_through_one_deterministic_synthetic_switch() -> None:
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
        node.key for node in topology.nodes if node.name in {"REAL-ZERO-1", "REAL-ZERO-2"}
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
