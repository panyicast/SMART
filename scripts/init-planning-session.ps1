param(
    [Parameter(Mandatory = $true)]
    [string]$TaskName,

    [switch]$NoActivate
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$planningRoot = Join-Path $repoRoot ".planning"
$templateRoot = Join-Path $repoRoot "doc\planning_templates"

if (-not (Test-Path -LiteralPath $templateRoot)) {
    throw "Planning templates not found: $templateRoot"
}

$slug = $TaskName.ToLowerInvariant()
$slug = [regex]::Replace($slug, "[^a-z0-9]+", "-")
$slug = $slug.Trim("-")
if ([string]::IsNullOrWhiteSpace($slug)) {
    $slug = "task"
}

$datePrefix = Get-Date -Format "yyyy-MM-dd"
$planId = "$datePrefix-$slug"
$planDir = Join-Path $planningRoot $planId

New-Item -ItemType Directory -Force -Path $planDir | Out-Null

$taskPlanPath = Join-Path $planDir "task_plan.md"
$findingsPath = Join-Path $planDir "findings.md"
$progressPath = Join-Path $planDir "progress.md"

Copy-Item -LiteralPath (Join-Path $templateRoot "task_plan.md") -Destination $taskPlanPath -Force
Copy-Item -LiteralPath (Join-Path $templateRoot "findings.md") -Destination $findingsPath -Force
Copy-Item -LiteralPath (Join-Path $templateRoot "progress.md") -Destination $progressPath -Force

$taskPlanContent = Get-Content -LiteralPath $taskPlanPath -Raw
$taskPlanContent = $taskPlanContent -replace "(?m)^- Title:\s*$", "- Title: $TaskName"
$taskPlanContent = $taskPlanContent -replace "(?m)^- Requested by:\s*$", "- Requested by: user"
Set-Content -LiteralPath $taskPlanPath -Value $taskPlanContent -Encoding UTF8

if (-not $NoActivate) {
    New-Item -ItemType Directory -Force -Path $planningRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $planningRoot ".active_plan") -Value $planId -Encoding UTF8
}

Write-Output "Created planning session:"
Write-Output "  Plan ID: $planId"
Write-Output "  Directory: $planDir"
Write-Output "  task_plan.md"
Write-Output "  findings.md"
Write-Output "  progress.md"
if ($NoActivate) {
    Write-Output "  Active plan unchanged"
} else {
    Write-Output "  Active plan set to $planId"
}
