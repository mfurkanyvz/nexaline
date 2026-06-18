$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Sdk = Join-Path $Root "tools\android-sdk"
$Gradle = Join-Path $Root "tools\gradle\gradle-8.10.2\bin\gradle.bat"
$ApkSource = Join-Path $Root "android-app\app\build\outputs\apk\release\app-release.apk"
$ApkTarget = Join-Path $Root "static\downloads\nexaline.apk"
$Port = if ($env:PORT) { $env:PORT } else { "5055" }
$LocalIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1 -ExpandProperty IPAddress)
$DefaultUrl = if ($LocalIp) { "http://$LocalIp`:$Port" } else { "https://nidar.com.tr" }
$NexaLineUrl = if ($env:NEXALINE_URL) { $env:NEXALINE_URL } else { $DefaultUrl }

if (-not $env:JAVA_HOME) {
    $jdk = Get-ChildItem "C:\Program Files\Eclipse Adoptium" -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if ($jdk) {
        $env:JAVA_HOME = $jdk.FullName
    }
}

if (-not $env:JAVA_HOME) {
    throw "JAVA_HOME bulunamadi. JDK 17 gerekli."
}

if (-not (Test-Path $Sdk)) {
    throw "Android SDK bulunamadi: $Sdk"
}

if (-not (Test-Path $Gradle)) {
    throw "Gradle bulunamadi: $Gradle"
}

$env:ANDROID_HOME = $Sdk
$env:ANDROID_SDK_ROOT = $Sdk
$env:Path = "$env:JAVA_HOME\bin;$Sdk\cmdline-tools\latest\bin;$Sdk\platform-tools;$env:Path"

& $Gradle -p (Join-Path $Root "android-app") "-PnexalineUrl=$NexaLineUrl" assembleRelease
if ($LASTEXITCODE -ne 0) {
    throw "Android derlemesi basarisiz oldu."
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ApkTarget) | Out-Null
Copy-Item -Force $ApkSource $ApkTarget
Write-Host "APK hazir: $ApkTarget"
Write-Host "APK acilis adresi: $NexaLineUrl"
exit 0
