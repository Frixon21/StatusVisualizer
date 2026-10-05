param(
    [string]$PythonPath = "python",
    [string]$OutputPath = "",
    [string]$ProjectDir = "",
    [switch]$KeepBuildDirs,
    [switch]$ConfigsOnly
)

$ErrorActionPreference = "Stop"
if (-not $ProjectDir) {
    $ProjectDir = Split-Path -Parent $PSScriptRoot
}
$projectDir = [IO.Path]::GetFullPath($ProjectDir)
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectDir "dist-status-visualizer"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$stagingDir = Join-Path $projectDir "dist-staging"

function Stop-PackLockedProcesses {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return }
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and
            [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)
        } |
        ForEach-Object {
            Write-Host "Stopping locked process $($_.ProcessId) ($($_.Name))"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Milliseconds 500
}

function Copy-DeploymentConfig {
    param(
        [string]$LocalName,
        [string]$TemplatePath,
        [string]$Destination
    )
    $localPath = Join-Path $projectDir (Join-Path "pack-local" $LocalName)
    $source = $TemplatePath
    if (Test-Path -LiteralPath $localPath) {
        Write-Host "Using pack-local\$LocalName"
        $source = $localPath
    }
    Copy-Item -LiteralPath $source -Destination $Destination
}

function Write-DeploymentConfigs {
    $dashboardDir = Join-Path $OutputPath "Dashboard"
    $serviceDir = Join-Path $OutputPath "MqttService"
    $linuxDashboardDir = Join-Path $OutputPath "Dashboard-Linux"
    $linuxServiceDir = Join-Path $OutputPath "MqttService-Linux"
    New-Item -ItemType Directory -Force -Path $dashboardDir, $serviceDir, $linuxDashboardDir, $linuxServiceDir | Out-Null

    $packTemplates = Join-Path $projectDir "pack-templates"
    $dashboardMqttTemplate = Join-Path $packTemplates "dashboard-mqtt.json"
    $serviceConfigTemplate = Join-Path $packTemplates "mqtt-service.config.json"
    $serviceConfigLinuxTemplate = Join-Path $packTemplates "mqtt-service.config.linux.json"
    foreach ($template in @($dashboardMqttTemplate, $serviceConfigTemplate, $serviceConfigLinuxTemplate)) {
        if (-not (Test-Path -LiteralPath $template)) {
            throw "Missing pack template: $template"
        }
    }

    Copy-DeploymentConfig -LocalName "dashboard-mqtt.json" -TemplatePath $dashboardMqttTemplate -Destination (Join-Path $dashboardDir "mqtt.json")
    Copy-DeploymentConfig -LocalName "mqtt-service.config.json" -TemplatePath $serviceConfigTemplate -Destination (Join-Path $serviceDir "config.json")
    Copy-DeploymentConfig -LocalName "dashboard-mqtt.json" -TemplatePath $dashboardMqttTemplate -Destination (Join-Path $linuxDashboardDir "mqtt.json")
    Copy-DeploymentConfig -LocalName "mqtt-service.config.linux.json" -TemplatePath $serviceConfigLinuxTemplate -Destination (Join-Path $linuxServiceDir "config.json")
}

if ($ConfigsOnly) {
    Write-DeploymentConfigs
    return
}

function Clear-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Stop-PackLockedProcesses -Root $Path
    for ($attempt = 1; $attempt -le 8; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($attempt -eq 8) { throw }
            Write-Host "Retrying cleanup of $Path ($attempt/8)..."
            Stop-PackLockedProcesses -Root $Path
            Start-Sleep -Seconds 1
        }
    }
}

Write-Host "Building Status Visualizer EXE..."
& (Join-Path $PSScriptRoot "build.ps1") -PythonPath $PythonPath
if ($LASTEXITCODE -ne 0) { throw "build.ps1 failed" }

Write-Host "Building MQTT service EXE..."
& (Join-Path $PSScriptRoot "build-mqtt-service.ps1") -PythonPath $PythonPath
if ($LASTEXITCODE -ne 0) { throw "build-mqtt-service.ps1 failed" }

$dashboard = Join-Path $OutputPath "Dashboard"
$service = Join-Path $OutputPath "MqttService"
$linuxDashboard = Join-Path $OutputPath "Dashboard-Linux"
$linuxService = Join-Path $OutputPath "MqttService-Linux"

Clear-Directory -Path $OutputPath
# Also remove the old pack name if present.
Clear-Directory -Path (Join-Path $projectDir "dist-click-run")
Clear-Directory -Path (Join-Path $projectDir "dist-helper")
New-Item -ItemType Directory -Force -Path $dashboard, $service, $linuxDashboard, $linuxService | Out-Null

Copy-Item (Join-Path $projectDir "dist\StatusVisualizer.exe") (Join-Path $dashboard "StatusVisualizer.exe")
Copy-Item (Join-Path $stagingDir "LanTopoLogMqttService.exe") (Join-Path $service "LanTopoLogMqttService.exe")

# Produce one PowerShell implementation per folder instead of shipping internal modules.
$credentials = Get-Content (Join-Path $projectDir "scripts\internal\mqtt-credentials.ps1") -Raw
$dashboardController = Get-Content (Join-Path $projectDir "scripts\internal\start-dashboard.ps1") -Raw
[IO.File]::WriteAllText(
    (Join-Path $dashboard "dashboard.ps1"),
    $credentials + [Environment]::NewLine + $dashboardController,
    [Text.UTF8Encoding]::new($false)
)
$siteController = Get-Content (Join-Path $projectDir "scripts\internal\site-service-controller.ps1") -Raw
$siteController = $siteController.Replace(
    '$ErrorActionPreference = "Stop"',
    $credentials + [Environment]::NewLine + '$ErrorActionPreference = "Stop"'
)
[IO.File]::WriteAllText(
    (Join-Path $service "site-service.ps1"),
    $siteController,
    [Text.UTF8Encoding]::new($false)
)

