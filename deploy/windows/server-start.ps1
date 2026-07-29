[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$setup = Join-Path $PSScriptRoot 'server-setup.ps1'

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'First launch: preparing the locked Python 3.12 environment...'
    & $setup
    if ($LASTEXITCODE -ne 0) { throw 'Environment initialization failed.' }
}

Set-Location -LiteralPath $projectRoot
$pythonVersion = & $python -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.12') {
    Write-Host 'Refreshing the environment with Python 3.12...'
    & $setup
    if ($LASTEXITCODE -ne 0) { throw 'Environment refresh failed.' }
}

$port = 8732
$envFile = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envFile) {
    $portLine = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^CATGIRL_PORT=\d+$' } | Select-Object -Last 1
    if ($portLine) { $port = [int]($portLine -replace '^CATGIRL_PORT=', '') }
}
$url = "http://127.0.0.1:$port/"
Write-Host "Starting A Cat Girl at $url. Close this window or press Ctrl+C to stop."
$server = Start-Process -FilePath $python -ArgumentList @('-m', 'catgirl') -WorkingDirectory $projectRoot -NoNewWindow -PassThru
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($server.HasExited) { throw "Server exited with code $($server.ExitCode)." }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 1
            if ($health.status -eq 'ok') {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $ready) { throw 'Server did not become healthy within 30 seconds.' }
    Start-Process $url
    Wait-Process -Id $server.Id
}
finally {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
