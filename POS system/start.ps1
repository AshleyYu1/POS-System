$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath $taskPython) {
    & $taskPython app.py
} else {
    python app.py
}
