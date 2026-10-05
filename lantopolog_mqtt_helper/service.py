from __future__ import annotations

import asyncio
import logging
import threading
import time

from .config import HelperConfig
from .identity import load_or_create_client_id
from .publisher import SnapshotPublisher
from .status import SiteStatusMonitor
from .watcher import ExportFolderWatcher

LOGGER = logging.getLogger("lantopolog-mqtt-helper")


def _connect_succeeded(reason_code: object) -> bool:
    """Paho v2 may pass int or ReasonCode depending on callback/protocol details."""

    is_failure = getattr(reason_code, "is_failure", None)
    if callable(is_failure):
        return not bool(is_failure())
    if isinstance(is_failure, bool):
        return not is_failure
    try:
        return int(reason_code) == 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        text = str(reason_code).casefold()
        return text in {"0", "success", "success."}


def create_mqtt_client(config: HelperConfig, client_id: str):
    try:
        import paho.mqtt.client as mqtt  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        raise RuntimeError("paho-mqtt is required to run the MQTT helper.") from error

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"statusvisualizer-helper-{client_id}",
        protocol=mqtt.MQTTv311,
    )
    broker = config.broker
    if broker.username:
        client.username_pw_set(broker.username, broker.password)
    if broker.tls.enabled:
        client.tls_set(
            ca_certs=str(broker.tls.ca_file) if broker.tls.ca_file else None,
            certfile=str(broker.tls.cert_file) if broker.tls.cert_file else None,
            keyfile=str(broker.tls.key_file) if broker.tls.key_file else None,
        )
        client.tls_insecure_set(False)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


class HelperService:
    def __init__(self, config: HelperConfig) -> None:
        self.config = config
        self.client_id = load_or_create_client_id(config.state_dir / "client-id")
        self.client = create_mqtt_client(config, self.client_id)
        self.publisher = SnapshotPublisher(config, self.client_id, self.client)
        self.status_monitor = SiteStatusMonitor(config, self.client_id, self.client)
        self.watcher = ExportFolderWatcher(config.export_folder, config.quiet_seconds)
        self.connected = threading.Event()
        self.force_publish = threading.Event()
        self.stop_requested = threading.Event()
        self.monitor_thread: threading.Thread | None = None
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        if not _connect_succeeded(reason_code):
            LOGGER.error("MQTT connection was rejected (reason %s).", reason_code)
            return
        self.connected.set()
        self.force_publish.set()
        LOGGER.info("Connected to MQTT broker; retained snapshot will be refreshed.")

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        self.connected.clear()
        if not self.stop_requested.is_set():
            LOGGER.warning("MQTT connection lost (reason %s); reconnecting.", reason_code)

    def run(self) -> None:
        broker = self.config.broker
        LOGGER.info("Starting helper for client %s.", self.client_id)
        self.client.connect_async(broker.host, broker.port, broker.keepalive)
        self.client.loop_start()
        retry_at = 0.0
        retry_delay = 1.0
        try:
            while not self.stop_requested.wait(self.config.poll_seconds):
                now = time.monotonic()
                if not self.connected.is_set() or now < retry_at:
                    continue
                force = self.force_publish.is_set()
                files = self.watcher.poll(now=now, force=force)
                if files is not None:
                    try:
                        self.status_monitor.update_topology(files)
                        if self.monitor_thread is None:
                            self.monitor_thread = threading.Thread(
                                target=lambda: asyncio.run(self.status_monitor.run(self.stop_requested)),
                                name="status-health-monitor",
                                daemon=True,
                            )
                            self.monitor_thread.start()
                        published = self.publisher.publish(files, force=force)
                        if force:
                            self.force_publish.clear()
                        if published:
                            LOGGER.info(
                                "Published retained snapshot containing %d export files.", len(files)
                            )
                        retry_delay = 1.0
                        retry_at = 0.0
                    except (OSError, RuntimeError, ValueError) as error:
                        # Errors are intentionally bounded; topology content and credentials are never logged.
                        LOGGER.error("Snapshot publication failed: %s", str(error)[:300])
                        retry_at = now + retry_delay
                        retry_delay = min(retry_delay * 2, 30.0)
        finally:
            self.stop_requested.set()
            if self.monitor_thread is not None:
                self.monitor_thread.join(timeout=self.config.ping_timeout_seconds + 1)
            self.client.disconnect()
            self.client.loop_stop()


def run_helper(config: HelperConfig) -> None:
    HelperService(config).run()
