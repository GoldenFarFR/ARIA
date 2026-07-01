# Push sessions/ + HANDOFF.md — monorepo ARIA
# Usage: .\push-session-manifest.ps1 [-TotpCode 123456]

param(
    [string]$TotpCode
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "git-operator-session.ps1")
. (Resolve-Path (Join-Path $PSScriptRoot "..\..\scripts\aria-paths.ps1"))

$ariaRepo = $script:AriaRepoRoot
$machine = $env:COMPUTERNAME
$msg = "session: $machine $(Get-Date -Format yyyy-MM-ddTHHmmss)"

$r = Invoke-GoldenFarGitPush -Path $ariaRepo -Message $msg -Add @("collegue-memoire/sessions/") -TotpCode $TotpCode
if ($r.pushed) {
    Write-Host "[OK] Manifeste pousse ($($r.commit))" -ForegroundColor Green
} elseif ($r.reason) {
    Write-Host "[SKIP] $($r.reason)" -ForegroundColor DarkGray
} else {
    Write-Host "[ERR] $($r.error)" -ForegroundColor Red
}