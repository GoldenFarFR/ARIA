# Met a jour l'URL d'un endpoint webhook Stripe existant (meme whsec_)
# Usage: .\update-stripe-webhook-url.ps1 [-Url https://test-1-nwf2.onrender.com/api/billing/webhook]

param(
    [string]$Url = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_site-common.ps1")
. (Join-Path $Root "_vault-common.ps1")

$config = Get-SiteConfig -Root $Root
$fallback = if ($config.renderBaseUrl) { $config.renderBaseUrl } else { "https://test-1-nwf2.onrender.com" }
$canonical = if ($config.holdingApiUrl) { $config.holdingApiUrl } else { $fallback }
if (-not $Url) {
    try {
        Resolve-DnsName ($config.holdingApiDomain) -Type CNAME -ErrorAction Stop | Out-Null
        $Url = "$($canonical.TrimEnd('/'))/api/billing/webhook"
        Write-Host "DNS OK -> URL canonique $Url" -ForegroundColor Green
    } catch {
        $Url = "$($fallback.TrimEnd('/'))/api/billing/webhook"
        Write-Host "DNS absent -> fallback Render $Url" -ForegroundColor Yellow
    }
}

$envPath = Get-ProductionEnvPath -ScriptsRoot $Root
$vars = @{}
Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $i = $line.IndexOf("=")
    if ($i -gt 0) { $vars[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim() }
}
$sk = $vars["STRIPE_SECRET_KEY"]
if (-not $sk) { throw "STRIPE_SECRET_KEY absent dans production.env" }

$cred = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${sk}:"))
$headers = @{ Authorization = "Basic $cred" }
$list = Invoke-RestMethod -Uri "https://api.stripe.com/v1/webhook_endpoints?limit=20" -Headers $headers
$endpoint = $list.data | Select-Object -First 1
if (-not $endpoint) {
    Write-Host "Aucun endpoint - lance .\setup-stripe-webhook.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "=== Stripe webhook URL ===" -ForegroundColor Cyan
Write-Host "Endpoint: $($endpoint.id)"
Write-Host "Ancienne: $($endpoint.url)"
Write-Host "Nouvelle: $Url"

$body = "url=$([uri]::EscapeDataString($Url))"
$updated = Invoke-RestMethod `
    -Uri "https://api.stripe.com/v1/webhook_endpoints/$($endpoint.id)" `
    -Headers $headers `
    -Method Post `
    -Body $body `
    -ContentType "application/x-www-form-urlencoded"

Write-Host "[OK] $($updated.url)" -ForegroundColor Green