# Enregistre la cle API IONOS (prefix + secret) puis cree le CNAME api
# Usage:
#   .\register-ionos-api-key.ps1                    # ouvre le portail + saisie interactive
#   .\register-ionos-api-key.ps1 -Prefix xxx -Secret yyy
#   .\register-ionos-api-key.ps1 -Combined "prefix.secret"

param(
    [string]$Prefix = "",
    [string]$Secret = "",
    [string]$Combined = "",
    [switch]$OpenPortal = $true,
    [switch]$SkipDns
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-common.ps1")
Initialize-GoldenFarVault | Out-Null
$KeyFile = Get-IonosApiKeyPath -ScriptsRoot $Root
$PortalUrl = "https://developer.hosting.ionos.fr/keys"

Write-Host "=== Cle API IONOS (DNS) ===" -ForegroundColor Cyan
Write-Host "Portail : $PortalUrl"
Write-Host ""

if ($OpenPortal) {
    Start-Process $PortalUrl
    Write-Host "Navigateur ouvert. Sur IONOS :" -ForegroundColor Green
    Write-Host "  1. Se connecter (meme compte que my.ionos.com / domaine ariavanguardzhc.com)"
    Write-Host "  2. Creer une cle API (bouton Create / Creer)"
    Write-Host "  3. Copier le Prefix ET le Secret (secret affiche une seule fois)"
    Write-Host ""
}

if ($Combined) {
    $apiKey = $Combined.Trim()
} elseif ($Prefix -and $Secret) {
    $apiKey = "$($Prefix.Trim()).$($Secret.Trim())"
} elseif (Test-Path $KeyFile) {
    $apiKey = (Get-Content $KeyFile -Raw).Trim()
    Write-Host "Cle existante detectee dans le coffre (keys\ionos.api-key)" -ForegroundColor Yellow
} else {
    if (-not $Prefix) {
        $Prefix = Read-Host "Prefix IONOS"
    }
    $Secret = Read-Host "Secret IONOS" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $apiKey = "$($Prefix.Trim()).$($plain.Trim())"
}

if ($apiKey -notmatch '^[A-Za-z0-9\-]+\.[A-Za-z0-9\-]+$') {
    Write-Host "Format attendu : prefix.secret (deux parties separees par un point)" -ForegroundColor Red
    exit 1
}

Set-Content -Path $KeyFile -Value $apiKey -Encoding UTF8 -NoNewline
Write-Host "[OK] Cle enregistree dans le coffre (non affichee)." -ForegroundColor Green

if (-not $SkipDns) {
    & (Join-Path $Root "setup-ionos-dns-api.ps1")
}