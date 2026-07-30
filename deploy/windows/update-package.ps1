[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$versionFile = Join-Path $packageRoot 'VERSION.txt'
$repository = 'MoEternal/A-Cat-Girl'

if (-not (Test-Path -LiteralPath $versionFile)) {
    throw 'VERSION.txt is missing; the current version cannot be determined.'
}

$currentText = (Get-Content -Raw -Encoding UTF8 -LiteralPath $versionFile).Trim().TrimStart('v')
$currentVersion = [version]$currentText
$webLauncher = Get-ChildItem -LiteralPath $packageRoot -File -Filter '*.cmd' |
    Select-Object -First 1
if (Test-Path -LiteralPath (Join-Path $packageRoot 'A Cat Girl.exe')) {
    $packageKind = 'windows-x64'
}
elseif ($webLauncher) {
    $packageKind = 'windows-web-x64'
}
else {
    throw 'The current Windows package type could not be detected.'
}

Write-Host "Checking for updates (current: v$currentText)..."
$headers = @{ 'User-Agent' = 'A-Cat-Girl-Updater' }
$release = Invoke-RestMethod `
    -Uri "https://api.github.com/repos/$repository/releases/latest" `
    -Headers $headers `
    -TimeoutSec 30
$latestText = ([string]$release.tag_name).Trim().TrimStart('v')
$latestVersion = [version]$latestText
if ($latestVersion -le $currentVersion) {
    Write-Host "Already up to date: v$currentText" -ForegroundColor Green
    exit 0
}

$assetName = "A-Cat-Girl-v$latestText-$packageKind.zip"
$asset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
$sumsAsset = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1
if (-not $asset -or -not $sumsAsset) {
    throw "Release v$latestText is missing $assetName or SHA256SUMS.txt."
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("a-cat-girl-update-" + [guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $temporaryRoot $assetName
$extractRoot = Join-Path $temporaryRoot 'extracted'
try {
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    Write-Host "Downloading $assetName..."
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -Headers $headers -OutFile $archivePath -TimeoutSec 300
    $sumsText = (Invoke-WebRequest -UseBasicParsing -Uri $sumsAsset.browser_download_url -Headers $headers -TimeoutSec 30).Content
    $assetPattern = [regex]::Escape($assetName)
    $sumMatch = [regex]::Match($sumsText, "(?mi)^([0-9a-f]{64})\s+\*?$assetPattern\s*$")
    if (-not $sumMatch.Success) {
        throw "SHA256SUMS.txt does not contain $assetName."
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash
    if ($actualHash -ne $sumMatch.Groups[1].Value.ToUpperInvariant()) {
        throw 'Update package SHA256 verification failed.'
    }

    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    $payloadRoot = Join-Path $extractRoot ([IO.Path]::GetFileNameWithoutExtension($assetName))
    if (-not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
        throw 'The update archive layout is invalid.'
    }

    if ($packageKind -eq 'windows-x64') {
        Get-Process -Name 'A Cat Girl' -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Seconds 1
    }
    else {
        Get-NetTCPConnection -LocalPort 8732 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }

    $excludedDirectories = @(
        (Join-Path $packageRoot 'data'),
        (Join-Path $packageRoot 'logs'),
        (Join-Path $packageRoot 'backups'),
        (Join-Path $packageRoot '.venv')
    )
    & robocopy $payloadRoot $packageRoot /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD $excludedDirectories /XF '.env'
    if ($LASTEXITCODE -ge 8) {
        throw "Robocopy failed with exit code $LASTEXITCODE."
    }

    Write-Host "Updated to v$latestText." -ForegroundColor Green
    if ($packageKind -eq 'windows-x64') {
        Start-Process -FilePath (Join-Path $packageRoot 'A Cat Girl.exe') -WorkingDirectory $packageRoot
    }
    else {
        Start-Process -FilePath $webLauncher.FullName -WorkingDirectory $packageRoot
    }
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporary = [IO.Path]::GetFullPath($temporaryRoot)
        $systemTemporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolvedTemporary.StartsWith($systemTemporary, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a path outside the system temp directory: $resolvedTemporary"
        }
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
