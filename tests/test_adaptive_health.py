from __future__ import annotations

from lantopolog_mqtt_helper.status import AdaptiveDeviceState, RollingHealth


def test_adaptive_state_machine_verifies_and_requires_two_successes_to_recover() -> None:
    state = AdaptiveDeviceState("device", "192.0.2.1", recovery_successes=2)

    assert state.record(True, 4.0, 0.0) == "normal"
    assert state.record(False, None, 15.0) == "verify"
    assert state.record(False, None, 15.5) == "verify"
    assert state.record(False, None, 16.0) == "degraded"
    assert state.record(True, 5.0, 19.0) == "degraded"
    assert state.record(False, None, 22.0) == "degraded"
    assert state.record(True, 4.0, 25.0) == "degraded"
    assert state.record(True, 4.0, 28.0) == "normal"


def test_verify_two_successes_return_to_normal() -> None:
    state = AdaptiveDeviceState("device", "192.0.2.1")
    state.record(True, 4.0, 0.0)
    state.record(False, None, 15.0)

    assert state.record(True, 4.0, 15.5) == "verify"
    assert state.record(True, 4.0, 16.0) == "normal"


def test_weighted_loss_uses_elapsed_time_not_probe_count() -> None:
    health = RollingHealth()
    for timestamp, success, rtt in (
        (0.0, True, 4.0),
        (15.0, False, None),
        (15.5, False, None),
        (16.0, False, None),
        (19.0, False, None),
        (22.0, True, 6.0),
        (25.0, True, 5.0),
    ):
        health.record(timestamp, success, rtt)

    result = health.windows(25.0)["5m"]
    assert result["observed_seconds"] == 25.0
    assert result["loss_percent"] == 28.0
    assert result["rtt_avg_ms"] == 4.3


def test_window_loss_never_exceeds_one_hundred_percent() -> None:
    health = RollingHealth()
    timestamp = 0.0
    while timestamp <= 86_400:
        health.record(timestamp, timestamp % 30 < 1, 4.0 if timestamp % 30 < 1 else None)
        timestamp += 3.0

    for window in health.windows(100.0).values():
        loss = window["loss_percent"]
        if loss is not None:
            assert 0 <= loss <= 100.0


def test_rolling_windows_expire_and_report_partial_coverage() -> None:
    health = RollingHealth()
    health.record(0.0, False, None)
    health.record(300.0, True, 10.0)
    health.record(900.0, True, 20.0)
    health.record(3600.0, False, None)
    health.record(43_200.0, True, 30.0)
    health.record(86_400.0, True, 40.0)

    windows = health.windows(86_400.0)
    assert windows["5m"]["observed_seconds"] == 300.0
    assert windows["15m"]["observed_seconds"] == 900.0
    assert windows["1h"]["observed_seconds"] == 3600.0
    assert windows["12h"]["observed_seconds"] == 43_200.0
    assert windows["24h"]["observed_seconds"] == 86_400.0
    assert windows["24h"]["loss_percent"] == 46.2
    assert windows["24h"]["coverage_percent"] == 100.0
