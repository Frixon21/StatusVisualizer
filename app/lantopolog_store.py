from __future__ import annotations

import json
import sqlite3
import uuid

from app.lantopolog import ImportedNode, ImportedTopology
from app.models import utc_now
from app.network import canonical_probe_address

_IMPORT_NAMESPACE = uuid.UUID("8b0386b2-5887-5ed7-8f06-2a12bece7c35")


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip().replace("\x00", "")[:limit]


def _identifier(kind: str, key: str) -> str:
    return str(uuid.uuid5(_IMPORT_NAMESPACE, f"{kind}:{key}"))


def _insert_node(
    connection: sqlite3.Connection,
    node: ImportedNode,
    device_id: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO devices
            (id, name, address, notes, x, y, source, external_id, node_type,
             icon_type, node_shape, mac_address, locked, probe_address,
             metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'lantopolog', ?, ?, 'auto', 'icon', ?, 0, ?, ?, ?, ?)
        """,
        (
            device_id, node.name, node.address, "", node.x, node.y, node.key,
            node.node_type, node.mac_address, canonical_probe_address(node.address) or "",
            json.dumps(dict(node.metadata), sort_keys=True), now, now,
        ),
    )


def _update_node(
    connection: sqlite3.Connection,
    existing: sqlite3.Row,
    node: ImportedNode,
    device_id: str,
    now: str,
) -> None:
    locked = bool(existing["locked"])
    connection.execute(
        """
        UPDATE devices SET name = ?, address = ?, notes = ?, x = ?, y = ?,
            node_type = ?, icon_type = ?, node_shape = ?, mac_address = ?,
            source = 'lantopolog', external_id = ?, probe_address = ?,
            liveness_state = CASE WHEN address != ? THEN 'unknown' ELSE liveness_state END,
            liveness_checked_at = CASE WHEN address != ? THEN NULL ELSE liveness_checked_at END,
            liveness_latency_ms = CASE WHEN address != ? THEN NULL ELSE liveness_latency_ms END,
            metadata_json = ?, updated_at = ? WHERE id = ?
        """,
        (
            existing["name"] if locked else node.name,
            node.address,
            existing["notes"],
            existing["x"] if locked else node.x,
            existing["y"] if locked else node.y,
            existing["node_type"] if locked else node.node_type,
            existing["icon_type"] if locked else "auto",
            existing["node_shape"] if locked else "icon",
            node.mac_address,
            node.key,
            canonical_probe_address(node.address) or "",
            node.address,
            node.address,
            node.address,
            json.dumps(dict(node.metadata), sort_keys=True),
            now,
            device_id,
        ),
    )


def _replace_nodes(
    connection: sqlite3.Connection,
    topology: ImportedTopology,
    device_ids: dict[str, str],
    now: str,
) -> tuple[str, ...]:
    for node in topology.nodes:
        device_id = device_ids[node.key]
        existing = connection.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if existing is None:
            _insert_node(connection, node, device_id, now)
        else:
            _update_node(connection, existing, node, device_id, now)

    imported_ids = tuple(device_ids.values())
    if not imported_ids:
        connection.execute("DELETE FROM devices WHERE source = 'lantopolog'")
        return imported_ids
    placeholders = ",".join("?" for _ in imported_ids)
    connection.execute(
        f"DELETE FROM devices WHERE source = 'lantopolog' AND id NOT IN ({placeholders})",
        imported_ids,
    )
    return imported_ids


def _replace_interfaces(
    connection: sqlite3.Connection,
    topology: ImportedTopology,
    device_ids: dict[str, str],
    imported_ids: tuple[str, ...],
) -> None:
    if imported_ids:
        placeholders = ",".join("?" for _ in imported_ids)
        connection.execute(f"DELETE FROM interfaces WHERE device_id IN ({placeholders})", imported_ids)
    for interface in topology.interfaces:
        device_id = device_ids.get(interface.node_key)
        if device_id is None:
            continue
        try:
            speed = int(float(interface.speed_mbps)) * 1_000_000 if interface.speed_mbps else None
        except ValueError:
            speed = None
        connection.execute(
            """
            INSERT INTO interfaces
                (id, device_id, if_index, name, description, speed_bps, admin_status,
                 oper_status, vlan, is_trunk, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _identifier("interface", interface.key), device_id,
                int(interface.if_index) if interface.if_index.isdigit() else None,
                interface.name or interface.port, interface.alias, speed,
                interface.admin_status, interface.oper_status, interface.pvid_vlan,
                int(bool(interface.tagged_vlan)),
                json.dumps(dict(interface.metadata), sort_keys=True),
            ),
        )


def _replace_edges(
    connection: sqlite3.Connection,
    topology: ImportedTopology,
    device_ids: dict[str, str],
    now: str,
) -> None:
    connection.execute("DELETE FROM topology_edges WHERE kind IN ('infrastructure', 'attachment')")
    for edge in topology.edges:
        first_id, second_id = device_ids[edge.source_key], device_ids[edge.target_key]
        source_id, target_id = sorted((first_id, second_id))
        reversed_edge = source_id != first_id
        source_interface = (edge.source_interface_key or "").rsplit(":port:", 1)[-1]
        target_interface = (edge.target_interface_key or "").rsplit(":port:", 1)[-1]
        evidence = edge.metadata.get("Source File") or "Lantopolog export"
        connection.execute(
            """
            INSERT INTO topology_edges
                (id, source_id, target_id, label, kind, evidence, source_interface,
                 target_interface, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _identifier("edge", edge.key), source_id, target_id,
                _bounded(edge.label, 120), edge.edge_type, _bounded(evidence, 500),
                target_interface if reversed_edge else source_interface,
                source_interface if reversed_edge else target_interface,
                json.dumps(dict(edge.metadata), sort_keys=True), now, now,
            ),
        )


def _replace_vlans(connection: sqlite3.Connection, topology: ImportedTopology) -> None:
    connection.execute("DELETE FROM lantopolog_vlans")
    connection.executemany(
        """
        INSERT INTO lantopolog_vlans
            (id, vlan_id, name, switch_key, tagged_ports, untagged_ports, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                _identifier("vlan", vlan.key), vlan.vlan_id, vlan.name, vlan.switch_key,
                vlan.tagged_ports, vlan.untagged_ports,
                json.dumps(dict(vlan.metadata), sort_keys=True),
            )
            for vlan in topology.vlans
        ],
    )


def replace_lantopolog_data(
    connection: sqlite3.Connection,
    topology: ImportedTopology,
) -> dict[str, object]:
    now = utc_now()
    device_ids = {node.key: _identifier("node", node.key) for node in topology.nodes}
    connection.execute("BEGIN IMMEDIATE")
    imported_ids = _replace_nodes(connection, topology, device_ids, now)
    _replace_interfaces(connection, topology, device_ids, imported_ids)
    _replace_edges(connection, topology, device_ids, now)
    _replace_vlans(connection, topology)

    status = {
        "imported_at": now,
        "files_used": len(topology.files_used),
        "file_names": list(topology.files_used),
        **topology.summary,
    }
    connection.execute(
        """
        INSERT INTO app_metadata (key, value_json) VALUES ('lantopolog_import', ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
        """,
        (json.dumps(status, sort_keys=True),),
    )
    return status
