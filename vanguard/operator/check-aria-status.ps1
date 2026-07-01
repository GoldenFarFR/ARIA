# Audit ARIA — production.env vs Render vs /api/health (pas de secrets affiches)
# Usage: .\check-aria-status.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

function Read-EnvHashtable {
    param([string]$Path)
    $h = @{}
    if (-not (Test-Path $Path)) { return $h }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $h[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim()
    }
    return $h
}

function Mask-Value {
    param([string]$Key, [string]$Value)
    if (-not $Value) { return "[empty]" }
    if ($Key -match 'TOKEN|SECRET|KEY|PASSWORD') { return "[set len=$($Value.Length)]" }
    return $Value
}

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) {
    Write-Host "ERREUR: cle Render manquante (coffre keys\render.api-key)" -ForegroundColor Red
    exit 1
}

$headers = Get-RenderHeaders -ApiKey $apiKey
$serviceId = Resolve-RenderServiceId -Headers $headers -Root $Root -FallbackName "aria-api"
$remote = Get-RenderEnvVars -Headers $headers -ServiceId $serviceId
$local = Read-EnvHashtable -Path (Get-ProductionEnvPath -ScriptsRoot $Root)
$localDev = Read-EnvHashtable -Path (Get-LocalEnvPath -ScriptsRoot $Root)

Write-Host "=== ARIA status audit ===" -ForegroundColor Cyan
Write-Host "Render service: $serviceId"
Write-Host ""

$critical = @(
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_IDS", "LLM_API_KEY",
    "GITHUB_TOKEN", "GITHUB_READ_REPOS", "GITHUB_WRITE_REPOS",
    "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
    "PRIVY_APP_ID"
)

$issues = @()
$warnings = @()
foreach ($k in $critical) {
    $r = $remote[$k]
    $l = $local[$k]
    $rm = Mask-Value $k $r
    $lm = Mask-Value $k $l
    $ok = ($r -and $l -and $r -eq $l) -or ($r -and -not $l)
    if (-not $r) { $issues += "Render manque: $k" }
    elseif ($l -and $r -ne $l) { $issues += "DIFF $k : Render=$rm Local=$lm" }
    Write-Host ("  {0,-28} Render={1,-18} Local={2}" -f $k, $rm, $lm)
}

Write-Host ""
Write-Host "=== Stripe Aria Market Pro (stripe/README.md) ===" -ForegroundColor Cyan
foreach ($k in @("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET")) {
    $r = $remote[$k]
    $l = $local[$k]
    Write-Host ("  {0,-28} Render={1,-18} Local={2}" -f $k, (Mask-Value $k $r), (Mask-Value $k $l))
    if (-not $r -and $l) { $warnings += "Render sans $k - lance sync-render.ps1" }
    elseif ($r -and $l -and $r -ne $l) { $issues += "DIFF $k Render vs production.env" }
}
$sk = $local["STRIPE_SECRET_KEY"]
$wh = $local["STRIPE_WEBHOOK_SECRET"]
if ($sk -and -not $wh) {
    $warnings += "STRIPE_WEBHOOK_SECRET absent - checkout OK mais abo Pro pas active apres paiement (voir stripe/README.md)"
}
if ($sk -match '^sk_test_' -and $wh -and $local["STRIPE_PRICE_ID"] -and -not $remote["STRIPE_WEBHOOK_SECRET"]) {
    $warnings += "STRIPE_WEBHOOK_SECRET pas sur Render - ajoute whsec_ puis sync-render"
}

Write-Host ""
Write-Host "=== Dev local (aria-vanguard/backend/.env via sync-local.ps1) ===" -ForegroundColor Cyan
$backendEnv = Read-EnvHashtable -Path (Join-Path $Root "..\backend\.env")
foreach ($k in @("X_API_KEY", "GITHUB_TOKEN")) {
    $be = $backendEnv[$k]
    $ld = $localDev[$k]
    Write-Host ("  {0,-28} backend/.env={1}" -f $k, (Mask-Value $k $be))
    if (-not $be -and -not $ld -and $local[$k]) {
        $script:warnings += "backend/.env sans $k - lance .\sync-local.ps1"
    }
}

