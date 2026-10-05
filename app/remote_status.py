from __future__ import annotations

import json
import math
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.database import Repository

MAX_STATUS_BYTES = 2 * 1024 * 1024
MAX_STATUS_DEVICES = 1000
WINDOW_SECONDS = {"5m": 300.0, "15m": 900.0, "1h": 3600.0, "12h": 43_200.0, "24h": 86_400.0}


class StatusValidationError(ValueError):
    """Raised when a remote status message is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    device_id: str
    online: bool | None
    monitoring_state: Literal["normal", "verify", "degraded"]
    current_rtt_ms: float | None
    last_rtt_ms: float | None
    last_success_at: str | None
    loss: dict[str, float | None]
    rtt_avg_ms: dict[str, float | None]
    observed_seconds: dict[str, float]


@dataclass(frozen=True, slots=True)
class SiteStatus:
    client_id: str
    checked_at: datetime
    checked_at_text: str
    devices: tuple[DeviceStatus, ...]


def _canonical_uuid(value: object) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise StatusValidationError("Status client ID is invalid.") from error
    if parsed.version != 4 or str(parsed) != value:
        raise StatusValidationError("Status client ID must be a canonical UUID v4.")
    return str(parsed)


def _checked_at(value: object) -> tuple[datetime, str]:
    if not isinstance(value, str) or len(value) > 40:
        raise StatusValidationError("Status checked_at is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StatusValidationError("Status checked_at is invalid.") from error
    if parsed.tzinfo is None:
        raise StatusValidationError("Status checked_at must include a timezone.")
    normalized = parsed.astimezone(timezone.utc)
    return normalized, normalized.isoformat().replace("+00:00", "Z")


def _optional_timestamp(value: object) -> str | None:
    if value is None:
        return None
    return _checked_at(value)[1]


def _number(value: object, maximum: float, *, nullable: bool = True) -> float | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatusValidationError("A device status number is invalid.")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise StatusValidationError("A device status number is invalid.")
    return result


def _loss_percent(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatusValidationError("A device status number is invalid.")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise StatusValidationError("A device status number is invalid.")
    return min(result, 100.0)


def _window_map(value: object, maxima: dict[str, float], *, nullable: bool, loss: bool = False) -> dict[str, float | None]:
    if not isinstance(value, dict) or set(value) != set(WINDOW_SECONDS):
        raise StatusValidationError("A device status window map is invalid.")
    if loss:
        return {name: _loss_percent(value[name]) for name in WINDOW_SECONDS}
    return {name: _number(value[name], maxima[name], nullable=nullable) for name in WINDOW_SECONDS}


def parse_status_payload(payload: bytes, expected_client_id: str) -> SiteStatus:
    expected = _canonical_uuid(expected_client_id)
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_STATUS_BYTES:
        raise StatusValidationError("Status payload is too large.")
    try:
        document = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StatusValidationError("Status payload is not valid JSON.") from error
    if not isinstance(document, dict) or set(document) != {
        "version",
        "client_id",
        "checked_at",
        "devices",
    }:
        raise StatusValidationError("Status payload has unsupported fields.")
    if document["version"] != 2 or document["client_id"] != expected:
        raise StatusValidationError("Status payload version or client ID is invalid.")
    checked_at, checked_at_text = _checked_at(document["checked_at"])
    raw_devices = document["devices"]
    if not isinstance(raw_devices, list) or len(raw_devices) > MAX_STATUS_DEVICES:
        raise StatusValidationError("Status device list is invalid.")
    devices: list[DeviceStatus] = []
    seen: set[str] = set()
    for item in raw_devices:
        if not isinstance(item, dict) or set(item) != {
            "device_id", "online", "monitoring_state", "current_rtt_ms",
            "last_rtt_ms", "last_success_at", "loss", "rtt_avg_ms",
            "observed_seconds",
        }:
            raise StatusValidationError("A device status has unsupported fields.")
        device_id = item["device_id"]
        online = item["online"]
        monitoring_state = item["monitoring_state"]
        if (
            not isinstance(device_id, str)
            or not device_id
            or len(device_id) > 500
            or device_id in seen
        ):
            raise StatusValidationError("A device status identifier is invalid.")
        if online is not None and not isinstance(online, bool):
            raise StatusValidationError("A device status online flag is invalid.")
        if monitoring_state not in {"normal", "verify", "degraded"}:
            raise StatusValidationError("A device monitoring state is invalid.")
        current_rtt = _number(item["current_rtt_ms"], 10_000)
        last_rtt = _number(item["last_rtt_ms"], 10_000)
        if online is not True and current_rtt is not None:
            raise StatusValidationError("An offline device cannot include current RTT.")
        last_success_at = _optional_timestamp(item["last_success_at"])
        loss = _window_map(item["loss"], {name: 100.0 for name in WINDOW_SECONDS}, nullable=True, loss=True)
        averages = _window_map(
            item["rtt_avg_ms"],
            {name: 10_000.0 for name in WINDOW_SECONDS},
            nullable=True,
        )
        observed_raw = _window_map(item["observed_seconds"], WINDOW_SECONDS, nullable=False)
        seen.add(device_id)
        devices.append(DeviceStatus(
            device_id, online, monitoring_state, current_rtt, last_rtt,
            last_success_at, loss, averages,
            {name: float(value) for name, value in observed_raw.items() if value is not None},
        ))
    return SiteStatus(expected, checked_at, checked_at_text, tuple(devices))


class RemoteStatusStore:
    def __init__(self, *, stale_after_seconds: float = 180) -> None:
        self.stale_after_seconds = stale_after_seconds
        self._statuses: dict[str, SiteStatus] = {}
        self._lock = threading.RLock()

    def ingest(self, client_id: str, payload: bytes) -> SiteStatus:
        status = parse_status_payload(payload, client_id)
        now = datetime.now(timezone.utc)
        if (status.checked_at - now).total_seconds() > 300:
            raise StatusValidationError("Status checked_at is too far in the future.")
        with self._lock:
            current = self._statuses.get(client_id)
            if current is None or status.checked_at >= current.checked_at:
                self._statuses[client_id] = status
        return status

    def liveness_payload(
        self,
        client_id: str,
        repository: Repository,
        *,
        now: datetime | None = None,
    ) -> dict:
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            report = self._statuses.get(client_id)
        stale = (
            report is None
            or (current_time - report.checked_at).total_seconds()
            > self.stale_after_seconds
        )
        base = repository.list_liveness_statuses()
        report_by_device_id: dict[str, DeviceStatus] = {}
        if report is not None and not stale:
            by_external_id = repository.imported_device_ids()
            report_by_device_id = {
                local_id: result
                for result in report.devices
                if (local_id := by_external_id.get(result.device_id)) is not None
            }
        devices = []
        for item in base:
            result = report_by_device_id.get(item.device_id)
            state = (
                "unknown" if result is None or result.online is None
                else ("online" if result.online else "offline")
            )
            devices.append({
                "device_id": item.device_id,
                "address": item.address,
                "state": state,
                "checked_at": report.checked_at_text if result and report else None,
                "latency_ms": result.current_rtt_ms if result else None,
                "online": result.online if result else None,
                "monitoring_state": result.monitoring_state if result else None,
                "current_rtt_ms": result.current_rtt_ms if result else None,
                "last_rtt_ms": result.last_rtt_ms if result else None,
                "last_success_at": result.last_success_at if result else None,
                "loss": result.loss if result else None,
                "rtt_avg_ms": result.rtt_avg_ms if result else None,
                "observed_seconds": result.observed_seconds if result else None,
            })
        return {
            "generated_at": current_time.isoformat().replace("+00:00", "Z"),
            "available": not stale,
            "stale": stale,
            "checked_at": report.checked_at_text if report else None,
            "summary": {
                "devices": len(devices),
                "online": sum(item["state"] == "online" for item in devices),
                "offline": sum(item["state"] == "offline" for item in devices),
                "unknown": sum(item["state"] == "unknown" for item in devices),
            },
            "devices": devices,
        }
