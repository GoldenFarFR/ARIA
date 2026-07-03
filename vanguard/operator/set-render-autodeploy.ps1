# Active/desactive autoDeploy Render + verifie l'etat.
# Usage: .\set-render-autodeploy.ps1 -Mode off
#        .\set-render-autodeploy.ps1 -Mode on

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("on", "off")]
    [string]$Mode
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) {
    Write-Host "Cle Render manquante." -ForegroundColor Red
    exit 1
}

$headers = Get-RenderHeaders -ApiKey $apiKey
$serviceId = Find-RenderServiceId -Headers $headers -ServiceName "aria-api"
if (-not $serviceId) {
    Write-Host "Service aria-api introuvable." -ForegroundColor Red
    exit 1
}

$auto = if ($Mode -eq "on") { "yes" } else { "no" }
$body = (@{ autoDeploy = $auto } | ConvertTo-Json -Compress)
$svc = Invoke-RestMethod -Uri "https://api.render.com/v1/services/$serviceId" -Method Patch -Headers $headers -Body $body
Write-Host "aria-api autoDeploy=$($svc.autoDeploy) (service $serviceId)" -ForegroundColor Green

if ($Mode -eq "off") {
    Write-Host "Deploy prod: deploy-render.ps1 (manuel) ou workflow render-daily-deploy.yml (1x/jour)." -ForegroundColor Cyan
}