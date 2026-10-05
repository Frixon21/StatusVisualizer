from __future__ import annotations

import sys
from pathlib import Path

from app.config import Settings, default_data_dir


def test_frozen_standalone_app_uses_portable_data_next_to_exe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    exe_path = exe_dir / "StatusVisualizer.exe"
    exe_path.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))
    monkeypatch.delenv("STATUS_VISUALIZER_DATA_DIR", raising=False)

    assert default_data_dir() == exe_dir / "data"


def test_explicit_data_directory_environment_variable_still_wins(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "ConfiguredData"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("STATUS_VISUALIZER_DATA_DIR", str(configured))

    assert default_data_dir() == configured.resolve()


def test_default_port_avoids_windows_blocked_port() -> None:
    assert Settings().port == 8092
