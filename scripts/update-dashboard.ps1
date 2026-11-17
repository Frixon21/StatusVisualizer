param(
    [string]$ProjectDir = "",
    [string]$ExecutablePath = "",
    [switch]$StartOnly
)

$ErrorActionPreference = "Stop"

function Get-DashboardFileHash {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace("-", "") }
    finally { $hasher.Dispose(); $stream.Dispose() }
}

function Assert-NoUpdateReparsePoint {
    param([string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a linked/junction path: $current"
            }
        }
        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Get-DashboardUpdatePaths {
    param([string]$Root)
    if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
    $Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    Assert-NoUpdateReparsePoint $Root
    foreach ($relative in @("run.py", "StatusVisualizer.spec", "app\static\index.html")) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relative) -PathType Leaf)) {
            throw "Not a Status Visualizer source project: missing $relative"
        }
    }
    # Deliberately update only this project's existing portable Dashboard.
    $dashboard = Join-Path $Root "dist-status-visualizer\Dashboard"
    Assert-NoUpdateReparsePoint $dashboard
    foreach ($name in @("StatusVisualizer.exe", "mqtt.json", "dashboard.ps1", "Start Status Visualizer.bat")) {
        $path = Join-Path $dashboard $name
        Assert-NoUpdateReparsePoint $path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Existing Dashboard is incomplete: missing $name"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $dashboard "data") -PathType Container)) {
        throw "Existing Dashboard data folder is missing; this updater is not an installer."
    }
    $backupRoot = Join-Path $dashboard ".dashboard-update-backups"
    $stageRoot = Join-Path $Root ".tmp\dashboard-updates"
    Assert-NoUpdateReparsePoint $backupRoot
    Assert-NoUpdateReparsePoint $stageRoot
    [pscustomobject]@{
        Root = $Root
        Dashboard = $dashboard
        Installed = Join-Path $dashboard "StatusVisualizer.exe"
        BackupRoot = $backupRoot
        StageRoot = $stageRoot
        Python = Join-Path $Root ".venv\Scripts\python.exe"
    }
}

