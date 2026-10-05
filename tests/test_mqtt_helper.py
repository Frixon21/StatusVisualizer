from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from lantopolog_mqtt_helper.config import HelperConfig, load_config
from lantopolog_mqtt_helper.identity import load_or_create_client_id
from lantopolog_mqtt_helper.publisher import SnapshotPublisher
from lantopolog_mqtt_helper.service import HelperService, create_mqtt_client
from lantopolog_mqtt_helper.watcher import ExportFolderWatcher, collect_export_files

VALID_SWITCH_LIST = (
    b'N;IP;Model;"Serial number";"MAC address";"SNMP Version";Name;Location;Description\n'
    b"1;192.168.1.2;J9299A;SER-1;C09134866580;v2c;Core;Rack;Switch\n"
)


def test_identity_is_created_once_and_persisted(tmp_path: Path) -> None:
    state_file = tmp_path / "client-id"

    first = load_or_create_client_id(state_file)
    second = load_or_create_client_id(state_file)

    assert uuid.UUID(first).version == 4
    assert second == first
    assert state_file.read_text(encoding="ascii").strip() == first


def test_corrupt_identity_is_rejected_instead_of_replaced(tmp_path: Path) -> None:
    state_file = tmp_path / "client-id"
    state_file.write_text("customer-office", encoding="ascii")

    with pytest.raises(ValueError, match="client identity"):
        load_or_create_client_id(state_file)

    assert state_file.read_text(encoding="ascii") == "customer-office"


