$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Desktop = Join-Path $Root "desktop-app"
$Unpacked = Join-Path $Desktop "dist\win-unpacked"
$ZipTarget = Join-Path $Root "static\downloads\NexaLine-PC.zip"
$Port = if ($env:PORT) { $env:PORT } else { "5055" }
$LocalIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1 -ExpandProperty IPAddress)
$DefaultUrl = if ($LocalIp) { "http://$LocalIp`:$Port" } else { "https://nexalineapp.xyz" }
$NexaLineUrl = if ($env:NEXALINE_URL) { $env:NEXALINE_URL } else { $DefaultUrl }
$Assets = Join-Path $Desktop "assets"

New-Item -ItemType Directory -Force -Path $Assets | Out-Null
$AppUrlJson = @{ url = $NexaLineUrl } | ConvertTo-Json
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $Assets "app-url.json"), $AppUrlJson, $Utf8NoBom)

if (-not (Test-Path (Join-Path $Desktop "node_modules"))) {
    npm --prefix $Desktop install
}

$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
npm --prefix $Desktop run pack
if ($LASTEXITCODE -ne 0 -and -not (Test-Path $Unpacked)) {
    throw "electron-builder basarisiz oldu ve win-unpacked uretilmedi."
}

if (-not (Test-Path $Unpacked)) {
    throw "PC uygulamasi paketlenemedi: $Unpacked"
}

$Locales = Join-Path $Unpacked "locales"
if (Test-Path $Locales) {
    Get-ChildItem $Locales -Filter "*.pak" |
        Where-Object { @("en-US", "tr") -notcontains $_.BaseName } |
        Remove-Item -Force
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ZipTarget) | Out-Null
if (Test-Path $ZipTarget) {
    Remove-Item -Force $ZipTarget
}
Compress-Archive -Path (Join-Path $Unpacked "*") -DestinationPath $ZipTarget -CompressionLevel Optimal
Write-Host "PC ZIP hazir: $ZipTarget"
Write-Host "PC acilis adresi: $NexaLineUrl"
exit 0
