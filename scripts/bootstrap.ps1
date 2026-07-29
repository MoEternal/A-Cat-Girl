[CmdletBinding()]
param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    throw 'uv was not found. Install uv on this machine and run this script again.'
}

Write-Host '[1/2] Rebuilding the Python environment from uv.lock...'
& $uv.Source sync --frozen
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to rebuild the Python environment.'
}

if (-not $SkipFrontend) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw 'Node.js/npm was not found. The management UI cannot be rebuilt.'
    }

    Write-Host '[2/2] Installing and building the management UI...'
    Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    try {
        & $npm.Source ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install frontend dependencies.' }
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Failed to build the management UI.' }
    }
    finally {
        Pop-Location
    }
}

Write-Host 'Environment setup completed.'
