from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.mqtt_snapshot import build_snapshot_zip
from app.site_registry import ClientState, SiteRegistry, SiteRegistryError
from tests.lantopolog_fixture import export_files


def _payload(client_id: str) -> bytes:
    files = {name: content.encode() for name, content in export_files().items()}
    return build_snapshot_zip(files, client_id)


def test_unknown_client_is_cached_as_pending_and_survives_restart(tmp_path: Path) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)

    result = registry.ingest(client_id, _payload(client_id))
    restarted = SiteRegistry(tmp_path)
    client = restarted.get_client(client_id)

    assert result.changed is True
    assert result.imported is False
    assert client.state is ClientState.PENDING
    assert client.snapshot_hash
    assert client.last_seen_at >= client.first_seen_at
    assert client.last_error == ""


def test_approval_imports_cached_snapshot_and_duplicates_are_ignored(tmp_path: Path) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)
    payload = _payload(client_id)
    registry.ingest(client_id, payload)

    approved = registry.patch_client(client_id, display_name="Branch One", state="approved")
    duplicate = registry.ingest(client_id, payload)

    assert approved.state is ClientState.APPROVED
    assert approved.last_imported_at is not None
    assert approved.last_snapshot_hash == approved.snapshot_hash
    assert duplicate.changed is False
    assert duplicate.imported is False
    assert registry.repository_for(client_id).topology_snapshot()["nodes"]
    assert registry.list_sites()[1].display_name == "Branch One"


def test_blocked_client_does_not_import_new_snapshot(tmp_path: Path) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)
    registry.ingest(client_id, _payload(client_id))
    registry.patch_client(client_id, display_name="Branch", state="approved")
    registry.patch_client(client_id, state="blocked")
    changed_files = {name: content.encode() for name, content in export_files().items()}
    changed_files["sw_list.csv"] += b"\n"

    result = registry.ingest(client_id, build_snapshot_zip(changed_files, client_id))
    client = registry.get_client(client_id)

    assert result.changed is True
    assert result.imported is False
    assert client.snapshot_hash != client.last_snapshot_hash


def test_registry_rejects_invalid_ids_names_and_unapproved_repository(tmp_path: Path) -> None:
    registry = SiteRegistry(tmp_path)
    client_id = str(uuid.uuid4())
    registry.ingest(client_id, _payload(client_id))

    with pytest.raises(SiteRegistryError):
        registry.patch_client(client_id, display_name="   ", state="approved")
    with pytest.raises(SiteRegistryError):
        registry.repository_for(client_id)
    with pytest.raises(SiteRegistryError):
        registry.ingest("not-a-uuid", b"bad")


def test_two_approved_clients_use_separate_databases(tmp_path: Path) -> None:
    registry = SiteRegistry(tmp_path)
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    for client_id, name in ((first, "First"), (second, "Second")):
        registry.ingest(client_id, _payload(client_id))
        registry.patch_client(client_id, display_name=name, state="approved")

    assert registry.repository_for(first).database_path != registry.repository_for(second).database_path
    assert registry.repository_for(first) is registry.repository_for(first)
    assert registry.repository_for(first).topology_snapshot()["nodes"]
    assert registry.repository_for(second).topology_snapshot()["nodes"]


def test_failed_import_remains_retryable_on_duplicate_delivery(tmp_path: Path, monkeypatch) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)
    registry.ingest(client_id, _payload(client_id))
    calls = 0
    original = registry._importer.import_package

    def fail_once(repository, package):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary import failure")
        return original(repository, package)

    monkeypatch.setattr(registry._importer, "import_package", fail_once)
    first = registry.patch_client(client_id, display_name="Retry", state="approved")
    retried = registry.ingest(client_id, _payload(client_id))

    assert first.last_snapshot_hash == ""
    assert first.last_error == "temporary import failure"
    assert retried.changed is False
    assert retried.imported is True
    assert retried.client.last_snapshot_hash == retried.client.snapshot_hash