$config = Get-Content (Join-Path $Root "site.config.json") -Raw | ConvertFrom-Json
$healthCandidates = @()
if ($config.siteBaseUrl) { $healthCandidates += $config.siteBaseUrl.TrimEnd('/') }
if ($config.holdingApiUrl -and $config.holdingApiUrl -notin $healthCandidates) {
    $healthCandidates += $config.holdingApiUrl.TrimEnd('/')
}
if ($config.renderBaseUrl -and $config.renderBaseUrl -notin $healthCandidates) {
    $healthCandidates += $config.renderBaseUrl.TrimEnd('/')
}
$health = $null
$healthUrl = $null
foreach ($base in $healthCandidates) {
    $candidate = "$base/api/health"
    Write-Host ""
    Write-Host "=== Live health: $candidate ===" -ForegroundColor Cyan
    try {
        $health = Invoke-RestMethod -Uri $candidate -TimeoutSec 30
        $healthUrl = $candidate
        if ($base -ne $healthCandidates[0]) {
            $warnings += "Health OK via fallback $base (DNS api holding pas pret?)"
        }
        break
    } catch {
        Write-Host "  ERREUR: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}
try {
    if (-not $health) { throw "Aucun endpoint health accessible" }
    Write-Host "  commit: $($health.commit)"
    if ($health.aria_x) {
        Write-Host "  aria_x.post: $($health.aria_x.post_configured) read: $($health.aria_x.read_configured)"
        if (-not $health.aria_x.post_configured) { $issues += "PROD: aria_x.post_configured=false (redeploy apres sync?)" }
    }
    if ($health.aria_github) {
        Write-Host "  aria_github: configured=$($health.aria_github.configured) unlimited=$($health.aria_github.unlimited)"
    }
    if ($health.aria_telegram) {
        Write-Host "  aria_telegram: $($health.aria_telegram.configured)"
    }
    if ($health.aria_llm) {
        Write-Host "  aria_llm: enabled=$($health.aria_llm.enabled) key=$($health.aria_llm.provider_configured)"
    }
    if ($health.billing) {
        Write-Host "  billing: stripe=$($health.billing.stripe_configured) webhook=$($health.billing.stripe_webhook_configured)"
        if ($health.billing.stripe_configured -and -not $health.billing.stripe_webhook_configured) {
            $warnings += "PROD: stripe_configured=true mais stripe_webhook_configured=false - ajoute STRIPE_WEBHOOK_SECRET (whsec_)"
        }
    }
    if ($null -ne $health.aria_core_build) {
        Write-Host "  aria_core_build: $($health.aria_core_build)"
        $reqPath = Join-Path $Root "..\backend\requirements.txt"
        if (Test-Path $reqPath) {
            $req = Get-Content $reqPath -Raw
            if ($req -match '@([a-f0-9]{7,40})#subdirectory=packages/aria-core') {
                $pinShort = $Matches[1].Substring(0, [Math]::Min(7, $Matches[1].Length))
                $build = [string]$health.aria_core_build
                $stale = @("6bd32bf", "655221c", "49e584c") -contains $build
                if ($stale) {
                    $warnings += "aria_core_build=$build obsolete (pin $pinShort) - redeploy ou bump pin"
                }
            }
        }
    }
} catch {
    $issues += "Health check failed: $($_.Exception.Message)"
    Write-Host "  ERREUR: $($_.Exception.Message)" -ForegroundColor Red
}

$wh = $remote["TELEGRAM_WEBHOOK_SECRET"]
$whLocal = $local["TELEGRAM_WEBHOOK_SECRET"]
if (-not $wh) {
    $warnings += "TELEGRAM_WEBHOOK_SECRET absent sur Render (webhook sans secret)"
} elseif ($whLocal -and $wh -ne $whLocal) {
    $warnings += "TELEGRAM_WEBHOOK_SECRET: Render != production.env (sync-render?)"
}

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "AVERTISSEMENTS ($($warnings.Count)):" -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host "  - $_" }
}

Write-Host ""
if ($issues.Count -eq 0) {
    Write-Host "OK - aucun probleme critique detecte." -ForegroundColor Green
    Write-Host "Apres toute modif production.env: .\sync-render.ps1 (redeploy auto inclus)." -ForegroundColor DarkGray
    exit 0
}

Write-Host "PROBLEMES ($($issues.Count)):" -ForegroundColor Red
$issues | ForEach-Object { Write-Host "  - $_" }
Write-Host ""
Write-Host "Fix: edite production.env puis .\sync-render.ps1" -ForegroundColor Yellow

$gapScript = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "local-sync\scripts\file-self-improve-gap.ps1"
if (Test-Path $gapScript) {
    $ctx = ($issues + $warnings | Select-Object -First 12) -join "`n"
    $capId = "operator_health_check_failed"
    if (($issues | Where-Object { $_ -match "DIFF " }).Count -gt 0) {
        $capId = "operator_env_mismatch"
    }
    & $gapScript -CapabilityId $capId -Context $ctx -NoPr | Out-Null
}

exit 1