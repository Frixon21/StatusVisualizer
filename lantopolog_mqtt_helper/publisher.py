from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from app.lantopolog import parse_lantopolog_export
from app.mqtt_snapshot import build_snapshot_zip, calculate_snapshot_hash

from .config import HelperConfig
from .identity import atomic_write_text


class PublishInfo(Protocol):
    def wait_for_publish(self, timeout: float) -> None: ...
    def is_published(self) -> bool: ...


class MqttPublisher(Protocol):
    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> PublishInfo: ...


class SnapshotPublisher:
    def __init__(self, config: HelperConfig, client_id: str, mqtt_client: MqttPublisher) -> None:
        self.config = config
        self.client_id = client_id
        self.mqtt_client = mqtt_client
        self.hash_path = config.state_dir / "last-published-hash"

    def _last_hash(self) -> str | None:
        if not self.hash_path.exists():
            return None
        return self.hash_path.read_text(encoding="ascii").strip() or None

    def publish(self, files: Mapping[str, bytes], *, force: bool = False) -> bool:
        # Validation uses the exact same parser as manual imports.
        parse_lantopolog_export(files)
        snapshot_hash = calculate_snapshot_hash(files)
        if not force and snapshot_hash == self._last_hash():
            return False
        payload = build_snapshot_zip(files, self.client_id)
        topic = f"statusvisualizer/{self.client_id}/snapshot"
        info = self.mqtt_client.publish(topic, payload, qos=1, retain=True)
        info.wait_for_publish(timeout=self.config.publish_timeout_seconds)
        if not info.is_published():
            raise RuntimeError("The MQTT broker did not confirm the snapshot publish.")
        atomic_write_text(self.hash_path, f"{snapshot_hash}\n")
        return True

