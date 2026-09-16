param(
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $projectDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList
    )

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
    "-m", "PyInstaller",
    "--clean",
    "--noconfirm",
    (Join-Path $projectDir "StatusVisualizer.spec"),
    "--distpath",
    (Join-Path $projectDir "dist"),
    "--workpath",
    (Join-Path $projectDir "build")
)

Write-Host "Build complete: $projectDir\dist\StatusVisualizer.exe"
