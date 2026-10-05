from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TlsConfig:
    enabled: bool = True
    ca_file: Path | None = None
    cert_file: Path | None = None
    key_file: Path | None = None
    insecure: bool = False


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    host: str
    port: int = 8883
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    tls: TlsConfig = TlsConfig()
    keepalive: int = 60


@dataclass(frozen=True, slots=True)
class HelperConfig:
    export_folder: Path
    state_dir: Path
    broker: BrokerConfig
    quiet_seconds: float = 10.0
    poll_seconds: float = 1.0
    publish_timeout_seconds: float = 30.0
    status_interval_seconds: float = 30.0
    ping_timeout_seconds: float = 2.0
    ping_concurrency: int = 20
    recovery_successes: int = 2

    @classmethod
    def for_test(cls, *, export_folder: Path, state_dir: Path) -> HelperConfig:
        return cls(
            export_folder=export_folder,
            state_dir=state_dir,
            broker=BrokerConfig(host="mqtt.test"),
        )


def _ensure_keys(value: dict[str, Any], allowed: set[str], section: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"The {section} configuration has unknown field(s): {', '.join(unknown)}")


def _optional_path(value: object, field: str) -> Path | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a file path.")
    return Path(value).expanduser().resolve()


def _default_state_dir() -> Path:
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        return Path(program_data) / "StatusVisualizer" / "MqttHelper"
    return Path.home() / ".statusvisualizer" / "mqtt-helper"


def _number(value: object, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number.")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}.")
    return result


def load_config(path: Path) -> HelperConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read helper configuration: {error}") from error
    if not isinstance(raw, dict):
        raise TypeError("The helper configuration must be a JSON object.")
    _ensure_keys(
        raw,
        {
            "export_folder",
            "state_dir",
            "broker",
            "quiet_seconds",
            "poll_seconds",
            "publish_timeout_seconds",
            "status_interval_seconds",
            "ping_timeout_seconds",
            "ping_concurrency",
            "recovery_successes",
        },
        "helper",
    )
    broker_raw = raw.get("broker")
    if not isinstance(broker_raw, dict):
        raise TypeError("broker must be a JSON object.")
    _ensure_keys(broker_raw, {"host", "port", "username", "password", "tls", "keepalive"}, "broker")
    tls_raw = broker_raw.get("tls", {})
    if not isinstance(tls_raw, dict):
        raise TypeError("broker.tls must be a JSON object.")
    _ensure_keys(tls_raw, {"enabled", "ca_file", "cert_file", "key_file", "insecure"}, "TLS")

    export_value = raw.get("export_folder")
    if not isinstance(export_value, str) or not export_value.strip():
        raise ValueError("export_folder is required.")
    export_folder = Path(export_value).expanduser().resolve()
    if not export_folder.is_dir():
        raise ValueError(f"The export folder does not exist: {export_folder}")
    host = broker_raw.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("broker.host is required.")
    state_value = raw.get("state_dir")
    state_dir = _optional_path(state_value, "state_dir") or _default_state_dir()
    port_value = broker_raw.get("port", 8883)
    if isinstance(port_value, bool) or not isinstance(port_value, int):
        raise TypeError("broker.port must be a whole number.")
    port = port_value
    if not 1 <= port <= 65535:
        raise ValueError("broker.port must be between 1 and 65535.")
    username = broker_raw.get("username")
    password = os.environ.get("STATUS_VISUALIZER_MQTT_PASSWORD")
    if password is None:
        password = broker_raw.get("password")
    if username is not None and (not isinstance(username, str) or not username):
        raise TypeError("broker.username must be a non-empty string.")
    if password is not None and not isinstance(password, str):
        raise TypeError("broker.password must be a string.")
    enabled = tls_raw.get("enabled", True)
    insecure = tls_raw.get("insecure", False)
    if not isinstance(enabled, bool) or not isinstance(insecure, bool):
        raise TypeError("TLS enabled and insecure flags must be boolean.")
    tls = TlsConfig(
        enabled=enabled,
        ca_file=_optional_path(tls_raw.get("ca_file"), "broker.tls.ca_file"),
        cert_file=_optional_path(tls_raw.get("cert_file"), "broker.tls.cert_file"),
        key_file=_optional_path(tls_raw.get("key_file"), "broker.tls.key_file"),
        insecure=insecure,
    )
    if tls.insecure:
        raise ValueError("broker.tls.insecure=true is not supported; use a trusted CA instead.")
    if bool(tls.cert_file) != bool(tls.key_file):
        raise ValueError("broker.tls.cert_file and key_file must be configured together.")
    keepalive = broker_raw.get("keepalive", 60)
    if isinstance(keepalive, bool) or not isinstance(keepalive, int) or not 10 <= keepalive <= 3600:
        raise TypeError("broker.keepalive must be a whole number between 10 and 3600.")
    quiet_seconds = _number(raw.get("quiet_seconds", 10), "quiet_seconds", 1, 3600)
    poll_seconds = _number(raw.get("poll_seconds", 1), "poll_seconds", 0.1, 60)
    publish_timeout = _number(
        raw.get("publish_timeout_seconds", 30), "publish_timeout_seconds", 1, 300
    )
    status_interval = _number(
        raw.get("status_interval_seconds", 30), "status_interval_seconds", 5, 3600
    )
    ping_timeout = _number(
        raw.get("ping_timeout_seconds", 2), "ping_timeout_seconds", 0.1, 2
    )
    ping_concurrency_value = raw.get("ping_concurrency", 20)
    if (
        isinstance(ping_concurrency_value, bool)
        or not isinstance(ping_concurrency_value, int)
        or not 1 <= ping_concurrency_value <= 64
    ):
        raise TypeError("ping_concurrency must be a whole number between 1 and 64.")
    recovery_successes = raw.get("recovery_successes", 2)
    if isinstance(recovery_successes, bool) or not isinstance(recovery_successes, int) or not 1 <= recovery_successes <= 10:
        raise TypeError("recovery_successes must be a whole number between 1 and 10.")
    return HelperConfig(
        export_folder=export_folder,
        state_dir=state_dir,
        broker=BrokerConfig(
            host=host.strip(),
            port=port,
            username=username,
            password=password,
            tls=tls,
            keepalive=keepalive,
        ),
        quiet_seconds=quiet_seconds,
        poll_seconds=poll_seconds,
        publish_timeout_seconds=publish_timeout,
        status_interval_seconds=status_interval,
        ping_timeout_seconds=ping_timeout,
        ping_concurrency=ping_concurrency_value,
        recovery_successes=recovery_successes,
    )
