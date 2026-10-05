from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.lantopolog import ImportedTopology, parse_lantopolog_export
from app.liveness import IcmpProbe, LivenessProbe
from app.network import canonical_probe_address

from .config import HelperConfig
from .publisher import MqttPublisher

WINDOWS = {"5m": 300.0, "15m": 900.0, "1h": 3600.0, "12h": 43_200.0, "24h": 86_400.0}
NORMAL_INTERVAL = 15.0
VERIFY_INTERVAL = 0.5
DEGRADED_INTERVAL = 3.0


@dataclass(frozen=True, slots=True)
class PingTarget:
    device_id: str
    address: str


def extract_ping_targets(topology: ImportedTopology) -> tuple[PingTarget, ...]:
    targets = (PingTarget(node.key, address) for node in topology.nodes if (address := canonical_probe_address(node.address)) is not None)
    return tuple(sorted(targets, key=lambda target: target.device_id.encode("utf-8")))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class _Bucket:
    start: float
    resolution: float
    good_seconds: float = 0.0
    bad_seconds: float = 0.0
    rtt_ms_seconds: float = 0.0
    rtt_seconds: float = 0.0


class RollingHealth:
    """Compact time-weighted observations; adaptive probes never count as equal votes."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[float, float], _Bucket] = {}
        self._last_at: float | None = None
        self._last_success: bool | None = None
        self._last_rtt_ms: float | None = None

    @staticmethod
    def _resolution(age: float) -> float:
        if age < 900:
            return 15.0
        if age < 3600:
            return 60.0
        if age < 43_200:
            return 300.0
        return 900.0

    def _add(self, start: float, end: float, success: bool, rtt_ms: float | None, now: float) -> None:
        cursor = max(start, now - WINDOWS["24h"])
        while cursor < end:
            resolution = self._resolution(now - cursor)
            bucket_start = math.floor(cursor / resolution) * resolution
            boundary = min(end, bucket_start + resolution)
            duration = boundary - cursor
            key = (resolution, bucket_start)
            bucket = self._buckets.setdefault(key, _Bucket(bucket_start, resolution))
            if success:
                bucket.good_seconds += duration
                if rtt_ms is not None:
                    bucket.rtt_ms_seconds += rtt_ms * duration
                    bucket.rtt_seconds += duration
            else:
                bucket.bad_seconds += duration
            cursor = boundary

    def _compact(self, now: float) -> None:
        retained: dict[tuple[float, float], _Bucket] = {}
        for bucket in self._buckets.values():
            if bucket.start + bucket.resolution <= now - WINDOWS["24h"]:
                continue
            resolution = self._resolution(max(0.0, now - bucket.start))
            start = math.floor(bucket.start / resolution) * resolution
            target = retained.setdefault((resolution, start), _Bucket(start, resolution))
            target.good_seconds += bucket.good_seconds
            target.bad_seconds += bucket.bad_seconds
            target.rtt_ms_seconds += bucket.rtt_ms_seconds
            target.rtt_seconds += bucket.rtt_seconds
        self._buckets = retained

    def record(self, at: float, success: bool, rtt_ms: float | None) -> None:
        if self._last_at is not None and at > self._last_at and self._last_success is not None:
            self._compact(at)
            self._add(self._last_at, at, self._last_success, self._last_rtt_ms, at)
        self._last_at = at
        self._last_success = success
        self._last_rtt_ms = rtt_ms if success else None

    def windows(self, now: float) -> dict[str, dict[str, float | None]]:
        self._compact(now)
        buckets = list(self._buckets.values())
        if self._last_at is not None and self._last_success is not None and now > self._last_at:
            ephemeral = RollingHealth()
            ephemeral._add(self._last_at, now, self._last_success, self._last_rtt_ms, now)
            buckets.extend(ephemeral._buckets.values())
        output: dict[str, dict[str, float | None]] = {}
        history_start = min((bucket.start for bucket in buckets), default=now)
        for name, duration in WINDOWS.items():
            cutoff = now - duration
            good = bad = rtt_sum = rtt_time = 0.0
            for bucket in buckets:
                total = bucket.good_seconds + bucket.bad_seconds
                if total <= 0 or bucket.start + bucket.resolution <= cutoff:
                    continue
                overlap = min(1.0, max(0.0, (bucket.start + bucket.resolution - cutoff) / bucket.resolution))
                good += bucket.good_seconds * overlap
                bad += bucket.bad_seconds * overlap
                rtt_sum += bucket.rtt_ms_seconds * overlap
                rtt_time += bucket.rtt_seconds * overlap
            available = max(0.0, min(duration, now - max(cutoff, history_start)))
            weighted_total = good + bad
            observed = min(available, weighted_total) if weighted_total > 0 else 0.0
            if observed > 0 and weighted_total > observed:
                scale = observed / weighted_total
                good *= scale
                bad *= scale
                rtt_sum *= scale
                rtt_time *= scale
            output[name] = {
                "loss_percent": round(100.0 * bad / observed, 1) if observed else None,
                "rtt_avg_ms": round(rtt_sum / rtt_time, 1) if rtt_time else None,
                "observed_seconds": round(observed, 1),
                "coverage_percent": round(100.0 * observed / duration, 1) if duration else 0.0,
            }
        return output


@dataclass(slots=True)
class AdaptiveDeviceState:
    device_id: str
    address: str
    recovery_successes: int = 2
    monitoring_state: Literal["normal", "verify", "degraded"] = "normal"
    online: bool | None = None
    current_rtt_ms: float | None = None
    last_rtt_ms: float | None = None
    last_success_at: str | None = None
    next_probe_at: float = 0.0
    _verify_results: int = 0
    _success_streak: int = 0
    health: RollingHealth = field(default_factory=RollingHealth)

    def record(self, success: bool, rtt_ms: float | None, at: float, success_at: str | None = None) -> str:
        value = round(float(rtt_ms), 1) if success and rtt_ms is not None else None
        self.health.record(at, success, value)
        self.online = success
        self.current_rtt_ms = value
        if success:
            self.last_rtt_ms = value
            self.last_success_at = success_at or self.last_success_at
            self._success_streak += 1
        else:
            self._success_streak = 0
        if self.monitoring_state == "normal" and not success:
            self.monitoring_state = "verify"
            self._verify_results = 0
        elif self.monitoring_state == "verify":
            self._verify_results += 1
            if success and self._success_streak >= self.recovery_successes:
                self.monitoring_state = "normal"
                self._verify_results = 0
            elif self._verify_results >= 2:
                self.monitoring_state = "degraded"
        elif self.monitoring_state == "degraded" and success and self._success_streak >= self.recovery_successes:
            self.monitoring_state = "normal"
        return self.monitoring_state

    def interval(self) -> float:
        return {"normal": NORMAL_INTERVAL, "verify": VERIFY_INTERVAL, "degraded": DEGRADED_INTERVAL}[self.monitoring_state]

    def snapshot(self, now: float) -> dict[str, object]:
        windows = self.health.windows(now)
        return {"device_id": self.device_id, "online": self.online, "monitoring_state": self.monitoring_state, "current_rtt_ms": self.current_rtt_ms if self.online else None, "last_rtt_ms": self.last_rtt_ms, "last_success_at": self.last_success_at, "loss": {name: values["loss_percent"] for name, values in windows.items()}, "rtt_avg_ms": {name: values["rtt_avg_ms"] for name, values in windows.items()}, "observed_seconds": {name: values["observed_seconds"] for name, values in windows.items()}}


class SiteStatusMonitor:
    def __init__(self, config: HelperConfig, client_id: str, mqtt_client: MqttPublisher, *, probe: LivenessProbe | None = None, checked_at: Callable[[], str] = _utc_now, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.config, self.client_id, self.mqtt_client = config, client_id, mqtt_client
        self.probe, self.checked_at, self.monotonic = probe or IcmpProbe(), checked_at, monotonic
        self.targets: tuple[PingTarget, ...] = ()
        self.has_topology = False
        self._states: dict[str, AdaptiveDeviceState] = {}
        self._lock = threading.RLock()

    def update_topology(self, files: Mapping[str, object]) -> None:
        targets = extract_ping_targets(parse_lantopolog_export(files))
        now = self.monotonic()
        with self._lock:
            prior, self._states = self._states, {}
            for index, target in enumerate(targets):
                state = prior.get(target.device_id)
                if state is None or state.address != target.address:
                    state = AdaptiveDeviceState(target.device_id, target.address, self.config.recovery_successes)
                    state.next_probe_at = now + NORMAL_INTERVAL * index / max(1, len(targets))
                self._states[target.device_id] = state
            self.targets, self.has_topology = targets, True

    async def _probe(self, state: AdaptiveDeviceState, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                outcome = await asyncio.wait_for(self.probe.check(state.address, self.config.ping_timeout_seconds), timeout=self.config.ping_timeout_seconds + 0.5)
            except (TimeoutError, OSError, RuntimeError, ValueError):
                outcome = None
        now = self.monotonic()
        success = outcome is not None and outcome.state == "online"
        with self._lock:
            if self._states.get(state.device_id) is not state:
                return
            state.record(success, outcome.latency_ms if success else None, now, self.checked_at() if success else None)
            state.next_probe_at = now + state.interval()

    async def probe_due(self, *, force_all: bool = False) -> int:
        now = self.monotonic()
        with self._lock:
            due = [state for state in self._states.values() if force_all or state.next_probe_at <= now]
        semaphore = asyncio.Semaphore(self.config.ping_concurrency)
        await asyncio.gather(*(self._probe(state, semaphore) for state in due))
        return len(due)

    def _payload(self, now: float) -> bytes:
        with self._lock:
            devices = [self._states[key].snapshot(now) for key in sorted(self._states)]
        return json.dumps({"version": 2, "client_id": self.client_id, "checked_at": self.checked_at(), "devices": devices}, separators=(",", ":")).encode("utf-8")

    async def publish_status(self) -> int:
        if not self.has_topology:
            return 0
        info = self.mqtt_client.publish(f"statusvisualizer/{self.client_id}/status", self._payload(self.monotonic()), qos=1, retain=True)
        await asyncio.to_thread(info.wait_for_publish, timeout=self.config.publish_timeout_seconds)
        if not info.is_published():
            raise RuntimeError("The MQTT broker did not confirm the status publish.")
        return len(self._states)

    async def run(self, stop_requested: threading.Event) -> None:
        next_publish = self.monotonic()
        while not stop_requested.is_set():
            await self.probe_due()
            now = self.monotonic()
            if self.has_topology and now >= next_publish:
                try:
                    await self.publish_status()
                except (OSError, RuntimeError, ValueError):
                    pass
                next_publish = now + self.config.status_interval_seconds
            await asyncio.sleep(0.1)
