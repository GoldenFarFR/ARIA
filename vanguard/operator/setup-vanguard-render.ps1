# Create / sync Render static site for aria-vanguard (holding)
# Usage: .\setup-vanguard-render.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) { throw ".render-api-key manquant" }
$headers = Get-RenderHeaders -ApiKey $apiKey
$config = Get-Content (Join-Path $Root "site.config.json") -Raw | ConvertFrom-Json

$ownerId = "tea-d8pbtij6sc1c73cjfkug"
$serviceName = if ($config.vanguardRenderServiceName) { $config.vanguardRenderServiceName } else { "aria-vanguard" }
$repo = if ($config.vanguardRepo) { "https://github.com/$($config.vanguardRepo)" } else { "https://github.com/GoldenFarFR/aria-vanguard" }
$holdingDomain = if ($config.holdingDomain) { $config.holdingDomain } else { "ariavanguardzhc.com" }

$serviceId = if ($config.vanguardRenderServiceId) { $config.vanguardRenderServiceId } else { Find-RenderServiceId -Headers $headers -ServiceName $serviceName }

if (-not $serviceId) {
    Write-Host "Creation static site $serviceName..." -ForegroundColor Cyan
    $privyAppId = if ($config.privyAppId) { $config.privyAppId } else { "" }
    $envVars = @(
        @{ key = "VITE_PRODUCT_URL"; value = $config.siteBaseUrl }
        @{ key = "VITE_PRODUCT_API_URL"; value = "$($config.siteBaseUrl)/api" }
    )
    if ($privyAppId) {
        $envVars += @{ key = "VITE_PRIVY_APP_ID"; value = $privyAppId }
    }
    $body = @{
        type           = "static_site"
        name           = $serviceName
        ownerId        = $ownerId
        repo           = $repo
        branch         = "main"
        autoDeploy     = "yes"
        serviceDetails = @{
            buildCommand = "npm ci && npm run build"
            publishPath  = "dist"
        }
        envVars        = $envVars
    } | ConvertTo-Json -Depth 6 -Compress
    $created = Invoke-RestMethod -Uri "https://api.render.com/v1/services" -Method Post -Headers $headers -Body $body
    $serviceId = $created.service.id
    Write-Host "Cree: $serviceId -> $($created.service.serviceDetails.url)" -ForegroundColor Green
}

$cfgPath = Join-Path $Root "site.config.json"
$cfgObj = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfgObj | Add-Member -NotePropertyName vanguardRenderServiceId -NotePropertyValue $serviceId -Force
($cfgObj | ConvertTo-Json -Depth 4) + "`n" | Set-Content -Path $cfgPath -Encoding UTF8 -NoNewline

Write-Host "aria-vanguard service: $serviceId" -ForegroundColor Green
Write-Host "Custom domain: $holdingDomain (configure in Render if not yet added)" -ForegroundColor Yellow