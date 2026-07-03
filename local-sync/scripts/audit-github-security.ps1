# Audit Git local — detecte activite suspecte sur repos GoldenFar
# Usage: .\audit-github-security.ps1 [-DaysBack 14]

param([int]$DaysBack = 14)

$ErrorActionPreference = "Stop"

$projets = Join-Path $env:USERPROFILE "projets"
$trustFile = Join-Path (Split-Path -Parent $PSScriptRoot) "security\github-trust.yaml"
. (Resolve-Path (Join-Path $PSScriptRoot "..\..\scripts\aria-paths.ps1"))

$script:MonorepoSensitiveMap = @{
    "aria-local-sync"  = "local-sync"
    "collegue-memoire" = "collegue-memoire"
    "aria-vanguard"    = "vanguard"
    "aria-skills"      = "skills"
}
$outJson = Join-Path $env:LOCALAPPDATA "GoldenFar\github-audit-latest.json"
$machine = $env:COMPUTERNAME

function Read-TrustConfig {
    $cfg = @{
        trusted_author_substrings = @("GoldenFar", "Sylvain")
        vault_rotation_hours_utc  = @(2, 3, 4, 5)
        sensitive_repos           = @("aria-local-sync", "collegue-memoire")
        vault_commit_patterns     = @("rotation quotidienne", "sync:")
        secret_patterns           = @("rnd_", "ghp_", ".vault-master-secret")
    }
    if (-not (Test-Path $trustFile)) { return $cfg }
    $lines = Get-Content $trustFile -Encoding UTF8
    $section = $null
    $list = [System.Collections.Generic.List[string]]::new()
    foreach ($line in $lines) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^(\w[\w_]*):\s*$') {
            if ($section -and $list.Count -gt 0) { $cfg[$section] = @($list) }
            $section = $Matches[1]
            $list = [System.Collections.Generic.List[string]]::new()
            continue
        }
        if ($line -match '^\s+-\s+"(.+)"\s*$' -and $section) {
            [void]$list.Add($Matches[1])
        }
        elseif ($line -match '^\s+-\s+([^"#\s][^\s#]*)\s*$' -and $section -and $section -ne "vault_rotation_hours_utc") {
            [void]$list.Add($Matches[1].Trim())
        }
        if ($line -match '^\s+-\s+(\d+)\s*$' -and $section -eq "vault_rotation_hours_utc") {
            [void]$list.Add([int]$Matches[1])
        }
    }
    if ($section -and $list.Count -gt 0) { $cfg[$section] = @($list) }
    return $cfg
}

function Add-Finding {
    param(
        [System.Collections.Generic.List[object]]$List,
        [string]$Severity,
        [string]$Repo,
        [string]$Rule,
        [string]$Detail,
        [string]$Commit = ""
    )
    [void]$List.Add([ordered]@{
        severity = $Severity
        repo     = $Repo
        rule     = $Rule
        detail   = $Detail
        commit   = $Commit
    })
}

function Test-AuthorTrusted {
    param($Author, $Cfg)
    if (-not $Author) { return $false }
    foreach ($sub in $Cfg.trusted_author_substrings) {
        if ($Author -like "*$sub*") { return $true }
    }
    return $false
}

function Test-VaultCommitLegit {
    param($Message, $Cfg)
    foreach ($p in $Cfg.vault_commit_patterns) {
        if ($Message -like "*$p*") { return $true }
    }
    return $false
}

$cfg = Read-TrustConfig
if (-not $cfg.known_machines) { $cfg.known_machines = @($machine) }
if (-not $cfg.trusted_github_logins) { $cfg.trusted_github_logins = @("GoldenFarFR") }
if (-not $cfg.critical_alert_rules) {
    $cfg.critical_alert_rules = @("ip_changed_vault", "unknown_machine_vault", "github_foreign_actor", "vault_untrusted_origin")
}

. (Join-Path $PSScriptRoot "_machine-origin.ps1")
$registry = Get-MachineRegistry
$ipRecords = Get-AllMachineIpRecords

$findings = [System.Collections.Generic.List[object]]::new()
$since = (Get-Date).AddDays(-$DaysBack).ToString("yyyy-MM-dd")
$scanned = @()

