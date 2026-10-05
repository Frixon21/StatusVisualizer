from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Repository
from app.liveness import (
    IcmpProbe,
    LivenessService,
    ProbeOutcome,
    canonical_probe_address,
    ping_command,
)
from app.main import create_app
from app.models import DeviceInput


def node(address: str, *, name: str = "Client") -> DeviceInput:
    return DeviceInput(name=name, address=address, x=0.5, y=0.5, node_type="workstation")


class RecordingProbe:
    def __init__(self, outcomes: list[ProbeOutcome] | None = None) -> None:
        self.addresses: list[str] = []
        self.outcomes = list(outcomes or [ProbeOutcome("online", 4.2)])

    async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
        self.addresses.append(address)
        return self.outcomes.pop(0) if self.outcomes else ProbeOutcome("online", 4.2)


def test_only_safe_literal_network_addresses_are_eligible() -> None:
    assert canonical_probe_address(" 192.168.1.5 ") == "192.168.1.5"
    assert canonical_probe_address("10.11.12.13") == "10.11.12.13"
    assert canonical_probe_address("fd00::5") == "fd00::5"
    assert canonical_probe_address("fe80::5") == "fe80::5"
    for unsafe in (
        "", "client.local", "https://192.168.1.5", "192.168.1.0/24",
        "127.0.0.1 & calc.exe", "-n 100", "127.0.0.1", "224.0.0.1", "8.8.8.8",
        "fe80::1%12", "192.168.1.0", "192.168.1.255",
    ):
        assert canonical_probe_address(unsafe) is None


def test_ping_command_is_an_argument_vector_with_a_bounded_timeout() -> None:
    windows = ping_command("192.168.1.5", 0.75, platform="win32", executable="ping.exe")
    linux = ping_command("192.168.1.5", 0.75, platform="linux", executable="/bin/ping")
    assert windows == ("ping.exe", "-n", "1", "-w", "750", "192.168.1.5")
    assert linux == ("/bin/ping", "-c", "1", "-W", "1", "192.168.1.5")
    assert all("shell" not in argument for argument in windows + linux)


