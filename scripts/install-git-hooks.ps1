Param()

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Path $PSScriptRoot -Parent

Push-Location $ProjectRoot
try {
    git config core.hooksPath .githooks
    Write-Host "Git hooks installed. hooksPath=.githooks"
}
finally {
    Pop-Location
}
