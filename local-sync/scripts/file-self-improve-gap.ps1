# Ouvre issue/PR self-improve (Phase 3b) - API ARIA ou fallback GitHub direct
# Usage: .\file-self-improve-gap.ps1 -CapabilityId security_ip_changed_vault -Context "detail..."

param(
    [Parameter(Mandatory = $true)]
    [string]$CapabilityId,

    [string]$Context = "",

    [switch]$NoPr
)

$ErrorActionPreference = "Stop"

function Get-VaultEnvValue {
    param([string]$Key)
    $prod = Join-Path $env:LOCALAPPDATA "GoldenFar\vault\production.env"
    if (-not (Test-Path $prod)) { return $null }
    foreach ($line in Get-Content $prod -Encoding UTF8) {
        if ($line -match "^\s*$Key\s*=\s*(.+)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Get-GapSpec {
    param([string]$Id)
    $specs = @{
        security_ip_changed_vault       = @{ repo = "aria-local-sync"; title = "Securite: IP changee vault"; labels = @("aria-security") }
        security_unknown_machine_vault  = @{ repo = "aria-local-sync"; title = "Securite: machine inconnue vault"; labels = @("aria-security") }
        security_github_foreign_actor   = @{ repo = "aria-local-sync"; title = "Securite: acteur GitHub etranger"; labels = @("aria-security") }
        security_vault_untrusted_origin = @{ repo = "aria-local-sync"; title = "Securite: origine vault non enregistree"; labels = @("aria-security") }
        health_render_regression        = @{ repo = "aria-vanguard"; title = "Incident: regression health Render"; labels = @("aria-ops") }
        operator_health_check_failed    = @{ repo = "aria-vanguard"; title = "Incident: check-aria-status echec"; labels = @("aria-ops") }
        operator_env_mismatch           = @{ repo = "aria-vanguard"; title = "Incident: env Render divergent"; labels = @("aria-ops") }
        skill_missing                   = @{ repo = "aria-skills"; title = "Capacite: skill manquant"; labels = @("aria-self-improve") }
        post_session_aria_core_bump     = @{ repo = "aria-vanguard"; title = "Deploy: bump pin aria-core"; labels = @("deploy", "aria-self-improve") }
        x_profile_banner                = @{ repo = "aria-sandbox"; title = "Capacite: banniere X"; labels = @("aria-self-improve") }
        x_oauth_write                   = @{ repo = "aria-sandbox"; title = "Capacite: OAuth X"; labels = @("aria-self-improve") }
        image_api_key                   = @{ repo = "aria-sandbox"; title = "Capacite: generation image"; labels = @("aria-self-improve") }
    }
    if ($specs.ContainsKey($Id)) { return $specs[$Id] }
    return @{ repo = "aria-sandbox"; title = "Gap: $Id"; labels = @("aria-self-improve") }
}

function Invoke-ViaAriaApi {
    param([string]$CapId, [string]$Ctx, [bool]$OpenPr)
    $bridge = Join-Path $PSScriptRoot "totp-aria-bridge.ps1"
    if (-not (Test-Path $bridge)) { return $null }
    . $bridge
    $cfg = Get-AriaApiConfig
    if (-not $cfg.Secret) { return $null }
    $headers = @{
        "X-Admin-Secret" = $cfg.Secret
        "Content-Type"   = "application/json"
    }
    $body = @{
        capability_id = $CapId
        context       = $Ctx
        open_pr       = $OpenPr
        lang          = "fr"
    } | ConvertTo-Json
    try {
        return Invoke-RestMethod -Method Post `
            -Uri "$($cfg.Base)/api/aria/operator/file-gap" `
            -Headers $headers -Body $body -TimeoutSec 45
    } catch {
        Write-Host "[GAP] API ARIA indisponible: $($_.Exception.Message)" -ForegroundColor DarkYellow
        return $null
    }
}

function Invoke-ViaGitHubDirect {
    param([string]$CapId, [string]$Ctx)
    $token = Get-VaultEnvValue "GITHUB_TOKEN"
    if (-not $token) {
        Write-Host "[GAP] Pas de GITHUB_TOKEN - spec locale seulement (pas d issue)" -ForegroundColor DarkYellow
        return @{ status = "local_only" }
    }
    $owner = "GoldenFarFR"
    $spec = Get-GapSpec -Id $CapId
    $repo = $spec.repo
    $title = $spec.title
    $body = @(
        "# Capability gap: ``$CapId``",
        "",
        "Genere par file-self-improve-gap.ps1 ($env:COMPUTERNAME)",
        "",
        "## Contexte",
        $(if ($Ctx) { $Ctx } else { "(aucun detail)" }),
        "",
        "## Machine",
        "- $($env:COMPUTERNAME) / $($env:USERNAME)",
        "- $(Get-Date -Format ""yyyy-MM-dd HH:mm:ss"")"
    ) -join "`n"
    $headers = @{
        Authorization          = "Bearer $token"
        Accept                 = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $payload = @{
        title  = $title
        body   = $body
        labels = $spec.labels
    } | ConvertTo-Json
    try {
        $issue = Invoke-RestMethod -Method Post `
            -Uri "https://api.github.com/repos/$owner/$repo/issues" `
            -Headers $headers -Body $payload -TimeoutSec 30
        Write-Host "[GAP] Issue $($issue.html_url)" -ForegroundColor Green
        return @{ status = "filed"; issue_url = $issue.html_url; capability_id = $CapId }
    } catch {
        Write-Host "[GAP] GitHub direct echec: $($_.Exception.Message)" -ForegroundColor Red
        return @{ status = "issue_failed"; error = $_.Exception.Message }
    }
}

$openPr = -not $NoPr
$apiResult = Invoke-ViaAriaApi -CapId $CapabilityId -Ctx $Context -OpenPr $openPr
if ($apiResult) {
    Write-Host "[GAP] Via API ARIA OK - $CapabilityId" -ForegroundColor Green
    return $apiResult
}

$ghResult = Invoke-ViaGitHubDirect -CapId $CapabilityId -Ctx $Context
return $ghResult