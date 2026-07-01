# Automatise l'URL du site Render partout (production.env, render.yaml, README, sync Render)
# Usage:
#   .\set-site.ps1                    # lit l'URL live Render et propage partout
#   .\set-site.ps1 -Name aria-api     # renomme le service + propage
#   .\set-site.ps1 -Name aria-api -Recreate   # nouveau service si le slug onrender.com est bloque

param(
    [string]$Name = "",
    [string]$Domain = "",
    [switch]$Recreate,
    [switch]$SkipSync,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) {
    Write-Host ".render-api-key manquant." -ForegroundColor Red
    exit 1
}

$headers = Get-RenderHeaders -ApiKey $apiKey
$config = Get-SiteConfig -Root $Root
$repoPath = Get-VanguardRepoPath -SecretsRoot $Root -Config $config
$serviceId = $config.renderServiceId
$targetName = if ($Name) { $Name } else { $config.renderServiceName }
if (-not $targetName) { $targetName = "aria-api" }

Write-Host "=== aria-api set-site ===" -ForegroundColor Cyan
Write-Host "Service ID: $serviceId"
Write-Host "Target name: $targetName"

$service = Get-RenderService -Headers $headers -ServiceId $serviceId
$currentUrl = Get-RenderPublicUrl -Service $service
$expectedUrl = Get-ExpectedRenderUrl -ServiceName $targetName

Write-Host "Current URL: $currentUrl"
Write-Host "Expected URL for '$targetName': $expectedUrl"

if ($currentUrl -ne $expectedUrl) {
    if ($Recreate) {
        if (-not $Force) {
            Write-Host ""
            Write-Host "ATTENTION: -Recreate cree un NOUVEAU service Render." -ForegroundColor Yellow
            Write-Host "Le disque persistant de l'ancien service ne migre pas automatiquement." -ForegroundColor Yellow
            Write-Host "Relance avec -Force pour confirmer." -ForegroundColor Yellow
            exit 2
        }

        Write-Host ""
        Write-Host "Recreation du service Render '$targetName'..." -ForegroundColor Cyan
        $envVars = Get-RenderEnvVars -Headers $headers -ServiceId $serviceId
        $legacyName = "$targetName-legacy-$($service.slug)"

        Write-Host "Renommage ancien service -> $legacyName" -ForegroundColor Yellow
        Invoke-RenderServiceRename -Headers $headers -ServiceId $serviceId -NewName $legacyName | Out-Null

        $region = $service.serviceDetails.region
        if (-not $region) { $region = "oregon" }

        $newService = New-RenderDexPulseService `
            -Headers $headers `
            -OwnerId $service.ownerId `
            -Name $targetName `
            -Repo $service.repo `
            -Branch $service.branch `
            -Region $region `
            -EnvVars $envVars

        $serviceId = $newService.id
        $service = Get-RenderService -Headers $headers -ServiceId $serviceId
        $currentUrl = Get-RenderPublicUrl -Service $service

        Write-Host "Nouveau service: $serviceId" -ForegroundColor Green
        Write-Host "Nouvelle URL: $currentUrl" -ForegroundColor Green

        Write-Host "Suspension de l'ancien service $($config.renderServiceId)..." -ForegroundColor Yellow
        try {
            Invoke-RenderSuspendService -Headers $headers -ServiceId $config.renderServiceId | Out-Null
        } catch {
            Write-Host "[WARN] Suspend failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }

        Invoke-RenderCreateDeploy -Headers $headers -ServiceId $serviceId | Out-Null
    } elseif ($Name) {
        Write-Host "Renommage du service Render -> $targetName" -ForegroundColor Cyan
        $service = Invoke-RenderServiceRename -Headers $headers -ServiceId $serviceId -NewName $targetName
        $currentUrl = Get-RenderPublicUrl -Service $service
        if ($currentUrl -ne $expectedUrl) {
            Write-Host ""
            Write-Host "Le slug onrender.com n'a pas change ($currentUrl)." -ForegroundColor Yellow
            Write-Host "Render ne permet pas de changer l'URL apres creation." -ForegroundColor Yellow
            Write-Host "Pour obtenir $expectedUrl : .\set-site.ps1 -Name $targetName -Recreate -Force" -ForegroundColor Cyan
        }
    }
}

if (-not $currentUrl) {
    Write-Host "URL Render introuvable." -ForegroundColor Red
    exit 1
}

$productUrl = if ($config.holdingApiUrl) { $config.holdingApiUrl } elseif ($config.siteBaseUrl) { $config.siteBaseUrl } else { $currentUrl }
$holdingDomain = if ($config.holdingDomain) { $config.holdingDomain } else { "ariavanguardzhc.com" }
$holdingSiteUrl = if ($config.holdingSiteUrl) { $config.holdingSiteUrl } else { "https://$holdingDomain" }

if ($Domain) {
    $holdingDomain = ($Domain -replace '^https?://', '').Split('/')[0]
    $holdingSiteUrl = Normalize-SiteUrl $Domain
    Write-Host ""
    Write-Host "Holding domain override: $holdingDomain" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Propagation SITE_BASE_URL=$productUrl (API holding)" -ForegroundColor Cyan
Write-Host "Holding domain: $holdingDomain (aria-vanguard repo)" -ForegroundColor Cyan
Update-SiteUrlEverywhere `
    -SecretsRoot $Root `
    -RepoPath $repoPath `
    -ServiceName $targetName `
    -SiteUrl $productUrl `
    -HoldingDomain $holdingDomain `
    -HoldingSiteUrl $holdingSiteUrl
Set-SiteConfig -Root $Root -ServiceId $serviceId -ServiceName $targetName -SiteBaseUrl $productUrl -RenderBaseUrl $currentUrl -HoldingDomain $holdingDomain -HoldingSiteUrl $holdingSiteUrl

if (-not $SkipSync) {
    Write-Host ""
    & (Join-Path $Root "sync-render.ps1")
}

Write-Host ""
Write-Host "Termine. API: $productUrl | Holding: $holdingSiteUrl" -ForegroundColor Green
Write-Host "Holding site: deploy aria-vanguard repo -> $holdingDomain on Render Static Site" -ForegroundColor Yellow
if ($currentUrl -ne $expectedUrl -and -not $Recreate -and -not $Domain) {
    Write-Host "Astuce: domaine custom -> .\set-site.ps1 -Domain ariavanguardzhc.com" -ForegroundColor Cyan
}