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
$dataDir = Join-Path $env:ProgramData "StatusVisualizer"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Get-NetFirewallRule -DisplayName "Status Visualizer TCP *" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

if (Test-Path -LiteralPath $installDir) {
    Remove-Item -LiteralPath $installDir -Recurse -Force
}
if ($PurgeData -and (Test-Path -LiteralPath $dataDir)) {
    Remove-Item -LiteralPath $dataDir -Recurse -Force
    Write-Host "Application and topology data removed."
} else {
    Write-Host "Application removed. Topology data was kept at $dataDir"
}
