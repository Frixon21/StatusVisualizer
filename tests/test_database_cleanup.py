from __future__ import annotations

import sqlite3

from app.database import Repository
from app.models import DeviceInput

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
