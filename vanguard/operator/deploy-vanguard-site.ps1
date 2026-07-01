# Deploy vitrine ariavanguardzhc.com (static site Render) — monorepo ARIA/vanguard
# Usage: .\deploy-vanguard-site.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) { throw "Cle Render manquante (coffre keys\render.api-key)" }
$headers = Get-RenderHeaders -ApiKey $apiKey
$config = Get-Content (Join-Path $Root "site.config.json") -Raw | ConvertFrom-Json

$serviceId = if ($config.vanguardRenderServiceId) { $config.vanguardRenderServiceId } else { $null }
if (-not $serviceId) {
    foreach ($name in @("Aria Vanguard ZHC", "aria-vanguard")) {
        $serviceId = Find-RenderServiceId -Headers $headers -ServiceName $name
        if ($serviceId) { break }
    }
}
if (-not $serviceId) { throw "Static site introuvable (Aria Vanguard ZHC)" }

$apiUrl = if ($config.holdingApiUrl) { $config.holdingApiUrl.TrimEnd('/') } else { $config.siteBaseUrl.TrimEnd('/') }
$productApi = "$apiUrl/api"

Write-Host "=== deploy-vanguard-site ===" -ForegroundColor Green
Write-Host "Service: $serviceId" -ForegroundColor Cyan
Write-Host "Root: vanguard | API: $productApi" -ForegroundColor DarkGray

$patchBody = @{
    serviceDetails = @{
        rootDir      = "vanguard"
        buildCommand = "npm ci && npm run build"
        publishPath  = "dist"
    }
} | ConvertTo-Json -Depth 4 -Compress

Invoke-RestMethod `
    -Uri "https://api.render.com/v1/services/$serviceId" `
    -Method Patch `
    -Headers $headers `
    -Body $patchBody | Out-Null
Write-Host "[OK] rootDir=vanguard" -ForegroundColor Green

foreach ($pair in @{
        VITE_PRODUCT_URL     = $apiUrl
        VITE_PRODUCT_API_URL = $productApi
    }.GetEnumerator()) {
    $url = "https://api.render.com/v1/services/$serviceId/env-vars/$($pair.Key)"
    $payload = (@{ value = $pair.Value } | ConvertTo-Json -Compress)
    Invoke-RestMethod -Uri $url -Method Put -Headers $headers -Body $payload | Out-Null
    Write-Host "[OK] $($pair.Key)" -ForegroundColor Green
}

$cfgPath = Join-Path $Root "site.config.json"
$cfgObj = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfgObj | Add-Member -NotePropertyName vanguardRenderServiceId -NotePropertyValue $serviceId -Force
($cfgObj | ConvertTo-Json -Depth 4) + "`n" | Set-Content -Path $cfgPath -Encoding UTF8 -NoNewline

$deploy = Start-RenderServiceDeploy -Headers $headers -ServiceId $serviceId
Wait-RenderServiceDeploy -Headers $headers -ServiceId $serviceId -DeployId $deploy.id -TimeoutSeconds 600 | Out-Null
Write-Host "Vitrine live — hard refresh (Ctrl+F5) sur https://ariavanguardzhc.com" -ForegroundColor Green