function Select-ExactDashboardProcesses {
    param([object[]]$Processes, [string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    @($Processes | Where-Object {
        $_.ExecutablePath -and
        [IO.Path]::GetFullPath($_.ExecutablePath).Equals($full, [StringComparison]::OrdinalIgnoreCase)
    })
}

function Get-ExactDashboardProcesses {
    param([string]$Path)
    $name = [IO.Path]::GetFileName($Path).Replace("'", "''")
    $processes = @(Get-CimInstance Win32_Process -Filter "Name = '$name'")
    Select-ExactDashboardProcesses -Processes $processes -Path $Path
}

function Stop-ExactDashboardProcesses {
    param([string]$Path)
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
        $matches = @(Get-ExactDashboardProcesses $Path)
        if (-not $matches.Count) { return }
        foreach ($matched in $matches) {
            # Recheck both path and start time before stopping a PID.
            $current = @(Get-CimInstance Win32_Process -Filter "ProcessId = $($matched.ProcessId)")
            $verified = @(Select-ExactDashboardProcesses -Processes $current -Path $Path)
            if ($verified.Count -eq 1 -and $verified[0].CreationDate -eq $matched.CreationDate) {
                try { Stop-Process -Id $matched.ProcessId -Force -ErrorAction Stop }
                catch {
                    if (@(Get-ExactDashboardProcesses $Path | Where-Object ProcessId -eq $matched.ProcessId).Count) {
                        throw
                    }
                }
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Could not stop the verified Dashboard processes. No executable replacement was attempted."
}

function Assert-DashboardExecutableUnlocked {
    param([string]$Path)
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    do {
        try {
            $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            $stream.Dispose()
            return
        } catch [IO.IOException] {
            Start-Sleep -Milliseconds 250
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "The Dashboard executable is still locked. It has not been replaced."
}

function Get-DashboardPreservedFiles {
    param([string]$Dashboard, [string]$BackupRoot)
    $result = @{}
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Dashboard)
    while ($pending.Count) {
        foreach ($item in @(Get-ChildItem -LiteralPath $pending.Pop() -Force)) {
            if ($item.FullName.Equals($BackupRoot, [StringComparison]::OrdinalIgnoreCase)) { continue }
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a linked/junction item in the Dashboard: $($item.FullName)"
            }
            $relative = $item.FullName.Substring($Dashboard.Length + 1)
            if ($relative -eq "StatusVisualizer.exe") { continue }
            if ($item.PSIsContainer) {
                $result[$relative] = "directory"
                $pending.Push($item.FullName)
            } else {
                $result[$relative] = (Get-DashboardFileHash $item.FullName)
            }
        }
    }
    return $result
}

function Assert-DashboardFilesPreserved {
    param([hashtable]$Before, [hashtable]$After)
    if ($Before.Count -ne $After.Count) { throw "Dashboard data/config file inventory changed during the update." }
    foreach ($name in $Before.Keys) {
        if (-not $After.ContainsKey($name) -or $Before[$name] -cne $After[$name]) {
            throw "A protected Dashboard file changed during the update: $name"
        }
    }
}

function Build-DashboardCandidate {
    param([object]$Paths, [string]$Stage)
    if (-not (Test-Path -LiteralPath $Paths.Python -PathType Leaf)) {
        throw "The project's Python environment is missing. No packages will be installed automatically."
    }
    $buildTemp = Join-Path $Stage "build-temp"
    New-Item -ItemType Directory -Path $buildTemp | Out-Null
    $previousEnvironment = @{}
    foreach ($name in @("TEMP", "TMP", "PYINSTALLER_CONFIG_DIR")) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
    $previousPreference = $ErrorActionPreference
    Push-Location -LiteralPath $Paths.Root
    try {
        $env:TEMP = $buildTemp
        $env:TMP = $buildTemp
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $Stage "pyinstaller-cache"
        $ErrorActionPreference = "Continue"
        & $Paths.Python -B -m PyInstaller --clean --noconfirm (Join-Path $Paths.Root "StatusVisualizer.spec") --distpath (Join-Path $Stage "dist") --workpath (Join-Path $Stage "build") | Out-Host
        $buildExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
        foreach ($name in $previousEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
        }
        Pop-Location
    }
    if ($buildExit -ne 0) { throw "Dashboard build failed (exit $buildExit). The running Dashboard was not touched." }
    return (Join-Path $Stage "dist\StatusVisualizer.exe")
}

function Test-DashboardCandidate {
    param([string]$Candidate, [string]$Stage, [string]$ExpectedStatic = "")
    $smokeData = Join-Path $Stage "smoke-data"
    New-Item -ItemType Directory -Path $smokeData | Out-Null
    $extractTemp = Join-Path $Stage "extract-temp"
    New-Item -ItemType Directory -Path $extractTemp | Out-Null
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Candidate
    $start.WorkingDirectory = $Stage
    $start.Arguments = "--host 127.0.0.1 --port $port --data-dir " + '"' + $smokeData + '"'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.EnvironmentVariables["TEMP"] = $extractTemp
    $start.EnvironmentVariables["TMP"] = $extractTemp
    $start.EnvironmentVariables["STATUS_VISUALIZER_DATA_DIR"] = $smokeData
    # A deliberately absent explicit config disables fallback to any real broker config.
    $start.EnvironmentVariables["STATUS_VISUALIZER_MQTT_CONFIG"] = Join-Path $Stage "mqtt-disabled.json"
    $start.EnvironmentVariables.Remove("STATUS_VISUALIZER_MQTT_PASSWORD")
    $process = $null
    $stdout = $null
    $stderr = $null
    $lastHealthError = "No healthy response"
    try {
        $process = [Diagnostics.Process]::Start($start)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $baseUrl = "http://127.0.0.1:$port"
        $health = $null
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        do {
            if ($process.HasExited) {
                throw "The candidate EXE exited before becoming healthy (exit $($process.ExitCode)); see isolated smoke logs."
            }
            try {
                $answer = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
                $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess)
                $ownIds = @(Get-ExactDashboardProcesses $Candidate | Select-Object -ExpandProperty ProcessId)
                if ($answer.status -eq "ok" -and @($owners | Where-Object { $_ -in $ownIds }).Count) {
                    $health = $answer
                    break
                }
            } catch { $lastHealthError = $_.Exception.Message }
            Start-Sleep -Milliseconds 500
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $health) { throw "The candidate EXE did not pass its isolated health check: $lastHealthError" }
        $html = (Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/" -TimeoutSec 5).Content
        $utilities = (Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/static/topology-utils.js" -TimeoutSec 5).Content
        if (-not $html.Contains("/static/topology-utils.js") -or -not $utilities.Contains("heatmapOverlayVisible")) {
            throw "The candidate EXE did not serve the expected dashboard assets."
        }
        if ($ExpectedStatic) {
            if ($html -cne [IO.File]::ReadAllText((Join-Path $ExpectedStatic "index.html")) -or
                $utilities -cne [IO.File]::ReadAllText((Join-Path $ExpectedStatic "topology-utils.js"))) {
                throw "The built EXE's UI does not match the current source files."
            }
        }
        Write-Host "Isolated EXE check passed: health and bundled UI assets."
        return [pscustomobject]@{ Version = $health.version; HasUpdatedHeatmapLabel = $html.Contains("Critical (100% loss)") }
    } finally {
        Stop-ExactDashboardProcesses $Candidate
        if ($process) {
            $process.WaitForExit(5000) | Out-Null
            if ($stdout -and $stdout.IsCompleted) {
                [IO.File]::WriteAllText((Join-Path $Stage "smoke-stdout.log"), $stdout.Result)
            }
            if ($stderr -and $stderr.IsCompleted) {
                [IO.File]::WriteAllText((Join-Path $Stage "smoke-stderr.log"), $stderr.Result)
            }
            $process.Dispose()
        }
    }
}

function Install-DashboardCandidate {
    param([string]$Candidate, [string]$Installed)
    # Both files are on the Dashboard volume. Replacement is atomic.
    [IO.File]::Replace($Candidate, $Installed, [NullString]::Value, $true)
}

function Restore-DashboardExecutable {
    param([string]$Backup, [string]$Installed, [string]$BackupDirectory)
    $rollback = Join-Path $BackupDirectory "rollback.exe"
    [IO.File]::Copy($Backup, $rollback, $false)
    if (Test-Path -LiteralPath $Installed) {
        [IO.File]::Replace($rollback, $Installed, [NullString]::Value, $true)
    } else {
        [IO.File]::Move($rollback, $Installed)
    }
}

function Start-DashboardWithLocalTemp {
    param([string]$Root = "")
    $paths = Get-DashboardUpdatePaths $Root
    if (@(Get-ExactDashboardProcesses $paths.Installed).Count) {
        Write-Host "This Dashboard is already running. Open http://127.0.0.1:8092 and refresh with Ctrl+F5."
        return
    }
    $runtimeTemp = Join-Path $paths.Root ".tmp\dashboard-runtime"
    Assert-NoUpdateReparsePoint $runtimeTemp
    New-Item -ItemType Directory -Path $runtimeTemp -Force | Out-Null
    $previousTemp = [Environment]::GetEnvironmentVariable("TEMP", "Process")
    $previousTmp = [Environment]::GetEnvironmentVariable("TMP", "Process")
    try {
        # Only this launcher and its child EXE use the project drive for extraction.
        $env:TEMP = $runtimeTemp
        $env:TMP = $runtimeTemp
        & (Join-Path $paths.Dashboard "dashboard.ps1")
    } finally {
        [Environment]::SetEnvironmentVariable("TEMP", $previousTemp, "Process")
        [Environment]::SetEnvironmentVariable("TMP", $previousTmp, "Process")
    }
}

function Invoke-DashboardUpdate {
    param([string]$Root = "", [string]$PrebuiltExecutable = "")
    $paths = Get-DashboardUpdatePaths $Root
    $id = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss") + "-" + [Guid]::NewGuid().ToString("N")
    $stage = Join-Path $paths.StageRoot $id
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    if ($PrebuiltExecutable) {
        $candidate = [IO.Path]::GetFullPath($PrebuiltExecutable)
        $expectedStatic = ""
    } else {
        Write-Host "Building only the Dashboard. The existing app stays running until validation succeeds."
        $candidate = Build-DashboardCandidate -Paths $paths -Stage $stage
        $expectedStatic = Join-Path $paths.Root "app\static"
    }
    Assert-NoUpdateReparsePoint $candidate
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf) -or
        $candidate.Equals($paths.Installed, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Candidate executable is missing or is the currently installed executable."
    }
    $smoke = Test-DashboardCandidate -Candidate $candidate -Stage $stage -ExpectedStatic $expectedStatic
    $newHash = (Get-DashboardFileHash $candidate)
    Assert-NoUpdateReparsePoint $paths.BackupRoot
    $backupDirectory = Join-Path $paths.BackupRoot $id
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $backup = Join-Path $backupDirectory "StatusVisualizer.exe"
    $incoming = Join-Path $backupDirectory "candidate.exe"
    [IO.File]::Copy($paths.Installed, $backup, $false)
    [IO.File]::Copy($candidate, $incoming, $false)
    $oldHash = (Get-DashboardFileHash $paths.Installed)
    if ((Get-DashboardFileHash $backup) -ne $oldHash -or
        (Get-DashboardFileHash $incoming) -ne $newHash) {
        throw "Executable copy verification failed. The running Dashboard was not touched."
    }
    $before = $null
    try {
        Write-Host "Stopping only the verified portable Dashboard, then replacing only its EXE..."
        Stop-ExactDashboardProcesses $paths.Installed
        Assert-DashboardExecutableUnlocked $paths.Installed
        Assert-NoUpdateReparsePoint $paths.Dashboard
        $before = Get-DashboardPreservedFiles -Dashboard $paths.Dashboard -BackupRoot $paths.BackupRoot
        Install-DashboardCandidate -Candidate $incoming -Installed $paths.Installed
        if ((Get-DashboardFileHash $paths.Installed) -ne $newHash) {
            throw "Installed executable hash did not match the validated build."
        }
        $after = Get-DashboardPreservedFiles -Dashboard $paths.Dashboard -BackupRoot $paths.BackupRoot
        Assert-DashboardFilesPreserved -Before $before -After $after
        $receipt = [ordered]@{
            updated_at_utc = [DateTime]::UtcNow.ToString("o")
            old_exe_sha256 = $oldHash
            new_exe_sha256 = $newHash
            preserved_entries = $before.Count
            smoke_version = $smoke.Version
            updated_heatmap_label = $smoke.HasUpdatedHeatmapLabel
        }
        [IO.File]::WriteAllText((Join-Path $backupDirectory "update-receipt.json"), ($receipt | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    } catch {
        $failure = $_.Exception.Message
        try {
            if (-not (Test-Path -LiteralPath $paths.Installed) -or
                (Get-DashboardFileHash $paths.Installed) -ne $oldHash) {
                Restore-DashboardExecutable -Backup $backup -Installed $paths.Installed -BackupDirectory $backupDirectory
            }
            if ((Get-DashboardFileHash $paths.Installed) -ne $oldHash) {
                throw "Restored executable failed its hash check."
            }
        } catch {
            throw "$failure ROLLBACK NEEDS ATTENTION: $($_.Exception.Message) Original EXE backup: $backup"
        }
        throw "$failure The original EXE is intact/restored. Restart using the normal Dashboard launcher. Backup: $backup"
    }
    Write-Host "Updated successfully. All $($before.Count) non-EXE files/directories are unchanged."
    Write-Host "Previous EXE backup: $backup"
    Write-Host "Restart from this project folder: .\scripts\update-dashboard.ps1 -StartOnly"
    Write-Host "Enter the broker password in that existing launcher, then refresh the browser with Ctrl+F5."
    Write-Host "To roll back later, run this script with -ExecutablePath pointing to the previous EXE backup."
    [pscustomobject]@{ Installed = $paths.Installed; Backup = $backup; PreservedEntries = $before.Count; NewHash = $newHash }
}

if ($MyInvocation.InvocationName -ne ".") {
    if ($StartOnly) {
        if ($ExecutablePath) { throw "Choose either -StartOnly or -ExecutablePath, not both." }
        Start-DashboardWithLocalTemp -Root $ProjectDir
    } else {
        Invoke-DashboardUpdate -Root $ProjectDir -PrebuiltExecutable $ExecutablePath
    }
}

