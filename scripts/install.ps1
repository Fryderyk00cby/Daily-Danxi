$ErrorActionPreference = 'Stop'
$readerRoot = Split-Path -Parent $PSScriptRoot
$readerPython = $null
foreach ($readerCommand in @('py', 'python')) {
    if (Get-Command $readerCommand -ErrorAction SilentlyContinue) {
        $readerArgs = @('-c', 'import sys; assert sys.version_info >= (3, 10); print(sys.executable)')
        if ($readerCommand -eq 'py') { $readerArgs = @('-3') + $readerArgs }
        $readerCandidate = & $readerCommand @readerArgs
        if ($LASTEXITCODE -eq 0 -and $readerCandidate) {
            $readerPython = ($readerCandidate | Select-Object -Last 1).Trim()
            break
        }
    }
}
if (-not $readerPython) {
    throw 'Install Python 3.10 or newer for Windows first, then run setup.cmd again.'
}
$readerVenv = Join-Path $readerRoot '.venv'
$readerVenvPython = Join-Path $readerVenv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $readerVenvPython)) {
    & $readerPython -m venv $readerVenv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
}
& $readerVenvPython -m pip install -r (Join-Path $readerRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network and retry setup.cmd.' }
& $readerVenvPython -c 'import playwright.sync_api, cryptography'
if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
Write-Output 'Dependencies ready.'
& (Join-Path $PSScriptRoot 'build_launcher.ps1')
