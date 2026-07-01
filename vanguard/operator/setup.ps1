# Setup une fois - plus de copier-coller manuel

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== aria-vanguard operator - setup ===" -ForegroundColor Cyan

& (Join-Path $Root "init-from-local.ps1")
& (Join-Path $Root "sync-local.ps1")

. (Join-Path $Root "_vault-common.ps1")
Initialize-GoldenFarVault | Out-Null
$apiKeyFile = Get-RenderApiKeyPath -ScriptsRoot $Root
if (Test-Path $apiKeyFile) {
    Write-Host ""
    Write-Host "Cle Render detectee - import des secrets prod..." -ForegroundColor Cyan
    & (Join-Path $Root "pull-render.ps1")
    Write-Host ""
    Write-Host "Pour pousser vers Render : .\sync-render.ps1" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "Etape restante (une seule fois) :" -ForegroundColor Yellow
    Write-Host "  1. Render Dashboard > Account Settings > API Keys > Create"
    Write-Host "  2. Colle rnd_... dans : $apiKeyFile"
    Write-Host "  3. Relance : .\setup.ps1"
    Write-Host ""
    Write-Host "Ou import direct : .\pull-render.ps1" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Setup termine. Usage quotidien :" -ForegroundColor Green
Write-Host "  .\sync-all.ps1   # local + Render d'un coup"