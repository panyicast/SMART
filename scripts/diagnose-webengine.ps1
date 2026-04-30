Param(
    [ValidateSet("swiftshader", "software", "swiftshader-webgl", "d3d11", "desktop")]
    [string]$Backend = "swiftshader"
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    Param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$ErrorMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$ErrorMessage (exit code: $LASTEXITCODE)"
    }
}

$ProjectRoot = Split-Path -Path $PSScriptRoot -Parent
$SetupScript = Join-Path $PSScriptRoot "setup.ps1"
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    & $SetupScript
}

Push-Location $ProjectRoot
try {
    $env:SMART_WEBENGINE_BACKEND = $Backend
    Invoke-Checked -FilePath $PythonExe -Arguments @("-m", "smart.webengine_diagnostics", "--backend", $Backend) -ErrorMessage "SMART WebEngine diagnostics exited with an error"
}
finally {
    Pop-Location
}
