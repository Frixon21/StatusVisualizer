from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MqttConfigError(ValueError):
    """Raised when external MQTT configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class MqttTlsConfig:
    enabled: bool = True
    ca_file: Path | None = None
    cert_file: Path | None = None
    key_file: Path | None = None
    insecure: bool = False


@dataclass(frozen=True, slots=True)
class MqttConfig:
    host: str
    port: int = 8883
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    keepalive: int = 60
    tls: MqttTlsConfig = field(default_factory=MqttTlsConfig)


def _optional_path(base: Path, value: Any, label: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MqttConfigError(f"MQTT {label} must be a file path.")
    path = Path(value).expanduser()
    resolved = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_file():
        raise MqttConfigError(f"MQTT {label} does not exist.")
    return resolved


def resolve_mqtt_config_path() -> Path | None:
    """Find MQTT config without requiring env vars for double-click EXE use.

    Search order:
    1. STATUS_VISUALIZER_MQTT_CONFIG
    2. mqtt.json next to the EXE / project root
    3. %LOCALAPPDATA%\\StatusVisualizer\\mqtt.json
    4. %PROGRAMDATA%\\StatusVisualizer\\mqtt.json
    """

    configured = os.getenv("STATUS_VISUALIZER_MQTT_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "mqtt.json")
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "mqtt.json")

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "StatusVisualizer" / "mqtt.json")
    program_data = os.getenv("PROGRAMDATA")
    if program_data:
        candidates.append(Path(program_data) / "StatusVisualizer" / "mqtt.json")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def load_mqtt_config(path: str | Path) -> MqttConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        document = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MqttConfigError("MQTT configuration could not be read.") from error
    if not isinstance(document, dict):
        raise MqttConfigError("MQTT configuration must be a JSON object.")
    host = document.get("host")
    if not isinstance(host, str) or not host.strip() or len(host) > 253:
        raise MqttConfigError("MQTT host is required.")
    port = document.get("port", 8883)
    keepalive = document.get("keepalive", 60)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise MqttConfigError("MQTT port must be between 1 and 65535.")
    if isinstance(keepalive, bool) or not isinstance(keepalive, int) or not 10 <= keepalive <= 3600:
        raise MqttConfigError("MQTT keepalive must be between 10 and 3600 seconds.")
    tls_value = document.get("tls", {})
    if not isinstance(tls_value, dict):
        raise MqttConfigError("MQTT TLS configuration must be an object.")
    enabled = tls_value.get("enabled", True)
    insecure = tls_value.get("insecure", False)
    if not isinstance(enabled, bool) or not isinstance(insecure, bool):
        raise MqttConfigError("MQTT TLS flags must be boolean.")
    if insecure:
        raise MqttConfigError("Disabling MQTT TLS certificate verification is not supported.")
    cert_file = _optional_path(config_path.parent, tls_value.get("cert_file"), "client certificate")
    key_file = _optional_path(config_path.parent, tls_value.get("key_file"), "client key")
    if (cert_file is None) != (key_file is None):
        raise MqttConfigError("MQTT client certificate and key must be configured together.")
    username = document.get("username")
    password = os.getenv("STATUS_VISUALIZER_MQTT_PASSWORD")
    if password is None:
        password = document.get("password")
    if username is not None and (not isinstance(username, str) or not username):
        raise MqttConfigError("MQTT username must be a non-empty string.")
    if password is not None and not isinstance(password, str):
        raise MqttConfigError("MQTT password must be a string.")
    return MqttConfig(
        host=host.strip(),
        port=port,
        username=username,
        password=password,
        keepalive=keepalive,
        tls=MqttTlsConfig(
            enabled=enabled,
            ca_file=_optional_path(config_path.parent, tls_value.get("ca_file"), "CA file"),
            cert_file=cert_file,
            key_file=key_file,
            insecure=False,
        ),
    )
