[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not $SkipFrontendBuild) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'npm was not found. Build the frontend before packaging.' }
    Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    try {
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    finally {
        Pop-Location
    }
}

$frontendIndex = Join-Path $projectRoot 'frontend\dist\index.html'
if (-not (Test-Path -LiteralPath $frontendIndex)) { throw 'frontend/dist is missing.' }

$pyproject = Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot 'pyproject.toml')
$versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) { throw 'Unable to read the project version.' }
$version = $versionMatch.Groups[1].Value

$artifactsDir = Join-Path $projectRoot 'artifacts'
$packageName = "A-Cat-Girl-v$version-windows-web-x64"
$stagingDir = Join-Path $artifactsDir $packageName
$zipPath = Join-Path $artifactsDir "$packageName.zip"

New-Item -ItemType Directory -Force -Path $artifactsDir | Out-Null
$resolvedArtifacts = (Resolve-Path -LiteralPath $artifactsDir).Path
foreach ($target in @($stagingDir, $zipPath)) {
    if (Test-Path -LiteralPath $target) {
        $resolvedTarget = (Resolve-Path -LiteralPath $target).Path
        if (-not $resolvedTarget.StartsWith($resolvedArtifacts, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a path outside artifacts: $resolvedTarget"
        }
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
foreach ($directory in @('backend', 'plugins')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $directory) -Destination $stagingDir -Recurse
}
$docsTarget = Join-Path $stagingDir 'docs'
New-Item -ItemType Directory -Force -Path $docsTarget | Out-Null
foreach ($document in @('CHAT_HISTORY.md', 'MEMORY_SYSTEM.md', 'ONEBOT.md', 'PLUGIN_DEVELOPMENT.md', 'RUNTIME.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\$document") -Destination $docsTarget
}

$frontendTarget = Join-Path $stagingDir 'frontend'
New-Item -ItemType Directory -Force -Path $frontendTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'frontend\dist') -Destination $frontendTarget -Recurse
$deployTarget = Join-Path $stagingDir 'deploy'
New-Item -ItemType Directory -Force -Path $deployTarget | Out-Null
$windowsDeployTarget = Join-Path $deployTarget 'windows'
New-Item -ItemType Directory -Force -Path $windowsDeployTarget | Out-Null
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'deploy\windows') -File | Where-Object {
    $_.Name -like 'server-*.ps1' -or $_.Name -eq 'update-package.ps1' -or $_.Extension -eq '.cmd' -or $_.Name -eq 'WEB-SERVER-README.txt'
} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $windowsDeployTarget
}
foreach ($file in @('pyproject.toml', 'uv.lock', 'README.md', '.env.example', 'THIRD_PARTY_NOTICES.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $file))) { continue }
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $stagingDir
}
$launchEntry = Get-ChildItem -LiteralPath (Join-Path $projectRoot 'deploy\windows') -File -Filter '*.cmd' | Where-Object {
    Select-String -LiteralPath $_.FullName -SimpleMatch 'server-start.ps1' -Quiet
}
if (@($launchEntry).Count -ne 1) { throw 'Expected exactly one Windows server launch entry.' }
Copy-Item -LiteralPath $launchEntry.FullName -Destination $stagingDir
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy\windows\WEB-SERVER-README.txt') -Destination $stagingDir
$updaterEntry = Get-ChildItem -LiteralPath (Join-Path $projectRoot 'deploy\windows') -File -Filter '*.bat'
if (@($updaterEntry).Count -ne 1) { throw 'Expected exactly one Windows updater batch entry.' }
Copy-Item -LiteralPath $updaterEntry.FullName -Destination $stagingDir
Set-Content -LiteralPath (Join-Path $stagingDir 'VERSION.txt') -Value $version -Encoding ASCII

New-Item -ItemType Directory -Force -Path (Join-Path $stagingDir 'data'), (Join-Path $stagingDir 'logs'), (Join-Path $stagingDir 'backups') | Out-Null
Get-ChildItem -LiteralPath $stagingDir -Recurse -Directory -Filter '__pycache__' | ForEach-Object {
    if (-not $_.FullName.StartsWith($stagingDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a cache path outside staging: $($_.FullName)"
    }
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
}
$forbidden = Get-ChildItem -LiteralPath $stagingDir -Recurse -Force | Where-Object {
    $_.Name -like '*_private' -or $_.Name -in @('node_modules', 'catgirl.db', 'secret.key') -or
    $_.Extension -in @('.log', '.bak')
}
if ($forbidden) {
    throw "Public package contains forbidden private or runtime data: $($forbidden[0].FullName)"
}
Compress-Archive -LiteralPath $stagingDir -DestinationPath $zipPath -CompressionLevel Optimal

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash
Write-Host "Created: $zipPath" -ForegroundColor Green
Write-Host "SHA256: $hash"
Write-Host 'The archive does not contain the local database, secret.key, .env, or .venv.'