def test_liveness_cycle_skips_hostnames_and_replaces_current_state(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    reachable = repository.create_device(node("192.168.1.20"))
    hostname = repository.create_device(node("printer.local", name="Printer"))
    probe = RecordingProbe([ProbeOutcome("online", 3.5), ProbeOutcome("offline", None)])
    service = LivenessService(repository, probe=probe, max_concurrency=2, timeout_seconds=0.2)

    first = asyncio.run(service.check_now())
    second = asyncio.run(service.check_now())
    statuses = {item.device_id: item for item in repository.list_liveness_statuses()}

    assert first.checked == second.checked == 1
    assert probe.addresses == ["192.168.1.20", "192.168.1.20"]
    assert statuses[reachable.id].state == "offline"
    assert statuses[reachable.id].checked_at is not None
    assert statuses[hostname.id].state == "unknown"
    assert statuses[hostname.id].checked_at is None
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 2


def test_address_change_invalidates_the_previous_result(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    saved = repository.create_device(node("192.168.1.20"))
    service = LivenessService(repository, probe=RecordingProbe(), timeout_seconds=0.2)
    asyncio.run(service.check_now())

    repository.update_device(saved.id, node("192.168.1.21"))
    refreshed = repository.get_device(saved.id)

    assert refreshed is not None
    assert refreshed.liveness_state == "unknown"
    assert refreshed.liveness_checked_at is None
    assert refreshed.liveness_latency_ms is None


def test_liveness_cycle_obeys_the_global_concurrency_limit(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    for index in range(1, 9):
        repository.create_device(node(f"192.168.10.{index}", name=f"Client {index}"))

    class BlockingProbe:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0
            self.saturated = asyncio.Event()
            self.release = asyncio.Event()

        async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 3:
                self.saturated.set()
            try:
                await self.release.wait()
                return ProbeOutcome("online", 1.0)
            finally:
                self.active -= 1

    async def exercise() -> tuple[int, int]:
        probe = BlockingProbe()
        service = LivenessService(repository, probe=probe, max_concurrency=3, timeout_seconds=1)
        task = asyncio.create_task(service.check_now())
        await asyncio.wait_for(probe.saturated.wait(), timeout=1)
        peak_before_release = probe.peak
        probe.release.set()
        result = await asyncio.wait_for(task, timeout=1)
        return peak_before_release, result.checked

    assert asyncio.run(exercise()) == (3, 8)


def test_liveness_cycle_caps_total_targets_without_allocating_unbounded_work(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    for index in range(1, 6):
        repository.create_device(node(f"192.168.20.{index}", name=f"Client {index}"))
    probe = RecordingProbe()
    service = LivenessService(repository, probe=probe, max_targets=3, timeout_seconds=0.2)

    result = asyncio.run(service.check_now())
    targets, total = repository.list_liveness_targets(3)

    assert result.checked == 3
    assert result.skipped == 2
    assert len(probe.addresses) == 3
    assert len(targets) == 3
    assert total == 5


def test_liveness_target_cap_applies_after_ineligible_addresses_are_filtered(tmp_path) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    for index, address in enumerate(("", "printer.local", "8.8.8.8", "192.168.30.10")):
        repository.create_device(node(address, name=f"Client {index}"))
    probe = RecordingProbe()
    service = LivenessService(repository, probe=probe, max_targets=1, timeout_seconds=0.2)

    result = asyncio.run(service.check_now())

    assert result.checked == 1
    assert probe.addresses == ["192.168.30.10"]


def test_current_liveness_api_checks_saved_nodes_and_never_accepts_a_target(tmp_path) -> None:
    probe = RecordingProbe([ProbeOutcome("online", 2.0)])
    settings = Settings(data_dir=tmp_path, liveness_interval_seconds=3600)
    app = create_app(settings, liveness_probe=probe)
    with TestClient(app) as client:
        created = client.post("/api/devices", json=node("192.168.1.40").model_dump()).json()
        checked = client.post(
            "/api/liveness/check",
            json={},
            headers={"X-Status-Visualizer-Request": "1"},
        )
        assert checked.status_code == 200
        assert checked.json()["summary"]["online"] == 1
        assert checked.json()["devices"][0]["device_id"] == created["id"]

        current = client.get("/api/liveness").json()
        assert current["devices"][0]["state"] == "online"
        assert client.post(
            "/api/liveness/check",
            json={"address": "192.168.1.99"},
            headers={"X-Status-Visualizer-Request": "1"},
        ).status_code == 422
        topology_node = client.get("/api/topology").json()["nodes"][0]
        assert topology_node["liveness_state"] == "online"


def test_liveness_post_rejects_cross_origin_browser_requests(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, liveness_interval_seconds=3600), liveness_probe=RecordingProbe())
    with TestClient(app) as client:
        response = client.post(
            "/api/liveness/check",
            json={},
            headers={
                "Origin": "https://attacker.example",
                "X-Status-Visualizer-Request": "1",
            },
        )
    assert response.status_code == 403

    with TestClient(app) as client:
        assert client.post("/api/liveness/check", json={}).status_code == 403


def test_liveness_post_rejects_an_overlapping_check(tmp_path) -> None:
    class BlockingProbe:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
            self.started.set()
            await asyncio.to_thread(self.release.wait)
            return ProbeOutcome("online", 1.0)

    probe = BlockingProbe()
    app = create_app(
        Settings(data_dir=tmp_path, liveness_interval_seconds=3600),
        liveness_probe=probe,
    )
    headers = {"X-Status-Visualizer-Request": "1"}
    with TestClient(app) as client:
        client.post("/api/devices", json=node("192.168.1.60").model_dump())
        first_response: list[int] = []
        first = threading.Thread(
            target=lambda: first_response.append(
                client.post("/api/liveness/check", json={}, headers=headers).status_code
            )
        )
        first.start()
        assert probe.started.wait(timeout=1)
        try:
            assert client.post("/api/liveness/check", json={}, headers=headers).status_code == 409
        finally:
            probe.release.set()
            first.join(timeout=2)
        assert first_response == [200]


def test_liveness_post_requires_a_small_declared_json_body(tmp_path) -> None:
    app = create_app(
        Settings(data_dir=tmp_path, liveness_interval_seconds=3600),
        liveness_probe=RecordingProbe(),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Status-Visualizer-Request": "1",
    }
    with TestClient(app) as client:
        missing_length = client.request(
            "POST",
            "/api/liveness/check",
            headers=headers,
            content=iter([b"{}"]),
        )
        oversized = client.post(
            "/api/liveness/check",
            headers={**headers, "Content-Length": "129"},
            content="{}",
        )
    assert missing_length.status_code == 411
    assert oversized.status_code == 413


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"liveness_interval_seconds": 0}, "interval"),
        ({"liveness_timeout_seconds": 3}, "timeout"),
        ({"liveness_max_concurrency": 0}, "concurrency"),
    ],
)
def test_liveness_settings_are_bounded(changes, message) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**changes)


