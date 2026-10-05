from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-dist-status-visualizer.ps1"

TEMPLATES = {
    "dashboard-mqtt.json": {
        "host": "mqtt.example.com",
        "port": 1883,
        "username": "status-visualizer",
        "keepalive": 60,
        "tls": {"enabled": False},
    },
    "mqtt-service.config.json": {
        "export_folder": "C:\\LanTopoLog\\Export",
        "broker": {
            "host": "mqtt.example.com",
            "port": 1883,
            "username": "status-visualizer",
            "tls": {"enabled": False},
        },
    },
    "mqtt-service.config.linux.json": {
        "export_folder": "/path/to/LanTopoLog/Export",
        "broker": {
            "host": "mqtt.example.com",
            "port": 1883,
            "username": "status-visualizer",
            "tls": {"enabled": False},
        },
    },
}

LOCAL = {
    "dashboard-mqtt.json": {
        "host": "10.1.2.3",
        "port": 1883,
        "username": "pack-local-user",
        "keepalive": 60,
        "tls": {"enabled": False},
    },
    "mqtt-service.config.json": {
        "export_folder": "D:\\Customer\\Export",
        "broker": {
            "host": "10.1.2.3",
            "port": 1883,
            "username": "pack-local-user",
            "tls": {"enabled": False},
        },
    },
    "mqtt-service.config.linux.json": {
        "export_folder": "/srv/lantopolog/export",
        "broker": {
            "host": "10.1.2.3",
            "port": 1883,
            "username": "pack-local-user",
            "tls": {"enabled": False},
        },
    },
}

OUTPUTS = {
    "Dashboard/mqtt.json": "dashboard-mqtt.json",
    "Dashboard-Linux/mqtt.json": "dashboard-mqtt.json",
    "MqttService/config.json": "mqtt-service.config.json",
    "MqttService-Linux/config.json": "mqtt-service.config.linux.json",
}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _project(root: Path, *, include_local: bool) -> None:
    for name, payload in TEMPLATES.items():
        _write(root / "pack-templates" / name, payload)
    _write(
        root / "mqtt.json",
        {"host": "dev-mqtt-should-not-ship", "username": "dev", "password": "secret"},
    )
    if include_local:
        for name, payload in LOCAL.items():
            _write(root / "pack-local" / name, payload)


def _build(project: Path, output: Path) -> None:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-ConfigsOnly",
            "-ProjectDir",
            str(project),
            "-OutputPath",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def _assert_pack(output: Path, expected: dict[str, dict]) -> None:
    for relative, source_name in OUTPUTS.items():
        built = json.loads((output / relative).read_text(encoding="utf-8"))
        assert built == expected[source_name]
        assert "password" not in json.dumps(built)
        assert "dev-mqtt-should-not-ship" not in json.dumps(built)


def _workspace() -> Path:
    root = ROOT / ".tmp" / "pack-config-test"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    return root


def test_pack_without_local_uses_templates() -> None:
    workspace = _workspace()
    project = workspace / "project"
    output = workspace / "dist"
    _project(project, include_local=False)

    _build(project, output)

    _assert_pack(output, TEMPLATES)


def test_pack_local_overrides_templates() -> None:
    workspace = _workspace()
    project = workspace / "project"
    output = workspace / "dist"
    _project(project, include_local=True)

    _build(project, output)

    _assert_pack(output, LOCAL)
