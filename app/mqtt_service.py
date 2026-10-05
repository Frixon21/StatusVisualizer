from __future__ import annotations

import logging
import ssl
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

SNAPSHOT_SUBSCRIPTION = "statusvisualizer/+/snapshot"
STATUS_SUBSCRIPTION = "statusvisualizer/+/status"


class MqttSnapshotSubscriber:
    """Small MQTT transport adapter; snapshot validation remains in the registry service."""

    def __init__(
        self,
        config: Any,
        on_snapshot: Callable[[tuple[str, bytes]], None],
        *,
        on_status: Callable[[tuple[str, bytes]], None] | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config
        self._on_snapshot = on_snapshot
        self._on_status = on_status
        self._client_factory = client_factory or self._default_client
        self._client: Any | None = None
        self._started = False
        self._connected_state = False

    @property
    def connected(self) -> bool:
        return self._connected_state

    @staticmethod
    def _default_client() -> Any:
        import paho.mqtt.client as mqtt

        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def start(self) -> None:
        if self._started:
            return
        client = self._client_factory()
        client.on_connect = self._connected
        client.on_disconnect = self._disconnected
        client.on_message = self._message
        username = getattr(self._config, "username", None)
        if username:
            client.username_pw_set(username, getattr(self._config, "password", None))
        tls = getattr(self._config, "tls", None)
        if tls is None or getattr(tls, "enabled", bool(tls)):
            client.tls_set(
                ca_certs=getattr(tls, "ca_file", None),
                certfile=getattr(tls, "cert_file", None),
                keyfile=getattr(tls, "key_file", None),
                cert_reqs=ssl.CERT_REQUIRED,
            )
            client.tls_insecure_set(False)
        client.reconnect_delay_set(1, 120)
        client.connect_async(
            self._config.host,
            self._config.port,
            getattr(self._config, "keepalive", 60),
        )
        client.loop_start()
        self._client = client
        self._started = True
        logger.info("MQTT snapshot subscriber started for %s:%s", self._config.host, self._config.port)

    def stop(self) -> None:
        if not self._started or self._client is None:
            return
        self._client.disconnect()
        self._client.loop_stop()
        self._started = False
        self._connected_state = False
        logger.info("MQTT snapshot subscriber stopped")

    def _connected(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, *_: Any) -> None:
        try:
            succeeded = int(reason_code) == 0
        except (TypeError, ValueError):
            succeeded = str(reason_code).casefold() == "success"
        if succeeded:
            self._connected_state = True
            client.subscribe(SNAPSHOT_SUBSCRIPTION, qos=1)
            client.subscribe(STATUS_SUBSCRIPTION, qos=1)
            logger.info("Subscribed to MQTT retained snapshots and site status")
        else:
            self._connected_state = False
            logger.warning("MQTT connection was rejected")

    def _disconnected(self, _client: Any, _userdata: Any, *_: Any) -> None:
        self._connected_state = False

    def _message(self, _client: Any, _userdata: Any, message: Any) -> None:
        parts = str(message.topic).split("/")
        if len(parts) != 3 or parts[0] != "statusvisualizer" or parts[2] not in {"snapshot", "status"}:
            return
        try:
            parsed = uuid.UUID(parts[1])
            client_id = str(parsed)
        except (ValueError, AttributeError):
            logger.warning("Ignored MQTT snapshot on an invalid client topic")
            return
        if parsed.version != 4 or client_id != parts[1].lower():
            logger.warning("Ignored MQTT snapshot with non-canonical client UUID")
            return
        callback = self._on_snapshot if parts[2] == "snapshot" else self._on_status
        if callback is not None:
            callback((client_id, bytes(message.payload)))