def test_icmp_probe_reports_unknown_when_subprocess_cannot_start(monkeypatch) -> None:
    async def fail_to_start(*_args, **_kwargs):
        raise OSError("ping unavailable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_to_start)

    assert asyncio.run(IcmpProbe().check("192.168.1.10", 0.1)) == ProbeOutcome(
        "unknown", None
    )


def test_icmp_probe_kills_a_timed_out_subprocess(monkeypatch) -> None:
    class HangingProcess:
        returncode = None

        def __init__(self) -> None:
            self.killed = False

        async def wait(self) -> int:
            if self.killed:
                return -9
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        def kill(self) -> None:
            self.killed = True

    process = HangingProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    assert asyncio.run(IcmpProbe().check("192.168.1.10", 0.1)) == ProbeOutcome(
        "offline", None
    )
    assert process.killed is True


def test_icmp_probe_kills_subprocess_and_propagates_cancellation(monkeypatch) -> None:
    class HangingProcess:
        returncode = None

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.killed = False

        async def wait(self) -> int:
            self.started.set()
            if self.killed:
                return -9
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        def kill(self) -> None:
            self.killed = True

    async def exercise() -> bool:
        process = HangingProcess()

        async def create_process(*_args, **_kwargs):
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
        task = asyncio.create_task(IcmpProbe().check("192.168.1.10", 1))
        await process.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return process.killed

    assert asyncio.run(exercise()) is True


@pytest.mark.parametrize(
    "failure",
    [
        OSError("socket error"),
        RuntimeError("probe error"),
        ValueError("invalid result"),
    ],
)
def test_liveness_cycle_converts_expected_probe_errors_to_unknown(tmp_path, failure) -> None:
    class FailingProbe:
        async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
            raise failure

    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    saved = repository.create_device(node("192.168.1.70"))
    result = asyncio.run(
        LivenessService(repository, probe=FailingProbe(), timeout_seconds=0.2).check_now()
    )

    status = repository.list_liveness_statuses()[0]
    assert result.unknown == 1
    assert status.device_id == saved.id
    assert status.state == "unknown"
    assert status.checked_at is not None


def test_liveness_cycle_converts_probe_timeout_to_offline(tmp_path) -> None:
    class HangingProbe:
        async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    repository.create_device(node("192.168.1.71"))
    service = LivenessService(repository, probe=HangingProbe(), timeout_seconds=0.1)
    service._timeout_seconds = 0.01

    result = asyncio.run(service.check_now())

    assert result.offline == 1
    assert repository.list_liveness_statuses()[0].state == "offline"


def test_liveness_run_logs_cycle_error_then_continues_until_stopped(
    tmp_path, monkeypatch, caplog
) -> None:
    repository = Repository(tmp_path / "status.db")
    repository.initialize()
    service = LivenessService(repository)
    service._interval_seconds = 0.001
    stop_event = asyncio.Event()
    calls = 0

    async def check_now():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database busy")
        stop_event.set()

    monkeypatch.setattr(service, "check_now", check_now)

    with caplog.at_level("WARNING", logger="app.liveness"):
        asyncio.run(service.run(stop_event))

    assert calls == 2
    assert "Liveness cycle failed (OperationalError)" in caplog.text
