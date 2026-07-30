[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [switch]$SkipWebView2Download
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not $SkipFrontendBuild) {
    Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    finally {
        Pop-Location
    }
}

$frontendIndex = Join-Path $projectRoot 'frontend\dist\index.html'
if (-not (Test-Path -LiteralPath $frontendIndex)) { throw 'frontend/dist is missing.' }

$iconPath = Join-Path $projectRoot 'deploy\windows\catgirl-window-black.ico'
if (-not (Test-Path -LiteralPath $iconPath)) { throw 'deploy/windows/catgirl-window-black.ico is missing.' }

$pyproject = Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot 'pyproject.toml')
$versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) { throw 'Unable to read the project version.' }
$version = $versionMatch.Groups[1].Value

$artifactsDir = Join-Path $projectRoot 'artifacts'
$buildRoot = Join-Path $artifactsDir '.webview-build'
$packageName = "A-Cat-Girl-v$version-windows-x64"
$stagingDir = Join-Path $artifactsDir $packageName
$zipPath = Join-Path $artifactsDir "$packageName.zip"

New-Item -ItemType Directory -Force -Path $artifactsDir | Out-Null
$resolvedArtifacts = (Resolve-Path -LiteralPath $artifactsDir).Path
foreach ($target in @($buildRoot, $stagingDir, $zipPath)) {
    if (Test-Path -LiteralPath $target) {
        $resolvedTarget = (Resolve-Path -LiteralPath $target).Path
        if (-not $resolvedTarget.StartsWith($resolvedArtifacts, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a path outside artifacts: $resolvedTarget"
        }
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { throw 'uv was not found.' }
& $uv.Source sync --frozen --extra desktop --group dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 build environment setup failed.' }

$runtimeVersion = & $uv.Source run --frozen python -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro, sep=chr(46))'
if ($LASTEXITCODE -ne 0 -or -not $runtimeVersion.StartsWith('3.12.')) {
    throw "WebView build must use Python 3.12, found $runtimeVersion"
}

& $uv.Source run --frozen pyinstaller --noconfirm --clean --distpath (Join-Path $buildRoot 'dist') --workpath (Join-Path $buildRoot 'work') (Join-Path $projectRoot 'deploy\windows\A Cat Girl.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$pyzToc = Join-Path $buildRoot 'work\A Cat Girl\PYZ-00.toc'
if (-not (Test-Path -LiteralPath $pyzToc)) {
    throw 'Packaged app PYZ manifest is missing; refusing to create an unverifiable archive.'
}
$requiredFrozenModules = @(
    'catgirl.plugins.file_memory',
    'tiktoken.core',
    'tiktoken.load',
    'tiktoken.model',
    'tiktoken.registry',
    'tiktoken_ext.openai_public'
)
foreach ($module in $requiredFrozenModules) {
    if (-not (Select-String -LiteralPath $pyzToc -SimpleMatch "'$module'" -Quiet)) {
        throw "Packaged app is missing $module; refusing to create a broken archive."
    }
}

$builtApp = Join-Path $buildRoot 'dist\A Cat Girl'
if (-not (Test-Path -LiteralPath (Join-Path $builtApp 'A Cat Girl.exe'))) {
    throw 'The packaged executable was not created.'
}
Move-Item -LiteralPath $builtApp -Destination $stagingDir
New-Item -ItemType Directory -Force -Path (Join-Path $stagingDir 'data'), (Join-Path $stagingDir 'logs'), (Join-Path $stagingDir 'backups') | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy\windows\WEBVIEW-README.txt') -Destination $stagingDir
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination $stagingDir
if (Test-Path -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md') -Destination $stagingDir
}
Set-Content -LiteralPath (Join-Path $stagingDir 'VERSION.txt') -Value $version -Encoding ASCII

if (-not $SkipWebView2Download) {
    $bootstrapper = Join-Path $stagingDir 'MicrosoftEdgeWebview2Setup.exe'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $bootstrapper
    $bootstrapperItem = Get-Item -LiteralPath $bootstrapper
    $signature = Get-AuthenticodeSignature -LiteralPath $bootstrapper
    if ($bootstrapperItem.Length -lt 1MB -or $signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft*') {
        throw 'Downloaded WebView2 installer failed size or Microsoft signature validation.'
    }
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
$size = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 2)

Write-Host "Created: $zipPath" -ForegroundColor Green
Write-Host "Size: $size MB"
Write-Host "SHA256: $hash"
Write-Host 'The archive contains no local database, login account, API keys, logs, or imported data.'
