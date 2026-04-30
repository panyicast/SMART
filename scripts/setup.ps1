Param(
    [switch]$ForceRecreate
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
$VenvDir = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

if ($ForceRecreate -and (Test-Path $VenvDir)) {
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path $PythonExe)) {
    Invoke-Checked -FilePath "python" -Arguments @("-m", "venv", $VenvDir) -ErrorMessage "Failed to create virtual environment"
}

if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment creation failed. Expected interpreter: $PythonExe"
}

Push-Location $ProjectRoot
try {
    Invoke-Checked -FilePath $PythonExe -Arguments @("-m", "pip", "install", "--upgrade", "pip") -ErrorMessage "Failed to upgrade pip"
    Invoke-Checked -FilePath $PythonExe -Arguments @("-m", "pip", "install", "-e", ".[dev]") -ErrorMessage "Failed to install project dependencies"
    Invoke-Checked -FilePath "powershell" -Arguments @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "install-git-hooks.ps1")) -ErrorMessage "Failed to install git hooks"
}
finally {
    Pop-Location
}

Write-Host "Setup complete. Virtual environment: $VenvDir"
