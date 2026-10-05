# This controller is combined with mqtt-credentials.ps1 by build-dist-status-visualizer.ps1.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$configPath = Join-Path $here "mqtt.json"
$password = Get-VerifiedMqttPassword -ConfigPath $configPath -Kind dashboard

$exe = Join-Path $here "StatusVisualizer.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "StatusVisualizer.exe not found next to this script."
}
try {
    # The child inherits the password; it is never written to mqtt.json or disk.
    $env:STATUS_VISUALIZER_MQTT_PASSWORD = $password
    Start-Process -FilePath $exe -WorkingDirectory $here
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8092/"
    Write-Host "Dashboard started. The password was passed only in process memory."
} finally {
    Remove-Item Env:STATUS_VISUALIZER_MQTT_PASSWORD -ErrorAction SilentlyContinue
    $password = $null
}