def test_config_defaults_to_verified_tls_and_keeps_state_external(tmp_path: Path) -> None:
    export_folder = tmp_path / "exports"
    export_folder.mkdir()
    config_path = tmp_path / "helper.json"
    config_path.write_text(
        json.dumps({"export_folder": str(export_folder), "broker": {"host": "mqtt.test"}}),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.broker.port == 8883
    assert config.broker.tls.enabled is True
    assert config.broker.tls.insecure is False
    assert config.state_dir != config_path.parent
    assert config.status_interval_seconds == 30
    assert config.ping_timeout_seconds == 2
    assert config.ping_concurrency == 20


def test_helper_config_rejects_insecure_tls_and_hides_password(tmp_path: Path) -> None:
    export_folder = tmp_path / "exports"
    export_folder.mkdir()
    config_path = tmp_path / "helper.json"
    config_path.write_text(
        json.dumps({
            "export_folder": str(export_folder),
            "state_dir": str(tmp_path / "state"),
            "broker": {
                "host": "mqtt.test",
                "username": "publisher",
                "password": "super-secret",
                "tls": {"enabled": True, "insecure": True},
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="insecure"):
        load_config(config_path)

    config_path.write_text(
        json.dumps({
            "export_folder": str(export_folder),
            "state_dir": str(tmp_path / "state"),
            "broker": {
                "host": "mqtt.test",
                "username": "publisher",
                "password": "super-secret",
            },
        }),
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.broker.password == "super-secret"
    assert "super-secret" not in repr(config.broker)


def test_config_rejects_unknown_keys_and_missing_export_folder(tmp_path: Path) -> None:
    config_path = tmp_path / "helper.json"
    config_path.write_text(
        json.dumps({"export_folder": str(tmp_path / "missing"), "broker": {"host": "x"}, "password2": "oops"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown"):
        load_config(config_path)


@pytest.mark.parametrize(
    "changes",
    (
        {"quiet_seconds": 0},
        {"poll_seconds": 0},
        {"publish_timeout_seconds": -1},
        {"status_interval_seconds": 0},
        {"ping_timeout_seconds": 3},
        {"ping_concurrency": 0},
        {"ping_concurrency": 2.5},
        {"broker": {"host": "mqtt.test", "port": "8883"}},
        {"broker": {"host": "mqtt.test", "username": ["bad"]}},
        {"broker": {"host": "mqtt.test", "tls": {"enabled": "false"}}},
    ),
)
def test_config_rejects_invalid_types_and_intervals(tmp_path: Path, changes: dict) -> None:
    export_folder = tmp_path / "exports"
    export_folder.mkdir()
    value = {"export_folder": str(export_folder), "broker": {"host": "mqtt.test"}, **changes}
    config_path = tmp_path / "helper.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises((TypeError, ValueError)):
        load_config(config_path)


def test_collect_export_files_preserves_relative_names_and_ignores_other_files(tmp_path: Path) -> None:
    (tmp_path / "Tmp").mkdir()
    (tmp_path / "sw_list.csv").write_bytes(VALID_SWITCH_LIST)
    (tmp_path / "Tmp" / "swlist.csv").write_bytes(b"fallback")
    (tmp_path / "notes.txt").write_text("private notes", encoding="utf-8")

    files = collect_export_files(tmp_path)

    assert files == {
        "Tmp/swlist.csv": b"fallback",
        "sw_list.csv": VALID_SWITCH_LIST,
    }


def test_watcher_only_returns_files_after_the_quiet_period(tmp_path: Path) -> None:
    export = tmp_path / "sw_list.csv"
    export.write_bytes(VALID_SWITCH_LIST)
    watcher = ExportFolderWatcher(tmp_path, quiet_seconds=10)

    assert watcher.poll(now=100) is None
    assert watcher.poll(now=109.9) is None
    assert watcher.poll(now=110) == {"sw_list.csv": VALID_SWITCH_LIST}
    assert watcher.poll(now=111) is None
    assert watcher.poll(now=200) is None


def test_watcher_force_recollects_after_emit(tmp_path: Path) -> None:
    export = tmp_path / "sw_list.csv"
    export.write_bytes(VALID_SWITCH_LIST)
    watcher = ExportFolderWatcher(tmp_path, quiet_seconds=10)
    assert watcher.poll(now=100) is None
    assert watcher.poll(now=110) == {"sw_list.csv": VALID_SWITCH_LIST}
    assert watcher.poll(now=111) is None
    assert watcher.poll(now=112, force=True) == {"sw_list.csv": VALID_SWITCH_LIST}


def test_watcher_restarts_quiet_period_when_a_file_changes(tmp_path: Path) -> None:
    export = tmp_path / "sw_list.csv"
    export.write_bytes(VALID_SWITCH_LIST)
    watcher = ExportFolderWatcher(tmp_path, quiet_seconds=10)
    assert watcher.poll(now=100) is None

    export.write_bytes(VALID_SWITCH_LIST + b"\n")
    assert watcher.poll(now=108) is None
    assert watcher.poll(now=117.9) is None
    assert watcher.poll(now=118) == {"sw_list.csv": VALID_SWITCH_LIST + b"\n"}


class _PublishInfo:
    def __init__(self, *, published: bool = True) -> None:
        self.published = published
        self.waited = False

    def wait_for_publish(self, timeout: float) -> None:
        self.waited = True

    def is_published(self) -> bool:
        return self.published


class _MqttClient:
    def __init__(self, info: _PublishInfo) -> None:
        self.info = info
        self.calls: list[tuple[str, bytes, int, bool]] = []

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> _PublishInfo:
        self.calls.append((topic, payload, qos, retain))
        return self.info


def _config(tmp_path: Path) -> HelperConfig:
    export = tmp_path / "exports"
    export.mkdir(exist_ok=True)
    return HelperConfig.for_test(export_folder=export, state_dir=tmp_path / "state")


def test_publisher_uses_qos_one_retain_and_saves_hash_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_id = "831e6502-0d5b-49e0-aac8-04f08c39fc4e"
    client = _MqttClient(_PublishInfo())
    monkeypatch.setattr("lantopolog_mqtt_helper.publisher.calculate_snapshot_hash", lambda files: "abc")
    monkeypatch.setattr("lantopolog_mqtt_helper.publisher.build_snapshot_zip", lambda files, cid: b"zip")
    publisher = SnapshotPublisher(_config(tmp_path), client_id, client)

    assert publisher.publish({"sw_list.csv": VALID_SWITCH_LIST}) is True

    assert client.calls == [(f"statusvisualizer/{client_id}/snapshot", b"zip", 1, True)]
    assert client.info.waited is True
    assert (_config(tmp_path).state_dir / "last-published-hash").read_text(encoding="ascii") == "abc\n"


def test_publisher_does_not_save_hash_when_confirmation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _MqttClient(_PublishInfo(published=False))
    monkeypatch.setattr("lantopolog_mqtt_helper.publisher.calculate_snapshot_hash", lambda files: "abc")
    monkeypatch.setattr("lantopolog_mqtt_helper.publisher.build_snapshot_zip", lambda files, cid: b"zip")
    publisher = SnapshotPublisher(_config(tmp_path), str(uuid.uuid4()), client)

    with pytest.raises(RuntimeError, match="confirm"):
        publisher.publish({"sw_list.csv": VALID_SWITCH_LIST})

    assert not (_config(tmp_path).state_dir / "last-published-hash").exists()


def test_publisher_skips_unchanged_snapshot_except_when_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _MqttClient(_PublishInfo())
    monkeypatch.setattr("lantopolog_mqtt_helper.publisher.calculate_snapshot_hash", lambda files: "same")
    monkeypatch.setattr("lantopolog_mqtt_helper.publisher.build_snapshot_zip", lambda files, cid: b"zip")
    config = _config(tmp_path)
    config.state_dir.mkdir()
    (config.state_dir / "last-published-hash").write_text("same\n", encoding="ascii")
    publisher = SnapshotPublisher(config, str(uuid.uuid4()), client)

    assert publisher.publish({"sw_list.csv": VALID_SWITCH_LIST}) is False
    assert publisher.publish({"sw_list.csv": VALID_SWITCH_LIST}, force=True) is True
    assert len(client.calls) == 1


class _RuntimeClient(_MqttClient):
    def __init__(self) -> None:
        super().__init__(_PublishInfo())
        self.on_connect = None
        self.on_disconnect = None
        self.operations: list[object] = []

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self.operations.append(("connect", host, port, keepalive))

    def loop_start(self) -> None:
        self.operations.append("start")

    def disconnect(self) -> None:
        self.operations.append("disconnect")

    def loop_stop(self) -> None:
        self.operations.append("stop")


def test_service_forces_current_snapshot_after_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    client = _RuntimeClient()
    monkeypatch.setattr("lantopolog_mqtt_helper.service.create_mqtt_client", lambda *_args: client)
    service = HelperService(config)
    files = {"sw_list.csv": VALID_SWITCH_LIST}
    monkeypatch.setattr(
        "lantopolog_mqtt_helper.service.ExportFolderWatcher.poll",
        lambda _self, **_kwargs: files,
    )
    calls: list[bool] = []

    def publish(_files: object, *, force: bool = False) -> bool:
        calls.append(force)
        service.stop_requested.set()
        return True

    service.publisher.publish = publish  # type: ignore[method-assign]
    service._on_connect(client, None, None, 0, None)

    service.run()

    assert calls == [True]
    assert service.force_publish.is_set() is False
    assert client.operations == [("connect", "mqtt.test", 8883, 60), "start", "disconnect", "stop"]


def test_service_starts_status_monitoring_from_the_stable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    client = _RuntimeClient()
    monkeypatch.setattr("lantopolog_mqtt_helper.service.create_mqtt_client", lambda *_args: client)
    service = HelperService(config)
    service.connected.set()
    files = {"sw_list.csv": VALID_SWITCH_LIST}
    monkeypatch.setattr(
        "lantopolog_mqtt_helper.service.ExportFolderWatcher.poll",
        lambda _self, **_kwargs: files,
    )
    service.publisher.publish = lambda _files, **_kwargs: True  # type: ignore[method-assign]
    status_calls: list[tuple[str, str]] = []
    original_update = service.status_monitor.update_topology

    def update_topology(value: object) -> None:
        original_update(value)  # type: ignore[arg-type]
        status_calls.extend((target.device_id, target.address) for target in service.status_monitor.targets)

    async def publish_status() -> int:
        service.stop_requested.set()
        return len(service.status_monitor.targets)

    service.status_monitor.update_topology = update_topology  # type: ignore[method-assign]
    service.status_monitor.publish_status = publish_status  # type: ignore[method-assign]

    service.run()

    assert status_calls
    assert set(status_calls) == {("switch:192.168.1.2", "192.168.1.2")}


def test_service_records_disconnect_and_rejected_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _RuntimeClient()
    monkeypatch.setattr("lantopolog_mqtt_helper.service.create_mqtt_client", lambda *_args: client)
    service = HelperService(_config(tmp_path))
    service.connected.set()

    service._on_disconnect(client, None, None, 7, None)
    service._on_connect(client, None, None, 5, None)

    assert service.connected.is_set() is False
    assert service.force_publish.is_set() is False


class _ReasonCode:
    def __init__(self, value: int) -> None:
        self.value = value

    def is_failure(self) -> bool:
        return self.value != 0

    def __str__(self) -> str:
        return "Success" if self.value == 0 else f"Failure({self.value})"


def test_service_accepts_paho_v2_reason_code_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _RuntimeClient()
    monkeypatch.setattr("lantopolog_mqtt_helper.service.create_mqtt_client", lambda *_args: client)
    service = HelperService(_config(tmp_path))

    service._on_connect(client, None, None, _ReasonCode(0), None)
    assert service.connected.is_set() is True
    assert service.force_publish.is_set() is True

    service._on_connect(client, None, None, _ReasonCode(5), None)
    assert service.connected.is_set() is True  # rejected connect does not clear prior success


def test_mqtt_client_applies_credentials_verified_tls_and_reconnect_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    class Client:
        def __init__(self, **kwargs: object) -> None:
            calls.append(("init", kwargs))

        def username_pw_set(self, username: str, password: str | None) -> None:
            calls.append(("auth", username, password))

        def tls_set(self, **kwargs: object) -> None:
            calls.append(("tls", kwargs))

        def tls_insecure_set(self, value: bool) -> None:
            calls.append(("insecure", value))

        def reconnect_delay_set(self, **kwargs: object) -> None:
            calls.append(("reconnect", kwargs))

    fake_paho = ModuleType("paho")
    fake_mqtt_package = ModuleType("paho.mqtt")
    fake_mqtt = ModuleType("paho.mqtt.client")
    fake_mqtt.Client = Client  # type: ignore[attr-defined]
    fake_mqtt.CallbackAPIVersion = SimpleNamespace(VERSION2=2)  # type: ignore[attr-defined]
    fake_mqtt.MQTTv311 = 4  # type: ignore[attr-defined]
    fake_paho.mqtt = fake_mqtt_package  # type: ignore[attr-defined]
    fake_mqtt_package.client = fake_mqtt  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "paho", fake_paho)
    monkeypatch.setitem(sys.modules, "paho.mqtt", fake_mqtt_package)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", fake_mqtt)
    base = _config(tmp_path)
    config = HelperConfig(
        export_folder=base.export_folder,
        state_dir=base.state_dir,
        broker=base.broker.__class__(host="broker", username="user", password="secret"),
    )

    create_mqtt_client(config, str(uuid.uuid4()))

    assert ("auth", "user", "secret") in calls
    assert ("insecure", False) in calls
    assert any(call[0] == "tls" for call in calls)


def test_cli_loads_external_config_and_runs_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lantopolog_mqtt_helper import cli

    config_path = tmp_path / "helper.json"
    config_path.write_text("{}", encoding="utf-8")
    sentinel = object()
    calls: list[object] = []
    monkeypatch.setattr(cli, "load_config", lambda path: sentinel if path == config_path else None)
    monkeypatch.setattr(cli, "run_helper", calls.append)
    monkeypatch.setattr(sys, "argv", ["helper", "--config", str(config_path)])

    cli.main()

    assert calls == [sentinel]


def test_cli_resolves_config_json_next_to_cwd_when_flag_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lantopolog_mqtt_helper import cli

    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    sentinel = object()
    calls: list[object] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda path: sentinel if path == config_path.resolve() else None)
    monkeypatch.setattr(cli, "run_helper", calls.append)
    monkeypatch.setattr(sys, "argv", ["helper"])

    cli.main()

    assert calls == [sentinel]
