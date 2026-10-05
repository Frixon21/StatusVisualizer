from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

paho = pytest.importorskip("paho.mqtt.client")

from app.mqtt_snapshot import build_snapshot_zip, read_snapshot_zip
from app.site_registry import ClientState, SiteRegistry
from tests.lantopolog_fixture import export_files


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _mosquitto_available() -> bool:
    return shutil.which("mosquitto") is not None


@pytest.mark.skipif(not _mosquitto_available(), reason="mosquitto broker is not installed")
def test_retained_snapshot_survives_publisher_offline(tmp_path: Path) -> None:
    port = _free_port()
    config_path = tmp_path / "mosquitto.conf"
    config_path.write_text(
        "\n".join(
            [
                f"listener {port}",
                "allow_anonymous true",
                "persistence true",
                f"persistence_location {(tmp_path / 'persist').as_posix()}",
                "max_packet_size 1048576",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "persist").mkdir()
    broker = subprocess.Popen(
        ["mosquitto", "-c", str(config_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        client_id = str(uuid.uuid4())
        files = {name: content.encode() for name, content in export_files().items()}
        payload = build_snapshot_zip(files, client_id)
        topic = f"statusvisualizer/{client_id}/snapshot"

        publisher = paho.Client(
            callback_api_version=paho.CallbackAPIVersion.VERSION2,
            client_id=f"publisher-{client_id}",
            protocol=paho.MQTTv311,
        )
        publisher.connect("127.0.0.1", port, keepalive=30)
        publisher.loop_start()
        info = publisher.publish(topic, payload, qos=1, retain=True)
        info.wait_for_publish(timeout=5)
        assert info.is_published()
        publisher.disconnect()
        publisher.loop_stop()

        received: dict[str, bytes] = {}

        def on_message(_client, _userdata, message) -> None:
            received["payload"] = bytes(message.payload)

        subscriber = paho.Client(
            callback_api_version=paho.CallbackAPIVersion.VERSION2,
            client_id=f"subscriber-{client_id}",
            protocol=paho.MQTTv311,
        )
        subscriber.on_message = on_message
        subscriber.connect("127.0.0.1", port, keepalive=30)
        subscriber.subscribe("statusvisualizer/+/snapshot", qos=1)
        subscriber.loop_start()
        deadline = time.time() + 5
        while "payload" not in received and time.time() < deadline:
            time.sleep(0.05)
        subscriber.disconnect()
        subscriber.loop_stop()

        assert "payload" in received
        package = read_snapshot_zip(received["payload"], client_id)
        registry = SiteRegistry(tmp_path / "data")
        result = registry.ingest(client_id, received["payload"])
        assert result.client.state is ClientState.PENDING
        assert result.client.snapshot_hash == package.snapshot_hash
        approved = registry.patch_client(client_id, display_name="Retained", state="approved")
        assert approved.last_imported_at is not None
        assert registry.repository_for(client_id).topology_snapshot()["nodes"]
    finally:
        broker.terminate()
        broker.wait(timeout=5)
