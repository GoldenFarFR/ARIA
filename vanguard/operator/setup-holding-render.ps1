# Create / sync Render static site for a holding / venture site (Lucky, etc.)
# Usage:
#   .\setup-holding-render.ps1 -SiteName lucky -UpdateCors
#   .\setup-holding-render.ps1 -SiteName lucky -Repo GoldenFarFR/lucky -Branch master -UpdateCors
#   .\setup-holding-render.ps1 -SiteName my-site -UpdateCors

param(
    [Parameter(Mandatory = $true)]
    [string]$SiteName,

    [string]$Repo = "",
    [string]$Branch = "master",
    [string]$PrivyAppId = "",
    [switch]$UpdateCors
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) { throw ".render-api-key manquant (voir aria-vanguard/operator/README.md)" }
$headers = Get-RenderHeaders -ApiKey $apiKey
$config = Get-SiteConfig -Root $Root

$ownerId = "tea-d8pbtij6sc1c73cjfkug"
$serviceName = $SiteName.ToLower()
if (-not $Repo) { $Repo = "GoldenFarFR/$serviceName" }
$repoUrl = if ($Repo -match '^https?://') { $Repo } else { "https://github.com/$Repo" }
$productUrl = if ($config.siteBaseUrl) { $config.siteBaseUrl } else { "https://test-1-nwf2.onrender.com" }
$apiUrl = "$($productUrl.TrimEnd('/'))/api"

if (-not $PrivyAppId) {
    $vanguardId = if ($config.vanguardRenderServiceId) { $config.vanguardRenderServiceId } else { $null }
    if ($vanguardId) {
        $vVars = Get-RenderEnvVars -Headers $headers -ServiceId $vanguardId
        if ($vVars['VITE_PRIVY_APP_ID']) { $PrivyAppId = $vVars['VITE_PRIVY_APP_ID'] }
    }
}
if (-not $PrivyAppId) {
    $siteExample = Join-Path (Split-Path $Root -Parent) "$serviceName\.env.example"
    if (Test-Path $siteExample) {
        $ex = Read-EnvFile -Path $siteExample
        if ($ex['VITE_PRIVY_APP_ID']) { $PrivyAppId = $ex['VITE_PRIVY_APP_ID'] }
    }
}
if (-not $PrivyAppId) {
    throw "VITE_PRIVY_APP_ID introuvable: passe -PrivyAppId ou configure aria-vanguard sur Render"
}

$cfgPath = Join-Path $Root "site.config.json"
$cfgRaw = Get-Content $cfgPath -Raw | ConvertFrom-Json
$holdingSites = @{}
if ($cfgRaw.holdingSites) {
    $cfgRaw.holdingSites.PSObject.Properties | ForEach-Object { $holdingSites[$_.Name] = $_.Value }
}

$existing = $holdingSites[$serviceName]
$serviceId = $null
if ($existing -and $existing.renderServiceId) {
    $serviceId = $existing.renderServiceId
}
if (-not $serviceId) {
    $serviceId = Find-RenderServiceId -Headers $headers -ServiceName $serviceName
}

if (-not $serviceId) {
    Write-Host "Creation static site '$serviceName' ($repoUrl @ $Branch)..." -ForegroundColor Cyan
    $body = @{
        type           = "static_site"
        name           = $serviceName
        ownerId        = $ownerId
        repo           = $repoUrl
        branch         = $Branch
        autoDeploy     = "yes"
        serviceDetails = @{
            buildCommand = "npm ci && npm run build"
            publishPath  = "dist"
        }
        envVars        = @(
            @{ key = "VITE_PRODUCT_URL"; value = $productUrl }
            @{ key = "VITE_PRODUCT_API_URL"; value = $apiUrl }
            @{ key = "VITE_PRIVY_APP_ID"; value = $PrivyAppId }
        )
    } | ConvertTo-Json -Depth 6 -Compress
    $created = Invoke-RestMethod -Uri "https://api.render.com/v1/services" -Method Post -Headers $headers -Body $body
    $serviceId = $created.service.id
    Write-Host "Cree: $serviceId" -ForegroundColor Green
} else {
    Write-Host "Service existant: $serviceId - sync env vars..." -ForegroundColor Cyan
    foreach ($pair in @{
            VITE_PRODUCT_URL     = $productUrl
            VITE_PRODUCT_API_URL = $apiUrl
            VITE_PRIVY_APP_ID     = $PrivyAppId
        }.GetEnumerator()) {
        $url = "https://api.render.com/v1/services/$serviceId/env-vars/$($pair.Key)"
        $payload = (@{ value = $pair.Value } | ConvertTo-Json -Compress)
        Invoke-RestMethod -Uri $url -Method Put -Headers $headers -Body $payload | Out-Null
    }
}

