Param(
    [switch]$SkipSetup
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
    if ($SkipSetup) {
        throw "Virtual environment not found at $PythonExe"
    }
    & $SetupScript
}

Push-Location $ProjectRoot
try {
    Invoke-Checked -FilePath $PythonExe -Arguments @("-m", "smart.main") -ErrorMessage "SMART application exited with an error"
}
finally {
    Pop-Location
}
