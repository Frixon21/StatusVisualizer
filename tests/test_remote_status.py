from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.lantopolog import parse_lantopolog_export
from app.mqtt_snapshot import build_snapshot_zip
from app.remote_status import (
    RemoteStatusStore,
    StatusValidationError,
    parse_status_payload,
)
from app.site_registry import SiteRegistry
from tests.lantopolog_fixture import export_files

CLIENT_ID = "550e8400-e29b-41d4-a716-446655440000"


def _payload(*, checked_at: str, client_id: str = CLIENT_ID) -> bytes:
    windows = {name: value for name, value in zip(("5m", "15m", "1h", "12h", "24h"), (0.0, 0.8, 2.1, 0.4, 0.2))}
    observed = {"5m": 300.0, "15m": 900.0, "1h": 3600.0, "12h": 7200.0, "24h": 7200.0}
    return json.dumps(
        {
            "version": 2,
            "client_id": client_id,
            "checked_at": checked_at,
            "devices": [
                {
                    "device_id": "switch:192.168.1.2",
                    "online": True, "monitoring_state": "normal",
                    "current_rtt_ms": 4.2, "last_rtt_ms": 4.2,
                    "last_success_at": checked_at, "loss": windows,
                    "rtt_avg_ms": {key: 4.3 for key in windows},
                    "observed_seconds": observed,
                },
                {
                    "device_id": "switch:192.168.1.3",
                    "online": False, "monitoring_state": "degraded",
                    "current_rtt_ms": None, "last_rtt_ms": 5.0,
                    "last_success_at": checked_at, "loss": windows,
                    "rtt_avg_ms": {key: 4.5 for key in windows},
                    "observed_seconds": observed,
                },
            ],
        }
    ).encode()


def test_status_payload_is_strict_and_bounded() -> None:
    now = datetime.now(timezone.utc)
    parsed = parse_status_payload(
        _payload(checked_at=now.isoformat().replace("+00:00", "Z")), CLIENT_ID
    )
    assert parsed.client_id == CLIENT_ID
    assert parsed.devices[0].online is True

    with pytest.raises(StatusValidationError):
        parse_status_payload(
            _payload(
                checked_at=now.isoformat(),
                client_id="550e8400-e29b-41d4-a716-446655440001",
            ),
            CLIENT_ID,
        )
    with pytest.raises(StatusValidationError):
        parse_status_payload(b"{" + b"x" * (2 * 1024 * 1024), CLIENT_ID)
    bad = json.loads(_payload(checked_at=now.isoformat()).decode())
    bad["devices"][0]["monitoring_state"] = "offline"
    with pytest.raises(StatusValidationError):
        parse_status_payload(json.dumps(bad).encode(), CLIENT_ID)


def test_store_maps_stable_external_ids_and_marks_old_status_stale(tmp_path) -> None:
    registry = SiteRegistry(tmp_path)
    files = {name: content.encode() for name, content in export_files().items()}
    registry.ingest(CLIENT_ID, build_snapshot_zip(files, CLIENT_ID))
    registry.patch_client(CLIENT_ID, display_name="Branch", state="approved")
    repository = registry.repository_for(CLIENT_ID)
    topology = repository.topology_snapshot()
    core = next(node for node in topology["nodes"] if node.name == "Core")
    remote_id = next(
        node.key for node in parse_lantopolog_export(files).nodes if node.name == "Core"
    )

    now = datetime.now(timezone.utc)
    payload = json.dumps(
        {
            "version": 2,
            "client_id": CLIENT_ID,
            "checked_at": now.isoformat().replace("+00:00", "Z"),
            "devices": [{
                "device_id": remote_id, "online": True, "monitoring_state": "normal",
                "current_rtt_ms": 3.5, "last_rtt_ms": 3.5, "last_success_at": now.isoformat(),
                "loss": {key: 0.0 for key in ("5m", "15m", "1h", "12h", "24h")},
                "rtt_avg_ms": {key: 3.5 for key in ("5m", "15m", "1h", "12h", "24h")},
                "observed_seconds": {"5m": 100.0, "15m": 100.0, "1h": 100.0, "12h": 100.0, "24h": 100.0},
            }],
        }
    ).encode()
    store = RemoteStatusStore(stale_after_seconds=180)
    store.ingest(CLIENT_ID, payload)

    fresh = store.liveness_payload(
        CLIENT_ID, repository, now=now + timedelta(seconds=179)
    )
    assert fresh["available"] is True
    assert (
        next(item for item in fresh["devices"] if item["device_id"] == core.id)["state"]
        == "online"
    )

    stale = store.liveness_payload(
        CLIENT_ID, repository, now=now + timedelta(seconds=181)
    )
    assert stale["available"] is False
    assert stale["stale"] is True
    assert all(item["state"] == "unknown" for item in stale["devices"])


def _status_document() -> dict:
    return json.loads(
        _payload(checked_at="2026-09-30T12:00:00Z").decode("utf-8")
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(version=1),
        lambda value: value.update(checked_at="not-a-date"),
        lambda value: value.update(checked_at="2026-09-30T12:00:00"),
        lambda value: value.update(devices={}),
        lambda value: value["devices"].append(value["devices"][0].copy()),
        lambda value: value["devices"].__setitem__(0, []),
        lambda value: value["devices"][0].update(device_id=""),
        lambda value: value["devices"][0].update(monitoring_state="unknown"),
        lambda value: value["devices"][0].update(current_rtt_ms=True),
        lambda value: value["devices"][0].update(current_rtt_ms=float("nan")),
        lambda value: value["devices"][0].update(current_rtt_ms=10_001),
        lambda value: value["devices"][1].update(current_rtt_ms=1),
    ],
)
def test_status_payload_rejects_invalid_shapes_and_values(mutate) -> None:
    document = _status_document()
    mutate(document)

    with pytest.raises(StatusValidationError):
        parse_status_payload(json.dumps(document).encode("utf-8"), CLIENT_ID)


@pytest.mark.parametrize("payload", [b"\xff", b"[]", "not-bytes"])
def test_status_payload_rejects_non_json_or_non_bytes(payload) -> None:
    with pytest.raises(StatusValidationError):
        parse_status_payload(payload, CLIENT_ID)


def test_store_rejects_future_status_and_preserves_newer_status() -> None:
    now = datetime.now(timezone.utc)
    store = RemoteStatusStore()
    latest = now.isoformat().replace("+00:00", "Z")
    older = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    store.ingest(CLIENT_ID, _payload(checked_at=latest))
    store.ingest(CLIENT_ID, _payload(checked_at=older))

    class _Repository:
        @staticmethod
        def list_liveness_statuses():
            return []

        @staticmethod
        def imported_device_ids():
            return {}

    result = store.liveness_payload(CLIENT_ID, _Repository(), now=now)
    assert result["checked_at"] == latest

    future = (now + timedelta(seconds=301)).isoformat().replace("+00:00", "Z")
    with pytest.raises(StatusValidationError, match="future"):
        store.ingest(CLIENT_ID, _payload(checked_at=future))