$service = Get-RenderService -Headers $headers -ServiceId $serviceId
$publicUrl = Get-RenderPublicUrl -Service $service
if (-not $publicUrl) { $publicUrl = "https://$serviceName.onrender.com" }

$holdingSites[$serviceName] = [ordered]@{
    repo               = $Repo
    branch             = $Branch
    renderServiceId    = $serviceId
    renderServiceName  = $serviceName
    renderBaseUrl      = $publicUrl
}

$merged = Get-Content $cfgPath -Raw | ConvertFrom-Json
if (-not $merged.PSObject.Properties['holdingSites']) {
    $merged | Add-Member -NotePropertyName holdingSites -NotePropertyValue ([pscustomobject]@{}) -Force
}
$siteEntry = [pscustomobject]@{
    repo              = $Repo
    branch            = $Branch
    renderServiceId   = $serviceId
    renderServiceName = $serviceName
    renderBaseUrl     = $publicUrl
}
if ($merged.holdingSites -is [pscustomobject]) {
    $merged.holdingSites | Add-Member -NotePropertyName $serviceName -NotePropertyValue $siteEntry -Force
} else {
    $merged.holdingSites = [pscustomobject]@{ $serviceName = $siteEntry }
}
($merged | ConvertTo-Json -Depth 6) + "`n" | Set-Content -Path $cfgPath -Encoding UTF8 -NoNewline

if ($UpdateCors) {
    $prodPath = Get-ProductionEnvPath -ScriptsRoot $Root
    if (-not (Test-Path $prodPath)) { throw "production.env manquant" }
    $vars = Read-EnvFile -Path $prodPath
    $origins = @()
    if ($vars['CORS_ORIGINS']) {
        $origins = $vars['CORS_ORIGINS'].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
    if ($publicUrl -notin $origins) {
        $origins += $publicUrl
        $newCors = ($origins -join ',')
        $content = Get-Content $prodPath -Raw
        if ($content -match '(?m)^CORS_ORIGINS=') {
            $content = $content -replace '(?m)^CORS_ORIGINS=.*', "CORS_ORIGINS=$newCors"
        } else {
            $content += "`nCORS_ORIGINS=$newCors`n"
        }
        Set-Content -Path $prodPath -Value $content.TrimEnd() -Encoding UTF8 -NoNewline
        Write-Host "CORS_ORIGINS + $publicUrl -> production.env" -ForegroundColor Cyan
    } else {
        Write-Host "CORS_ORIGINS contient deja $publicUrl" -ForegroundColor DarkGray
    }

    Write-Host "Sync CORS vers Render backend..." -ForegroundColor Cyan
    & (Join-Path $Root "sync-render.ps1")

    $backendId = Resolve-RenderServiceId -Headers $headers -Root $Root -FallbackName "dexpulse"
    if (-not $backendId) { throw "Service DEXPulse introuvable sur Render" }

    Write-Host "Redeploiement force backend DEXPulse (prise en compte CORS)..." -ForegroundColor Cyan
    $deploy = Start-RenderServiceDeploy -Headers $headers -ServiceId $backendId
    Wait-RenderServiceDeploy -Headers $headers -ServiceId $backendId -DeployId $deploy.id | Out-Null

    Write-Host "Verification CORS pour $publicUrl..." -ForegroundColor Cyan
    if (-not (Test-ApiCorsOrigin -ApiBaseUrl $apiUrl -Origin $publicUrl)) {
        throw "CORS inactif pour $publicUrl apres redeploiement. Verifie production.env puis relance avec -UpdateCors."
    }
    Write-Host "CORS OK pour $publicUrl - backend pret pour Sign in." -ForegroundColor Green
}

Write-Host ""
Write-Host "=== $serviceName deploye sur Render ===" -ForegroundColor Green
Write-Host "Service ID : $serviceId"
Write-Host "URL        : $publicUrl"
Write-Host "Privy      : ajouter $publicUrl dans Allowed origins (dashboard.privy.io)"
if ($UpdateCors) {
    Write-Host "CORS       : synchronise, redeploye et verifie automatiquement"
} else {
    Write-Host "CORS       : lance avec -UpdateCors pour sync + redeploy + verification"
}
Write-Host "Site local : mettre a jour src/lib/site.ts (SITE_DOMAIN) avec l URL ci-dessus"