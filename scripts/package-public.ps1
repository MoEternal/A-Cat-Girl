[CmdletBinding()]
param(
    [switch]$SkipWebView2Download
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot 'package-webview.ps1') -SkipFrontendBuild -SkipWebView2Download:$SkipWebView2Download
if ($LASTEXITCODE -ne 0) { throw 'Windows WebView packaging failed.' }
& (Join-Path $PSScriptRoot 'package-server.ps1') -SkipFrontendBuild
if ($LASTEXITCODE -ne 0) { throw 'Windows web packaging failed.' }
& (Join-Path $PSScriptRoot 'package-linux.ps1') -SkipFrontendBuild
if ($LASTEXITCODE -ne 0) { throw 'Linux packaging failed.' }

$pyproject = Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot 'pyproject.toml')
$versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) { throw 'Unable to read the project version.' }
$version = $versionMatch.Groups[1].Value
$artifactsDir = Join-Path $projectRoot 'artifacts'
$releaseDir = Join-Path $artifactsDir "public-release-v$version"
if (Test-Path -LiteralPath $releaseDir) {
    $resolvedArtifacts = (Resolve-Path -LiteralPath $artifactsDir).Path
    $resolvedRelease = (Resolve-Path -LiteralPath $releaseDir).Path
    if (-not $resolvedRelease.StartsWith($resolvedArtifacts + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace a release directory outside artifacts: $resolvedRelease"
    }
    Remove-Item -LiteralPath $resolvedRelease -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseDir | Out-Null
$releaseFiles = @(
    "A-Cat-Girl-v$version-windows-x64.zip",
    "A-Cat-Girl-v$version-windows-web-x64.zip",
    "A-Cat-Girl-v$version-linux.tar.gz"
)
$hashLines = foreach ($file in $releaseFiles) {
    $source = Join-Path $artifactsDir $file
    if (-not (Test-Path -LiteralPath $source)) { throw "Missing public artifact: $file" }
    Copy-Item -LiteralPath $source -Destination $releaseDir
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
    "$hash  $file"
}
$hashLines | Set-Content -LiteralPath (Join-Path $releaseDir 'SHA256SUMS.txt') -Encoding ASCII

Write-Host "All three public packages were created in: $releaseDir" -ForegroundColor Green
