from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.lantopolog_store import replace_lantopolog_data
from app.models import (
    DeviceInput,
    DevicePosition,
    DeviceRecord,
    EdgeInput,
    EdgeRecord,
    LivenessStatus,
    utc_now,
)
from app.network import canonical_probe_address

if TYPE_CHECKING:
    from app.lantopolog import ImportedTopology
    from app.liveness import LivenessUpdate
_OBSOLETE_COLUMNS = {
    "devices": (
        "enabled", "ping_enabled", "ports_json", "source_device_id",
        "source_received_at", "discovery_state", "last_seen_at",
        "discovery_misses", "topology_visible",
    ),
    "topology_edges": ("locked", "confidence"),
    "interfaces": (
        "bridge_port", "mac_address", "lag_id", "is_uplink", "source",
        "confidence", "first_seen_at", "last_seen_at",
    ),
}


def _normalize_mac(value: str) -> str:
    compact = "".join(character for character in value.upper() if character in "0123456789ABCDEF")
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2)) if len(compact) == 12 else ""


def _metadata_json(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, separators=(",", ":"), sort_keys=True)


class Repository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._session() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    address TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    source TEXT NOT NULL DEFAULT 'local',
                    external_id TEXT,
                    node_type TEXT NOT NULL DEFAULT 'unknown',
                    icon_type TEXT NOT NULL DEFAULT 'auto',
                    node_shape TEXT NOT NULL DEFAULT 'icon',
                    mac_address TEXT NOT NULL DEFAULT '',
                    locked INTEGER NOT NULL DEFAULT 1,
                    probe_address TEXT NOT NULL DEFAULT '',
                    liveness_state TEXT NOT NULL DEFAULT 'unknown',
                    liveness_checked_at TEXT,
                    liveness_latency_ms REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_devices_source_external
                    ON devices(source, external_id);

                CREATE TABLE IF NOT EXISTS topology_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                    target_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                    label TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'manual',
                    evidence TEXT NOT NULL DEFAULT '',
                    source_interface TEXT NOT NULL DEFAULT '',
                    target_interface TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interfaces (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                    if_index INTEGER,
                    name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    speed_bps INTEGER,
                    admin_status TEXT,
                    oper_status TEXT,
                    vlan TEXT NOT NULL DEFAULT '',
                    is_trunk INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(device_id, if_index, name)
                );

                CREATE TABLE IF NOT EXISTS lantopolog_vlans (
                    id TEXT PRIMARY KEY,
                    vlan_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    switch_key TEXT NOT NULL,
                    tagged_ports TEXT NOT NULL DEFAULT '',
                    untagged_ports TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                """
            )
            self._ensure_columns(connection)
            self._remove_legacy_edge_uniqueness(connection)
            self._migrate_legacy_device_state(connection)
            self._initialize_probe_addresses(connection)
            self._drop_obsolete_columns(connection)

    @staticmethod
    def _ensure_columns(connection: sqlite3.Connection) -> None:
        additions = {
            "devices": {
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                "external_id": "TEXT",
                "source": "TEXT NOT NULL DEFAULT 'local'",
                "icon_type": "TEXT NOT NULL DEFAULT 'auto'",
                "node_shape": "TEXT NOT NULL DEFAULT 'icon'",
                "probe_address": "TEXT",
                "liveness_state": "TEXT NOT NULL DEFAULT 'unknown'",
                "liveness_checked_at": "TEXT",
                "liveness_latency_ms": "REAL",
            },
            "topology_edges": {
                "source_interface": "TEXT NOT NULL DEFAULT ''",
                "target_interface": "TEXT NOT NULL DEFAULT ''",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "interfaces": {"metadata_json": "TEXT NOT NULL DEFAULT '{}'"},
        }
        for table, columns in additions.items():
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @staticmethod
    def _initialize_probe_addresses(connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT id, address, probe_address FROM devices").fetchall()
        repairs = [
            (canonical_probe_address(row["address"]) or "", row["id"])
            for row in rows
            if (canonical_probe_address(row["address"]) or "") != (row["probe_address"] or "")
        ]
        connection.executemany(
            """
            UPDATE devices
            SET probe_address = ?, liveness_state = 'unknown',
                liveness_checked_at = NULL, liveness_latency_ms = NULL
            WHERE id = ?
            """,
            repairs,
        )

    @staticmethod
    def _migrate_legacy_device_state(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(devices)")}
        connection.execute("DELETE FROM devices WHERE source IN ('discovery', 'mqtt-api')")
        if "topology_visible" in columns:
            connection.execute("DELETE FROM devices WHERE topology_visible = 0")

    @staticmethod
    def _drop_obsolete_columns(connection: sqlite3.Connection) -> None:
        for table, obsolete_columns in _OBSOLETE_COLUMNS.items():
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column in obsolete_columns:
                if column in existing:
                    connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    @staticmethod
    def _remove_legacy_edge_uniqueness(connection: sqlite3.Connection) -> None:
        table_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'topology_edges'"
        ).fetchone()
        compact_sql = "".join(str(table_row["sql"] if table_row else "").lower().split())
        if "unique(source_id,target_id" not in compact_sql:
            return
        connection.executescript(
            """
            CREATE TABLE topology_edges_rebuilt (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                target_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                label TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'manual',
                evidence TEXT NOT NULL DEFAULT '',
                source_interface TEXT NOT NULL DEFAULT '',
                target_interface TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO topology_edges_rebuilt
                (id, source_id, target_id, label, kind, evidence,
                 source_interface, target_interface, metadata_json, created_at, updated_at)
            SELECT id, source_id, target_id, label, kind, evidence,
                   source_interface, target_interface, metadata_json, created_at, updated_at
            FROM topology_edges;
            DROP TABLE topology_edges;
            ALTER TABLE topology_edges_rebuilt RENAME TO topology_edges;
            """
        )

    @staticmethod
    def _device(row: sqlite3.Row) -> DeviceRecord:
        return DeviceRecord(
            id=row["id"], name=row["name"], address=row["address"], notes=row["notes"],
            x=row["x"], y=row["y"], node_type=row["node_type"],
            icon_type=row["icon_type"], node_shape=row["node_shape"],
            mac_address=row["mac_address"], locked=bool(row["locked"]), source=row["source"],
            metadata=json.loads(row["metadata_json"] or "{}"), created_at=row["created_at"],
            updated_at=row["updated_at"],
            liveness_state=row["liveness_state"],
            liveness_checked_at=row["liveness_checked_at"],
            liveness_latency_ms=row["liveness_latency_ms"],
        )

    @staticmethod
    def _edge(row: sqlite3.Row) -> EdgeRecord:
        return EdgeRecord(
            id=row["id"], source_id=row["source_id"], target_id=row["target_id"],
            label=row["label"], kind=row["kind"], source_interface=row["source_interface"],
            target_interface=row["target_interface"], evidence=row["evidence"],
            metadata=json.loads(row["metadata_json"] or "{}"), created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_devices(self) -> list[DeviceRecord]:
        with self._lock, self._session() as connection:
            rows = connection.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE").fetchall()
        return [self._device(row) for row in rows]

    def get_device(self, device_id: str) -> DeviceRecord | None:
        with self._lock, self._session() as connection:
            row = connection.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return self._device(row) if row else None

    def create_device(self, payload: DeviceInput) -> DeviceRecord:
        device_id, now = str(uuid.uuid4()), utc_now()
        with self._lock, self._session() as connection:
            connection.execute(
                """
                INSERT INTO devices
                    (id, name, address, notes, x, y, source, node_type, icon_type,
                     node_shape, mac_address, locked, probe_address, metadata_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, payload.name, payload.address, payload.notes, payload.x, payload.y,
                 payload.node_type, payload.icon_type, payload.node_shape,
                 _normalize_mac(payload.mac_address), int(payload.locked),
                 canonical_probe_address(payload.address) or "", _metadata_json(payload.metadata), now, now),
            )
        result = self.get_device(device_id)
        assert result is not None
        return result

    def update_device(self, device_id: str, payload: DeviceInput) -> DeviceRecord | None:
        with self._lock, self._session() as connection:
            assignments = """
                    name = ?, address = ?, notes = ?, x = ?, y = ?,
                    node_type = ?, icon_type = ?, node_shape = ?, mac_address = ?, locked = ?,
                    probe_address = ?,
                    liveness_state = CASE WHEN address != ? THEN 'unknown' ELSE liveness_state END,
                    liveness_checked_at = CASE WHEN address != ? THEN NULL ELSE liveness_checked_at END,
                    liveness_latency_ms = CASE WHEN address != ? THEN NULL ELSE liveness_latency_ms END,
                    updated_at = ?
            """
            values: list[Any] = [
                payload.name, payload.address, payload.notes, payload.x, payload.y,
                payload.node_type, payload.icon_type, payload.node_shape,
                _normalize_mac(payload.mac_address), int(payload.locked),
                canonical_probe_address(payload.address) or "",
                payload.address, payload.address, payload.address,
                utc_now(),
            ]
            if "metadata" in payload.model_fields_set:
                assignments += (
                    ", metadata_json = CASE WHEN source IN ('local', 'manual') "
                    "THEN ? ELSE metadata_json END"
                )
                values.append(_metadata_json(payload.metadata))
            values.append(device_id)
            cursor = connection.execute(
                f"""
                UPDATE devices SET {assignments}
                WHERE id = ?
                """,
                values,
            )
        return self.get_device(device_id) if cursor.rowcount else None

    def update_device_positions(self, positions: list[DevicePosition]) -> list[DeviceRecord]:
        ids = tuple(position.id for position in positions)
        placeholders = ",".join("?" for _ in ids)
        now = utc_now()
        with self._lock, self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"SELECT id FROM devices WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            if len(existing) != len(ids):
                raise ValueError("Device not found")
            connection.executemany(
                "UPDATE devices SET x = ?, y = ?, locked = 1, updated_at = ? WHERE id = ?",
                [(position.x, position.y, now, position.id) for position in positions],
            )
            updated_rows = connection.execute(
                f"SELECT * FROM devices WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        updated_by_id = {row["id"]: self._device(row) for row in updated_rows}
        return [updated_by_id[device_id] for device_id in ids]

    def get_custom_layout(self) -> dict[str, Any]:
        with self._lock, self._session() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_metadata WHERE key = 'topology_custom_layout'"
            ).fetchone()
        if row is None:
            return {"exists": False, "positions": [], "saved_at": None}
        try:
            value = json.loads(row["value_json"])
            positions = value["positions"]
            saved_at = value["saved_at"]
            if not isinstance(positions, list) or not isinstance(saved_at, str):
                raise TypeError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {"exists": False, "positions": [], "saved_at": None}
        return {"exists": True, "positions": positions, "saved_at": saved_at}

    def save_custom_layout(self, positions: list[DevicePosition]) -> dict[str, Any]:
        payload = {
            "positions": [position.model_dump(mode="json") for position in positions],
            "saved_at": utc_now(),
        }
        with self._lock, self._session() as connection:
            connection.execute(
                """
                INSERT INTO app_metadata (key, value_json)
                VALUES ('topology_custom_layout', ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (json.dumps(payload, separators=(",", ":")),),
            )
        return {"exists": True, **payload}

    def delete_all_devices(self) -> int:
        with self._lock, self._session() as connection:
            cursor = connection.execute("DELETE FROM devices")
        return int(cursor.rowcount)

    def delete_device(self, device_id: str) -> bool:
        with self._lock, self._session() as connection:
            cursor = connection.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        return bool(cursor.rowcount)

    def list_edges(self) -> list[EdgeRecord]:
        with self._lock, self._session() as connection:
            rows = connection.execute("SELECT * FROM topology_edges ORDER BY created_at, id").fetchall()
        return [self._edge(row) for row in rows]

    def create_edge(self, payload: EdgeInput) -> EdgeRecord:
        source_id, target_id = sorted((payload.source_id, payload.target_id))
        edge_id, now = str(uuid.uuid4()), utc_now()
        with self._lock, self._session() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM devices WHERE id IN (?, ?)",
                (source_id, target_id),
            ).fetchone()[0]
            if count != 2:
                raise ValueError("Both connection endpoints must exist")
            duplicate = connection.execute(
                """
                SELECT 1 FROM topology_edges
                WHERE kind = 'manual' AND source_id = ? AND target_id = ? LIMIT 1
                """,
                (source_id, target_id),
            ).fetchone()
            if duplicate:
                raise ValueError("These nodes are already connected")
            try:
                connection.execute(
                    """
                    INSERT INTO topology_edges
                        (id, source_id, target_id, label, kind, evidence,
                         source_interface, target_interface, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'manual', 'User-created connection', '', '', '{}', ?, ?)
                    """,
                    (edge_id, source_id, target_id, payload.label, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("These nodes are already connected") from error
        return next(edge for edge in self.list_edges() if edge.id == edge_id)

    def delete_edge(self, edge_id: str) -> bool:
        with self._lock, self._session() as connection:
            cursor = connection.execute("DELETE FROM topology_edges WHERE id = ?", (edge_id,))
        return bool(cursor.rowcount)

    def list_liveness_targets(self, limit: int) -> tuple[list[tuple[str, str]], int]:
        bounded_limit = max(1, min(2048, limit))
        with self._lock, self._session() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM devices"
            ).fetchone()[0]
            rows = connection.execute(
                """
                SELECT id, address FROM devices
                WHERE probe_address != '' ORDER BY id LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [(str(row["id"]), str(row["address"] or "")) for row in rows], int(total)

    def update_liveness(self, updates: tuple[LivenessUpdate, ...]) -> None:
        if not updates:
            return
        with self._lock, self._session() as connection:
            connection.executemany(
                """
                UPDATE devices
                SET liveness_state = ?, liveness_checked_at = ?, liveness_latency_ms = ?
                WHERE id = ? AND address = ?
                """,
                [
                    (
                        update.state,
                        update.checked_at,
                        update.latency_ms,
                        update.device_id,
                        update.expected_address,
                    )
                    for update in updates
                ],
            )

    def list_liveness_statuses(self) -> list[LivenessStatus]:
        with self._lock, self._session() as connection:
            rows = connection.execute(
                """
                SELECT id, address, liveness_state, liveness_checked_at, liveness_latency_ms
                FROM devices ORDER BY name COLLATE NOCASE, id
                """
            ).fetchall()
        return [
            LivenessStatus(
                device_id=row["id"],
                address=row["address"],
                state=row["liveness_state"],
                checked_at=row["liveness_checked_at"],
                latency_ms=row["liveness_latency_ms"],
            )
            for row in rows
        ]

    def imported_device_ids(self) -> dict[str, str]:
        with self._lock, self._session() as connection:
            rows = connection.execute(
                """
                SELECT external_id, id FROM devices
                WHERE source = 'lantopolog' AND external_id IS NOT NULL AND external_id != ''
                """
            ).fetchall()
        return {row["external_id"]: row["id"] for row in rows}

    def topology_snapshot(self) -> dict[str, Any]:
        with self._lock, self._session() as connection:
            devices = connection.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE").fetchall()
            edges = connection.execute(
                "SELECT * FROM topology_edges ORDER BY created_at, id"
            ).fetchall()
            interfaces = connection.execute(
                """
                SELECT i.* FROM interfaces i JOIN devices d ON d.id = i.device_id
                ORDER BY d.name, i.name
                """
            ).fetchall()
            vlans = connection.execute(
                "SELECT vlan_id, name, switch_key, tagged_ports, untagged_ports, metadata_json "
                "FROM lantopolog_vlans ORDER BY CAST(vlan_id AS INTEGER), name"
            ).fetchall()
            status = connection.execute(
                "SELECT value_json FROM app_metadata WHERE key = 'lantopolog_import'"
            ).fetchone()
        return {
            "nodes": [self._device(row) for row in devices],
            "edges": [self._edge(row) for row in edges],
            "interfaces": [dict(row) for row in interfaces],
            "vlans": [
                {**dict(row), "metadata": json.loads(row["metadata_json"] or "{}")} for row in vlans
            ],
            "import_status": json.loads(status["value_json"]) if status else {},
        }

    def replace_lantopolog(
        self,
        topology: ImportedTopology,
        *,
        snapshot_hash: str | None = None,
    ) -> dict[str, object]:
        with self._lock, self._session() as connection:
            result = replace_lantopolog_data(connection, topology)
            if snapshot_hash is not None:
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value_json)
                    VALUES ('mqtt_snapshot_hash', ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                    """,
                    (json.dumps(snapshot_hash),),
                )
            return result

    def imported_snapshot_hash(self) -> str:
        with self._lock, self._session() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_metadata WHERE key = 'mqtt_snapshot_hash'"
            ).fetchone()
        if not row:
            return ""
        try:
            value = json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError):
            return ""
        return value if isinstance(value, str) else ""

