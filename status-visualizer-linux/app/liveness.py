from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.models import LivenessState, utc_now
from app.network import canonical_probe_address

if TYPE_CHECKING:
    from app.database import Repository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    state: LivenessState
    latency_ms: float | None


@dataclass(frozen=True, slots=True)
class LivenessUpdate:
    device_id: str
    expected_address: str
    state: LivenessState
    checked_at: str
    latency_ms: float | None


@dataclass(frozen=True, slots=True)
class LivenessSummary:
    checked: int
    online: int
    offline: int
    unknown: int
    skipped: int
    checked_at: str


class LivenessProbe(Protocol):
    async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome: ...


def ping_command(
    address: str,
    timeout_seconds: float,
    *,
    platform: str | None = None,
    executable: str | None = None,
) -> tuple[str, ...]:
    current_platform = platform or sys.platform
    timeout = max(0.1, min(2.0, timeout_seconds))
    if current_platform == "win32":
        binary = executable or str(
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ping.exe"
        )
        return (binary, "-n", "1", "-w", str(math.ceil(timeout * 1000)), address)
    binary = executable or shutil.which("ping") or "/bin/ping"
    return (binary, "-c", "1", "-W", str(math.ceil(timeout)), address)


class IcmpProbe:
    async def check(self, address: str, timeout_seconds: float) -> ProbeOutcome:
        safe_address = canonical_probe_address(address)
        if safe_address is None:
            return ProbeOutcome("unknown", None)
        command = ping_command(safe_address, timeout_seconds)
        started = time.perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return ProbeOutcome("unknown", None)
        try:
            return_code = await asyncio.wait_for(
                process.wait(), timeout=min(2.5, timeout_seconds + 0.5)
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return ProbeOutcome("offline", None)
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        latency = round((time.perf_counter() - started) * 1000, 1)
        return ProbeOutcome("online", latency) if return_code == 0 else ProbeOutcome("offline", None)


class LivenessService:
    def __init__(
        self,
        repository: Repository,
        *,
        probe: LivenessProbe | None = None,
        max_concurrency: int = 12,
        timeout_seconds: float = 1.0,
        interval_seconds: float = 30.0,
        max_targets: int = 512,
    ) -> None:
        self._repository = repository
        self._probe = probe or IcmpProbe()
        self._max_concurrency = max(1, min(32, max_concurrency))
        self._timeout_seconds = max(0.1, min(2.0, timeout_seconds))
        self._interval_seconds = max(5.0, min(3600.0, interval_seconds))
        self._max_targets = max(1, min(2048, max_targets))
        self._cycle_lock = asyncio.Lock()

    @property
    def is_checking(self) -> bool:
        return self._cycle_lock.locked()

    async def check_now(self) -> LivenessSummary:
        async with self._cycle_lock:
            targets, total_targets = await asyncio.to_thread(
                self._repository.list_liveness_targets, self._max_targets
            )
            eligible: dict[str, list[tuple[str, str]]] = {}
            selected_count = 0
            for device_id, stored_address in targets:
                address = canonical_probe_address(stored_address)
                if address is not None and selected_count < self._max_targets:
                    eligible.setdefault(address, []).append((device_id, stored_address))
                    selected_count += 1

            async def check_one(address: str) -> tuple[str, ProbeOutcome]:
                try:
                    outcome = await asyncio.wait_for(
                        self._probe.check(address, self._timeout_seconds),
                        timeout=self._timeout_seconds + 1.0,
                    )
                except TimeoutError:
                    outcome = ProbeOutcome("offline", None)
                except asyncio.CancelledError:
                    raise
                except (OSError, RuntimeError, ValueError):
                    outcome = ProbeOutcome("unknown", None)
                return address, outcome

            checked_at = utc_now()
            addresses = tuple(eligible)
            outcome_batches = []
            for start in range(0, len(addresses), self._max_concurrency):
                batch = addresses[start:start + self._max_concurrency]
                outcome_batches.extend(await asyncio.gather(*(check_one(address) for address in batch)))
            outcomes = tuple(outcome_batches)
            updates = tuple(
                LivenessUpdate(
                    device_id=device_id,
                    expected_address=stored_address,
                    state=outcome.state,
                    checked_at=checked_at,
                    latency_ms=outcome.latency_ms,
                )
                for address, outcome in outcomes
                for device_id, stored_address in eligible[address]
            )
            if updates:
                await asyncio.to_thread(self._repository.update_liveness, updates)
            return LivenessSummary(
                checked=len(updates),
                online=sum(update.state == "online" for update in updates),
                offline=sum(update.state == "offline" for update in updates),
                unknown=sum(update.state == "unknown" for update in updates),
                skipped=total_targets - len(updates),
                checked_at=checked_at,
            )

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.check_now()
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
                # A later cycle should still run; never log addresses or command output.
                logger.warning("Liveness cycle failed (%s)", type(error).__name__)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
