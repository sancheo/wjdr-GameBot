param([Parameter(Mandatory = $true)][string]$ProjectRoot, [int[]]$DesktopPid = @())

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
$processes = @(Get-CimInstance Win32_Process)
$desktops = @($processes | Where-Object {
    $_.Name -match '^GameBot(?:\.[A-Za-z0-9-]+)?\.exe$' -and
    $_.ExecutablePath -and
    [System.IO.Path]::GetDirectoryName($_.ExecutablePath) -eq $resolvedRoot -and
    (-not $DesktopPid.Count -or [int]$_.ProcessId -in $DesktopPid)
})
$ownedIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($desktop in $desktops) { [void]$ownedIds.Add([int]$desktop.ProcessId) }
foreach ($item in $processes) {
    if (-not $DesktopPid.Count -and $item.Name -in @('python.exe', 'pythonw.exe') -and $item.ExecutablePath -and
        [System.IO.Path]::GetDirectoryName($item.ExecutablePath) -eq (Join-Path $resolvedRoot '.venv\Scripts') -and
        $item.CommandLine -match '(?:^|[\s"\\])help_bot\.py(?:[\s"]|$)') {
        [void]$ownedIds.Add([int]$item.ProcessId)
    }
}
# Preserve ownership before closing parents; venv Python can spawn another interpreter.
do {
    $added = $false
    foreach ($item in $processes) {
        if ($ownedIds.Contains([int]$item.ParentProcessId) -and
            $item.Name -in @('python.exe', 'pythonw.exe')) {
            if ($ownedIds.Add([int]$item.ProcessId)) { $added = $true }
        }
    }
} while ($added)
foreach ($desktop in $desktops) {
    $process = Get-Process -Id $desktop.ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    [void]$process.CloseMainWindow()
    if (-not $process.WaitForExit(25000)) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $desktop.ProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $desktop.ProcessId -ErrorAction SilentlyContinue)) {
            throw "Could not close GameBot PID $($desktop.ProcessId)"
        }
    }
}
foreach ($item in $processes) {
    if (-not $ownedIds.Contains([int]$item.ProcessId) -or $item.Name -notin @('python.exe', 'pythonw.exe')) { continue }
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($item.ProcessId)"
    if ($current -and $current.CreationDate -eq $item.CreationDate) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $item.ProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue)) {
            throw "Could not stop owned Python PID $($item.ProcessId)"
        }
    }
}
