# Aide setup multi-PC — affiche la stack et ouvre les liens utiles
# Usage: .\setup-multi-pc.ps1 [-OpenSyncthing]

param([switch]$OpenSyncthing)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-common.ps1")

$vault = Get-GoldenFarVaultRoot
$hasVault = (Test-Path (Get-ProductionEnvPath -ScriptsRoot $Root))

Write-Host "=== GoldenFar multi-PC (gratuit) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Coffre ce PC : $vault"
Write-Host "Coffre pret   : $(if ($hasVault) { 'oui' } else { 'NON - Syncthing ou import .gfv' })"
Write-Host ""
Write-Host "1. SYNCTHING (sync auto entre PC)" -ForegroundColor Green
Write-Host "   winget install Syncthing.Syncthing"
Write-Host "   Dossier a partager : $vault"
Write-Host "   Guide : MULTI-PC-VAULT.md"
Write-Host ""
Write-Host "2. BITWARDEN (logins + passphrase sauvegarde)" -ForegroundColor Green
Write-Host "   https://bitwarden.com/pricing/ (gratuit)"
Write-Host ""
Write-Host "3. SECOURS USB" -ForegroundColor Green
Write-Host "   .\export-vault-encrypted.ps1  -> fichier .gfv"
Write-Host "   Autre PC : .\import-vault-encrypted.ps1"
Write-Host ""
Write-Host "4. RENDER = prod 24/7 (deja en ligne, pas lie au PC allume)" -ForegroundColor DarkGray
Write-Host ""

if ($OpenSyncthing) {
    Start-Process "https://syncthing.net/downloads/"
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8384"
}