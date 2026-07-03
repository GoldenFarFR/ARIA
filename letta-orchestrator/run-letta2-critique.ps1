# ARIA-Critique (Letta-2) — leçons → pending-lessons.md
param(
    [switch]$DryRun,
    [switch]$Force,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$VaultDir = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $k = $Matches[1]; $v = $Matches[2].Trim()
            if ($v -match '^"(.*)"$') { $v = $Matches[1] }
            if (-not [string]::IsNullOrWhiteSpace($v)) { Set-Item -Path "env:$k" -Value $v }
        }
    }
}

Import-DotEnv (Join-Path $VaultDir "local.env")
$env:ARIA_REPO_ROOT = if ($env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT } else { (Resolve-Path (Join-Path $Here "..")).Path }

$py = Join-Path $Here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$args = @((Join-Path $Here "letta2_critique.py"))
if ($DryRun) { $args += "--dry-run" }
if ($Force) { $args += "--force" }

& $py @args
$code = $LASTEXITCODE
if (-not $Quiet) { Write-Host "letta2-critique exit $code" -ForegroundColor DarkCyan }
exit 0