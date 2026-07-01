# Sync production.env vers Render (service aria-api — repo aria-vanguard)
# Deploy prod: preferer deploy-render.ps1 -Reason "..." (build local + 1 redeploy).
# Ce script seul: .\sync-render.ps1 -SkipRedeploy pour vars sans rebuild.

param(
    [switch]$SkipRedeploy
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$EnvFile = Get-ProductionEnvPath -ScriptsRoot $Root
$ServiceName = "aria-api"

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) {
    Write-Host "Cle Render manquante (coffre keys\render.api-key)." -ForegroundColor Red
    Write-Host "1. Render Dashboard > Account Settings > API Keys > Create"
    Write-Host "2. Colle rnd_... dans : $(Get-RenderApiKeyPath -ScriptsRoot $Root)"
    exit 1
}

if (-not (Test-Path $EnvFile)) {
    Write-Host "Lance d'abord .\setup.ps1 ou .\pull-render.ps1" -ForegroundColor Red
    exit 1
}

$headers = Get-RenderHeaders -ApiKey $apiKey

# Auto-align SITE_BASE_URL from site.config.json (API holding, pas la vitrine)
try {
    & (Join-Path $Root "set-site.ps1") -SkipSync | Out-Null
} catch {
    Write-Host "[WARN] set-site auto-sync skipped: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "Recherche du service Render..." -ForegroundColor Cyan
$serviceId = Resolve-RenderServiceId -Headers $headers -Root $Root -FallbackName $ServiceName
if (-not $serviceId) {
    Write-Host "Service Render introuvable (verifie site.config.json)." -ForegroundColor Red
    exit 1
}

Write-Host "Service ID: $serviceId" -ForegroundColor Green

$vars = Read-EnvFile -Path $EnvFile
$toSync = @{}
foreach ($key in $vars.Keys) {
    if ($vars[$key]) { $toSync[$key] = $vars[$key] }
}

if ($toSync.Count -eq 0) {
    Write-Host "Aucune variable a synchroniser dans production.env" -ForegroundColor Yellow
    exit 0
}

$ok = 0
foreach ($key in $toSync.Keys) {
    $body = (@{ value = $toSync[$key] } | ConvertTo-Json -Compress)
    $url = "https://api.render.com/v1/services/$serviceId/env-vars/$key"
    try {
        Invoke-RestMethod -Uri $url -Method Put -Headers $headers -Body $body | Out-Null
        Write-Host "[OK] $key" -ForegroundColor Green
        $ok++
    } catch {
        Write-Host "[ERREUR] $key : $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "$ok variable(s) synchronisee(s)." -ForegroundColor Cyan

if ($SkipRedeploy) {
    Write-Host "[SKIP] redeploy — nouvelles vars actives au prochain deploy-render.ps1" -ForegroundColor Yellow
    Write-Host "       (process Python actuel garde l'ancien env jusqu'au redeploy)" -ForegroundColor DarkGray
    exit 0
}

$pipe = Test-RenderPipelineAvailable -Headers $headers -ServiceId $serviceId
if (-not $pipe.available) {
    Write-Host "[BLOQUE] $($pipe.reason)" -ForegroundColor Red
    Write-Host "Vars sync OK sur Render. Utilise deploy-render.ps1 quand quota disponible." -ForegroundColor Yellow
    exit 2
}

Write-Host "Redemarrage Render (obligatoire pour charger les nouvelles variables)..." -ForegroundColor Cyan
Write-Host "Astuce: groupe les changements → deploy-render.ps1 une fois." -ForegroundColor DarkGray
try {
    $deploy = Start-RenderServiceDeploy -Headers $headers -ServiceId $serviceId
    $deployId = if ($deploy) { $deploy.id } else { "" }
    if ($deployId) {
        Write-Host "Deploy lance: $deployId" -ForegroundColor Green
    } else {
        Write-Host "Deploy lance (id inconnu) - attente du dernier deploy..." -ForegroundColor Yellow
    }
    Wait-RenderServiceDeploy -Headers $headers -ServiceId $serviceId -DeployId $deployId -TimeoutSeconds 420 | Out-Null
    Write-Host "Service live - variables actives." -ForegroundColor Green
} catch {
    Write-Host "[WARN] Redemarrage auto echoue: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Relance manuellement un deploy depuis le dashboard Render." -ForegroundColor Yellow
}