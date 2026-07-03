# Simulation complete - l'utilisateur saisit le code Google Authenticator
# Lance via: Start-Process powershell -ArgumentList ... (fenetre interactive)

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SIMULATION SECURITE GoldenFar Vault" -ForegroundColor Cyan
Write-Host "  Entre le code Google Authenticator" -ForegroundColor Cyan
Write-Host "  (compte: GoldenFar Vault)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

. (Join-Path $scriptDir "totp-gate.ps1")
if (-not (Get-TotpSecret)) {
    Write-Host "TOTP non configure. Lance .\setup-totp-vault.ps1" -ForegroundColor Red
    exit 1
}

Write-Host "--- Etape 1/3 : Google Authenticator via Telegram (TOI) ---" -ForegroundColor Yellow
Write-Host "ARIA va t'ecrire sur Telegram - reponds avec les 6 chiffres" -ForegroundColor DarkGray
Assert-TotpGate
Write-Host ""

Write-Host "--- Etape 2/3 : Export coffre ---" -ForegroundColor Yellow
& (Join-Path $scriptDir "collect-local.ps1") -SkipMetier -SkipIde -SkipTotp
Write-Host ""

Write-Host "--- Etape 3/3 : Simulation attaque ---" -ForegroundColor Yellow
& (Join-Path $scriptDir "test-vault-security.ps1"
Write-Host ""
Write-Host "=== FIN simulation ===" -ForegroundColor Green
Write-Host "Fenetre laissee ouverte pour lire les resultats." -ForegroundColor DarkGray
Write-Host ""