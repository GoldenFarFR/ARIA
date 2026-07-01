# Rechiffre le coffre avec la cle du jour et pousse sur GitHub
# Usage: .\rotate-daily-vault.ps1  (ou tache planifiee 03:00)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "daily-vault-key.ps1")

if (-not (Test-DailyVaultMode)) {
    Write-Host "Lance d'abord .\setup-daily-vault.ps1" -ForegroundColor Red
    exit 1
}

Show-DailyVaultStatus
# SkipTotp : tache 03h00 non interactive (TOTP reste obligatoire pour collect manuel)
& (Join-Path $PSScriptRoot "collect-local.ps1") -SkipMetier -SkipIde -SkipTotp

. (Join-Path $PSScriptRoot "git-operator-session.ps1")
$repo = Split-Path -Parent $PSScriptRoot
$msg = "sync: rotation quotidienne vault $(Get-Date -Format yyyy-MM-dd)"
$r = Invoke-GoldenFarGitPush -Path $repo -Message $msg -Add @("sync/vault/goldenfar-vault.gfv", "machines/") -SkipGitGate
if ($r.pushed) {
    Write-Host "[OK] .gfv du jour pousse sur GitHub" -ForegroundColor Green
} elseif ($r.reason) {
    Write-Host "[SKIP] $($r.reason)" -ForegroundColor DarkGray
}