$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

function Get-DesktopExecutable {
    @('GameBot.exe', 'GameBot.next.exe') |
        ForEach-Object { Get-Item -LiteralPath (Join-Path $projectRoot $_) -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
}

$executable = Get-DesktopExecutable
$source = Get-Item -LiteralPath (Join-Path $projectRoot 'desktop\GameBot.cs')
if (-not $executable -or $source.LastWriteTimeUtc -gt $executable.LastWriteTimeUtc) {
    & (Join-Path $projectRoot 'build_desktop.ps1')
    $executable = Get-DesktopExecutable
}
if (-not $executable) { throw 'GameBot executable was not found after compilation.' }
Start-Process -FilePath $executable.FullName -WorkingDirectory $projectRoot -WindowStyle Normal
