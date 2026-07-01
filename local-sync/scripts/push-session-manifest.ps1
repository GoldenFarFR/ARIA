# Push sessions/ + HANDOFF.md avec gate TOTP 12h (IDE — pas Telegram)
# Usage: .\push-session-manifest.ps1 [-TotpCode 123456]

param(
    [string]$TotpCode
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "git-operator-session.ps1")

$collegue = Join-Path $env:USERPROFILE "projets\collegue-memoire"
$machine = $env:COMPUTERNAME
$msg = "session: $machine $(Get-Date -Format yyyy-MM-ddTHHmmss)"

$r = Invoke-GoldenFarGitPush -Path $collegue -Message $msg -Add @("sessions/") -TotpCode $TotpCode
if ($r.pushed) {
    Write-Host "[OK] Manifeste pousse ($($r.commit))" -ForegroundColor Green
} elseif ($r.reason) {
    Write-Host "[SKIP] $($r.reason)" -ForegroundColor DarkGray
} else {
    Write-Host "[ERR] $($r.error)" -ForegroundColor Red
}