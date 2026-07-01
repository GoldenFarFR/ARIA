# Deploy Phase D memoire vectorielle — 1) code bbfc827+chromadb  2) ARIA_VECTOR_MEMORY=true
# Usage: .\deploy-vector-memory.ps1
# Bloque si quota Render epuise — relancer apres reset ou upgrade Starter.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_vault-common.ps1")

$prodPath = Get-ProductionEnvPath -ScriptsRoot $Root

Write-Host "=== deploy-vector-memory ===" -ForegroundColor Green
Write-Host "Etape 1/2 : deploy code (pin bbfc827 + chromadb, flag OFF)" -ForegroundColor Cyan

& (Join-Path $Root "deploy-render.ps1") -Reason "Phase D memoire vectorielle bbfc827 + chromadb"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Deploy code echoue ou bloque (quota?) — ARIA_VECTOR_MEMORY reste false" -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host "Etape 2/2 : activer ARIA_VECTOR_MEMORY=true + sync env" -ForegroundColor Cyan
Update-EnvFileKey -Path $prodPath -Key "ARIA_VECTOR_MEMORY" -Value "true"
if (Test-Path (Join-Path $Root "production.env.example")) {
    Update-EnvFileKey -Path (Join-Path $Root "production.env.example") -Key "ARIA_VECTOR_MEMORY" -Value "true"
}

& (Join-Path $Root "deploy-render.ps1") -Reason "Activer ARIA_VECTOR_MEMORY prod" -EnvOnly
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $Root "check-aria-status.ps1")
Write-Host "Memoire vectorielle prod activee — nourrir via Telegram (corrections + lecons)" -ForegroundColor Green