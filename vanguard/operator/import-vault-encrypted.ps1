# Restaure le coffre depuis une sauvegarde .gfv chiffree
# Usage: .\import-vault-encrypted.ps1 -InFile goldenfar-vault.gfv

param(
    [Parameter(Mandatory)][string]$InFile,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-crypto.ps1")

if (-not (Test-Path $InFile)) { throw "Fichier introuvable: $InFile" }

Write-Host "=== Import coffre chiffre ===" -ForegroundColor Cyan
$pass = Read-Host "Mot de passe de la sauvegarde" -AsSecureString
Import-EncryptedVaultArchive -InFile $InFile -Password $pass -Force:$Force
Write-Host "[OK] Coffre restaure dans $(Get-GoldenFarVaultRoot)" -ForegroundColor Green
Write-Host "Verification: .\check-aria-status.ps1" -ForegroundColor Cyan