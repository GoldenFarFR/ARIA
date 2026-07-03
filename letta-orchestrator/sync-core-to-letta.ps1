# Sync aria-core (journal, reflections, pitfalls) → Letta archival
# Usage: .\sync-core-to-letta.ps1 [-DryRun] [-Quiet]
param(
    [switch]$DryRun,
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
Import-DotEnv (Join-Path $VaultDir "production.env")

$repo = if ($env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT } else { (Resolve-Path (Join-Path $Here "..")).Path }
$env:ARIA_REPO_ROOT = $repo

$py = Join-Path $Here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$args = @((Join-Path $Here "sync_core_to_letta.py"))
if ($DryRun) { $args += "--dry-run" }
$args += "--json"

$proc = Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $Here -Wait -PassThru -NoNewWindow -RedirectStandardOutput "$env:TEMP\sync-letta-out.json" -RedirectStandardError "$env:TEMP\sync-letta-err.txt"
$out = Get-Content "$env:TEMP\sync-letta-out.json" -Raw -ErrorAction SilentlyContinue
$err = Get-Content "$env:TEMP\sync-letta-err.txt" -Raw -ErrorAction SilentlyContinue

if (-not $Quiet) {
    if ($out) { Write-Host $out.Trim() }
    if ($err) { Write-Host $err.Trim() -ForegroundColor DarkYellow }
}

if ($proc.ExitCode -ne 0 -and -not $Quiet) {
    Write-Host "sync-core-to-letta exit $($proc.ExitCode)" -ForegroundColor Yellow
}
exit 0