Set-Content -LiteralPath (Join-Path $dashboard "Start Status Visualizer.bat") -Encoding ascii -Value @'
@echo off
setlocal
cd /d "%~dp0"
echo The dashboard will ask for the MQTT broker password and check it before starting.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dashboard.ps1"
if errorlevel 1 pause
'@

Set-Content -LiteralPath (Join-Path $service "INSTALL Service.bat") -Encoding ascii -Value @'
@echo off
setlocal
cd /d "%~dp0"
echo Edit config.json first if needed: export_folder, broker host, username.
echo INSTALL will ask for the password and verify it.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0site-service.ps1" -Action Install
if errorlevel 1 pause
'@

Set-Content -LiteralPath (Join-Path $service "UNINSTALL Service.bat") -Encoding ascii -Value @'
@echo off
setlocal
cd /d "%~dp0"
echo This removes the service scheduled task and Program Files install.
echo Identity/config/encrypted password under ProgramData are kept.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0site-service.ps1" -Action Uninstall
if errorlevel 1 pause
'@

# Linux bundles install source into a local venv because Windows cannot cross-build Linux executables.
Copy-Item (Join-Path $projectDir "run.py") (Join-Path $linuxDashboard "run.py")
Copy-Item (Join-Path $projectDir "requirements.txt") (Join-Path $linuxDashboard "requirements.txt")
Copy-Item (Join-Path $projectDir "app") (Join-Path $linuxDashboard "app") -Recurse
Copy-Item (Join-Path $projectDir "mqtt_helper.py") (Join-Path $linuxService "mqtt_service.py")
Copy-Item (Join-Path $projectDir "lantopolog_mqtt_helper") (Join-Path $linuxService "lantopolog_mqtt_helper") -Recurse
Copy-Item (Join-Path $projectDir "app") (Join-Path $linuxService "app") -Recurse

Copy-Item (Join-Path $projectDir "scripts\internal\linux\install-dashboard.sh") (Join-Path $linuxDashboard "INSTALL.sh")
Copy-Item (Join-Path $projectDir "scripts\internal\linux\uninstall-dashboard.sh") (Join-Path $linuxDashboard "UNINSTALL.sh")
Copy-Item (Join-Path $projectDir "scripts\internal\linux\install-site-service.sh") (Join-Path $linuxService "INSTALL.sh")
Copy-Item (Join-Path $projectDir "scripts\internal\linux\uninstall-site-service.sh") (Join-Path $linuxService "UNINSTALL.sh")

Write-DeploymentConfigs
[IO.File]::WriteAllText(
    (Join-Path $linuxService "requirements.txt"),
    "paho-mqtt>=2.1,<3`npydantic>=2.10,<3`n",
    [Text.UTF8Encoding]::new($false)
)

# Do not ship Python/build caches inside either Linux transfer folder.
Get-ChildItem -Path $linuxDashboard, $linuxService -Directory -Recurse -Force |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache", ".ruff_cache") } |
    Remove-Item -Recurse -Force
Get-ChildItem -Path $linuxDashboard, $linuxService -File -Recurse -Force |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
    Remove-Item -Force

foreach ($devStatic in @(
    (Join-Path $linuxDashboard "app\static\heatmap-dev.html"),
    (Join-Path $linuxService "app\static\heatmap-dev.html")
)) {
    if (Test-Path -LiteralPath $devStatic) {
        Remove-Item -LiteralPath $devStatic -Force
    }
}

Set-Content -LiteralPath (Join-Path $OutputPath "README.txt") -Encoding ascii -Value @'
Status Visualizer deployment
============================

dist-status-visualizer\
  Dashboard\          <- Windows PC that shows the UI
  MqttService\        <- Windows LanTopoLog VM
  Dashboard-Linux\    <- Linux dashboard host
  MqttService-Linux\  <- Linux LanTopoLog host

1. Edit Dashboard\mqtt.json host/username if needed.
2. Double-click Dashboard\Start Status Visualizer.bat
   - it asks for the MQTT password and checks it against the broker
3. On each LanTopoLog VM:
   - edit MqttService\config.json (export_folder + broker host/username)
   - double-click INSTALL Service.bat once (Admin/UAC Yes)
   - enter the MQTT password when asked; INSTALL verifies and encrypts it
4. Export in LanTopoLog, wait ~10 seconds.
5. In the UI: Manage sites -> Approve with a friendly name.

To remove the service from a VM, double-click UNINSTALL Service.bat.

Linux:
1. Copy the matching Linux folder to its host.
2. Edit mqtt.json or config.json.
3. Run: chmod +x INSTALL.sh UNINSTALL.sh
4. Run: sudo ./INSTALL.sh
   - the installer prompts for and checks the MQTT password
   - Dashboard-Linux listens on 127.0.0.1 by default; use --host 0.0.0.0 only on a trusted network
5. Remove later with: sudo ./UNINSTALL.sh
'@

if (-not $KeepBuildDirs) {
    Write-Host "Cleaning temporary build folders..."
    foreach ($name in @("build", "dist", "build-helper", "dist-staging")) {
        Clear-Directory -Path (Join-Path $projectDir $name)
    }
}

Write-Host ""
Write-Host "Pack ready: $OutputPath"
Write-Host "  Dashboard / MqttService             = Windows"
Write-Host "  Dashboard-Linux / MqttService-Linux = Linux source installers"
