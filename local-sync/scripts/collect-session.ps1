# Exporte un manifeste leger de la derniere session Grok (pas tout .grok/sessions)
# Usage: .\collect-session.ps1  puis commit collegue-memoire/sessions/

$ErrorActionPreference = "Stop"

. (Resolve-Path (Join-Path $PSScriptRoot "..\..\scripts\aria-paths.ps1"))

$machine = $env:COMPUTERNAME
$ariaRepo = $script:AriaRepoRoot
$collegue = $script:AriaCollegueRoot
$sessionsRoot = Join-Path $collegue "sessions"
$machineDir = Join-Path $sessionsRoot $machine
$grokSessions = Join-Path $env:USERPROFILE ".grok\sessions"

if (-not (Test-Path $collegue)) {
    throw "Clone monorepo ARIA : git clone https://github.com/GoldenFarFR/ARIA.git"
}

function Get-LatestGrokSessionDir {
    if (-not (Test-Path $grokSessions)) { return $null }
    $best = $null
    $bestTime = [datetime]::MinValue
    Get-ChildItem $grokSessions -Recurse -Filter "hunk_records.jsonl" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.LastWriteTime -gt $bestTime) {
            $bestTime = $_.LastWriteTime
            $best = $_.Directory.FullName
        }
    }
    return $best
}

function Get-RelAriaPath {
    param([string]$FullPath)
    $norm = $FullPath -replace '\\', '/'
    $root = ($ariaRepo -replace '\\', '/')
    if ($norm -like "$root/*") {
        return $norm.Substring($root.Length + 1)
    }
    return $FullPath
}

$sessionDir = Get-LatestGrokSessionDir
$sessionId = if ($sessionDir) { Split-Path $sessionDir -Leaf } else { "unknown" }
$filesTouched = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$reposTouched = @{}

if ($sessionDir) {
    $hunks = Join-Path $sessionDir "hunk_records.jsonl"
    if (Test-Path $hunks) {
        Get-Content $hunks -Encoding UTF8 | ForEach-Object {
            try {
                $o = $_ | ConvertFrom-Json
                if ($o.filePath) {
                    [void]$filesTouched.Add((Get-RelAriaPath $o.filePath))
                    if ($o.filePath -match [regex]::Escape($ariaRepo) -or $o.filePath -like "*GitHub-Repos\ARIA*") {
                        $reposTouched["ARIA"] = $true
                    }
                }
            } catch { }
        }
    }
}

$reposStatus = @()
$reposToScan = if ($reposTouched.Count -gt 0) { @($reposTouched.Keys) } else { @() }
foreach ($repoName in $reposToScan) {
    $rp = if ($repoName -eq "ARIA") { $ariaRepo } else { Join-Path (Split-Path $ariaRepo -Parent) $repoName }
    if (-not (Test-Path (Join-Path $rp ".git"))) { continue }
    Push-Location $rp
    try {
        $reposStatus += [ordered]@{
            repo    = $repoName
            commit  = (git rev-parse --short HEAD 2>$null).Trim()
            message = (git log -1 --format=%s 2>$null).Trim()
            dirty   = [bool](git status --porcelain 2>$null)
        }
    } finally { Pop-Location }
}

$journalTail = @()
$journalPath = Join-Path $collegue "JOURNAL.md"
if (Test-Path $journalPath) {
    $journalTail = @(Get-Content $journalPath -Encoding UTF8 -Tail 15 | ForEach-Object { [string]$_ })
}

$filesList = @($filesTouched | Sort-Object | Select-Object -First 120)

$ipScript = Join-Path $PSScriptRoot "report-machine-ip.ps1"
$publicIp = $null
if (Test-Path $ipScript) {
    $ipRec = & $ipScript
    if ($ipRec) { $publicIp = $ipRec.public_ip }
}

$now = Get-Date
$stamp = $now.ToString("yyyy-MM-ddTHHmmss")
$manifest = [ordered]@{
    machine     = $machine
    user        = $env:USERNAME
    public_ip   = $publicIp
    session_id  = $sessionId
    collected_at = $now.ToString("yyyy-MM-ddTHH:mm:ss")
    files_touched = $filesList
    files_touched_total = $filesTouched.Count
    repos_in_session = @($reposTouched.Keys | Sort-Object)
    repos_status = $reposStatus
    journal_tail = $journalTail
}

if (-not (Test-Path $machineDir)) { New-Item -ItemType Directory -Path $machineDir -Force | Out-Null }

$outFile = Join-Path $machineDir "$stamp.json"
$latestFile = Join-Path $machineDir "latest.json"
$json = $manifest | ConvertTo-Json -Depth 6
Set-Content -Path $outFile -Value $json -Encoding UTF8
Set-Content -Path $latestFile -Value $json -Encoding UTF8

