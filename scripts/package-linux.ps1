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

$gnuTarCandidates = @(
    'C:\Program Files\Git\usr\bin\tar.exe',
    'C:\Program Files (x86)\Git\usr\bin\tar.exe'
)
$tar = $gnuTarCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $tar) {
    throw 'Git for Windows GNU tar was not found; it is required to write Linux file permissions correctly.'
}

$artifactsDir = Join-Path $projectRoot 'artifacts'
$packageName = "A-Cat-Girl-v$version-linux"
$stagingDir = Join-Path $artifactsDir $packageName
$archivePath = Join-Path $artifactsDir "$packageName.tar.gz"

New-Item -ItemType Directory -Force -Path $artifactsDir | Out-Null
$resolvedArtifacts = (Resolve-Path -LiteralPath $artifactsDir).Path
foreach ($target in @($stagingDir, $archivePath)) {
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
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy\linux') -Destination $deployTarget -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy\linux\start.sh') -Destination $stagingDir
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy\linux\README-LINUX.txt') -Destination $stagingDir
Set-Content -LiteralPath (Join-Path $stagingDir 'VERSION.txt') -Value $version -Encoding ASCII
foreach ($file in @('pyproject.toml', 'uv.lock', 'README.md', '.env.example', 'THIRD_PARTY_NOTICES.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $file))) { continue }
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $stagingDir
}

New-Item -ItemType Directory -Force -Path (Join-Path $stagingDir 'data'), (Join-Path $stagingDir 'logs'), (Join-Path $stagingDir 'backups') | Out-Null
Get-ChildItem -LiteralPath $stagingDir -Recurse -Directory -Filter '__pycache__' | ForEach-Object {
    if (-not $_.FullName.StartsWith($stagingDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a cache path outside staging: $($_.FullName)"
    }
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
}

$forbidden = Get-ChildItem -LiteralPath $stagingDir -Recurse -Force | Where-Object {
    $_.Name -like '*_private' -or $_.Name -in @('node_modules', 'catgirl.db', 'secret.key') -or
    $_.Extension -in @('.log', '.bak', '.ps1', '.cmd', '.exe', '.dll')
}
if ($forbidden) {
    throw "Linux public package contains forbidden private, Windows, or runtime data: $($forbidden[0].FullName)"
}

$previousPath = $env:PATH
Push-Location -LiteralPath $artifactsDir
try {
    $env:PATH = "$(Split-Path -Parent $tar);$previousPath"
    & $tar --force-local -czf "$packageName.tar.gz" --owner=0 --group=0 --mode='u+rwX,go+rX,go-w' $packageName
    if ($LASTEXITCODE -ne 0) { throw 'tar.gz creation failed.' }
}
finally {
    $env:PATH = $previousPath
    Pop-Location
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash
$size = [math]::Round((Get-Item -LiteralPath $archivePath).Length / 1MB, 2)

Write-Host "Created: $archivePath" -ForegroundColor Green
Write-Host "Size: $size MB"
Write-Host "SHA256: $hash"
Write-Host 'The archive contains no local database, login account, API keys, logs, private plugins, or imported data.'
