# Alerte Telegram si audit GitHub contient du CRITICAL (anti-spam 6h meme fingerprint)
# Usage: .\send-audit-alert.ps1 [-ReportPath ...]

param(
    [string]$ReportPath = (Join-Path $env:LOCALAPPDATA "GoldenFar\github-audit-latest.json")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ReportPath)) {
    Write-Host "[AUDIT-ALERT] Pas de rapport - skip" -ForegroundColor DarkGray
    return $false
}

$trustFile = Join-Path (Split-Path $PSScriptRoot -Parent) "security\github-trust.yaml"
$alertRules = @(
    "ip_changed_vault", "unknown_machine_vault", "github_foreign_actor", "vault_untrusted_origin"
)
if (Test-Path $trustFile) {
    $inRules = $false
    Get-Content $trustFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match 'critical_alert_rules:') { $script:inRules = $true; return }
        if ($inRules -and $_ -match '^\s+-\s+(\S+)') { $alertRules += $Matches[1] }
        if ($inRules -and $_ -match '^\w') { $script:inRules = $false }
    }
    $alertRules = @($alertRules | Select-Object -Unique)
}

$report = Get-Content $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
$criticals = @(
    $report.findings | Where-Object {
        $_.severity -eq "critical" -and $_.rule -in $alertRules
    }
)
if ($criticals.Count -eq 0) {
    Write-Host "[AUDIT-ALERT] Aucun critical origine/IP - pas d'alerte Telegram" -ForegroundColor DarkGray
    return $false
}

$fingerprint = ($criticals | ForEach-Object { "$($_.repo)|$($_.rule)|$($_.commit)" } | Sort-Object) -join ";"
$statePath = Join-Path $env:LOCALAPPDATA "GoldenFar\github-audit-alert-state.json"
$now = Get-Date
if (Test-Path $statePath) {
    try {
        $state = Get-Content $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($state.fingerprint -eq $fingerprint) {
            $last = [datetime]::Parse($state.sent_at)
            if (($now - $last).TotalHours -lt 6) {
                Write-Host "[AUDIT-ALERT] Deja notifie (meme critical, < 6h) - skip" -ForegroundColor DarkGray
                return $false
            }
        }
    } catch { }
}

$lines = @(
    "ALERTE SECURITE GitHub (origine / IP)",
    "",
    "Machine: $($report.machine)",
    "IP locale: $($report.public_ip)",
    "Scan: $($report.scanned_at)",
    "Critical: $($criticals.Count)",
    ""
)
$criticals | Select-Object -First 6 | ForEach-Object {
    $lines += "- $($_.repo) / $($_.rule)"
    $lines += "  $($_.detail)"
    if ($_.commit) { $lines += "  commit: $($_.commit)" }
}
if ($criticals.Count -gt 6) {
    $lines += "... +$($criticals.Count - 6) autre(s)"
}
$lines += ""
$lines += "Action: verifier git log + collegue-memoire/JOURNAL.md"

$notify = Join-Path $PSScriptRoot "notify-aria-telegram.ps1"
$ok = & $notify -Text ($lines -join "`n") -Source "github-audit"

if ($ok) {
    @{
        fingerprint = $fingerprint
        sent_at     = $now.ToString("yyyy-MM-ddTHH:mm:ss")
        count       = $criticals.Count
    } | ConvertTo-Json | Set-Content -Path $statePath -Encoding UTF8
}

$gapScript = Join-Path $PSScriptRoot "file-self-improve-gap.ps1"
if (Test-Path $gapScript) {
    $ruleMap = @{
        ip_changed_vault       = "security_ip_changed_vault"
        unknown_machine_vault  = "security_unknown_machine_vault"
        github_foreign_actor   = "security_github_foreign_actor"
        vault_untrusted_origin = "security_vault_untrusted_origin"
    }
    $filedRules = @{}
    foreach ($c in $criticals) {
        $capId = $ruleMap[$c.rule]
        if (-not $capId -or $filedRules.ContainsKey($capId)) { continue }
        $filedRules[$capId] = $true
        $ctx = "machine=$($report.machine) ip=$($report.public_ip)`n$($c.detail)"
        & $gapScript -CapabilityId $capId -Context $ctx -NoPr | Out-Null
    }
}

return $ok