from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.lantopolog_fixture import export_files


def import_payload() -> dict:
    return {
        "files": [
            {"path": path, "name": path.rsplit("/", 1)[-1], "content": content}
            for path, content in export_files().items()
        ]
    }


def manual_node(**changes) -> dict:
    payload = {
        "name": "Manual appliance",
        "address": "192.168.1.50",
        "notes": "Added by an operator",
        "x": 0.4,
        "y": 0.7,
        "node_type": "other",
        "icon_type": "auto",
        "node_shape": "icon",
        "mac_address": "",
        "locked": True,
    }
    return {**payload, **changes}


def test_one_request_imports_the_folder_and_returns_a_ready_topology(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/import/lantopolog", json=import_payload())
        assert response.status_code == 200
        assert response.json()["summary"]["nodes"] == 4
        assert response.json()["summary"]["connections"] == 3

        topology = client.get("/api/topology").json()
        assert len(topology["nodes"]) == 4
        assert len(topology["edges"]) == 3
        assert topology["import_status"]["files_used"] == 6
        assert topology["vlans"][0]["name"] == "DEFAULT_VLAN"


def test_shared_managed_port_persists_as_an_inferred_switch_branch(tmp_path) -> None:
    files = export_files()
    desktop_row = files["complist.csv"].splitlines()[1]
    files["complist.csv"] += "\n".join(
        desktop_row.replace("001122334455", f"0011223344{suffix}")
        .replace("192.168.1.20", f"192.168.1.{address}")
        .replace("DESKTOP-1", f"DESKTOP-{suffix}")
        for suffix, address in (("56", 21), ("57", 22), ("58", 23), ("59", 24))
    ) + "\n"
    payload = {
        "files": [
            {"path": path, "name": path.rsplit("/", 1)[-1], "content": content}
            for path, content in files.items()
        ]
    }
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app) as client:
        imported = client.post("/api/import/lantopolog", json=payload)
        topology = client.get("/api/topology").json()

    assert imported.status_code == 200
    assert imported.json()["summary"]["inferred_switches"] == 1
    inferred = next(
        node
        for node in topology["nodes"]
        if node["metadata"].get("Synthetic Role") == "shared-port-fanout"
    )
    branch_edges = [
        edge
        for edge in topology["edges"]
        if inferred["id"] in {edge["source_id"], edge["target_id"]}
    ]
    assert inferred["node_type"] == "switch"
    assert inferred["metadata"]["Endpoint Count"] == "5"
    assert len(branch_edges) == 6
    assert sum(edge["kind"] == "infrastructure" for edge in branch_edges) == 1
    assert sum(edge["kind"] == "attachment" for edge in branch_edges) == 5


def test_import_preserves_parallel_switch_connections(tmp_path) -> None:
    files = export_files()
    files["sw_conn.csv"] = (
        files["sw_conn.csv"]
        + "Core;Rack;192.168.1.2;2;-;9;192.168.1.3;Gateway;Office\n"
    )
    payload = {
        "files": [
            {"path": path, "name": path.rsplit("/", 1)[-1], "content": content}
            for path, content in files.items()
        ]
    }
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/import/lantopolog", json=payload)

        assert response.status_code == 200
        topology = client.get("/api/topology").json()
        infrastructure_edges = [edge for edge in topology["edges"] if edge["kind"] == "infrastructure"]
        assert len(infrastructure_edges) == 2
        assert {edge["label"] for edge in infrastructure_edges} == {"1 - 8", "2 - 9"}


