# Debut de session : delta depuis l'autre PC + sync git + HANDOFF.md pour Grok
# Usage: .\session-handoff.ps1 [-SkipGitGate] [-TotpCode 123456]

param(
    [switch]$SkipGitGate,
    [string]$TotpCode
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "git-operator-session.ps1")
. (Resolve-Path (Join-Path $PSScriptRoot "..\..\scripts\aria-paths.ps1"))

$ariaRepo = $script:AriaRepoRoot
$collegue = $script:AriaCollegueRoot
$projets = Split-Path $ariaRepo -Parent
$sessionsRoot = Join-Path $collegue "sessions"
$machine = $env:COMPUTERNAME
$stateFile = Join-Path (Join-Path $sessionsRoot $machine) "handoff-state.json"
$handoffSsot = Join-Path $sessionsRoot "HANDOFF.md"
$sessionStart = Join-Path $collegue "SESSION-START.md"
Write-Host "=== session-handoff ($machine) ===" -ForegroundColor Cyan

$ensureScript = Join-Path $PSScriptRoot "ensure-pc-ready.ps1"
if (Test-Path $ensureScript) {
    $boot = & $ensureScript -SkipGitGate:$SkipGitGate -TotpCode $TotpCode
    if ($boot.is_new_pc) {
        Write-Host "[BOOT] Nouveau PC detecte - voir sessions\$machine\boot-status.json" -ForegroundColor Yellow
    }
}

$ipScript = Join-Path $PSScriptRoot "report-machine-ip.ps1"
if (Test-Path $ipScript) { & $ipScript | Out-Null }

Assert-GitOperatorSession -SkipGate:$SkipGitGate -TotpCode $TotpCode

if (Test-Path $ariaRepo) {
    $r = Invoke-GoldenFarGitPull -Path $ariaRepo -SkipGitGate:$SkipGitGate -TotpCode $TotpCode
    if ($r -and $r.updated) {
        Write-Host "[PULL] ARIA $($r.before) -> $($r.after)" -ForegroundColor Green
    }
}

if (-not (Test-Path $sessionsRoot)) {
    Write-Host "Pas encore de sessions/ - lance collect-session.ps1 sur chaque PC" -ForegroundColor Yellow
}

$others = @()
if (Test-Path $sessionsRoot) {
    Get-ChildItem $sessionsRoot -Directory | Where-Object { $_.Name -ne $machine } | ForEach-Object {
        $latest = Join-Path $_.FullName "latest.json"
        if (Test-Path $latest) {
            $others += Get-Content $latest -Raw -Encoding UTF8 | ConvertFrom-Json
        }
    }
}

$myLatest = Join-Path (Join-Path $sessionsRoot $machine) "latest.json"
$myManifest = $null
if (Test-Path $myLatest) {
    $myManifest = Get-Content $myLatest -Raw -Encoding UTF8 | ConvertFrom-Json
}

