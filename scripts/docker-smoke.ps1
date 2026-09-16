[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 18092
)

$ErrorActionPreference = "Stop"
$smokeProject = "status-visualizer-smoke-$PID"
$previousPublishedPort = $env:STATUS_VISUALIZER_PORT
$baseUri = "http://127.0.0.1:$Port"

function Wait-StatusVisualizerHealth {
    param([Parameter(Mandatory)][string]$Uri)

    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$Uri/api/health" -TimeoutSec 2
            if ($health.status -eq "ok") {
                return
            }
        }
        catch {
            if ($attempt -eq 30) {
                throw
            }
        }
        Start-Sleep -Seconds 1
    }
    throw "Status Visualizer did not become healthy at $Uri"
}

try {
    $env:STATUS_VISUALIZER_PORT = [string]$Port
    docker compose --project-name $smokeProject up --build --detach
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to start the smoke-test stack"
    }
    Wait-StatusVisualizerHealth -Uri $baseUri

    $recordName = "Docker persistence check $PID"
    $payload = @{
        name = $recordName
        address = "192.168.250.250"
        notes = "Temporary Docker smoke-test record"
        x = 0.5
        y = 0.5
        node_type = "other"
        icon_type = "auto"
        node_shape = "icon"
        mac_address = ""
        locked = $true
    } | ConvertTo-Json
    $created = Invoke-RestMethod -Uri "$baseUri/api/devices" -Method Post -ContentType "application/json" -Body $payload

    docker compose --project-name $smokeProject stop
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to stop the first smoke-test container"
    }
    docker compose --project-name $smokeProject rm --force
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to remove the first smoke-test container"
    }
    docker compose --project-name $smokeProject up --detach --no-build
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to recreate the smoke-test container"
    }
    Wait-StatusVisualizerHealth -Uri $baseUri

    $devices = Invoke-RestMethod -Uri "$baseUri/api/devices" -TimeoutSec 5
    if (-not ($devices | Where-Object { $_.id -eq $created.id -and $_.name -eq $recordName })) {
        throw "Persistence check failed after container replacement"
    }

    Write-Host "Docker smoke test passed: health, non-root startup, and SQLite persistence verified."
}
finally {
    docker compose --project-name $smokeProject down --volumes --remove-orphans
    if ($null -eq $previousPublishedPort) {
        Remove-Item Env:STATUS_VISUALIZER_PORT -ErrorAction SilentlyContinue
    }
    else {
        $env:STATUS_VISUALIZER_PORT = $previousPublishedPort
    }
}
