from __future__ import annotations

from types import MappingProxyType

from app.database import Repository
from app.lantopolog import ImportedInterface, ImportedNode, ImportedTopology
from app.models import DeviceInput, EdgeInput


def topology(
    *,
    nodes=(),
    interfaces=(),
    files_used=("sw_list.csv",),
    summary=None,
) -> ImportedTopology:
    return ImportedTopology(
        nodes=tuple(nodes),
        edges=(),
        interfaces=tuple(interfaces),
        vlans=(),
        files_used=tuple(files_used),
        summary=MappingProxyType(dict(summary or {"switches": len(nodes)})),
    )


def imported_node(key: str, name: str, address: str) -> ImportedNode:
    return ImportedNode(
        key=key,
        name=name,
        address=address,
        node_type="switch",
        mac_address="AA:BB:CC:DD:EE:FF",
        metadata=MappingProxyType({"Source File": "sw_list.csv"}),
    )


def imported_interface(key: str, node_key: str, speed: str = "1000") -> ImportedInterface:
    return ImportedInterface(
        key=key,
        node_key=node_key,
        port="1",
        if_index="1",
        name="GigabitEthernet1",
        speed_mbps=speed,
        metadata=MappingProxyType({"Role": "uplink"}),
    )


def test_empty_import_removes_imported_rows_but_persists_status_and_local_topology(
    tmp_path,
) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    first = repository.create_device(DeviceInput(name="Local A", x=0.1, y=0.2))
    second = repository.create_device(DeviceInput(name="Local B", x=0.3, y=0.4))
    manual_edge = repository.create_edge(EdgeInput(source_id=first.id, target_id=second.id))
    node = imported_node("switch:192.168.1.2", "Imported", "192.168.1.2")
    repository.replace_lantopolog(
        topology(
            nodes=(node,),
            interfaces=(imported_interface("port:1", node.key),),
        ),
        snapshot_hash="first-hash",
    )

    status = repository.replace_lantopolog(
        topology(nodes=(), interfaces=(), files_used=(), summary={"switches": 0}),
        snapshot_hash="empty-hash",
    )
    snapshot = repository.topology_snapshot()

    assert {item.id for item in snapshot["nodes"]} == {first.id, second.id}
    assert [item.id for item in snapshot["edges"]] == [manual_edge.id]
    assert snapshot["interfaces"] == []
    assert status["files_used"] == 0
    assert status["file_names"] == []
    assert snapshot["import_status"] == status
    assert repository.imported_snapshot_hash() == "empty-hash"


def test_reimport_drops_stale_interfaces_and_ignores_orphan_interface_rows(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    old = imported_node("switch:old", "Old", "192.168.1.2")
    kept = imported_node("switch:kept", "Kept", "192.168.1.3")
    repository.replace_lantopolog(
        topology(
            nodes=(old, kept),
            interfaces=(
                imported_interface("old:port:1", old.key),
                imported_interface("kept:port:1", kept.key),
            ),
        )
    )

    repository.replace_lantopolog(
        topology(
            nodes=(kept,),
            interfaces=(
                imported_interface("kept:port:2", kept.key, speed="not-a-number"),
                imported_interface("orphan:port:1", "switch:missing"),
            ),
            files_used=("port_list.csv",),
        )
    )
    snapshot = repository.topology_snapshot()

    assert {item.name for item in snapshot["nodes"]} == {"Kept"}
    assert len(snapshot["interfaces"]) == 1
    assert snapshot["interfaces"][0]["id"]
    assert snapshot["interfaces"][0]["speed_bps"] is None
    assert snapshot["interfaces"][0]["metadata_json"] == '{"Role": "uplink"}'
    assert snapshot["import_status"]["file_names"] == ["port_list.csv"]
