from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_helper_entrypoint_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "mqtt_helper.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--config" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows helper EXE packaging is Windows-only")
def test_pyinstaller_spec_smoke() -> None:
    spec = ROOT / "LanTopoLogMqttHelper.spec"
    assert spec.is_file()
    build_script = ROOT / "scripts" / "build-mqtt-service.ps1"
    assert build_script.is_file()
    result = subprocess.run(
        [sys.executable, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
