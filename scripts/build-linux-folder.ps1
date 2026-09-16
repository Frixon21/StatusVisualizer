param(
    [string]$OutputPath = (Join-Path (Get-Location) "status-visualizer-linux"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$OutputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$ProjectFullPath = [System.IO.Path]::GetFullPath($ProjectRoot.Path)
$ProjectPrefix = $ProjectFullPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar

if (-not $OutputFullPath.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputPath must be a child of the project root."
}

if (Test-Path -LiteralPath $OutputFullPath) {
    if (-not $Force) {
        throw "OutputPath already exists: $OutputFullPath. Re-run with -Force to replace it."
    }
    Remove-Item -LiteralPath $OutputFullPath -Recurse -Force
}

New-Item -ItemType Directory -Path $OutputFullPath | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputFullPath "scripts") | Out-Null

$files = @(
    "run.py",
    "expo_launcher.py",
    "requirements.txt",
    "Dockerfile",
    "compose.yaml",
    "README.md",
    "LINUX-README.md",
    "EXPO-RUNBOOK.md",
    "scripts/install-linux.sh",
    "scripts/install-expo-linux.sh",
    "scripts/uninstall-linux.sh",
    "scripts/uninstall-expo-linux.sh",
    "scripts/status-visualizer.service.template",
    "scripts/status-visualizer@.service.template",
    "scripts/status-visualizer-launcher.service.template"
)

foreach ($relativePath in $files) {
    $source = Join-Path $ProjectRoot $relativePath
    $target = Join-Path $OutputFullPath $relativePath
    $targetParent = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $targetParent)) {
        New-Item -ItemType Directory -Path $targetParent | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $target -Force
}

$appTarget = Join-Path $OutputFullPath "app"
New-Item -ItemType Directory -Path $appTarget | Out-Null
Copy-Item -Path (Join-Path $ProjectRoot "app\*") -Destination $appTarget -Recurse -Force

$launcherTarget = Join-Path $OutputFullPath "expo-launcher"
New-Item -ItemType Directory -Path $launcherTarget | Out-Null
Copy-Item -Path (Join-Path $ProjectRoot "expo-launcher\*") -Destination $launcherTarget -Recurse -Force

$excludedRuntimeJunk = @(
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "data",
    "build",
    "dist",
    "tests",
    "*.exe",
    "*.pyc"
)

Get-ChildItem -LiteralPath $appTarget -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $appTarget -Recurse -File |
    Where-Object { $_.Extension -eq ".pyc" } |
    Remove-Item -Force

Write-Host "Created Linux transfer folder:"
Write-Host "  $OutputFullPath"
Write-Host ""
Write-Host "Copy this entire folder to the Linux machine, then read LINUX-README.md."
