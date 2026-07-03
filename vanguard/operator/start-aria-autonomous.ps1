# Demarrage mode autonome ARIA — revenu ACP, scan marche, promo X, initiatives LLM
# Usage : cd %ARIA_REPO_ROOT%\vanguard\operator ; .\start-aria-autonomous.ps1

param(
    [switch]$SkipBot,
    [switch]$SkipListener,
    [switch]$NoSync
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-common.ps1")
. (Join-Path $Root "_render-common.ps1")

$autonomyKeys = @{
    "ARIA_AUTONOMOUS"              = "true"
    "ARIA_REVENUE_AUTONOMY"        = "true"
    "ARIA_PROACTIVE_IDEAS"         = "true"
    "ARIA_ACP_PROVIDER_ENABLED"    = "true"
    "ARIA_AUTONOMY_CYCLE_MINUTES"  = "360"
    "ARIA_AUTONOMY_PROMO_HOURS"    = "72"
    "ARIA_AUTONOMY_INITIATIVE_HOURS" = "8"
}

function Ensure-LocalEnvAutonomy {
    $localPath = Get-LocalEnvPath -ScriptsRoot $Root
    if (-not (Test-Path $localPath)) {
        $example = Join-Path $Root "local.env.example"
        if (Test-Path $example) {
            Copy-Item $example $localPath
            Write-Host "local.env cree depuis example" -ForegroundColor Yellow
        } else {
            New-Item -ItemType File -Path $localPath -Force | Out-Null
        }
    }
    $envMap = Read-EnvFile -Path $localPath
    $changed = $false
    foreach ($key in $autonomyKeys.Keys) {
        if (-not $envMap[$key] -or $envMap[$key].Trim().ToLower() -in @("0", "false", "no", "off")) {
            $envMap[$key] = $autonomyKeys[$key]
            $changed = $true
        }
    }
    if (-not $envMap["ARIA_ACP_EVENTS_FILE"]) {
        $envMap["ARIA_ACP_EVENTS_FILE"] = Join-Path $env:LOCALAPPDATA "GoldenFar\acp-events.jsonl"
        $changed = $true
    }
    if ($changed) {
        $lines = @("# GoldenFar local.env — autonomie $(Get-Date -Format 'yyyy-MM-dd HH:mm')", "")
        foreach ($key in ($envMap.Keys | Sort-Object)) {
            $lines += "$key=$($envMap[$key])"
        }
        Set-Content -Path $localPath -Value $lines -Encoding UTF8
        Write-Host "local.env — flags autonomie actives" -ForegroundColor Green
    }
}

Write-Host "=== ARIA mode autonome ===" -ForegroundColor Cyan
Ensure-LocalEnvAutonomy

if (-not $NoSync) {
    & (Join-Path $Root "sync-local.ps1")
}

$startAcp = Join-Path $Root "start-acp-local.ps1"
$args = @()
if ($SkipBot) { $args += "-SkipBot" }
if ($SkipListener) { $args += "-SkipListener" }
& $startAcp @args

Write-Host ""
Write-Host "Boucle autonome (heartbeat bot :8000) :" -ForegroundColor Cyan
Write-Host "  • poll jobs ACP — 5 min"
Write-Host "  • cycle revenu complet — 6 h (scan, promo, initiative)"
Write-Host "  • scan marche ACP — 24 h"
Write-Host ""
Write-Host "Console : aria `"mode autonome`" | aria `"scan marche acp`"" -ForegroundColor Green
Write-Host "Journal : $env:LOCALAPPDATA\GoldenFar\aria-autonomy.jsonl" -ForegroundColor DarkGray