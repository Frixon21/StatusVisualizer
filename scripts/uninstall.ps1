param(
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this uninstaller from an elevated PowerShell window."
}

$taskName = "StatusVisualizer"
$installDir = Join-Path $env:ProgramFiles "StatusVisualizer"
$dataDir = Join-Path $installDir "data"
$legacyProgramData = Join-Path $env:ProgramData "StatusVisualizer"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Get-NetFirewallRule -DisplayName "Status Visualizer TCP *" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

if (Test-Path -LiteralPath $installDir) {
    Remove-Item -LiteralPath $installDir -Recurse -Force
}

if ($PurgeData) {
    foreach ($path in @($dataDir, $legacyProgramData)) {
        if (Test-Path -LiteralPath $path) {
            # Keep MQTT service identity under the legacy ProgramData\...\MqttHelper path.
            if ($path -eq $legacyProgramData) {
                Get-ChildItem -LiteralPath $path -Force -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -ne "MqttHelper" } |
                    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            } else {
                Remove-Item -LiteralPath $path -Recurse -Force
            }
        }
    }
    Write-Host "Application and topology data removed (MQTT service identity kept if present)."
} else {
    Write-Host "Application removed. Topology data was kept under $dataDir (and any legacy $legacyProgramData)."
}
