from __future__ import annotations

import sqlite3

import pytest

from app.database import Repository
from app.models import DeviceInput, DevicePosition, EdgeInput

OBSOLETE_COLUMNS = {
    "devices": {
        "enabled",
        "ping_enabled",
        "ports_json",
        "source_device_id",
        "source_received_at",
        "discovery_state",
        "last_seen_at",
        "discovery_misses",
        "topology_visible",
    },
    "topology_edges": {"locked", "confidence"},
    "interfaces": {
        "bridge_port",
        "mac_address",
        "lag_id",
        "is_uplink",
        "source",
        "confidence",
        "first_seen_at",
        "last_seen_at",
    },
}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_legacy_columns(database_path) -> None:
    definitions = {
        "devices": {
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "ping_enabled": "INTEGER NOT NULL DEFAULT 0",
            "ports_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_device_id": "TEXT",
            "source_received_at": "TEXT",
            "discovery_state": "TEXT NOT NULL DEFAULT 'confirmed'",
            "last_seen_at": "TEXT",
            "discovery_misses": "INTEGER NOT NULL DEFAULT 0",
            "topology_visible": "INTEGER NOT NULL DEFAULT 1",
        },
        "topology_edges": {
            "locked": "INTEGER NOT NULL DEFAULT 1",
            "confidence": "REAL NOT NULL DEFAULT 1",
        },
        "interfaces": {
            "bridge_port": "INTEGER",
            "mac_address": "TEXT NOT NULL DEFAULT ''",
            "lag_id": "TEXT",
            "is_uplink": "INTEGER NOT NULL DEFAULT 0",
            "source": "TEXT NOT NULL DEFAULT 'lantopolog'",
            "confidence": "REAL NOT NULL DEFAULT 1",
            "first_seen_at": "TEXT NOT NULL DEFAULT ''",
            "last_seen_at": "TEXT NOT NULL DEFAULT ''",
        },
    }
    with sqlite3.connect(database_path) as connection:
        for table, columns in definitions.items():
            existing = _columns(connection, table)
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def test_initialization_removes_obsolete_monitoring_schema_and_keeps_current_data(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    saved = repository.create_device(
        DeviceInput(name="Current client", address="192.168.1.20", x=0.4, y=0.6)
    )
    obsolete = repository.create_device(
        DeviceInput(name="Old MQTT client", address="192.168.1.21", x=0.5, y=0.6)
    )
    _add_legacy_columns(repository.database_path)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE devices SET source = 'mqtt-api' WHERE id = ?", (obsolete.id,))

    repository.initialize()

    assert repository.get_device(saved.id) is not None
    assert repository.get_device(obsolete.id) is None
    with sqlite3.connect(repository.database_path) as connection:
        for table, obsolete_columns in OBSOLETE_COLUMNS.items():
            assert _columns(connection, table).isdisjoint(obsolete_columns)


def test_legacy_hidden_rows_stay_hidden_without_reusing_old_ping_flags(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    hidden = repository.create_device(
        DeviceInput(name="Hidden client", address="192.168.1.30", x=0.4, y=0.6)
    )
    disabled = repository.create_device(
        DeviceInput(name="Disabled client", address="192.168.1.31", x=0.5, y=0.6)
    )
    _add_legacy_columns(repository.database_path)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE devices SET topology_visible = 0 WHERE id = ?", (hidden.id,))
        connection.execute(
            "UPDATE devices SET enabled = 0, ping_enabled = 0 WHERE id = ?",
            (disabled.id,),
        )

    repository.initialize()

    assert repository.get_device(hidden.id) is None
    assert repository.get_device(disabled.id) is not None
    targets, _ = repository.list_liveness_targets(10)
    assert disabled.id in {device_id for device_id, _ in targets}


def test_initialization_repairs_blank_probe_addresses_from_the_broken_cleanup_release(
    tmp_path,
) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    saved = repository.create_device(
        DeviceInput(name="Migrated client", address="192.168.1.32", x=0.5, y=0.6)
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            UPDATE devices
            SET probe_address = '', liveness_state = 'online',
                liveness_checked_at = '2026-08-13T18:00:00Z'
            WHERE id = ?
            """,
            (saved.id,),
        )

    repository.initialize()

    targets, _ = repository.list_liveness_targets(10)
    assert targets == [(saved.id, "192.168.1.32")]
    repaired = repository.get_device(saved.id)
    assert repaired is not None
    assert repaired.liveness_state == "unknown"
    assert repaired.liveness_checked_at is None


@pytest.mark.parametrize(
    "stored_value",
    [
        "{not-json",
        "null",
        "[]",
        '{"positions": {}, "saved_at": "2026-09-30T12:00:00Z"}',
        '{"positions": [], "saved_at": 123}',
        '{"positions": []}',
    ],
)
def test_corrupt_custom_layout_metadata_is_treated_as_absent(tmp_path, stored_value) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO app_metadata (key, value_json) VALUES (?, ?)",
            ("topology_custom_layout", stored_value),
        )

    assert repository.get_custom_layout() == {
        "exists": False,
        "positions": [],
        "saved_at": None,
    }


def test_custom_layout_round_trips_positions_and_timestamp(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    positions = [DevicePosition(id="device-1", x=-10, y=101)]

    saved = repository.save_custom_layout(positions)

    assert saved["exists"] is True
    assert saved["positions"] == [{"id": "device-1", "x": -10.0, "y": 101.0}]
    assert saved["saved_at"].endswith("Z")
    assert repository.get_custom_layout() == saved


def test_create_edge_rejects_missing_self_and_duplicate_endpoints(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    first = repository.create_device(DeviceInput(name="A", x=0.1, y=0.2))
    second = repository.create_device(DeviceInput(name="B", x=0.3, y=0.4))

    with pytest.raises(ValueError, match="endpoints must exist"):
        repository.create_edge(EdgeInput(source_id=first.id, target_id="missing"))
    with pytest.raises(ValueError, match="different nodes"):
        EdgeInput(source_id=first.id, target_id=first.id)

    created = repository.create_edge(
        EdgeInput(source_id=second.id, target_id=first.id, label="uplink")
    )
    with pytest.raises(ValueError, match="already connected"):
        repository.create_edge(EdgeInput(source_id=first.id, target_id=second.id))

    assert (created.source_id, created.target_id) == tuple(sorted((first.id, second.id)))
    assert repository.delete_edge("missing") is False
    assert repository.delete_edge(created.id) is True


@pytest.mark.parametrize("stored_value", ["{broken", "123", "null", '{"hash":"value"}'])
def test_invalid_imported_snapshot_metadata_returns_empty_string(tmp_path, stored_value) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO app_metadata (key, value_json) VALUES (?, ?)",
            ("mqtt_snapshot_hash", stored_value),
        )

    assert repository.imported_snapshot_hash() == ""


def test_topology_snapshot_decodes_import_and_vlan_metadata(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO lantopolog_vlans
                (id, vlan_id, name, switch_key, tagged_ports, untagged_ports, metadata_json)
            VALUES ('vlan-1', '20', 'Voice', 'switch:1', '1', '2', '{"source":"csv"}')
            """
        )
        connection.execute(
            "INSERT INTO app_metadata (key, value_json) VALUES (?, ?)",
            ("lantopolog_import", '{"files_used":2,"file_names":["a.csv","b.csv"]}'),
        )

    result = repository.topology_snapshot()

    assert result["vlans"][0]["metadata"] == {"source": "csv"}
    assert result["import_status"] == {
        "files_used": 2,
        "file_names": ["a.csv", "b.csv"],
    }
