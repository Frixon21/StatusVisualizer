from __future__ import annotations

import sys
from pathlib import Path

from app.config import Settings, default_data_dir


def test_frozen_standalone_app_uses_current_users_local_app_data(
    monkeypatch,
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    program_data = tmp_path / "ProgramData"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.delenv("STATUS_VISUALIZER_DATA_DIR", raising=False)

    assert default_data_dir() == local_app_data / "StatusVisualizer"


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
