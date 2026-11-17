from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update-dashboard.ps1"
POWERSHELL = shutil.which("powershell")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell is unavailable")


def _quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project with spaces"
    dashboard = project / "dist-status-visualizer" / "Dashboard"
    for path, contents in {
        project / "run.py": b"# fixture",
        project / "StatusVisualizer.spec": b"# fixture",
        project / "app/static/index.html": b"<html>fixture</html>",
        dashboard / "StatusVisualizer.exe": b"original-executable",
        dashboard / "mqtt.json": b'{"host":"fixture.invalid","username":"fixture"}',
        dashboard / "dashboard.ps1": b"# fixture launcher",
        dashboard / "Start Status Visualizer.bat": b"@echo fixture",
        dashboard / "data/status.db": b"preserve-local-database",
        dashboard / "data/status.db-wal": b"preserve-wal",
        dashboard / "data/mqtt_registry.db": b"preserve-registry",
        dashboard / "data/sites/site-one/status.db": b"preserve-site-database",
        dashboard / "user-note.txt": b"preserve-extra-user-file",
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    (dashboard / "data/empty-directory").mkdir()
    return project, dashboard


def _protected(dashboard: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(dashboard).as_posix(): path.read_bytes() if path.is_file() else None
        for path in dashboard.rglob("*")
        if ".dashboard-update-backups" not in path.parts and path.name != "StatusVisualizer.exe"
    }


def _run(project: Path, mode: str = "success", prebuilt: Path | None = None) -> subprocess.CompletedProcess[str]:
    driver = project.parent / "run-update-test.ps1"
    events = project.parent / "events.txt"
    driver.write_text(
        f"""
. {_quote(SCRIPT)}
$eventLog = {_quote(events)}
$mode = {_quote(mode)}
function Write-TestEvent {{ param([string]$Value) [IO.File]::AppendAllText($eventLog, $Value + [Environment]::NewLine) }}
function Build-DashboardCandidate {{
    param($Paths, $Stage)
    Write-TestEvent "build"
    if ($mode -eq "build-failure") {{ throw "Injected build failure" }}
    $candidate = Join-Path $Stage "fixture.exe"
    [IO.File]::WriteAllText($candidate, "updated-executable")
    return $candidate
}}
function Test-DashboardCandidate {{
    param($Candidate, $Stage, $ExpectedStatic)
    Write-TestEvent "smoke"
    if ($mode -eq "smoke-failure") {{ throw "Injected smoke failure" }}
    return [pscustomobject]@{{ Version = "fixture"; HasUpdatedHeatmapLabel = $true }}
}}
function Stop-ExactDashboardProcesses {{ param($Path) Write-TestEvent "stop:$Path" }}
function Assert-DashboardExecutableUnlocked {{
    param($Path)
    if ($mode -eq "locked") {{ throw "Injected locked executable" }}
}}
if ($mode -in @("after-replace-failure", "hash-mismatch")) {{
    function Install-DashboardCandidate {{
        param($Candidate, $Installed)
        [IO.File]::Replace($Candidate, $Installed, [NullString]::Value, $true)
        Write-TestEvent "installed"
        if ($mode -eq "after-replace-failure") {{ throw "Injected failure after atomic replacement" }}
        [IO.File]::WriteAllText($Installed, "corrupted-candidate")
    }}
}}
try {{
    $result = Invoke-DashboardUpdate -Root {_quote(project)} -PrebuiltExecutable {_quote(prebuilt or "")}
    $result | ConvertTo-Json -Compress
    exit 0
}} catch {{
    Write-Output ("UPDATE ERROR: " + $_.Exception.Message)
    exit 7
}}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-File", str(driver)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_update_preserves_every_non_exe_file_and_is_repeatable(tmp_path: Path) -> None:
    project, dashboard = _project(tmp_path)
    before = _protected(dashboard)
    first = _run(project)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (dashboard / "StatusVisualizer.exe").read_bytes() == b"updated-executable"
    assert _protected(dashboard) == before
    backups = list((dashboard / ".dashboard-update-backups").glob("*/StatusVisualizer.exe"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"original-executable"
    receipt = json.loads((backups[0].parent / "update-receipt.json").read_text())
    assert receipt["old_exe_sha256"] != receipt["new_exe_sha256"]
    assert receipt["updated_heatmap_label"] is True

    second = _run(project)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _protected(dashboard) == before
    backups = list((dashboard / ".dashboard-update-backups").glob("*/StatusVisualizer.exe"))
    assert len(backups) == 2
    events = (tmp_path / "events.txt").read_text().splitlines()
    assert events[:2] == ["build", "smoke"]
    assert events[2] == f"stop:{dashboard / 'StatusVisualizer.exe'}"


@pytest.mark.parametrize("mode", ["build-failure", "smoke-failure"])
def test_predeployment_failure_does_not_stop_or_replace_dashboard(tmp_path: Path, mode: str) -> None:
    project, dashboard = _project(tmp_path)
    before = _protected(dashboard)
    result = _run(project, mode)
    assert result.returncode != 0
    assert not any(line.startswith("stop:") for line in (tmp_path / "events.txt").read_text().splitlines())
    assert (dashboard / "StatusVisualizer.exe").read_bytes() == b"original-executable"
    assert _protected(dashboard) == before


@pytest.mark.parametrize("mode", ["locked", "after-replace-failure", "hash-mismatch"])
def test_deployment_failure_preserves_data_and_restores_original_exe(tmp_path: Path, mode: str) -> None:
    project, dashboard = _project(tmp_path)
    before = _protected(dashboard)
    result = _run(project, mode)
    assert result.returncode != 0
    assert "original EXE is intact/restored" in result.stdout
    if mode != "locked":
        assert "installed" in (tmp_path / "events.txt").read_text().splitlines()
    assert (dashboard / "StatusVisualizer.exe").read_bytes() == b"original-executable"
    assert _protected(dashboard) == before
    backups = list((dashboard / ".dashboard-update-backups").glob("*/StatusVisualizer.exe"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"original-executable"


def test_missing_existing_data_folder_is_rejected_before_build(tmp_path: Path) -> None:
    project, dashboard = _project(tmp_path)
    shutil.rmtree(dashboard / "data")
    result = _run(project)
    assert result.returncode != 0
    assert "data folder is missing" in result.stdout
    assert not (tmp_path / "events.txt").exists()
    assert (dashboard / "StatusVisualizer.exe").read_bytes() == b"original-executable"


def test_dashboard_junction_is_rejected_without_touching_target(tmp_path: Path) -> None:
    project, dashboard = _project(tmp_path)
    external = tmp_path / "external-dashboard"
    dashboard.rename(external)
    linked = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dashboard), str(external)],
        check=False, capture_output=True, text=True,
    )
    if linked.returncode:
        pytest.skip("Directory junctions are not available in this test environment")
    before = _protected(external)
    result = _run(project)
    assert result.returncode != 0
    assert "linked/junction path" in result.stdout
    assert not (tmp_path / "events.txt").exists()
    assert _protected(external) == before
    assert (external / "StatusVisualizer.exe").read_bytes() == b"original-executable"


def test_process_selection_requires_exact_executable_path(tmp_path: Path) -> None:
    target = tmp_path / "Dashboard/StatusVisualizer.exe"
    driver = tmp_path / "select-processes.ps1"
    driver.write_text(
        f"""
. {_quote(SCRIPT)}
$path = {_quote(target)}
$items = @(
    [pscustomobject]@{{ ProcessId = 1; ExecutablePath = $path }},
    [pscustomobject]@{{ ProcessId = 2; ExecutablePath = $path.ToUpperInvariant() }},
    [pscustomobject]@{{ ProcessId = 3; ExecutablePath = $path + ".other" }},
    [pscustomobject]@{{ ProcessId = 4; ExecutablePath = $null }},
    [pscustomobject]@{{ ProcessId = 5; ExecutablePath = {_quote(tmp_path / 'Other/StatusVisualizer.exe')} }}
)
@(Select-ExactDashboardProcesses -Processes $items -Path $path | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-File", str(driver)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [1, 2]


def test_update_script_has_no_destructive_pack_rebuild_or_credential_capture() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "Remove-Item" not in script
    assert "build-dist-status-visualizer.ps1" not in script
    assert "pip install" not in script
    assert "Read-Host" not in script
    assert "Stop-Process -Name" not in script
    assert 'EnvironmentVariables.Remove("STATUS_VISUALIZER_MQTT_PASSWORD")' in script
    assert "Assert-DashboardFilesPreserved" in script
    assert "[IO.File]::Replace" in script



def test_explicit_backup_can_be_used_for_later_rollback(tmp_path: Path) -> None:
    project, dashboard = _project(tmp_path)
    before = _protected(dashboard)
    result = _run(project)
    assert result.returncode == 0, result.stdout + result.stderr
    backup = next((dashboard / ".dashboard-update-backups").glob("*/StatusVisualizer.exe"))
    result = _run(project, prebuilt=backup)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (dashboard / "StatusVisualizer.exe").read_bytes() == b"original-executable"
    assert _protected(dashboard) == before
    assert (tmp_path / "events.txt").read_text().splitlines().count("build") == 1


def test_launch_only_uses_project_temp_and_restores_callers_environment(tmp_path: Path) -> None:
    project, dashboard = _project(tmp_path)
    report = tmp_path / "launch-environment.json"
    (dashboard / "dashboard.ps1").write_text(
        f"[IO.File]::WriteAllText({_quote(report)}, (@{{temp=$env:TEMP; tmp=$env:TMP}} | ConvertTo-Json -Compress))",
        encoding="utf-8",
    )
    before = _protected(dashboard)
    driver = tmp_path / "launch-only-test.ps1"
    driver.write_text(
        f"""
. {_quote(SCRIPT)}
function Get-ExactDashboardProcesses {{ param($Path) return @() }}
$beforeTemp = $env:TEMP
$beforeTmp = $env:TMP
Start-DashboardWithLocalTemp -Root {_quote(project)}
if ($env:TEMP -ne $beforeTemp -or $env:TMP -ne $beforeTmp) {{ throw "Caller environment changed" }}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-File", str(driver)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(report.read_text())
    expected = str(project / ".tmp/dashboard-runtime")
    assert actual == {"temp": expected, "tmp": expected}
    assert _protected(dashboard) == before
    assert not (dashboard / ".dashboard-update-backups").exists()