$state = @{ last_handoff_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") }
if (Test-Path $stateFile) {
    try { $state = Get-Content $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
}
$lastSeen = $state.last_seen_other_at
if (-not $lastSeen) { $lastSeen = "1970-01-01T00:00:00" }

$bootStatusPath = Join-Path (Join-Path $sessionsRoot $machine) "boot-status.json"
$bootHint = ""
if (Test-Path $bootStatusPath) {
    try {
        $bs = Get-Content $bootStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($bs.is_new_pc) {
            $bootHint = "**NOUVEAU PC** - Grok execute bootstrap ; Sylvain : rien sauf Bitwarden si secrets absents."
        }
        if ($bs.agent_next) {
            $bootHint += " Prochaine action agent : $($bs.agent_next)"
        }
    } catch { }
}

$lines = @(
    "# HANDOFF - $machine",
    "",
    "> Genere par session-handoff.ps1 - lire en debut de session Grok/Cursor.",
    "> Sylvain ne dit rien : le handoff est automatique (pas besoin de 'lis le github').",
    "",
    "Horodatage : $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
    ""
)
if ($bootHint) {
    $lines += $bootHint
    $lines += ""
}

$actions = [System.Collections.Generic.List[string]]::new()

foreach ($o in ($others | Sort-Object collected_at -Descending)) {
    if ($o.collected_at -le $lastSeen) { continue }
    $lines += "## Depuis $($o.machine) ($($o.collected_at))"
    $lines += ""
    if ($o.repos_in_session) {
        $lines += "**Repos touches** : $($o.repos_in_session -join ', ')"
        foreach ($repo in $o.repos_in_session) {
            $rp = Join-Path $projets $repo
            if (Test-Path $rp) {
                $pull = Invoke-GoldenFarGitPull -Path $rp -SkipGitGate:$SkipGitGate -TotpCode $TotpCode
                if ($pull -and $pull.updated) {
                    $actions.Add("git pull $repo")
                    $lines += "- [fait] pull $repo"
                }
            }
        }
    }
    if ($o.files_touched -and $o.files_touched.Count -gt 0) {
        $lines += ""
        $lines += "**Fichiers modifies** (extrait) :"
        $o.files_touched | Select-Object -First 25 | ForEach-Object { $lines += "- $_" }
        if ($o.files_touched.Count -gt 25) {
            $lines += "- ... (+$($o.files_touched.Count - 25) autres)"
        }
    }
    if ($o.journal_tail) {
        $lines += ""
        $lines += "**Journal** :"
        $o.journal_tail | ForEach-Object { $lines += "- $_" }
    }
    $lines += ""
    $state.last_seen_other_at = $o.collected_at
}

if ($others.Count -eq 0) {
    $lines += "Aucun manifeste d'un autre PC - lance collect-session.ps1 sur l'autre machine."
    $lines += ""
}

if ($myManifest) {
    $lines += "## Derniere session sur ce PC"
    $lines += "- Collecte : $($myManifest.collected_at)"
    $lines += "- Fichiers : $($myManifest.files_touched.Count)"
    $lines += ""
}

$lines += "## Actions recommandees"
if ($actions.Count -eq 0) {
    $lines += "- Rien de critique detecte - verifier check-aria-status si deploy recent"
} else {
    $actions | Select-Object -Unique | ForEach-Object { $lines += "- $_" }
}
if ($others | Where-Object { $_.repos_in_session -contains "aria-local-sync" }) {
    $lines += '- Si coffre change : apply-local.ps1 -TotpCode <6 chiffres> (chat IDE)'
}
$lines += "- Lire sessions/CONSOMMATION-GROK.md (mode concis Grok/Cursor)"
$lines += "- Lire COLLEGUE.md + JOURNAL.md"
$workerFile = Join-Path $sessionsRoot "ARIA-WORKER.md"
if (Test-Path $workerFile) {
    $workerRaw = Get-Content $workerFile -Raw -Encoding UTF8
    $pendingCount = ([regex]::Matches($workerRaw, '(?m)^##\s+\[pending\]')).Count
    if ($pendingCount -gt 0) {
        $lines += "- **OUVRIER** : $pendingCount tache(s) [pending] dans sessions/ARIA-WORKER.md - traiter en priorite"
    }
}

$auditScript = Join-Path $PSScriptRoot "audit-github-security.ps1"
$auditReport = $null
if (Test-Path $auditScript) {
    $auditReport = & $auditScript
    $lines += ""
    $lines += "## Audit GitHub ($machine)"
    $lines += "- $($auditReport.summary)"
    if ($auditReport.findings -and $auditReport.findings.Count -gt 0) {
        $auditReport.findings | Select-Object -First 8 | ForEach-Object {
            $lines += "- **$($_.severity)** $($_.repo): $($_.rule) - $($_.detail)"
        }
    }
}

$gitSess = Get-GitOperatorSession
if ($gitSess) {
    $lines += ""
    $lines += "**Session Git TOTP** : valide jusqu au $($gitSess.expires_at)"
}

$dir = Split-Path $stateFile -Parent
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$state | Add-Member -NotePropertyName last_handoff_at -NotePropertyValue (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") -Force
Set-Content $stateFile ($state | ConvertTo-Json) -Encoding UTF8
$bootNotes = ($lines -join "`n")
if (Test-Path $handoffSsot) {
    $ssot = Get-Content $handoffSsot -Raw -Encoding UTF8
    Set-Content $sessionStart ($bootNotes + "`n`n---`n`n" + $ssot) -Encoding UTF8
} else {
    Set-Content $sessionStart $bootNotes -Encoding UTF8
}

$checklistScript = Join-Path $PSScriptRoot "write-session-checklist.ps1"
if (Test-Path $checklistScript) {
    & $checklistScript
}

Write-Host ""
Write-Host "SSOT GitHub : $handoffSsot" -ForegroundColor Green
Write-Host "Lecture Grok : $sessionStart" -ForegroundColor Green
if (Test-Path $handoffSsot) { Get-Content $handoffSsot -Head 40 }
else { Get-Content $sessionStart }