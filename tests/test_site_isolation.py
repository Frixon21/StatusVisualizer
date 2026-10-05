from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mqtt_snapshot import build_snapshot_zip
from tests.lantopolog_fixture import export_files


def _site_headers(site_id: str, *, mutate: bool = False) -> dict[str, str]:
    headers = {"X-Status-Visualizer-Site": site_id}
    if mutate:
        headers["X-Status-Visualizer-Request"] = "1"
        headers["Content-Type"] = "application/json"
    return headers


def _approve_two_identical_sites(app, tmp_path):
    files = {name: content.encode() for name, content in export_files().items()}
    first = "550e8400-e29b-41d4-a716-446655440000"
    second = "550e8400-e29b-41d4-a716-446655440001"
    for client_id, name in ((first, "Alpha"), (second, "Beta")):
        app.state.site_registry.ingest(client_id, build_snapshot_zip(files, client_id))
        app.state.site_registry.patch_client(client_id, display_name=name, state="approved")
    return first, second


def test_identical_device_ids_remain_isolated_across_site_scoped_endpoints(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    first, second = _approve_two_identical_sites(app, tmp_path)
    first_headers = _site_headers(first, mutate=True)

    with TestClient(app) as client:
        first_topology = client.get("/api/topology", headers=_site_headers(first)).json()
        second_topology = client.get("/api/topology", headers=_site_headers(second)).json()
        shared_ids = {node["id"] for node in first_topology["nodes"]} & {
            node["id"] for node in second_topology["nodes"]
        }
        assert shared_ids
        core = next(node for node in first_topology["nodes"] if node["name"] == "Core")
        core_id = core["id"]
        assert core_id in shared_ids

        payload = {
            key: core[key]
            for key in (
                "name", "address", "notes", "x", "y", "node_type", "icon_type",
                "node_shape", "mac_address", "locked",
            )
        }
        payload.update({"name": "Alpha Core", "notes": "site-alpha-only", "locked": True})
        renamed = client.put(f"/api/devices/{core_id}", headers=first_headers, json=payload)
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Alpha Core"

        layout = client.put(
            "/api/layouts/custom",
            headers=first_headers,
            json={"positions": [{"id": core_id, "x": 0.11, "y": 0.22}]},
        )
        assert layout.status_code == 200

        manual = client.post(
            "/api/devices",
            headers=first_headers,
            json={
                "name": "Alpha Manual",
                "address": "192.168.1.99",
                "notes": "",
                "x": 0.4,
                "y": 0.4,
                "node_type": "other",
                "icon_type": "auto",
                "node_shape": "icon",
                "mac_address": "",
                "locked": True,
            },
        ).json()
        edge = client.post(
            "/api/edges",
            headers=first_headers,
            json={"source_id": core_id, "target_id": manual["id"]},
        )
        assert edge.status_code == 201

        beta_core = client.get(f"/api/devices/{core_id}", headers=_site_headers(second)).json()
        assert beta_core["name"] == "Core"
        assert beta_core["notes"] != "site-alpha-only"
        first_layout = client.get("/api/layouts/custom", headers=_site_headers(first)).json()
        second_layout = client.get("/api/layouts/custom", headers=_site_headers(second)).json()
        assert first_layout != second_layout
        beta_names = {
            node["name"]
            for node in client.get("/api/topology", headers=_site_headers(second)).json()["nodes"]
        }
        assert "Alpha Manual" not in beta_names
        assert "Alpha Core" not in beta_names

        assert client.delete(
            f"/api/devices/{manual['id']}", headers=first_headers
        ).status_code == 204
        assert client.get(
            f"/api/devices/{core_id}", headers=_site_headers(second)
        ).status_code == 200
        assert client.post(
            "/api/exports/topology.pdf",
            headers=first_headers,
            json={"node_ids": [core_id], "label_mode": "hostname", "vlan_view": False, "theme": "light"},
        ).status_code == 200
        assert client.get("/api/liveness", headers=_site_headers(first)).json()["available"] is False
        assert client.get("/api/liveness", headers=_site_headers(second)).json()["available"] is False


def test_site_header_rejects_unknown_site_for_read_and_write_routes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    bad = _site_headers("missing", mutate=True)
    with TestClient(app) as client:
        created = client.post(
            "/api/devices",
            json={
                "name": "Local",
                "address": "",
                "notes": "",
                "x": 0.5,
                "y": 0.5,
                "node_type": "other",
                "icon_type": "auto",
                "node_shape": "icon",
                "mac_address": "",
                "locked": True,
            },
        ).json()
        assert client.get("/api/topology", headers=bad).status_code == 404
        assert client.get("/api/devices", headers=bad).status_code == 404
        assert client.get(f"/api/devices/{created['id']}", headers=bad).status_code == 404
        assert client.get("/api/edges", headers=bad).status_code == 404
        assert client.get("/api/layouts/custom", headers=bad).status_code == 404
        assert client.get("/api/liveness", headers=bad).status_code == 404
        assert client.post(
            "/api/exports/topology.pdf",
            headers=bad,
            json={"node_ids": [created["id"]], "label_mode": "hostname", "vlan_view": False, "theme": "light"},
        ).status_code == 404


def test_mqtt_patch_rejects_cross_origin(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    client_id = str(uuid.uuid4())
    files = {name: content.encode() for name, content in export_files().items()}
    with TestClient(app) as client:
        app.state.site_registry.ingest(client_id, build_snapshot_zip(files, client_id))
        response = client.patch(
            f"/api/mqtt/clients/{client_id}",
            json={"display_name": "Branch", "state": "approved"},
            headers={
                "X-Status-Visualizer-Request": "1",
                "Origin": "https://evil.example",
            },
        )
        assert response.status_code == 403


def test_app_starts_when_mqtt_config_is_missing_or_invalid(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("STATUS_VISUALIZER_MQTT_CONFIG", raising=False)
    with TestClient(create_app(Settings(data_dir=tmp_path / "plain"))) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/mqtt/clients").json()["mqtt"]["configured"] is False

    bad = tmp_path / "bad-mqtt.json"
    bad.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("STATUS_VISUALIZER_MQTT_CONFIG", str(bad))
    with TestClient(create_app(Settings(data_dir=tmp_path / "bad"))) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/mqtt/clients").json()["mqtt"]["configured"] is False
