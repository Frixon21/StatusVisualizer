from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from app.lantopolog import parse_lantopolog_export
from app.liveness import ProbeOutcome
from lantopolog_mqtt_helper.config import HelperConfig
from lantopolog_mqtt_helper.status import SiteStatusMonitor, extract_ping_targets

SWITCHES = (
    b'N;IP;Model;"Serial number";"MAC address";"SNMP Version";Name;Location;Description\n'
    b"1;192.168.1.2;J9299A;SER-1;C09134866580;v2c;Core;Rack;Switch\n"
)


class _PublishInfo:
    def __init__(self, published: bool = True) -> None:
        self.published = published
        self.wait_timeout: float | None = None

    def wait_for_publish(self, timeout: float) -> None:
        self.wait_timeout = timeout

    def is_published(self) -> bool:
        return self.published


class _Client:
    def __init__(self, published: bool = True) -> None:
        self.info = _PublishInfo(published)
        self.calls: list[tuple[str, bytes, int, bool]] = []

    def publish(
        self, topic: str, payload: bytes, qos: int, retain: bool
    ) -> _PublishInfo:
        self.calls.append((topic, payload, qos, retain))
        return self.info


class _Probe:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.calls: list[tuple[str, float]] = []

    async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
        self.calls.append((address, timeout_seconds))
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0.001)
        self.active -= 1
        if address.endswith(".2"):
            return ProbeOutcome("online", 4.25)
        return ProbeOutcome("offline", None)


class _FailingProbe:
    async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
        if address.endswith(".2"):
            raise RuntimeError("ping failed")
        return ProbeOutcome("online", 1.0)


def _config(tmp_path: Path, **changes: object) -> HelperConfig:
    export = tmp_path / "exports"
    export.mkdir()
    values = {
        "export_folder": export,
        "state_dir": tmp_path / "state",
        "broker": HelperConfig.for_test(
            export_folder=export, state_dir=tmp_path / "state"
        ).broker,
        "status_interval_seconds": 60.0,
        "ping_timeout_seconds": 2.0,
        "ping_concurrency": 20,
    }
    values.update(changes)
    return HelperConfig(**values)


def test_extract_targets_uses_imported_node_key_and_skips_missing_or_unsafe_ips() -> (
    None
):
    topology = parse_lantopolog_export({"sw_list.csv": SWITCHES})

    targets = extract_ping_targets(topology)

    assert [(target.device_id, target.address) for target in targets] == [
        ("switch:192.168.1.2", "192.168.1.2")
    ]


def test_status_cycle_publishes_one_confirmed_retained_site_payload(
    tmp_path: Path,
) -> None:
    client_id = str(uuid.uuid4())
    client = _Client()
    probe = _Probe()
    monitor = SiteStatusMonitor(
        _config(tmp_path),
        client_id,
        client,
        probe=probe,
        checked_at=lambda: "2026-09-29T12:00:00Z",
    )
    monitor.update_topology({"sw_list.csv": SWITCHES})

    asyncio.run(monitor.probe_due(force_all=True))
    asyncio.run(monitor.publish_status())

    assert len(client.calls) == 1
    topic, encoded, qos, retained = client.calls[0]
    assert topic == f"statusvisualizer/{client_id}/status"
    assert qos == 1
    assert retained is True
    assert json.loads(encoded) == {
        "version": 2,
        "client_id": client_id,
        "checked_at": "2026-09-29T12:00:00Z",
        "devices": [
            {
                "device_id": "switch:192.168.1.2",
                "online": True,
                "monitoring_state": "normal",
                "current_rtt_ms": 4.2,
                "last_rtt_ms": 4.2,
                "last_success_at": "2026-09-29T12:00:00Z",
                "loss": {"5m": None, "15m": None, "1h": None, "12h": None, "24h": None},
                "rtt_avg_ms": {"5m": None, "15m": None, "1h": None, "12h": None, "24h": None},
                "observed_seconds": {"5m": 0.0, "15m": 0.0, "1h": 0.0, "12h": 0.0, "24h": 0.0},
            }
        ],
    }
    assert probe.calls == [("192.168.1.2", 2.0)]
    assert client.info.wait_timeout == 30.0


def test_status_cycle_enforces_concurrency_and_maps_no_reply(tmp_path: Path) -> None:
    rows = [SWITCHES.rstrip(b"\n")]
    for number in range(3, 10):
        rows.append(
            f"{number};192.168.1.{number};model;serial-{number};AABBCCDDEE{number:02d};v2c;s{number};rack;switch".encode()
        )
    files = {"sw_list.csv": b"\n".join(rows) + b"\n"}
    client = _Client()
    probe = _Probe()
    monitor = SiteStatusMonitor(
        _config(tmp_path, ping_concurrency=3), str(uuid.uuid4()), client, probe=probe
    )
    monitor.update_topology(files)

    asyncio.run(monitor.probe_due(force_all=True))
    asyncio.run(monitor.publish_status())

    devices = json.loads(client.calls[0][1])["devices"]
    assert probe.maximum_active <= 3
    assert all(
        device["online"] is False
        for device in devices
        if device["device_id"] != "switch:192.168.1.2"
    )
    assert all(
        device["current_rtt_ms"] is None
        for device in devices
        if device["online"] is False
    )


def test_status_publish_requires_broker_confirmation(tmp_path: Path) -> None:
    monitor = SiteStatusMonitor(
        _config(tmp_path), str(uuid.uuid4()), _Client(False), probe=_Probe()
    )
    monitor.update_topology({"sw_list.csv": SWITCHES})

    try:
        asyncio.run(monitor.publish_status())
    except RuntimeError as error:
        assert "confirm" in str(error)
    else:
        raise AssertionError("Expected an unconfirmed status publication to fail")


def test_status_cycle_maps_probe_failures_to_no_reply(tmp_path: Path) -> None:
    monitor = SiteStatusMonitor(
        _config(tmp_path), str(uuid.uuid4()), _Client(), probe=_FailingProbe()
    )
    monitor.update_topology({"sw_list.csv": SWITCHES})

    asyncio.run(monitor.probe_due(force_all=True))
    asyncio.run(monitor.publish_status())

    devices = json.loads(monitor.mqtt_client.calls[0][1])["devices"]
    assert devices[0]["device_id"] == "switch:192.168.1.2"
    assert devices[0]["online"] is False
    assert devices[0]["current_rtt_ms"] is None
    assert devices[0]["monitoring_state"] == "verify"
