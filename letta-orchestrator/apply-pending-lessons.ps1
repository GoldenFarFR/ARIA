# Applique les leçons validées (pending-lessons.md) vers aria-core
param(
    [switch]$List,
    [int]$Approve,
    [int]$Apply,
    [switch]$ApplyApproved,
    [switch]$ApplyAllPending
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$env:ARIA_REPO_ROOT = if ($env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT } else { (Resolve-Path (Join-Path $Here "..")).Path }

$py = Join-Path $Here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$args = @((Join-Path $Here "apply_pending_lessons.py"))
if ($List) { $args += "--list" }
if ($Approve -gt 0) { $args += "--approve"; $args += $Approve }
if ($Apply -gt 0) { $args += "--apply"; $args += $Apply }
if ($ApplyApproved) { $args += "--apply-approved" }
if ($ApplyAllPending) { $args += "--apply-all-pending" }

& $py @args
exit $LASTEXITCODE