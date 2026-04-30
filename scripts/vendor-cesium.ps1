$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

param(
    [string]$Version = "1.140"
)

$workspace = Split-Path -Parent $PSScriptRoot
$tempDir = Join-Path $workspace ".tmp"
$zipPath = Join-Path $tempDir ("Cesium-" + $Version + ".zip")
$extractDir = Join-Path $tempDir ("Cesium-" + $Version)
$vendorRoot = Join-Path $workspace "src\smart\assets\cesium\vendor"
$downloadUrl = "https://github.com/CesiumGS/cesium/releases/download/$Version/Cesium-$Version.zip"

Write-Host "Downloading Cesium $Version from $downloadUrl"
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

if (Test-Path -LiteralPath $extractDir) {
    Remove-Item -LiteralPath $extractDir -Recurse -Force
}

if (Test-Path -LiteralPath $vendorRoot) {
    Remove-Item -LiteralPath $vendorRoot -Recurse -Force
}

Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -UseBasicParsing
Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force

$sourceBuild = Join-Path $extractDir "Build\Cesium"
if (-not (Test-Path -LiteralPath $sourceBuild)) {
    throw "Cesium build folder not found: $sourceBuild"
}

New-Item -ItemType Directory -Path (Join-Path $vendorRoot "Build") -Force | Out-Null
Copy-Item -LiteralPath $sourceBuild -Destination (Join-Path $vendorRoot "Build") -Recurse -Force

Write-Host "Vendored Cesium runtime to $(Join-Path $vendorRoot 'Build\Cesium')"
