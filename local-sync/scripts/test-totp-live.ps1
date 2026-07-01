# Verifie que ton Google Authenticator fonctionne (sans export coffre)
# Usage interactif : .\test-totp-live.ps1
# Usage rapide   : .\test-totp-live.ps1 -TotpCode 123456

param([string]$TotpCode)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "totp-gate.ps1")

Write-Host "=== Test Google Authenticator ===" -ForegroundColor Cyan
if (-not (Get-TotpSecret)) {
    Write-Host "TOTP non configure. Lance .\setup-totp-vault.ps1" -ForegroundColor Red
    exit 1
}
Assert-TotpGate -Code $TotpCode
Write-Host ""
Write-Host "[OK] Authenticator valide - tu peux lancer collect-local ou apply-local" -ForegroundColor Green