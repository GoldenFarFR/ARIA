# Initialise le coffre local GoldenFar (hors Git)
# Usage: .\setup-vault.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-common.ps1")

$vault = Initialize-GoldenFarVault
Write-Host "=== Coffre GoldenFar ===" -ForegroundColor Cyan
Write-Host "Emplacement : $vault"
Write-Host "Variable    : GOLDENFAR_VAULT (utilisateur)"
Write-Host ""
Write-Host "Dossier cache + ACL utilisateur uniquement." -ForegroundColor Green
Write-Host "Migration depuis aria-vanguard/operator : .\migrate-to-vault.ps1" -ForegroundColor DarkGray