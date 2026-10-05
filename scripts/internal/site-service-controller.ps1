param(
    [ValidateSet("Install", "Uninstall", "Run")]
    [string]$Action = "Install"
)

$ErrorActionPreference = "Stop"
$taskName = "StatusVisualizer-LanTopoLog-MQTT"
$installDir = Join-Path $env:ProgramFiles "StatusVisualizer\MqttHelper"
$dataDir = Join-Path $env:ProgramData "StatusVisualizer\MqttHelper"
$installedExe = Join-Path $installDir "LanTopoLogMqttHelper.exe"
$installedScript = Join-Path $installDir "site-helper.ps1"
$installedConfig = Join-Path $dataDir "config.json"
$secretPath = Join-Path $dataDir "mqtt-password.bin"

Add-Type -AssemblyName System.Security

function Protect-Password {
    param([Parameter(Mandatory = $true)][string]$Password)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Password)
    try {
        return [Security.Cryptography.ProtectedData]::Protect(
            $bytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine
        )
    } finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function Unprotect-Password {
    param([Parameter(Mandatory = $true)][byte[]]$ProtectedBytes)
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $ProtectedBytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    try {
        return [Text.Encoding]::UTF8.GetString($bytes)
    } finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Elevated {
    param([Parameter(Mandatory = $true)][string]$RequestedAction)
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Action $RequestedAction"
    $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    return $process.ExitCode
}

function Stop-InstalledHelper {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name = 'LanTopoLogMqttHelper.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and
            [IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $installedExe, [StringComparison]::OrdinalIgnoreCase
            )
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Install-Helper {
    $sourceDir = Split-Path -Parent $PSCommandPath
    $sourceExe = Join-Path $sourceDir "LanTopoLogMqttService.exe"
    $sourceConfig = Join-Path $sourceDir "config.json"
    if (-not (Test-Path -LiteralPath $sourceExe)) { throw "LanTopoLogMqttService.exe is missing." }
    if (-not (Test-Path -LiteralPath $sourceConfig)) { throw "config.json is missing." }

    $config = Read-MqttJsonConfig -Path $sourceConfig
    if (-not $config.export_folder -or -not (Test-Path -LiteralPath $config.export_folder -PathType Container)) {
        throw "Export folder does not exist: $($config.export_folder). Edit config.json and try again."
    }

    $password = Get-VerifiedMqttPassword -ConfigPath $sourceConfig -Kind helper
    try {
        Stop-InstalledHelper
        New-Item -ItemType Directory -Path $installDir, $dataDir -Force | Out-Null
        & icacls.exe $dataDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to secure $dataDir." }

        Copy-Item -LiteralPath $sourceExe -Destination $installedExe -Force
        Copy-Item -LiteralPath $PSCommandPath -Destination $installedScript -Force
        Copy-Item -LiteralPath $sourceConfig -Destination $installedConfig -Force
        [IO.File]::WriteAllBytes($secretPath, (Protect-Password -Password $password))

        $taskArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$installedScript`" -Action Run"
        $taskAction = New-ScheduledTaskAction `
            -Execute "powershell.exe" `
            -Argument $taskArguments `
            -WorkingDirectory $installDir
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 5 `
            -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $taskAction `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description "Publishes retained LanTopoLog snapshots to Status Visualizer" `
            -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 2
        $task = Get-ScheduledTask -TaskName $taskName
        if ($task.State -notin @("Running", "Ready")) {
            throw "Scheduled task entered unexpected state: $($task.State)."
        }
        Write-Host ""
        Write-Host "INSTALL SUCCEEDED" -ForegroundColor Green
        Write-Host "Task: $taskName ($($task.State))"
    } finally {
        $password = $null
        [GC]::Collect()
    }
}

function Uninstall-Helper {
    Stop-InstalledHelper
    if (Test-Path -LiteralPath $installDir) {
        Remove-Item -LiteralPath $installDir -Recurse -Force
    }
    Write-Host ""
    Write-Host "UNINSTALL SUCCEEDED" -ForegroundColor Green
    Write-Host "Identity, config, and encrypted password were preserved at $dataDir."
}

function Run-Helper {
    if (-not (Test-Path -LiteralPath $secretPath)) { throw "Encrypted MQTT password is missing." }
    $password = Unprotect-Password -ProtectedBytes ([IO.File]::ReadAllBytes($secretPath))
    try {
        $env:STATUS_VISUALIZER_MQTT_PASSWORD = $password
        & $installedExe --config $installedConfig
        exit $LASTEXITCODE
    } finally {
        Remove-Item Env:STATUS_VISUALIZER_MQTT_PASSWORD -ErrorAction SilentlyContinue
        $password = $null
    }
}

if ($Action -eq "Run") {
    Run-Helper
    exit
}

if (-not (Test-Administrator)) {
    exit (Invoke-Elevated -RequestedAction $Action)
}

$exitCode = 0
try {
    if ($Action -eq "Install") { Install-Helper } else { Uninstall-Helper }
} catch {
    $exitCode = 1
    Write-Host ""
    Write-Host "$($Action.ToUpperInvariant()) FAILED" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    Write-Host ""
    Read-Host "Press Enter to close"
}
exit $exitCode
