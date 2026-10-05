from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings
from app.main import create_app
from app.mqtt_snapshot import build_snapshot_zip
from tests.lantopolog_fixture import export_files


def _manual(name: str) -> dict:
    return {
        "name": name, "address": "", "notes": "", "x": 0.5, "y": 0.5,
        "node_type": "other", "icon_type": "auto", "node_shape": "icon",
        "mac_address": "", "locked": True,
    }


def test_sites_api_always_exposes_local_and_rejects_unknown_site(tmp_path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        sites = client.get("/api/sites").json()
        assert sites[0]["id"] == "local"
        assert sites[0]["display_name"] == "Local"
        assert sites[0]["kind"] == "local"
        assert client.get(
            "/api/topology", headers={"X-Status-Visualizer-Site": "missing"}
        ).status_code == 404


def test_site_header_is_applied_to_every_mutating_topology_route(tmp_path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        bad = {"X-Status-Visualizer-Site": "missing"}
        assert client.post("/api/devices", json=_manual("wrong"), headers=bad).status_code == 404
        assert client.delete("/api/devices", headers=bad).status_code == 404
        assert client.post(
            "/api/edges", json={"source_id": "a", "target_id": "b"}, headers=bad
        ).status_code == 404
        assert client.put(
            "/api/layouts/custom",
            json={"positions": [{"id": "a", "x": 0, "y": 0}]},
            headers=bad,
        ).status_code == 404

        created = client.post("/api/devices", json=_manual("local")).json()
        assert client.get("/api/devices").json()[0]["id"] == created["id"]


def test_remote_site_liveness_is_unavailable(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        client_id = "550e8400-e29b-41d4-a716-446655440000"
        files = {name: content.encode() for name, content in export_files().items()}
        app.state.site_registry.ingest(client_id, build_snapshot_zip(files, client_id))
        app.state.site_registry.patch_client(client_id, display_name="Remote", state="approved")
        headers = {"X-Status-Visualizer-Site": client_id}
        response = client.get("/api/liveness", headers=headers)
        assert response.status_code == 200
        assert response.json()["available"] is False
        check = client.post(
            "/api/liveness/check",
            content="{}",
            headers={**headers, "Content-Type": "application/json", "X-Status-Visualizer-Request": "1"},
        )
        assert check.status_code == 409


def test_remote_site_liveness_uses_latest_helper_status(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    client_id = "550e8400-e29b-41d4-a716-446655440000"
    files = {name: content.encode() for name, content in export_files().items()}
    with TestClient(app) as client:
        registry = app.state.site_registry
        registry.ingest(client_id, build_snapshot_zip(files, client_id))
        registry.patch_client(client_id, display_name="Remote", state="approved")
        repository = registry.repository_for(client_id)
        external_id, device_id = next(iter(repository.imported_device_ids().items()))
        windows = ("5m", "15m", "1h", "12h", "24h")
        payload = json.dumps({
            "version": 2,
            "client_id": client_id,
            "checked_at": datetime.now(UTC).isoformat(),
            "devices": [{
                "device_id": external_id, "online": True, "monitoring_state": "normal",
                "current_rtt_ms": 4.2, "last_rtt_ms": 4.2,
                "last_success_at": datetime.now(UTC).isoformat(),
                "loss": {key: 0.0 for key in windows},
                "rtt_avg_ms": {key: 4.2 for key in windows},
                "observed_seconds": {key: 120.0 for key in windows},
            }],
        }).encode()
        app.state.remote_status_store.ingest(client_id, payload)

        response = client.get(
            "/api/liveness", headers={"X-Status-Visualizer-Site": client_id}
        ).json()
        assert response["available"] is True
        assert response["stale"] is False
        assert next(item for item in response["devices"] if item["device_id"] == device_id) == {
            "device_id": device_id,
            "address": next(
                item.address for item in repository.list_liveness_statuses()
                if item.device_id == device_id
            ),
            "state": "online",
            "checked_at": response["checked_at"],
            "latency_ms": 4.2,
            "online": True,
            "monitoring_state": "normal",
            "current_rtt_ms": 4.2,
            "last_rtt_ms": 4.2,
            "last_success_at": next(item for item in response["devices"] if item["device_id"] == device_id)["last_success_at"],
            "loss": {key: 0.0 for key in windows},
            "rtt_avg_ms": {key: 4.2 for key in windows},
            "observed_seconds": {key: 120.0 for key in windows},
        }


def test_pending_client_can_be_approved_and_imported_from_cached_snapshot(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    client_id = "550e8400-e29b-41d4-a716-446655440000"
    files = {name: content.encode() for name, content in export_files().items()}
    with TestClient(app) as client:
        app.state.site_registry.ingest(client_id, build_snapshot_zip(files, client_id))
        pending = client.get("/api/mqtt/clients").json()["clients"]
        assert pending[0]["state"] == "pending"
        assert "snapshot_zip" not in pending[0]

        assert client.patch(
            f"/api/mqtt/clients/{client_id}",
            json={"display_name": "Branch office", "state": "approved"},
        ).status_code == 403
        approved = client.patch(
            f"/api/mqtt/clients/{client_id}",
            json={"display_name": "Branch office", "state": "approved"},
            headers={"X-Status-Visualizer-Request": "1"},
        )
        assert approved.status_code == 200
        assert approved.json()["last_imported_at"]

        remote = client.get(
            "/api/topology", headers={"X-Status-Visualizer-Site": client_id}
        ).json()
        assert remote["nodes"]
        assert client.get("/api/topology").json()["nodes"] == []


def test_blocked_client_keeps_existing_topology_and_does_not_import_updates(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    client_id = "550e8400-e29b-41d4-a716-446655440000"
    files = {name: content.encode() for name, content in export_files().items()}
    with TestClient(app) as client:
        registry = app.state.site_registry
        registry.ingest(client_id, build_snapshot_zip(files, client_id))
        registry.patch_client(client_id, display_name="Remote", state="approved")
        original = registry.get_client(client_id).last_snapshot_hash
        registry.patch_client(client_id, state="blocked")
        changed = {**files, "sw_list.csv": files["sw_list.csv"] + b"\n"}
        registry.ingest(client_id, build_snapshot_zip(changed, client_id))

        record = registry.get_client(client_id)
        assert record.snapshot_hash != original
        assert record.last_snapshot_hash == original
        assert client.get(
            "/api/topology", headers={"X-Status-Visualizer-Site": client_id}
        ).status_code == 404


def test_api_returns_specific_errors_for_missing_resources_and_bad_client_updates(
    tmp_path,
) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get("/api/devices/missing").status_code == 404
        assert client.put("/api/devices/missing", json=_manual("missing")).status_code == 404
        assert client.delete("/api/devices/missing").status_code == 404
        assert client.delete("/api/edges/missing").status_code == 404

        headers = {
            "Content-Type": "application/json",
            "X-Status-Visualizer-Request": "1",
        }
        assert client.patch(
            "/api/mqtt/clients/not-a-uuid",
            json={"display_name": "Branch"},
            headers=headers,
        ).status_code == 422
        assert client.patch(
            "/api/mqtt/clients/550e8400-e29b-41d4-a716-446655440000",
            json={"display_name": "Branch"},
            headers=headers,
        ).status_code == 404


def test_request_guards_and_safe_import_error(tmp_path, monkeypatch) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        invalid_length = client.post(
            "/api/devices",
            content="{}",
            headers={"Content-Type": "application/json", "Content-Length": "invalid"},
        )
        assert invalid_length.status_code == 400
        assert client.post(
            "/api/liveness/check",
            content="{}",
            headers={"Content-Type": "application/json"},
        ).status_code == 403

        def invalid_data(_files):
            raise UnicodeError(r"sensitive F:\export\name")

        monkeypatch.setattr(main_module, "parse_lantopolog_export", invalid_data)
        response = client.post(
            "/api/import/lantopolog",
            json={
                "files": [
                    {"path": "sw_list.csv", "name": "sw_list.csv", "content": "x"}
                ]
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "The export contains invalid data"


def test_local_liveness_read_and_manual_check(tmp_path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        current = client.get("/api/liveness")
        assert current.status_code == 200
        assert current.json()["available"] is True

        checked = client.post(
            "/api/liveness/check",
            json={},
            headers={"X-Status-Visualizer-Request": "1"},
        )
        assert checked.status_code == 200
        assert checked.json()["check"]["checked"] == 0


def test_mqtt_lifespan_starts_dispatches_and_stops_subscriber(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_id = "550e8400-e29b-41d4-a716-446655440000"
    files = {name: content.encode() for name, content in export_files().items()}
    payload = build_snapshot_zip(files, client_id)
    ingested = threading.Event()
    status_ingested = threading.Event()
    original_ingest = main_module.SiteRegistry.ingest
    original_status_ingest = main_module.RemoteStatusStore.ingest

    def recording_ingest(self, received_id, received_payload):
        result = original_ingest(self, received_id, received_payload)
        self.patch_client(received_id, display_name="Remote", state="approved")
        ingested.set()
        return result

    def recording_status_ingest(self, received_id, received_payload):
        result = original_status_ingest(self, received_id, received_payload)
        status_ingested.set()
        return result

    class FakeSubscriber:
        instances: ClassVar[list[FakeSubscriber]] = []

        def __init__(self, _config, on_snapshot, *, on_status=None):
            self.on_snapshot = on_snapshot
            self.on_status = on_status
            self.connected = True
            self.started = False
            self.stopped = False
            self.instances.append(self)

        def start(self):
            self.started = True
            self.on_snapshot((client_id, payload))
            self.on_status(
                (
                    client_id,
                    json.dumps(
                        {
                            "version": 2,
                            "client_id": client_id,
                            "checked_at": datetime.now(UTC).isoformat(),
                            "devices": [],
                        }
                    ).encode(),
                )
            )

        def stop(self):
            self.stopped = True

    config_path = tmp_path / "mqtt.json"
    config_path.write_text('{"host":"broker"}', encoding="utf-8")
    monkeypatch.setattr(main_module, "resolve_mqtt_config_path", lambda: config_path)
    monkeypatch.setattr(main_module, "load_mqtt_config", lambda _path: object())
    monkeypatch.setattr(main_module, "MqttSnapshotSubscriber", FakeSubscriber)
    monkeypatch.setattr(main_module.SiteRegistry, "ingest", recording_ingest)
    monkeypatch.setattr(main_module.RemoteStatusStore, "ingest", recording_status_ingest)

    app = create_app(Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        assert ingested.wait(timeout=5)
        assert status_ingested.wait(timeout=5)
        mqtt = client.get("/api/mqtt/clients").json()["mqtt"]
        assert mqtt == {"configured": True, "connected": True}
        assert FakeSubscriber.instances[0].started is True

    assert FakeSubscriber.instances[0].stopped is True