function Update-HandoffMarkdown {
    param($Manifest)
    $handoffPath = Join-Path $sessionsRoot "HANDOFF.md"
    $header = @(
        "# Session handoff - SSOT GitHub",
        "",
        "> Mis a jour par collect-session.ps1. Grok Build lit ce fichier au demarrage.",
        "",
        "Derniere regeneration : $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
        ""
    )
    $section = @(
        "## $($Manifest.machine)",
        "",
        "- **Derniere session** : $($Manifest.collected_at)",
        "- **Session Grok** : ``$($Manifest.session_id)``",
        "- **Repos** : $($Manifest.repos_in_session -join ', ')",
        "- **Fichiers modifies** : $($Manifest.files_touched_total) (extrait ci-dessous)",
        ""
    )
    if ($Manifest.repos_status) {
        $section += "**Etat git** :"
        foreach ($r in $Manifest.repos_status) {
            $d = if ($r.dirty) { "dirty" } else { "clean" }
            $section += "- ``$($r.repo)`` @ $($r.commit) ($d) - $($r.message)"
        }
        $section += ""
    }
    if ($Manifest.files_touched -and $Manifest.files_touched.Count -gt 0) {
        $section += "**Fichiers (extrait)** :"
        $Manifest.files_touched | Select-Object -First 20 | ForEach-Object { $section += "- $_" }
        if ($Manifest.files_touched_total -gt 20) {
            $section += "- ... (+$($Manifest.files_touched_total - 20) autres)"
        }
        $section += ""
    }
    if ($Manifest.journal_tail) {
        $section += "**Journal** :"
        $Manifest.journal_tail | ForEach-Object { $section += "- $_" }
        $section += ""
    }

    $existing = @()
    if (Test-Path $handoffPath) {
        $existing = Get-Content $handoffPath -Encoding UTF8
    }
    $marker = "## $($Manifest.machine)"
    $newBody = New-Object System.Collections.Generic.List[string]
    $skip = $false
    foreach ($line in $existing) {
        if ($line -eq $marker) { $skip = $true; continue }
        if ($skip -and $line -match '^## ') { $skip = $false }
        if (-not $skip) { [void]$newBody.Add($line) }
    }
    while ($newBody.Count -gt 0 -and [string]::IsNullOrWhiteSpace($newBody[$newBody.Count - 1])) {
        $newBody.RemoveAt($newBody.Count - 1)
    }
    if ($newBody.Count -eq 0) {
        foreach ($h in $header) { [void]$newBody.Add($h) }
    }
    [void]$newBody.Add("")
    foreach ($s in $section) { [void]$newBody.Add($s) }
    Set-Content -Path $handoffPath -Value ($newBody -join "`n") -Encoding UTF8
}

Update-HandoffMarkdown -Manifest $manifest

Write-Host "=== collect-session ===" -ForegroundColor Cyan
Write-Host "Machine   : $machine"
Write-Host "Session   : $sessionId"
Write-Host "Fichiers  : $($filesTouched.Count)"
Write-Host "Manifeste : sessions\$machine\$stamp.json"
Write-Host ""
$touchesAriaCore = @($filesList | Where-Object { $_ -match '^aria-sandbox/packages/aria-core/' })
if ($touchesAriaCore.Count -gt 0) {
    $gapScript = Join-Path $PSScriptRoot "file-self-improve-gap.ps1"
    if (Test-Path $gapScript) {
        $ctx = "session=$sessionId machine=$machine`nfiles=$($touchesAriaCore.Count)`n" + (($touchesAriaCore | Select-Object -First 8) -join "`n")
        & $gapScript -CapabilityId "post_session_aria_core_bump" -Context $ctx | Out-Null
        Write-Host "[GAP] post_session_aria_core_bump declenche ($($touchesAriaCore.Count) fichiers aria-core)" -ForegroundColor DarkCyan
    }
}

$syncLetta = Join-Path $ariaRepo "letta-orchestrator\sync-core-to-letta.ps1"
if (Test-Path $syncLetta) {
    try {
        & $syncLetta -Quiet
        Write-Host "[Letta] sync aria-core -> archival (fin session)" -ForegroundColor DarkCyan
    } catch {
        Write-Host "[Letta] sync ignore : $($_.Exception.Message)" -ForegroundColor DarkGray
    }
}

Write-Host "Etape suivante (TOTP 12h si session expiree) :" -ForegroundColor Yellow
Write-Host "  .\push-session-manifest.ps1 [-TotpCode <code>]"
Write-Host "  # ou manuel: cd $ariaRepo ; git add collegue-memoire/sessions/ ; git commit ; git push"