def test_legacy_edge_pair_constraint_is_migrated_for_parallel_imports(tmp_path) -> None:
    database_path = tmp_path / "status.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE topology_edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'manual',
                locked INTEGER NOT NULL DEFAULT 1,
                confidence REAL NOT NULL DEFAULT 1,
                evidence TEXT NOT NULL DEFAULT '',
                source_interface TEXT NOT NULL DEFAULT '',
                target_interface TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_id, target_id, kind)
            );
            """
        )
    files = export_files()
    files["sw_conn.csv"] = (
        files["sw_conn.csv"]
        + "Core;Rack;192.168.1.2;2;-;9;192.168.1.3;Gateway;Office\n"
    )
    payload = {
        "files": [
            {"path": path, "name": path.rsplit("/", 1)[-1], "content": content}
            for path, content in files.items()
        ]
    }

    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/import/lantopolog", json=payload)

        assert response.status_code == 200
        assert response.json()["summary"]["connections"] == 4


def test_reimport_is_atomic_idempotent_and_preserves_manual_nodes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        manual = client.post("/api/devices", json=manual_node()).json()
        first = client.post("/api/import/lantopolog", json=import_payload())
        second = client.post("/api/import/lantopolog", json=import_payload())

        assert first.status_code == second.status_code == 200
        topology = client.get("/api/topology").json()
        assert len(topology["nodes"]) == 5
        assert len(topology["edges"]) == 3
        assert any(node["id"] == manual["id"] for node in topology["nodes"])

        broken = import_payload()
        broken["files"][0]["content"] = "not;a;valid;switch;export"
        assert client.post("/api/import/lantopolog", json=broken).status_code == 422
        assert len(client.get("/api/topology").json()["nodes"]) == 5


def test_manual_topology_crud_still_works(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        first = client.post("/api/devices", json=manual_node()).json()
        second = client.post(
            "/api/devices", json=manual_node(name="Second", address="192.168.1.51", x=0.7)
        ).json()
        edge = client.post(
            "/api/edges",
            json={"source_id": first["id"], "target_id": second["id"], "label": "LAN"},
        )
        assert edge.status_code == 201
        duplicate = client.post(
            "/api/edges",
            json={"source_id": first["id"], "target_id": second["id"], "label": "duplicate"},
        )
        assert duplicate.status_code == 422

        updated = client.put(
            f"/api/devices/{first['id']}",
            json=manual_node(name="Renamed", x=0.5, icon_type="printer", node_shape="circle"),
        )
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["icon_type"] == "printer"
        assert updated.json()["node_shape"] == "circle"
        assert client.delete(f"/api/edges/{edge.json()['id']}").status_code == 204
        assert client.delete(f"/api/devices/{second['id']}").status_code == 204


def test_delete_all_devices_removes_the_current_topology(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        first = client.post("/api/devices", json=manual_node()).json()
        second = client.post(
            "/api/devices", json=manual_node(name="Second", address="192.168.1.51", x=0.7)
        ).json()
        assert client.post(
            "/api/edges",
            json={"source_id": first["id"], "target_id": second["id"], "label": "LAN"},
        ).status_code == 201

        response = client.delete("/api/devices")
        assert response.status_code == 200
        assert response.json()["deleted"] == 2

        topology = client.get("/api/topology").json()
        assert topology["nodes"] == []
        assert topology["edges"] == []


def test_batch_position_update_is_atomic_and_persists_visual_choices(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        first = client.post(
            "/api/devices",
            json=manual_node(name="Printer", icon_type="printer", node_shape="circle"),
        ).json()
        second = client.post(
            "/api/devices",
            json=manual_node(name="Switch", address="192.168.1.51", icon_type="switch"),
        ).json()

        moved = client.put(
            "/api/devices/positions",
            json={"positions": [{"id": first["id"], "x": -0.2, "y": 0.4}, {"id": second["id"], "x": 1.45, "y": 1.2}]},
        )

        assert moved.status_code == 200
        by_id = {node["id"]: node for node in moved.json()}
        assert (by_id[first["id"]]["x"], by_id[first["id"]]["y"]) == (-0.2, 0.4)
        assert (by_id[second["id"]]["x"], by_id[second["id"]]["y"]) == (1.45, 1.2)
        assert by_id[first["id"]]["icon_type"] == "printer"
        assert by_id[first["id"]]["node_shape"] == "circle"

        rejected = client.put(
            "/api/devices/positions",
            json={"positions": [{"id": first["id"], "x": 0.9, "y": 0.9}, {"id": "missing", "x": 0.1, "y": 0.1}]},
        )
        assert rejected.status_code == 404
        unchanged = client.get(f"/api/devices/{first['id']}").json()
        assert (unchanged["x"], unchanged["y"]) == (-0.2, 0.4)

        wide_workspace = client.put(
            "/api/devices/positions",
            json={"positions": [{"id": first["id"], "x": -25, "y": 75}]},
        )
        assert wide_workspace.status_code == 200

        outside_safety_limit = client.put(
            "/api/devices/positions",
            json={"positions": [{"id": first["id"], "x": 102, "y": 0.4}]},
        )
        assert outside_safety_limit.status_code == 422

        too_large = client.put(
            "/api/devices/positions",
            content="{}",
            headers={"Content-Length": str(2 * 1024 * 1024), "Content-Type": "application/json"},
        )
        assert too_large.status_code == 413


def test_custom_layout_is_absent_until_saved_without_moving_devices(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        absent = client.get("/api/layouts/custom")

        assert absent.status_code == 200
        assert absent.json() == {"exists": False, "positions": [], "saved_at": None}

        first = client.post(
            "/api/devices",
            json=manual_node(name="Left", x=0.25, y=0.35),
        ).json()
        second = client.post(
            "/api/devices",
            json=manual_node(name="Right", address="192.168.1.51", x=0.75, y=0.65),
        ).json()
        positions = [
            {"id": first["id"], "x": -100.0, "y": 101.0},
            {"id": second["id"], "x": 101.0, "y": -100.0},
        ]

        saved = client.put("/api/layouts/custom", json={"positions": positions})

        assert saved.status_code == 200
        assert saved.json()["exists"] is True
        assert saved.json()["positions"] == positions
        saved_at = saved.json()["saved_at"]
        assert saved_at.endswith("Z")
        datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
        fetched = client.get("/api/layouts/custom")
        assert fetched.json() == saved.json()

        unchanged = {node["id"]: node for node in client.get("/api/devices").json()}
        assert (unchanged[first["id"]]["x"], unchanged[first["id"]]["y"]) == (0.25, 0.35)
        assert (unchanged[second["id"]]["x"], unchanged[second["id"]]["y"]) == (0.75, 0.65)


def test_custom_layout_persists_across_app_recreation(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    first_app = create_app(settings)
    with TestClient(first_app) as client:
        device = client.post("/api/devices", json=manual_node()).json()
        save_response = client.put(
            "/api/layouts/custom",
            json={"positions": [{"id": device["id"], "x": -2.5, "y": 3.75}]},
        )
        assert save_response.status_code == 200
        saved = save_response.json()

    recreated_app = create_app(settings)
    with TestClient(recreated_app) as client:
        fetched = client.get("/api/layouts/custom")
        assert fetched.status_code == 200
        assert fetched.json() == saved


def test_custom_layout_uses_device_position_input_limits(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.put("/api/layouts/custom", json={"positions": []}).status_code == 422
        assert client.put(
            "/api/layouts/custom",
            json={
                "positions": [
                    {"id": "same", "x": 0, "y": 0},
                    {"id": "same", "x": 1, "y": 1},
                ]
            },
        ).status_code == 422
        assert client.put(
            "/api/layouts/custom",
            json={"positions": [{"id": "outside-min", "x": -100.01, "y": 0}]},
        ).status_code == 422
        assert client.put(
            "/api/layouts/custom",
            json={"positions": [{"id": "outside-max", "x": 0, "y": 101.01}]},
        ).status_code == 422
        assert client.put(
            "/api/layouts/custom",
            json={
                "positions": [
                    {"id": f"device-{index}", "x": 0, "y": 0}
                    for index in range(501)
                ]
            },
        ).status_code == 422


def test_visual_override_survives_lantopolog_reimport(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.post("/api/import/lantopolog", json=import_payload()).status_code == 200
        imported = next(node for node in client.get("/api/devices").json() if node["name"] == "Core")
        payload = {
            key: imported[key]
            for key in ("name", "address", "notes", "x", "y", "node_type", "mac_address", "locked")
        }
        payload.update({"icon_type": "server", "node_shape": "card", "locked": True})
        assert client.put(f"/api/devices/{imported['id']}", json=payload).status_code == 200

        assert client.post("/api/import/lantopolog", json=import_payload()).status_code == 200
        refreshed = client.get(f"/api/devices/{imported['id']}").json()
        assert refreshed["icon_type"] == "server"
        assert refreshed["node_shape"] == "card"


def test_import_payload_limits_and_error_messages_are_safe(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/import/lantopolog",
            json={"files": [{"name": "secrets.txt", "path": "secrets.txt", "content": "x"}]},
        )
        assert response.status_code == 422
        assert "recognized" in response.json()["detail"].lower()
        assert "F:\\" not in response.json()["detail"]

        oversized = client.post(
            "/api/import/lantopolog",
            json={
                "files": [
                    {
                        "name": "sw_list.csv",
                        "path": "sw_list.csv",
                        "content": "x" * (5 * 1024 * 1024 + 1),
                    }
                ]
            },
        )
        assert oversized.status_code == 422

        too_large_total = client.post(
            "/api/import/lantopolog",
            json={
                "files": [
                    {
                        "name": f"sw_list_{index}.csv",
                        "path": f"sw_list_{index}.csv",
                        "content": "x" * (1100 * 1024),
                    }
                    for index in range(20)
                ]
            },
        )
        assert too_large_total.status_code == 413

        rejected_before_parsing = client.post(
            "/api/import/lantopolog",
            content="{}",
            headers={"Content-Length": str(22 * 1024 * 1024), "Content-Type": "application/json"},
        )
        assert rejected_before_parsing.status_code == 413


def test_manual_and_parallel_imported_links_can_coexist(tmp_path) -> None:
    files = export_files()
    files["sw_conn.csv"] += "Core;Rack;192.168.1.2;2;-;9;192.168.1.3;Gateway;Office\n"
    payload = {
        "files": [
            {"path": path, "name": path.rsplit("/", 1)[-1], "content": content}
            for path, content in files.items()
        ]
    }
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.post("/api/import/lantopolog", json=payload).status_code == 200
        nodes = client.get("/api/devices").json()
        core = next(node for node in nodes if node["name"] == "Core")
        gateway = next(node for node in nodes if node["name"] == "Gateway")
        assert client.post(
            "/api/edges",
            json={"source_id": core["id"], "target_id": gateway["id"], "label": "Logical"},
        ).status_code == 201
        assert client.post("/api/import/lantopolog", json=payload).status_code == 200
        between = [
            edge for edge in client.get("/api/edges").json()
            if {edge["source_id"], edge["target_id"]} == {core["id"], gateway["id"]}
        ]
        assert len(between) == 3


def test_frontend_and_health_are_served(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        page = client.get("/")
        assert page.status_code == 200
        assert "Import Lantopolog" in page.text
        assert client.get("/static/app.js").status_code == 200


def test_topology_pdf_export_returns_a_vector_pdf_download(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.post("/api/import/lantopolog", json=import_payload()).status_code == 200
        topology = client.get("/api/topology").json()
        response = client.post(
            "/api/exports/topology.pdf",
            json={
                "node_ids": [node["id"] for node in topology["nodes"]],
                "label_mode": "both",
                "vlan_view": True,
                "theme": "light",
            },
            headers={"X-Status-Visualizer-Request": "1"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert "status-visualizer-topology" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-topology-theme"] == "light"
    assert response.content.startswith(b"%PDF-")
    assert response.content.rstrip().endswith(b"%%EOF")
    assert b"/Subtype /Image" not in response.content


def test_topology_pdf_export_uses_only_the_requested_visible_nodes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        first = client.post(
            "/api/devices", json=manual_node(name="Visible device", address="192.168.1.50")
        ).json()
        client.post(
            "/api/devices",
            json=manual_node(name="Hidden device", address="192.168.1.51", x=0.8),
        )
        response = client.post(
            "/api/exports/topology.pdf",
            json={"node_ids": [first["id"]], "label_mode": "both", "vlan_view": False},
            headers={"X-Status-Visualizer-Request": "1"},
        )

    assert response.status_code == 200
    assert response.headers["x-topology-node-count"] == "1"


def test_topology_pdf_export_validates_selection_and_options(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.post(
            "/api/exports/topology.pdf",
            json={"node_ids": [], "label_mode": "both", "vlan_view": False},
            headers={"X-Status-Visualizer-Request": "1"},
        ).status_code == 422
        assert client.post(
            "/api/exports/topology.pdf",
            json={"node_ids": ["same", "same"], "label_mode": "both", "vlan_view": False},
            headers={"X-Status-Visualizer-Request": "1"},
        ).status_code == 422
        assert client.post(
            "/api/exports/topology.pdf",
            json={"node_ids": ["missing"], "label_mode": "both", "vlan_view": False},
            headers={"X-Status-Visualizer-Request": "1"},
        ).status_code == 404
        assert client.post(
            "/api/exports/topology.pdf",
            json={"node_ids": ["missing"], "label_mode": "everything", "vlan_view": False},
            headers={"X-Status-Visualizer-Request": "1"},
        ).status_code == 422
        assert client.post(
            "/api/exports/topology.pdf",
            json={
                "node_ids": ["missing"],
                "label_mode": "both",
                "vlan_view": False,
                "theme": "sepia",
            },
            headers={"X-Status-Visualizer-Request": "1"},
        ).status_code == 422
        assert client.post(
            "/api/exports/topology.pdf",
            json={"node_ids": ["missing"], "label_mode": "both", "vlan_view": False},
        ).status_code == 403
        assert client.post(
            "/api/exports/topology.pdf",
            json={"node_ids": ["missing"], "label_mode": "both", "vlan_view": False},
            headers={
                "X-Status-Visualizer-Request": "1",
                "Origin": "https://attacker.invalid",
            },
        ).status_code == 403
        assert client.post(
            "/api/exports/topology.pdf",
            content="not-json",
            headers={
                "Content-Type": "text/plain",
                "X-Status-Visualizer-Request": "1",
            },
        ).status_code == 415
