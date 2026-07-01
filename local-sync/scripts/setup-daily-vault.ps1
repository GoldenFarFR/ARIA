# Active la rotation quotidienne du mot de passe de chiffrement .gfv
# Usage: .\setup-daily-vault.ps1

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "daily-vault-key.ps1")

$path = Get-VaultMasterPath
if (Test-Path $path) {
    Write-Host "Secret maitre deja present: $path" -ForegroundColor Yellow
    Show-DailyVaultStatus
    exit 0
}

$bytes = New-Object byte[] 48
(New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
$master = [Convert]::ToBase64String($bytes)

Set-Content -Path $path -Value $master -Encoding UTF8 -NoNewline

Write-Host "=== Rotation quotidienne GoldenFar ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "SECRET MAITRE (une seule fois dans Bitwarden) :" -ForegroundColor Yellow
Write-Host $master
Write-Host ""
Write-Host "Entree Bitwarden suggeree : goldenfar-vault-master"
Write-Host ""
Write-Host "Ce secret NE CHANGE JAMAIS. Ce qui change chaque jour :"
Write-Host "  la cle derivee pour chiffrer goldenfar-vault.gfv sur GitHub."
Write-Host ""
Write-Host "Si un attaquant vole le .gfv lundi, mardi le fichier sur GitHub"
Write-Host "est rechiffre avec la cle de mardi - la copie de lundi est obsolete."
Write-Host ""
Write-Host "Prochaine etape :" -ForegroundColor Green
Write-Host "  1. Sauvegarder le secret maitre dans Bitwarden"
Write-Host "  2. .\rotate-daily-vault.ps1   (rechiffre et pousse)"
Write-Host "  3. .\setup-daily-vault-task.ps1 (chaque nuit 03h00)"
Show-DailyVaultStatus