from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    return (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_reinstall_stops_existing_task_before_replacing_executable() -> None:
    script = _script("install.ps1")

    assert script.index("Stop-ScheduledTask") < script.index("Copy-Item")
    assert "Wait-InstalledExecutableUnlocked" in script


def test_reinstall_only_force_stops_processes_from_the_installed_path() -> None:
    script = _script("install.ps1")

    assert "ExecutablePath" in script
    assert "[System.IO.Path]::GetFullPath($_.ExecutablePath)" in script
    assert "Stop-Process -Id $_.ProcessId -Force" in script


def test_installer_rejects_an_unrelated_port_owner_and_checks_health() -> None:
    script = _script("install.ps1")

    assert "Get-NetTCPConnection -LocalPort $Port -State Listen" in script
    assert "already in use by process" in script
    assert "Invoke-RestMethod -Uri $healthUrl" in script
    assert "did not become healthy" in script


def test_reinstall_removes_old_status_visualizer_firewall_rules() -> None:
    script = _script("install.ps1")

    assert 'Get-NetFirewallRule -DisplayName "Status Visualizer TCP *"' in script
    assert "New-NetFirewallRule" not in script


def test_installer_binds_to_localhost_by_default() -> None:
    script = _script("install.ps1")

    assert "--host 127.0.0.1" in script
    assert "--host 0.0.0.0" not in script
    assert "[int]$Port = 8092" in script


def test_installer_keeps_portable_data_next_to_the_exe() -> None:
    script = _script("install.ps1")
    assert 'Join-Path $installDir "data"' in script
    assert 'Join-Path $env:ProgramData "StatusVisualizer"' not in script


def test_installer_has_no_obsolete_discovery_scope() -> None:
    script = _script("install.ps1")

    assert "[string]$Subnets" not in script
    assert "--subnets" not in script


def test_build_script_stops_when_native_tools_fail() -> None:
    script = _script("build.ps1")

    assert "Invoke-CheckedCommand" in script
    assert "$LASTEXITCODE" in script
    assert 'throw "$FilePath failed with exit code $LASTEXITCODE"' in script
    assert script.index("PyInstaller") < script.index("Build complete")
