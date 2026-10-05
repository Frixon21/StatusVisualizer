param([string]$PythonPath = "python")

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $projectDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$stagingDir = Join-Path $projectDir "dist-staging"

function Invoke-CheckedCommand {
    param([string]$FilePath, [string[]]$ArgumentList)
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-CheckedCommand $PythonPath @("-m", "venv", $venvDir)
}
Invoke-CheckedCommand $venvPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-CheckedCommand $venvPython @("-m", "pip", "install", "-r", (Join-Path $projectDir "requirements-dev.txt"))
Invoke-CheckedCommand $venvPython @(
    "-m", "PyInstaller", "--clean", "--noconfirm",
    (Join-Path $projectDir "LanTopoLogMqttHelper.spec"),
    "--distpath", $stagingDir,
    "--workpath", (Join-Path $projectDir "build-helper")
)
$internalExe = Join-Path $stagingDir "LanTopoLogMqttHelper.exe"
$serviceExe = Join-Path $stagingDir "LanTopoLogMqttService.exe"
Move-Item -LiteralPath $internalExe -Destination $serviceExe -Force
Write-Host "Service build complete: $serviceExe"
