param(
    [string]$ExecutablePath = "",
    [ValidateRange(1, 65535)]
    [int]$Port = 8092
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
if (-not $ExecutablePath) {
    $ExecutablePath = Join-Path $projectDir "dist\StatusVisualizer.exe"
}
$ExecutablePath = [System.IO.Path]::GetFullPath($ExecutablePath)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer from an elevated PowerShell window."
}
if (-not (Test-Path -LiteralPath $ExecutablePath)) {
    throw "Executable not found at $ExecutablePath. Run scripts\build.ps1 first."
}

$installDir = Join-Path $env:ProgramFiles "StatusVisualizer"
$dataDir = Join-Path $installDir "data"
$installedExe = Join-Path $installDir "StatusVisualizer.exe"
$taskName = "StatusVisualizer"

function Test-InstalledExecutableUnlocked {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $true
    }
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $stream.Dispose()
        return $true
    } catch [System.IO.IOException] {
        return $false
    } catch [System.UnauthorizedAccessException] {
        return $false
    }
}

function Wait-InstalledExecutableUnlocked {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$TimeoutSeconds = 10
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-InstalledExecutableUnlocked -Path $Path) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Get-InstalledStatusVisualizerProcesses {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fileName = [System.IO.Path]::GetFileName($Path).Replace("'", "''")
    Get-CimInstance Win32_Process -Filter "Name = '$fileName'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and
            [System.IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $Path,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }
}

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Stopping the existing Status Visualizer instance..."
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

if (-not (Wait-InstalledExecutableUnlocked -Path $installedExe -TimeoutSeconds 8)) {
    # PyInstaller one-file applications use a parent and child process. Stop only
    # processes whose executable path is this installation, never a source run.
    Get-InstalledStatusVisualizerProcesses -Path $installedExe |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if (-not (Wait-InstalledExecutableUnlocked -Path $installedExe -TimeoutSeconds 8)) {
    $lockedPids = @(
        Get-InstalledStatusVisualizerProcesses -Path $installedExe |
            Select-Object -ExpandProperty ProcessId
    )
    $ownerText = if ($lockedPids.Count) { $lockedPids -join ", " } else { "unknown" }
    throw "The installed executable is still locked by process(es): $ownerText. Stop them and run the installer again."
}

$portOwners = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)
if ($portOwners.Count) {
    throw "TCP port $Port is already in use by process(es): $($portOwners -join ', '). Choose another port with -Port or stop the other service."
}

New-Item -ItemType Directory -Path $installDir -Force | Out-Null
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
if (-not $ExecutablePath.Equals($installedExe, [System.StringComparison]::OrdinalIgnoreCase)) {
    Copy-Item -LiteralPath $ExecutablePath -Destination $installedExe -Force
}

$taskArgs = "--host 127.0.0.1 --port $Port --data-dir `"$dataDir`""
$action = New-ScheduledTaskAction -Execute $installedExe -Argument $taskArgs -WorkingDirectory $installDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings -Description "Lantopolog topology visualizer" -Force | Out-Null

Get-NetFirewallRule -DisplayName "Status Visualizer TCP *" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Start-ScheduledTask -TaskName $taskName
$healthUrl = "http://127.0.0.1:$Port/api/health"
$healthy = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
        if ($health.status -eq "ok") {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $healthy) {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    $taskResult = if ($taskInfo) { $taskInfo.LastTaskResult } else { "unknown" }
    throw "Status Visualizer was installed, but it did not become healthy at $healthUrl. Scheduled-task result: $taskResult"
}

Write-Host "Status Visualizer installed and healthy. Open http://localhost:$Port"