# --- Origine multi-PC / IP ---
$sessionsRoot = Get-SessionsRoot
if (Test-Path $sessionsRoot) {
    Get-ChildItem $sessionsRoot -Directory | ForEach-Object {
        $mName = $_.Name
        $latest = Join-Path $_.FullName "latest.json"
        $ipFile = Join-Path $_.FullName "ip-latest.json"
        if (-not (Test-Path $latest)) { return }
        try {
            $manifest = Get-Content $latest -Raw -Encoding UTF8 | ConvertFrom-Json
            $touchesVault = $false
            if ($manifest.repos_in_session -contains "aria-local-sync") { $touchesVault = $true }
            if ($manifest.files_touched) {
                $touchesVault = $touchesVault -or [bool]($manifest.files_touched | Where-Object {
                    $_ -match 'goldenfar-vault|sync/vault|aria-local-sync'
                })
            }
            if (-not $touchesVault) { return }

            $knownMachine = $cfg.known_machines -contains $mName
            if (-not $knownMachine) {
                Add-Finding $findings "critical" "sessions" "unknown_machine_vault" `
                    "Machine inconnue $mName a touche vault/sync" ""
            }

            if (Test-Path $ipFile) {
                $ipRec = Get-Content $ipFile -Raw -Encoding UTF8 | ConvertFrom-Json
                $ip = $ipRec.public_ip
                if ($ip -and -not (Test-IpKnownForMachine $mName $ip $registry)) {
                    Add-Finding $findings "critical" "sessions" "vault_untrusted_origin" `
                        "IP $ip non enregistree pour $mName (vault/sync)" ""
                }
                if ($ipRec.ip_changed -and $knownMachine) {
                    Add-Finding $findings "critical" "sessions" "ip_changed_vault" `
                        "IP changee $mName : $($ipRec.previous_ip) -> $ip (activite vault)" ""
                }
            }
        } catch { }
    }
}

function Get-GithubToken {
    $prod = Join-Path $env:LOCALAPPDATA "GoldenFar\vault\production.env"
    if (-not (Test-Path $prod)) { return $null }
    foreach ($line in Get-Content $prod -Encoding UTF8) {
        if ($line -match '^\s*GITHUB_TOKEN\s*=\s*(.+)\s*$') {
            return $Matches[1].Trim().Trim('"')
        }
    }
    return $null
}

$ghToken = Get-GithubToken
if ($ghToken) {
    $headers = @{
        Authorization = "Bearer $ghToken"
        Accept        = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    foreach ($repoName in @("ARIA")) {
        try {
            $events = Invoke-RestMethod -Headers $headers `
                -Uri "https://api.github.com/repos/GoldenFarFR/$repoName/events?per_page=15" -TimeoutSec 15
            foreach ($ev in $events) {
                if ($ev.type -ne "PushEvent") { continue }
                $actor = $ev.actor.login
                $trusted = $false
                foreach ($login in $cfg.trusted_github_logins) {
                    if ($actor -eq $login) { $trusted = $true; break }
                }
                if (-not $trusted) {
                    Add-Finding $findings "critical" $repoName "github_foreign_actor" `
                        "Push GitHub par @$actor (non autorise)" ""
                    break
                }
            }
        } catch { }
    }
}

function Invoke-SensitiveRepoScan {
    param(
        [string]$RepoPath,
        [string]$RepoName,
        [string]$PathPrefix,
        [object]$Cfg,
        [string]$Since,
        [System.Collections.Generic.List[object]]$Findings
    )
    if (-not (Test-Path (Join-Path $RepoPath ".git"))) { return $false }
    Push-Location $RepoPath
    try {
        $commits = git log --since=$Since --format="%H|%an|%ae|%ci|%s" 2>$null
        if (-not $commits) { return $true }

        $gfvPerDay = @{}
        foreach ($line in $commits) {
            if (-not $line) { continue }
            $p = $line -split '\|', 5
            if ($p.Count -lt 5) { continue }
            $hash = $p[0]; $an = $p[1]; $ae = $p[2]; $ci = $p[3]; $subj = $p[4]
            $author = "$an <$ae>"

            $files = @(git diff-tree --no-commit-id --name-only -r $hash 2>$null)
            if ($PathPrefix) {
                $prefix = $PathPrefix.TrimEnd('/') + '/'
                $files = @($files | Where-Object { $_ -like "$prefix*" })
                if ($files.Count -eq 0) { continue }
            }

            $authorTrusted = (Test-AuthorTrusted $an $Cfg) -or (Test-AuthorTrusted $ae $Cfg)
            if (-not $authorTrusted) {
                Add-Finding $Findings "high" $RepoName "unknown_author" "Auteur non reconnu: $author" $hash.Substring(0, 7)
            }

            $touchesVault = $files | Where-Object { $_ -match 'goldenfar-vault\.gfv|sync/vault/' }
            if ($touchesVault) {
                $dt = [datetime]::Parse($ci)
                $hourUtc = $dt.ToUniversalTime().Hour
                $dayKey = $dt.ToUniversalTime().ToString("yyyy-MM-dd")
                if (-not $gfvPerDay.ContainsKey($dayKey)) { $gfvPerDay[$dayKey] = 0 }
                $gfvPerDay[$dayKey]++

                if ($gfvPerDay[$dayKey] -gt 2) {
                    Add-Finding $Findings "medium" $RepoName "vault_multi_push" `
                        "Plus de 2 commits vault le $dayKey" $hash.Substring(0, 7)
                }
                if ($Cfg.vault_rotation_hours_utc -notcontains $hourUtc -and -not (Test-VaultCommitLegit $subj $Cfg)) {
                    if (-not (Test-AuthorTrusted $an $Cfg)) {
                        Add-Finding $Findings "high" $RepoName "vault_off_hours" `
                            "Vault modifie hors fenetre UTC ($hourUtc h): $subj" $hash.Substring(0, 7)
                    }
                }
            }

            $added = git show $hash --pretty=format: -U0 2>$null | Where-Object {
                $_ -match '^\+' -and $_ -notmatch '^\+\+\+'
            }
            foreach ($diffLine in $added) {
                if ($diffLine -match '\.md$|Bitwarden|Test-Path|chemin|fichier|SETUP-AUTRE|SECURITE-CLES|gitignore|\.gitignore') { continue }
                foreach ($pat in $Cfg.secret_patterns) {
                    if ($diffLine -notlike "*$pat*") { continue }
                    if ($pat -eq "rnd_" -and $diffLine -notmatch 'rnd_[A-Za-z0-9]{8,}') { continue }
                    if ($pat -like '.vault-*') { continue }
                    if ($pat -eq "ADMIN_API_SECRET=" -and $diffLine -match '<|placeholder|exemple|your_|xxx') { continue }
                    $sev = if ($authorTrusted) { "high" } else { "critical" }
                    $rule = if ($authorTrusted) { "secret_in_history" } else { "vault_untrusted_origin" }
                    Add-Finding $Findings $sev $RepoName $rule `
                        "Ligne suspecte ($pat) auteur=$an" $hash.Substring(0, 7)
                    break
                }
            }
        }

        $reflog = git reflog --since=$Since 2>$null
        if ($reflog -match 'reset|rebase.*onto|push.*forced') {
            Add-Finding $Findings "high" $RepoName "force_history" "Reflog: reset/rebase/force detecte" ""
        }
        return $true
    } finally { Pop-Location }
}

$scannedSet = [System.Collections.Generic.HashSet[string]]::new()
foreach ($repoName in $cfg.sensitive_repos) {
    $legacyPath = Join-Path $projets $repoName
    if (Invoke-SensitiveRepoScan -RepoPath $legacyPath -RepoName $repoName -PathPrefix "" -Cfg $cfg -Since $since -Findings $findings) {
        [void]$scannedSet.Add($repoName)
    }
}

$ariaRoot = $script:AriaRepoRoot
if (Test-Path (Join-Path $ariaRoot ".git")) {
    foreach ($repoName in $cfg.sensitive_repos) {
        if ($scannedSet.Contains($repoName)) { continue }
        $prefix = $script:MonorepoSensitiveMap[$repoName]
        if (-not $prefix) { continue }
        if (Invoke-SensitiveRepoScan -RepoPath $ariaRoot -RepoName $repoName -PathPrefix $prefix -Cfg $cfg -Since $since -Findings $findings) {
            [void]$scannedSet.Add($repoName)
        }
    }
}
$scanned = @($scannedSet)

$severityRank = @{ critical = 4; high = 3; medium = 2; low = 1; ok = 0 }
$maxSev = "ok"
foreach ($f in $findings) {
    if ($severityRank[$f.severity] -gt $severityRank[$maxSev]) { $maxSev = $f.severity }
}

$currentIp = $null
$ipRec = $ipRecords | Where-Object { $_.machine -eq $machine } | Select-Object -First 1
if ($ipRec) { $currentIp = $ipRec.public_ip }

$report = [ordered]@{
    machine     = $machine
    public_ip   = $currentIp
    scanned_at  = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    days_back   = $DaysBack
    repos       = $scanned
    status      = if ($findings.Count -eq 0) { "clean" } else { $maxSev }
    findings    = @($findings)
    summary     = if ($findings.Count -eq 0) {
        "Aucune anomalie detectee sur $($scanned.Count) repo(s)."
    } else {
        "$($findings.Count) alerte(s) - severite max: $maxSev"
    }
}

$dir = Split-Path $outJson -Parent
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
Set-Content -Path $outJson -Value ($report | ConvertTo-Json -Depth 5) -Encoding UTF8

Write-Host ""
Write-Host "=== audit-github-security ($machine) ===" -ForegroundColor Cyan
Write-Host $report.summary
foreach ($f in $findings) {
    $color = switch ($f.severity) {
        "critical" { "Red" }
        "high" { "Red" }
        "medium" { "Yellow" }
        default { "DarkYellow" }
    }
    Write-Host "[$($f.severity.ToUpper())] $($f.repo): $($f.rule) - $($f.detail)" -ForegroundColor $color
}
Write-Host ""
Write-Host "Rapport: $outJson" -ForegroundColor DarkGray

$alertScript = Join-Path $PSScriptRoot "send-audit-alert.ps1"
if ((Test-Path $alertScript) -and $maxSev -eq "critical") {
    & $alertScript -ReportPath $outJson
}

return $report