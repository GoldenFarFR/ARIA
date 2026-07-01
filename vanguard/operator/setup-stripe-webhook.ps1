# Cree l'endpoint webhook Stripe + enregistre whsec_ dans production.env + sync Render
# Usage: .\setup-stripe-webhook.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_site-common.ps1")
. (Join-Path $Root "_vault-common.ps1")

$config = Get-SiteConfig -Root $Root
$apiUrl = if ($config.holdingApiUrl) { $config.holdingApiUrl } else { "https://test-1-nwf2.onrender.com" }
$webhookUrl = "$($apiUrl.TrimEnd('/'))/api/billing/webhook"
$envPath = Get-ProductionEnvPath -ScriptsRoot $Root

$vars = @{}
Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $i = $line.IndexOf("=")
    if ($i -gt 0) { $vars[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim() }
}

$sk = $vars["STRIPE_SECRET_KEY"]
if (-not $sk) {
    Write-Host "STRIPE_SECRET_KEY absent dans production.env" -ForegroundColor Red
    exit 1
}

if ($vars["STRIPE_WEBHOOK_SECRET"]) {
    Write-Host "STRIPE_WEBHOOK_SECRET deja present (len=$($vars['STRIPE_WEBHOOK_SECRET'].Length))." -ForegroundColor Yellow
    Write-Host "Supprime la ligne pour recreer, ou garde l'existant."
    exit 0
}

$events = @(
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted"
)

Write-Host "=== Stripe webhook ===" -ForegroundColor Cyan
Write-Host "URL: $webhookUrl"

$parts = @("url=$([uri]::EscapeDataString($webhookUrl))")
foreach ($ev in $events) { $parts += "enabled_events[]=$([uri]::EscapeDataString($ev))" }
$bytes = [System.Text.Encoding]::UTF8.GetBytes(($parts -join "&"))

$req = [System.Net.HttpWebRequest]::Create("https://api.stripe.com/v1/webhook_endpoints")
$req.Method = "POST"
$req.ContentType = "application/x-www-form-urlencoded"
$cred = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${sk}:"))
$req.Headers.Add("Authorization", "Basic $cred")
$req.ContentLength = $bytes.Length
$stream = $req.GetRequestStream()
$stream.Write($bytes, 0, $bytes.Length)
$stream.Close()

try {
    $resp = $req.GetResponse()
    $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
    $json = $reader.ReadToEnd() | ConvertFrom-Json
    $whsec = $json.secret
    if (-not $whsec) { throw "Pas de secret dans la reponse Stripe" }
    Update-EnvFileKey -Path $envPath -Key "STRIPE_WEBHOOK_SECRET" -Value $whsec
    Write-Host "[OK] Webhook cree. whsec enregistre dans production.env (non affiche)." -ForegroundColor Green
    Write-Host "Sync Render..." -ForegroundColor Cyan
    & (Join-Path $Root "sync-render.ps1")
} catch [System.Net.WebException] {
    $err = $_.Exception
    if ($err.Response) {
        $r = New-Object System.IO.StreamReader($err.Response.GetResponseStream())
        Write-Host $r.ReadToEnd() -ForegroundColor Red
    }
    throw
}