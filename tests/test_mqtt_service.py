from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.mqtt_service import MqttSnapshotSubscriber


@dataclass
class _Message:
    topic: str
    payload: bytes


class _FakeClient:
    def __init__(self) -> None:
        self.on_connect = None
        self.on_message = None
        self.calls: list[tuple] = []

    def username_pw_set(self, username, password=None):
        self.calls.append(("auth", username, password))

    def tls_set(self, **kwargs):
        self.calls.append(("tls", kwargs))

    def tls_insecure_set(self, value):
        self.calls.append(("insecure", value))

    def reconnect_delay_set(self, minimum, maximum):
        self.calls.append(("reconnect", minimum, maximum))

    def connect_async(self, host, port, keepalive):
        self.calls.append(("connect", host, port, keepalive))

    def loop_start(self):
        self.calls.append(("start",))

    def loop_stop(self):
        self.calls.append(("stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def subscribe(self, topic, qos):
        self.calls.append(("subscribe", topic, qos))


def _config(**changes):
    values = {
        "host": "broker.example",
        "port": 8883,
        "username": "reader",
        "password": "secret",
        "tls": True,
        "ca_file": None,
        "cert_file": None,
        "key_file": None,
        "keepalive": 60,
    }
    values.update(changes)
    return type("Config", (), values)()


def test_subscriber_connects_with_verified_tls_and_resubscribes() -> None:
    fake = _FakeClient()
    received = []
    subscriber = MqttSnapshotSubscriber(_config(), received.append, client_factory=lambda: fake)

    subscriber.start()
    fake.on_connect(fake, None, None, 0)

    assert ("auth", "reader", "secret") in fake.calls
    assert any(call[0] == "tls" for call in fake.calls)
    assert ("insecure", False) in fake.calls
    assert ("subscribe", "statusvisualizer/+/snapshot", 1) in fake.calls
    assert ("subscribe", "statusvisualizer/+/status", 1) in fake.calls


def test_subscriber_only_dispatches_canonical_uuid_topics() -> None:
    fake = _FakeClient()
    received = []
    subscriber = MqttSnapshotSubscriber(_config(tls=False), received.append, client_factory=lambda: fake)
    subscriber.start()

    fake.on_message(fake, None, _Message(
        "statusvisualizer/550e8400-e29b-41d4-a716-446655440000/snapshot", b"zip"
    ))
    fake.on_message(fake, None, _Message("statusvisualizer/not-a-uuid/snapshot", b"bad"))
    fake.on_message(fake, None, _Message(
        "statusvisualizer/12345678-1234-1234-1234-123456789abc/snapshot", b"bad"
    ))
    fake.on_message(fake, None, _Message(
        "statusvisualizer/550e8400-e29b-41d4-a716-446655440000/other", b"bad"
    ))

    assert received == [("550e8400-e29b-41d4-a716-446655440000", b"zip")]


def test_subscriber_dispatches_status_separately() -> None:
    fake = _FakeClient()
    snapshots = []
    statuses = []
    subscriber = MqttSnapshotSubscriber(
        _config(tls=False), snapshots.append, on_status=statuses.append, client_factory=lambda: fake
    )
    subscriber.start()
    fake.on_message(fake, None, _Message(
        "statusvisualizer/550e8400-e29b-41d4-a716-446655440000/status", b"json"
    ))

    assert snapshots == []
    assert statuses == [("550e8400-e29b-41d4-a716-446655440000", b"json")]


def test_subscriber_stop_is_idempotent() -> None:
    fake = _FakeClient()
    subscriber = MqttSnapshotSubscriber(_config(tls=False), lambda _: None, client_factory=lambda: fake)
    subscriber.start()
    subscriber.stop()
    subscriber.stop()

    assert fake.calls.count(("disconnect",)) == 1
    assert fake.calls.count(("stop",)) == 1


def test_subscriber_start_is_idempotent_and_tracks_disconnects() -> None:
    fake = _FakeClient()
    subscriber = MqttSnapshotSubscriber(
        _config(username=None, tls=False),
        lambda _: None,
        client_factory=lambda: fake,
    )

    subscriber.stop()
    subscriber.start()
    subscriber.start()
    fake.on_connect(fake, None, None, "Success")
    assert subscriber.connected is True
    fake.on_disconnect(fake, None)

    assert subscriber.connected is False
    assert fake.calls.count(("connect", "broker.example", 8883, 60)) == 1
    assert not any(call[0] in {"auth", "tls"} for call in fake.calls)


@pytest.mark.parametrize("reason", [1, object()])
def test_subscriber_rejected_connection_does_not_subscribe(reason) -> None:
    fake = _FakeClient()
    subscriber = MqttSnapshotSubscriber(
        _config(tls=False), lambda _: None, client_factory=lambda: fake
    )
    subscriber.start()

    fake.on_connect(fake, None, None, reason)

    assert subscriber.connected is False
    assert not any(call[0] == "subscribe" for call in fake.calls)


def test_subscriber_uses_default_tls_settings_when_tls_config_is_absent() -> None:
    fake = _FakeClient()
    subscriber = MqttSnapshotSubscriber(
        _config(tls=None), lambda _: None, client_factory=lambda: fake
    )
    subscriber.start()

    tls_call = next(call for call in fake.calls if call[0] == "tls")
    assert tls_call[1]["ca_certs"] is None
    assert tls_call[1]["certfile"] is None
    assert tls_call[1]["keyfile"] is None


@pytest.mark.parametrize(
    "topic",
    [
        "statusvisualizer/client",
        "other/550e8400-e29b-41d4-a716-446655440000/snapshot",
    ],
)
def test_subscriber_ignores_noncanonical_topics(topic: str) -> None:
    fake = _FakeClient()
    received = []
    subscriber = MqttSnapshotSubscriber(
        _config(tls=False), received.append, client_factory=lambda: fake
    )
    subscriber.start()

    fake.on_message(fake, None, _Message(topic, b"ignored"))

    assert received == []


def test_subscriber_normalizes_uppercase_uuid_topics() -> None:
    fake = _FakeClient()
    received = []
    subscriber = MqttSnapshotSubscriber(
        _config(tls=False), received.append, client_factory=lambda: fake
    )
    subscriber.start()

    fake.on_message(
        fake,
        None,
        _Message(
            "statusvisualizer/550E8400-E29B-41D4-A716-446655440000/snapshot",
            b"zip",
        ),
    )

    assert received == [("550e8400-e29b-41d4-a716-446655440000", b"zip")]


def test_status_messages_are_ignored_without_a_status_callback() -> None:
    fake = _FakeClient()
    received = []
    subscriber = MqttSnapshotSubscriber(
        _config(tls=False), received.append, client_factory=lambda: fake
    )
    subscriber.start()

    fake.on_message(
        fake,
        None,
        _Message(
            "statusvisualizer/550e8400-e29b-41d4-a716-446655440000/status",
            bytearray(b"json"),
        ),
    )

    assert received == []
