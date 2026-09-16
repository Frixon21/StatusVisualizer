import json
from pathlib import Path

from fastapi.testclient import TestClient

from expo_launcher import app


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_DIR = ROOT / "expo-launcher"


def test_health_endpoint_identifies_launcher() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "status-visualizer-launcher",
    }


def test_index_is_served_with_security_headers() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Tech Expo Network Lab" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "x-frame-options" not in response.headers


def test_launcher_has_a_systemd_compatible_command_line_entrypoint() -> None:
    source = (ROOT / "expo_launcher.py").read_text(encoding="utf-8")

    assert 'parser.add_argument("--host"' in source
    assert 'parser.add_argument("--port"' in source
    assert 'if __name__ == "__main__":' in source


def test_launcher_configuration_has_expected_visibility_and_ports() -> None:
    config = json.loads((LAUNCHER_DIR / "networks.json").read_text(encoding="utf-8"))

    assert [(item["id"], item["port"], item["visible"]) for item in config["networks"]] == [
        ("flat", 6043, True),
        ("compartmentalized", 6044, True),
        ("live", 6045, False),
    ]


def test_frontend_builds_links_from_browser_location_without_html_injection() -> None:
    script = (LAUNCHER_DIR / "launcher.js").read_text(encoding="utf-8")

    assert "window.location.hostname" in script
    assert "window.location.protocol" in script
    assert "document.createElement" in script
    assert ".textContent" in script
    assert "innerHTML" not in script
    assert "document.write" not in script


def test_static_assets_are_available() -> None:
    client = TestClient(app)

    assert client.get("/assets/launcher.js").status_code == 200
    assert client.get("/assets/styles.css").status_code == 200
    assert client.get("/assets/networks.json").status_code == 200
