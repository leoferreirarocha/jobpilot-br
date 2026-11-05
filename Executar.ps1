param([ValidateSet('check','login','plan','run')][string]$Mode = 'plan')
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskPython = Join-Path $TaskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $TaskPython)) {
    throw 'Ambiente Python ausente. Consulte o README para instalar as dependencias.'
}
& $TaskPython (Join-Path $TaskRoot 'jobpilot.py') $Mode
exit $LASTEXITCODE
