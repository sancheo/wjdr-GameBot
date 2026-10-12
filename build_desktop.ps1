param([string]$OutputName = '')

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if (-not $OutputName) {
    $OutputName = 'GameBot.exe'
    $primaryExecutable = Join-Path $projectRoot $OutputName
    if (Test-Path -LiteralPath $primaryExecutable) {
        try {
            $probe = [System.IO.File]::Open($primaryExecutable, 'Open', 'ReadWrite', 'None')
            $probe.Dispose()
        } catch [System.IO.IOException] {
            $OutputName = 'GameBot.next.exe'
            Write-Host 'GameBot.exe is running; building GameBot.next.exe for the next launch.'
        }
    }
}
if ($OutputName -notmatch '^GameBot(?:\.[A-Za-z0-9-]+)?\.exe$') {
    throw 'OutputName must be a GameBot executable filename in this project.'
}
$outputPath = Join-Path $projectRoot $OutputName
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$sdk = Join-Path $projectRoot '.venv\Lib\site-packages\webview\lib'
$icon = Join-Path $projectRoot 'assets\gamebot-icon.ico'
if (-not (Test-Path -LiteralPath $compiler)) {
    throw 'Windows x64 .NET Framework C# compiler was not found.'
}
if (-not (Test-Path -LiteralPath (Join-Path $sdk 'Microsoft.Web.WebView2.Core.dll'))) {
    throw 'Install Python dependencies first: .venv\Scripts\python.exe -m pip install -r requirements.txt'
}
if (-not (Test-Path -LiteralPath $icon)) {
    throw 'Application icon was not found: assets\gamebot-icon.ico'
}
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 `
    "/out:$outputPath" `
    "/win32icon:$icon" `
    /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll `
    "/reference:$sdk\Microsoft.Web.WebView2.Core.dll" `
    "/reference:$sdk\Microsoft.Web.WebView2.WinForms.dll" `
    "$projectRoot\desktop\GameBot.cs"
if ($LASTEXITCODE -ne 0) { throw 'Desktop EXE compilation failed.' }
Write-Host "Built: $outputPath"
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 `
    "/out:$projectRoot\GameBot.Updater.exe" `
    /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll /reference:System.Core.dll `
    "$projectRoot\desktop\Updater.cs"
if ($LASTEXITCODE -ne 0) { throw 'Updater EXE compilation failed.' }