def test_invalid_snapshot_does_not_replace_cached_payload_or_topology(tmp_path: Path) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)
    good = _payload(client_id)
    registry.ingest(client_id, good)
    registry.patch_client(client_id, display_name="Branch", state="approved")
    before = registry.get_client(client_id)
    topology_before = registry.repository_for(client_id).topology_snapshot()

    with pytest.raises(SiteRegistryError):
        registry.ingest(client_id, b"not-a-zip")

    after = registry.get_client(client_id)
    assert after.snapshot_hash == before.snapshot_hash
    assert after.last_snapshot_hash == before.last_snapshot_hash
    assert after.last_error
    assert registry.repository_for(client_id).topology_snapshot()["nodes"] == topology_before["nodes"]


def test_reapproval_imports_latest_cached_snapshot(tmp_path: Path) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)
    original = _payload(client_id)
    registry.ingest(client_id, original)
    registry.patch_client(client_id, display_name="Branch", state="approved")
    first_hash = registry.get_client(client_id).last_snapshot_hash
    registry.patch_client(client_id, state="blocked")

    changed = {name: content.encode() for name, content in export_files().items()}
    changed["sw_list.csv"] += b"\n"
    updated = build_snapshot_zip(changed, client_id)
    registry.ingest(client_id, updated)
    reapproved = registry.patch_client(client_id, display_name="Branch", state="approved")

    assert reapproved.state is ClientState.APPROVED
    assert reapproved.last_snapshot_hash != first_hash
    assert reapproved.last_snapshot_hash == reapproved.snapshot_hash
    assert registry.repository_for(client_id).topology_snapshot()["nodes"]


def test_restart_reconciles_approved_client_import_marker(tmp_path: Path) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)
    payload = _payload(client_id)
    registry.ingest(client_id, payload)
    registry.patch_client(client_id, display_name="Branch", state="approved")
    imported_hash = registry.get_client(client_id).last_snapshot_hash

    with registry._lock, registry._session() as connection:
        connection.execute(
            "UPDATE mqtt_clients SET last_snapshot_hash = '' WHERE client_id = ?",
            (client_id,),
        )

    restarted = SiteRegistry(tmp_path)
    client = restarted.get_client(client_id)
    assert client.last_snapshot_hash == imported_hash
    assert restarted.repository_for(client_id).topology_snapshot()["nodes"]


def test_registry_reports_unknown_clients_and_invalid_updates(tmp_path: Path) -> None:
    registry = SiteRegistry(tmp_path)
    client_id = str(uuid.uuid4())

    with pytest.raises(SiteRegistryError, match="not found"):
        registry.get_client(client_id)

    registry.ingest(client_id, _payload(client_id))
    with pytest.raises(SiteRegistryError, match="too long"):
        registry.patch_client(client_id, display_name="x" * 101)
    with pytest.raises(SiteRegistryError, match="state"):
        registry.patch_client(client_id, state="deleted")


def test_registry_reports_missing_cached_snapshot(tmp_path: Path) -> None:
    registry = SiteRegistry(tmp_path)
    client_id = str(uuid.uuid4())
    registry.ingest(client_id, _payload(client_id))
    with registry._session() as connection:
        connection.execute(
            "UPDATE mqtt_clients SET snapshot_zip = NULL WHERE client_id = ?",
            (client_id,),
        )

    with pytest.raises(SiteRegistryError, match="No validated snapshot"):
        registry._cached_payload(client_id)


def test_restart_reimports_when_cached_snapshot_is_ahead_of_repository(
    tmp_path: Path,
) -> None:
    client_id = str(uuid.uuid4())
    registry = SiteRegistry(tmp_path)
    registry.ingest(client_id, _payload(client_id))
    registry.patch_client(client_id, display_name="Branch", state="approved")
    with registry._session() as connection:
        connection.execute(
            "UPDATE mqtt_clients SET snapshot_hash = ?, last_snapshot_hash = ? "
            "WHERE client_id = ?",
            ("f" * 64, "f" * 64, client_id),
        )

    restarted = SiteRegistry(tmp_path)

    assert restarted.get_client(client_id).last_snapshot_hash != "f" * 64
