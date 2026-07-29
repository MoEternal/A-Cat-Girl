[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$frontendIndex = Join-Path $projectRoot 'frontend\dist\index.html'

if (-not (Test-Path -LiteralPath $venvPython)) {
    & (Join-Path $PSScriptRoot 'bootstrap.ps1') -SkipFrontend:(Test-Path -LiteralPath $frontendIndex)
}

if (-not (Test-Path -LiteralPath $frontendIndex)) {
    & (Join-Path $PSScriptRoot 'bootstrap.ps1')
}

Set-Location -LiteralPath $projectRoot
& $venvPython -m catgirl

