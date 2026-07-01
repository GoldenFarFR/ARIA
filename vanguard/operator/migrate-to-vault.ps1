# Deplace tous les secrets de aria-vanguard/operator vers le coffre local
# Usage: .\migrate-to-vault.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-common.ps1")

$vault = Initialize-GoldenFarVault
Write-Host "=== Migration vers coffre ===" -ForegroundColor Cyan
Write-Host "Cible: $vault"
Write-Host ""

$moves = @(
    @{ Src = "production.env"; Dst = "production.env" }
    @{ Src = "local.env"; Dst = "local.env" }
    @{ Src = "vanguard.env"; Dst = "vanguard.env" }
    @{ Src = ".render-api-key"; Dst = "keys\render.api-key" }
    @{ Src = ".ionos-api-key"; Dst = "keys\ionos.api-key" }
    @{ Src = "stripe\recovery-codes.txt"; Dst = "stripe\recovery-codes.txt" }
)

$migrated = 0
foreach ($m in $moves) {
    $srcPath = Join-Path $Root $m.Src
    $dstPath = Join-Path $vault $m.Dst
    if (-not (Test-Path $srcPath)) { continue }
    $dstDir = Split-Path -Parent $dstPath
    if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
    Copy-Item -Path $srcPath -Destination $dstPath -Force
    Remove-Item -Path $srcPath -Force
    Write-Host "[OK] $($m.Src) -> vault\$($m.Dst)" -ForegroundColor Green
    $migrated++
}

if ($migrated -eq 0) {
    Write-Host "Rien a migrer (deja dans le coffre ou fichiers absents)." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "$migrated fichier(s) migres. Plus aucun secret dans aria-vanguard/operator." -ForegroundColor Green
}

Write-Host ""
Write-Host "Verification: .\check-aria-status.ps1" -ForegroundColor Cyan