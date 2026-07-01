# Genere un secret TOTP pour proteger collect/apply (comme Google Authenticator)
# Usage: .\setup-totp-vault.ps1

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "totp-gate.ps1")

$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root ".vault-totp-secret"

if (Test-Path $out) {
    Write-Host "Secret TOTP deja present: $out" -ForegroundColor Yellow
    Write-Host "Supprime le fichier pour regenerer." -ForegroundColor DarkGray
    exit 0
}

$bytes = New-Object byte[] 20
(New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
$alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
$sb = New-Object System.Text.StringBuilder
foreach ($b in $bytes) { [void]$sb.Append($alphabet[$b % 32]) }
$secret = $sb.ToString()

Set-Content -Path $out -Value $secret -Encoding UTF8 -NoNewline
Write-Host "=== TOTP GoldenFar Vault ===" -ForegroundColor Cyan
Write-Host "Secret (base32) : $secret"
Write-Host ""
Write-Host "Google Authenticator : Ajouter un compte > saisie manuelle"
Write-Host "  Nom   : GoldenFar Vault"
Write-Host "  Cle   : $secret"
Write-Host "  Type  : Based on time"
Write-Host ""
Write-Host "Ou URI otpauth (QR generator) :"
Write-Host "  otpauth://totp/GoldenFar%20Vault?secret=$secret&issuer=GoldenFar"
Write-Host ""
Write-Host "Fichier local (gitignore) : $out"
Write-Host "Desormais collect-local et apply-local demandent le code a 6 chiffres." -ForegroundColor Green