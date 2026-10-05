from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.database import Repository
from app.lantopolog import ExportValidationError, parse_lantopolog_export
from app.models import utc_now
from app.mqtt_snapshot import (
    SnapshotPackage,
    SnapshotValidationError,
    read_snapshot_zip,
)

LOCAL_SITE_ID = "local"
_MAX_DISPLAY_NAME = 100
_MAX_ERROR = 500


class SiteRegistryError(ValueError):
    """Raised when a registry operation is invalid."""


class ClientState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ClientRecord:
    client_id: str
    display_name: str
    state: ClientState
    first_seen_at: str
    last_seen_at: str
    snapshot_hash: str
    last_snapshot_hash: str
    last_imported_at: str | None
    last_error: str


@dataclass(frozen=True, slots=True)
class SiteRecord:
    site_id: str
    display_name: str
    is_local: bool
    last_imported_at: str | None = None
    last_snapshot_hash: str = ""


@dataclass(frozen=True, slots=True)
class IngestResult:
    client: ClientRecord
    changed: bool
    imported: bool


def _client_id(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise SiteRegistryError("Client ID must be a canonical UUID v4.") from error
    if parsed.version != 4 or str(parsed) != str(value):
        raise SiteRegistryError("Client ID must be a canonical UUID v4.")
    return str(parsed)


def _safe_error(error: Exception) -> str:
    # Validation/import messages contain file names at most, never payload contents.
    message = " ".join(str(error).split()) or type(error).__name__
    return message[:_MAX_ERROR]


class SiteImportService:
    """Imports a validated package with the existing parser in one site transaction."""

    def import_package(self, repository: Repository, package: SnapshotPackage) -> dict[str, object]:
        topology = parse_lantopolog_export(package.files)
        return repository.replace_lantopolog(
            topology,
            snapshot_hash=package.snapshot_hash,
        )

    def imported_hash(self, repository: Repository) -> str:
        return repository.imported_snapshot_hash()


class SiteRegistry:
    def __init__(self, data_dir: str | Path, importer: SiteImportService | None = None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.database_path = self.data_dir / "mqtt_registry.db"
        self.sites_dir = self.data_dir / "sites"
        self._lock = threading.RLock()
        self._repositories: dict[Path, Repository] = {}
        self._importer = importer or SiteImportService()
        self.initialize()
        self._reconcile()

    @contextmanager
    def _session(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sites_dir.mkdir(parents=True, exist_ok=True)
        with self._lock, self._session() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mqtt_clients (
                    client_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'blocked')),
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL DEFAULT '',
                    last_snapshot_hash TEXT NOT NULL DEFAULT '',
                    last_imported_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    snapshot_zip BLOB
                );
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> ClientRecord:
        return ClientRecord(
            client_id=row["client_id"],
            display_name=row["display_name"],
            state=ClientState(row["state"]),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            snapshot_hash=row["snapshot_hash"],
            last_snapshot_hash=row["last_snapshot_hash"],
            last_imported_at=row["last_imported_at"],
            last_error=row["last_error"],
        )

    def list_clients(self) -> tuple[ClientRecord, ...]:
        with self._lock, self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM mqtt_clients ORDER BY first_seen_at, client_id"
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def get_client(self, client_id: str) -> ClientRecord:
        canonical_id = _client_id(client_id)
        with self._lock, self._session() as connection:
            row = connection.execute(
                "SELECT * FROM mqtt_clients WHERE client_id = ?", (canonical_id,)
            ).fetchone()
        if row is None:
            raise SiteRegistryError("MQTT client was not found.")
        return self._record(row)

    def list_sites(self) -> tuple[SiteRecord, ...]:
        sites = [SiteRecord(site_id=LOCAL_SITE_ID, display_name="Local", is_local=True)]
        sites.extend(
            SiteRecord(
                site_id=client.client_id,
                display_name=client.display_name,
                is_local=False,
                last_imported_at=client.last_imported_at,
                last_snapshot_hash=client.last_snapshot_hash,
            )
            for client in self.list_clients()
            if client.state is ClientState.APPROVED
        )
        return tuple(sites)

    def repository_for(self, site_id: str) -> Repository:
        if site_id == LOCAL_SITE_ID:
            database_path = self.data_dir / "status.db"
        else:
            client = self.get_client(site_id)
            if client.state is not ClientState.APPROVED:
                raise SiteRegistryError("The MQTT client is not approved.")
            database_path = self.sites_dir / client.client_id / "status.db"
        with self._lock:
            repository = self._repositories.get(database_path)
            if repository is None:
                repository = Repository(database_path)
                repository.initialize()
                self._repositories[database_path] = repository
            return repository

    def ingest(self, client_id: str, payload: bytes) -> IngestResult:
        canonical_id = _client_id(client_id)
        try:
            package = read_snapshot_zip(payload, canonical_id)
        except SnapshotValidationError as error:
            self._record_error(canonical_id, error)
            raise SiteRegistryError(_safe_error(error)) from error
        now = utc_now()
        with self._lock:
            with self._session() as connection:
                existing = connection.execute(
                    "SELECT * FROM mqtt_clients WHERE client_id = ?", (canonical_id,)
                ).fetchone()
                changed = existing is None or existing["snapshot_hash"] != package.snapshot_hash
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO mqtt_clients
                            (client_id, state, first_seen_at, last_seen_at, snapshot_hash,
                             last_snapshot_hash, last_error, snapshot_zip)
                        VALUES (?, 'pending', ?, ?, ?, '', '', ?)
                        """,
                        (canonical_id, now, now, package.snapshot_hash, bytes(payload)),
                    )
                elif changed:
                    connection.execute(
                        """
                        UPDATE mqtt_clients SET last_seen_at = ?, snapshot_hash = ?,
                            snapshot_zip = ?, last_error = '' WHERE client_id = ?
                        """,
                        (now, package.snapshot_hash, bytes(payload), canonical_id),
                    )
                else:
                    connection.execute(
                        "UPDATE mqtt_clients SET last_seen_at = ?, last_error = '' WHERE client_id = ?",
                        (now, canonical_id),
                    )
            imported = False
            client = self.get_client(canonical_id)
            if (
                client.state is ClientState.APPROVED
                and client.snapshot_hash != client.last_snapshot_hash
            ):
                imported = self._import_cached(canonical_id)
                client = self.get_client(canonical_id)
            return IngestResult(client=client, changed=changed, imported=imported)

    def patch_client(
        self,
        client_id: str,
        *,
        display_name: str | None = None,
        state: str | ClientState | None = None,
    ) -> ClientRecord:
        canonical_id = _client_id(client_id)
        with self._lock:
            current = self.get_client(canonical_id)
            name = current.display_name if display_name is None else display_name.strip()
            if len(name) > _MAX_DISPLAY_NAME:
                raise SiteRegistryError("Display name is too long.")
            try:
                target_state = current.state if state is None else ClientState(state)
            except ValueError as error:
                raise SiteRegistryError("Client state is invalid.") from error
            if target_state is ClientState.APPROVED and not name:
                raise SiteRegistryError("A friendly display name is required before approval.")
            with self._session() as connection:
                connection.execute(
                    "UPDATE mqtt_clients SET display_name = ?, state = ?, last_error = '' WHERE client_id = ?",
                    (name, target_state.value, canonical_id),
                )
            if target_state is ClientState.APPROVED:
                refreshed = self.get_client(canonical_id)
                if refreshed.snapshot_hash != refreshed.last_snapshot_hash:
                    self._import_cached(canonical_id)
            return self.get_client(canonical_id)

    def _cached_payload(self, client_id: str) -> bytes:
        with self._session() as connection:
            row = connection.execute(
                "SELECT snapshot_zip FROM mqtt_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        if row is None or row["snapshot_zip"] is None:
            raise SiteRegistryError("No validated snapshot is cached for this client.")
        return bytes(row["snapshot_zip"])

    def _import_cached(self, client_id: str) -> bool:
        with self._lock:
            try:
                package = read_snapshot_zip(self._cached_payload(client_id), client_id)
                repository = self.repository_for(client_id)
                self._importer.import_package(repository, package)
            except (SnapshotValidationError, ExportValidationError, OSError, sqlite3.Error, SiteRegistryError) as error:
                self._record_error(client_id, error)
                return False
            imported_at = utc_now()
            with self._session() as connection:
                connection.execute(
                    """
                    UPDATE mqtt_clients SET last_snapshot_hash = ?, last_imported_at = ?, last_error = ''
                    WHERE client_id = ?
                    """,
                    (package.snapshot_hash, imported_at, client_id),
                )
            return True

    def _record_error(self, client_id: str, error: Exception) -> None:
        with self._lock, self._session() as connection:
            connection.execute(
                "UPDATE mqtt_clients SET last_error = ? WHERE client_id = ?",
                (_safe_error(error), client_id),
            )

    def _reconcile(self) -> None:
        for client in self.list_clients():
            if client.state is not ClientState.APPROVED:
                continue
            repository = self.repository_for(client.client_id)
            imported_hash = self._importer.imported_hash(repository)
            if imported_hash and imported_hash != client.last_snapshot_hash:
                with self._lock, self._session() as connection:
                    connection.execute(
                        "UPDATE mqtt_clients SET last_snapshot_hash = ? WHERE client_id = ?",
                        (imported_hash, client.client_id),
                    )
            current = self.get_client(client.client_id)
            if current.snapshot_hash and current.snapshot_hash != imported_hash:
                self._import_cached(client.client_id)
