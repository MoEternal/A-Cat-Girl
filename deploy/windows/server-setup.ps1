[CmdletBinding()]
param(
    [ValidateSet('127.0.0.1', '0.0.0.0')]
    [string]$BindHost = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$Port = 8732
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $projectRoot

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $candidate = Join-Path $HOME '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host '[1/4] Downloading the official uv installer...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $installer = Join-Path $env:TEMP 'catgirl-uv-installer.ps1'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw 'uv installation failed.' }
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    $uv = Find-Uv
    if (-not $uv) { throw 'uv was installed but uv.exe could not be found.' }
}
else {
    Write-Host '[1/4] uv is available.'
}

Write-Host '[2/4] Preparing Python 3.12...'
& $uv python install 3.12
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 installation failed.' }

Write-Host '[3/4] Creating the locked application environment...'
& $uv sync --frozen --no-dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'Python environment setup failed.' }

$dataDir = Join-Path $projectRoot 'data'
$logsDir = Join-Path $projectRoot 'logs'
$backupsDir = Join-Path $projectRoot 'backups'
New-Item -ItemType Directory -Force -Path $dataDir, $logsDir, $backupsDir | Out-Null

$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    @(
        "CATGIRL_HOST=$BindHost"
        "CATGIRL_PORT=$Port"
        'CATGIRL_DATA_DIR=./data'
        'CATGIRL_LOG_LEVEL=INFO'
        'CATGIRL_MODEL_TIMEOUT_SECONDS=120'
        'CATGIRL_MEDIA_DOWNLOAD_TIMEOUT_SECONDS=30'
    ) | Set-Content -LiteralPath $envFile -Encoding ASCII
    Write-Host "[4/4] Created .env with host $BindHost and port $Port."
}
else {
    if ($PSBoundParameters.ContainsKey('BindHost')) {
        $lines = @(Get-Content -LiteralPath $envFile)
        $foundHost = $false
        $lines = @($lines | ForEach-Object {
            if ($_ -match '^CATGIRL_HOST=') {
                $foundHost = $true
                "CATGIRL_HOST=$BindHost"
            }
            else {
                $_
            }
        })
        if (-not $foundHost) { $lines += "CATGIRL_HOST=$BindHost" }
        $lines | Set-Content -LiteralPath $envFile -Encoding ASCII
        Write-Host "[4/4] Updated existing .env to host $BindHost."
    }
    else {
        Write-Host '[4/4] Existing .env was preserved.'
    }
}

Write-Host ''
Write-Host 'Server test environment is ready.' -ForegroundColor Green
Write-Host 'Run the launcher in the package root to start the program.'
if ($BindHost -eq '0.0.0.0') {
    Write-Warning 'Create the administrator account locally before LAN access. Restrict port access with Windows Firewall and do not expose it directly to the public Internet.'
}
