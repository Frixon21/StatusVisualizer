from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import mqtt_config
from app.mqtt_config import MqttConfigError, load_mqtt_config


def test_load_config_defaults_to_verified_tls(tmp_path: Path) -> None:
    path = tmp_path / "mqtt.json"
    path.write_text(json.dumps({"host": "broker.example", "username": "agent", "password": "secret"}))

    config = load_mqtt_config(path)

    assert config.host == "broker.example"
    assert config.port == 8883
    assert config.tls.enabled is True
    assert config.tls.insecure is False
    assert config.password == "secret"
    assert "secret" not in repr(config)


def test_environment_password_does_not_require_json_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mqtt.json"
    path.write_text(json.dumps({"host": "broker.example", "username": "agent"}))
    monkeypatch.setenv("STATUS_VISUALIZER_MQTT_PASSWORD", "runtime-secret")

    config = load_mqtt_config(path)

    assert config.password == "runtime-secret"


def test_load_config_supports_custom_ca_and_client_certificate(tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    cert = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    for item in (ca, cert, key):
        item.write_text("test")
    path = tmp_path / "mqtt.json"
    path.write_text(json.dumps({
        "host": "broker.example",
        "tls": {"ca_file": "ca.pem", "cert_file": "client.pem", "key_file": "client.key"},
    }))

    config = load_mqtt_config(path)

    assert config.tls.ca_file == ca.resolve()
    assert config.tls.cert_file == cert.resolve()
    assert config.tls.key_file == key.resolve()


@pytest.mark.parametrize("document", ({}, {"host": "x", "port": 0}, {"host": "x", "tls": {"insecure": True}}))
def test_load_config_rejects_missing_or_insecure_settings(tmp_path: Path, document: dict) -> None:
    path = tmp_path / "mqtt.json"
    path.write_text(json.dumps(document))

    with pytest.raises(MqttConfigError):
        load_mqtt_config(path)


def test_resolve_mqtt_config_path_prefers_env_then_appdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.mqtt_config import resolve_mqtt_config_path

    monkeypatch.delenv("STATUS_VISUALIZER_MQTT_CONFIG", raising=False)
    appdata = tmp_path / "appdata"
    program = tmp_path / "program"
    appdata.mkdir()
    program.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.setenv("PROGRAMDATA", str(program))

    env_path = tmp_path / "env-mqtt.json"
    env_path.write_text(json.dumps({"host": "from-env"}))
    monkeypatch.setenv("STATUS_VISUALIZER_MQTT_CONFIG", str(env_path))
    assert resolve_mqtt_config_path() == env_path.resolve()

    monkeypatch.delenv("STATUS_VISUALIZER_MQTT_CONFIG", raising=False)
    discovered = appdata / "StatusVisualizer" / "mqtt.json"
    discovered.parent.mkdir(parents=True)
    discovered.write_text(json.dumps({"host": "from-appdata"}))
    assert resolve_mqtt_config_path() == discovered.resolve()


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "JSON object"),
        ({"host": "x", "port": True}, "port"),
        ({"host": "x", "keepalive": 9}, "keepalive"),
        ({"host": "x", "tls": []}, "TLS configuration"),
        ({"host": "x", "tls": {"enabled": "yes"}}, "TLS flags"),
        ({"host": "x", "tls": {"ca_file": ""}}, "file path"),
        ({"host": "x", "tls": {"ca_file": "missing.pem"}}, "does not exist"),
        ({"host": "x", "tls": {"cert_file": "missing.pem"}}, "does not exist"),
        ({"host": "x", "username": ""}, "username"),
        ({"host": "x", "password": 123}, "password"),
    ],
)
def test_load_config_rejects_malformed_values(
    tmp_path: Path, document: object, message: str
) -> None:
    path = tmp_path / "mqtt.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(MqttConfigError, match=message):
        load_mqtt_config(path)


def test_load_config_requires_client_certificate_pair(tmp_path: Path) -> None:
    cert = tmp_path / "client.pem"
    cert.write_text("certificate", encoding="utf-8")
    path = tmp_path / "mqtt.json"
    path.write_text(
        json.dumps({"host": "broker", "tls": {"cert_file": str(cert)}}),
        encoding="utf-8",
    )

    with pytest.raises(MqttConfigError, match="configured together"):
        load_mqtt_config(path)


@pytest.mark.parametrize("contents", ["{", "\ud800"])
def test_load_config_reports_unreadable_documents(
    tmp_path: Path, contents: str
) -> None:
    path = tmp_path / "mqtt.json"
    if contents == "\ud800":
        path.write_bytes(b"\xff")
    else:
        path.write_text(contents, encoding="utf-8")

    with pytest.raises(MqttConfigError, match="could not be read"):
        load_mqtt_config(path)


def test_resolve_config_checks_frozen_and_programdata_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STATUS_VISUALIZER_MQTT_CONFIG", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    executable = tmp_path / "bin" / "StatusVisualizer.exe"
    executable.parent.mkdir()
    beside_exe = executable.parent / "mqtt.json"
    beside_exe.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mqtt_config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mqtt_config.sys, "executable", str(executable))
    assert mqtt_config.resolve_mqtt_config_path() == beside_exe.resolve()

    beside_exe.unlink()
    program_data = tmp_path / "programdata"
    configured = program_data / "StatusVisualizer" / "mqtt.json"
    configured.parent.mkdir(parents=True)
    configured.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    assert mqtt_config.resolve_mqtt_config_path() == configured.resolve()

    configured.unlink()
    assert mqtt_config.resolve_mqtt_config_path() is None

