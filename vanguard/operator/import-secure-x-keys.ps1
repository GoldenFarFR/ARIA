# Importe les cles X depuis secure-keys (fichiers texte perso) vers le coffre uniquement.
# Ne touche PAS backend/.env directement — sync-local.ps1 propage via variables.
#
# Usage:
#   .\import-secure-x-keys.ps1              # Aria_ZHC (defaut)
#   .\import-secure-x-keys.ps1 -Account Golden
#   $env:SECURE_KEYS_ROOT = "D:\backup\secure-keys"  # optionnel

param(
    [ValidateSet("Aria", "Golden")]
    [string]$Account = "Aria"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_vault-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$secureRoot = if ($env:SECURE_KEYS_ROOT) {
    $env:SECURE_KEYS_ROOT.TrimEnd('\')
} else {
    Join-Path $env:USERPROFILE "secure-keys\config\secure"
}

$srcName = if ($Account -eq "Golden") { "x - Golden.txt" } else { "x - Aria.txt" }
$srcPath = Join-Path $secureRoot $srcName
if (-not (Test-Path $srcPath)) {
    Write-Host "Fichier introuvable: $srcPath" -ForegroundColor Red
    Write-Host "Definis SECURE_KEYS_ROOT ou place secure-keys sous %USERPROFILE%" -ForegroundColor Yellow
    exit 1
}

$fieldMap = [ordered]@{
    bearerToken   = "X_BEARER_TOKEN"
    apiKey        = "X_API_KEY"
    apiSecret     = "X_API_SECRET"
    accessToken   = "X_ACCESS_TOKEN"
    accessSecret  = "X_ACCESS_TOKEN_SECRET"
}

$parsed = @{}
Get-Content $srcPath | ForEach-Object {
    if ($_ -match '^\s*(\w+)\s*=\s*(.+)\s*$') {
        $k = $Matches[1]
        if ($fieldMap.Contains($k)) {
            $parsed[$fieldMap[$k]] = [System.Uri]::UnescapeDataString($Matches[2].Trim())
        }
    }
}

if ($parsed.Count -lt 4) {
    Write-Host "Parse incomplet ($($parsed.Count)/5 cles) dans $srcPath" -ForegroundColor Red
    exit 1
}

$prodPath = Get-ProductionEnvPath -ScriptsRoot $Root
if (-not (Test-Path $prodPath)) {
    Write-Host "production.env manquant dans le coffre — lance .\setup-vault.ps1 ou .\new-pc.ps1" -ForegroundColor Red
    exit 1
}

foreach ($key in $parsed.Keys) {
    Update-EnvFileKey -Path $prodPath -Key $key -Value $parsed[$key]
}
Write-Host "[OK] $($parsed.Count) cles X -> coffre production.env ($Account)" -ForegroundColor Green
Write-Host "     Chemin coffre: $(Get-GoldenFarVaultRoot)" -ForegroundColor DarkGray

& (Join-Path $Root "sync-local.ps1")
Write-Host "Puis prod: .\sync-render.ps1 -Reason 'sync X keys'" -ForegroundColor DarkGray