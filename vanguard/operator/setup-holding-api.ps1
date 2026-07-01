# API holding permanente — api.ariavanguardzhc.com → service Render DEXPulse (backend ARIA)
# Usage: .\setup-holding-api.ps1
# DNS (chez ton registrar): CNAME api → test-1-nwf2.onrender.com

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$ApiDomain = "api.ariavanguardzhc.com"
$ApiUrl = "https://$ApiDomain"
$HoldingDomain = "ariavanguardzhc.com"
$HoldingUrl = "https://$HoldingDomain"
$RenderCname = "test-1-nwf2.onrender.com"

$config = Get-SiteConfig -Root $Root
$serviceId = $config.renderServiceId
if (-not $serviceId) { throw "renderServiceId manquant dans site.config.json" }

Write-Host "=== API holding GoldenFar ===" -ForegroundColor Cyan
Write-Host "API canonique : $ApiUrl"
Write-Host "Vitrine       : $HoldingUrl"
Write-Host "Render CNAME  : $RenderCname"
Write-Host ""

# site.config.json
$cfgPath = Join-Path $Root "site.config.json"
$cfgObj = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfgObj | Add-Member -NotePropertyName holdingApiDomain -NotePropertyValue $ApiDomain -Force
$cfgObj | Add-Member -NotePropertyName holdingApiUrl -NotePropertyValue $ApiUrl -Force
$cfgObj | Add-Member -NotePropertyName holdingDomain -NotePropertyValue $HoldingDomain -Force
$cfgObj | Add-Member -NotePropertyName holdingSiteUrl -NotePropertyValue $HoldingUrl -Force
($cfgObj | ConvertTo-Json -Depth 5) + "`n" | Set-Content $cfgPath -Encoding UTF8 -NoNewline

# production.env
Update-EnvFileKey -Path (Get-ProductionEnvPath -ScriptsRoot $Root) -Key "SITE_BASE_URL" -Value $ApiUrl
Update-EnvFileKey -Path (Get-ProductionEnvPath -ScriptsRoot $Root) -Key "HOLDING_DOMAIN" -Value $HoldingDomain
Update-EnvFileKey -Path (Join-Path $Root "production.env.example") -Key "SITE_BASE_URL" -Value $ApiUrl
Update-EnvFileKey -Path (Join-Path $Root "production.env.example") -Key "HOLDING_DOMAIN" -Value $HoldingDomain

$prodPath = Get-ProductionEnvPath -ScriptsRoot $Root
$lines = Get-Content $prodPath
$corsKey = "CORS_ORIGINS"
$origins = @(
    $HoldingUrl,
    "https://www.$HoldingDomain",
    $ApiUrl,
    "https://$RenderCname",
    "https://kikou-9z7a.onrender.com"
) | Select-Object -Unique
$newCors = ($origins -join ",")
$found = $false
$out = foreach ($line in $lines) {
    if ($line -match "^$corsKey=") {
        $found = $true
        "$corsKey=$newCors"
    } else { $line }
}
if (-not $found) { $out += "$corsKey=$newCors" }
Set-Content -Path $prodPath -Value $out -Encoding UTF8

# vanguard.env
$vanguardEnv = Get-VanguardEnvPath -ScriptsRoot $Root
Update-EnvFileKey -Path $vanguardEnv -Key "VITE_PRODUCT_API_URL" -Value "$ApiUrl/api"
Update-EnvFileKey -Path $vanguardEnv -Key "VITE_PRODUCT_URL" -Value $ApiUrl
Update-EnvFileKey -Path (Join-Path $Root "vanguard.env.example") -Key "VITE_PRODUCT_API_URL" -Value "$ApiUrl/api"
Update-EnvFileKey -Path (Join-Path $Root "vanguard.env.example") -Key "VITE_PRODUCT_URL" -Value $ApiUrl

# aria-vanguard repo
$vRel = if ($config.vanguardRepoPath) { $config.vanguardRepoPath } else { ".." }
$vanguardRepo = Resolve-Path (Join-Path $Root $vRel) -ErrorAction SilentlyContinue
if ($vanguardRepo) {
    $renderYaml = Join-Path $vanguardRepo "render.yaml"
    if (Test-Path $renderYaml) {
        $c = Get-Content $renderYaml -Raw
        $c = $c -replace '(?m)(- key: VITE_PRODUCT_URL\s*\r?\n\s*value:\s*).*$', "`${1}$ApiUrl"
        $c = $c -replace '(?m)(- key: VITE_PRODUCT_API_URL\s*\r?\n\s*value:\s*).*$', "`${1}$ApiUrl/api"
        Set-Content $renderYaml -Value $c -Encoding UTF8 -NoNewline
    }
    $siteTs = Join-Path $vanguardRepo "src\lib\site.ts"
    if (Test-Path $siteTs) {
        $c = Get-Content $siteTs -Raw
        $c = $c -replace "import\.meta\.env\.VITE_PRODUCT_URL \?\? '[^']+'", "import.meta.env.VITE_PRODUCT_URL ?? '$ApiUrl'"
        $c = $c -replace "import\.meta\.env\.VITE_PRODUCT_API_URL \?\?[^\n]+", "import.meta.env.VITE_PRODUCT_API_URL ?? '$ApiUrl/api'"
        Set-Content $siteTs -Value $c -Encoding UTF8 -NoNewline
    }
}

Write-Host "[OK] Fichiers locaux mis a jour." -ForegroundColor Green
Write-Host ""
Write-Host "DNS OBLIGATOIRE (registrar ariavanguardzhc.com) :" -ForegroundColor Yellow
Write-Host "  Type CNAME | Nom: api | Cible: $RenderCname"
Write-Host ""
Write-Host "Stripe webhook URL :" -ForegroundColor Cyan
Write-Host "  $ApiUrl/api/billing/webhook"
Write-Host ""
Write-Host "Sync Render + Vanguard..." -ForegroundColor Cyan
& (Join-Path $Root "sync-render.ps1")
& (Join-Path $Root "sync-vanguard.ps1")

Write-Host ""
Write-Host "Verification: .\check-aria-status.ps1" -ForegroundColor DarkGray