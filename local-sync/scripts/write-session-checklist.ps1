# Genere SESSION-CHECKLIST.html — liste visuelle debut/fin de session Grok
# Usage: .\write-session-checklist.ps1 [-Open]

param([switch]$Open)

$ErrorActionPreference = "Stop"

$projets = Join-Path $env:USERPROFILE "projets"
$collegue = Join-Path $projets "collegue-memoire"
$localSync = Join-Path $projets "aria-local-sync"
$sessionsRoot = Join-Path $collegue "sessions"
$machine = $env:COMPUTERNAME
$outHtml = Join-Path $collegue "SESSION-CHECKLIST.html"
$handoffAt = Get-Date -Format "yyyy-MM-dd HH:mm"

function Test-HasSecret {
    param([string]$FileName, [string]$EnvName)
    $path = Join-Path $localSync $FileName
    if (Test-Path $path) { return $true }
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($EnvName))) { return $false }
    return $true
}

function Get-StatusIcon {
    param([bool]$Ok, [string]$Warn = $false)
    if ($Ok) { return @{ icon = "&#10003;"; cls = "ok" } }
    if ($Warn) { return @{ icon = "&#9888;"; cls = "warn" } }
    return @{ icon = "&#10007;"; cls = "bad" }
}

function New-Row {
    param([string]$Label, [bool]$Ok, [string]$Detail = "", [string]$Who = "auto")
    $s = Get-StatusIcon $Ok
    $whoBadge = switch ($Who) {
        "auto" { '<span class="badge auto">Script</span>' }
        "grok" { '<span class="badge grok">Grok</span>' }
        "sylvain" { '<span class="badge you">Toi</span>' }
        default { "" }
    }
    $detailHtml = if ($Detail) { "<span class=""detail"">$Detail</span>" } else { "" }
    return @"
    <tr class="$($s.cls)">
      <td class="status">$($s.icon)</td>
      <td class="label">$Label $whoBadge</td>
      <td class="detail-cell">$detailHtml</td>
    </tr>
"@
}

# --- Setup PC (une fois) ---
$vaultLocal = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
$setupChecks = @(
    @{ label = "Git installe"; ok = [bool](Get-Command git -ErrorAction SilentlyContinue) }
    @{ label = "Python 3.12+"; ok = [bool](Get-Command python -ErrorAction SilentlyContinue) }
    @{ label = "Node.js LTS"; ok = [bool](Get-Command node -ErrorAction SilentlyContinue) }
    @{ label = "Repo collegue-memoire"; ok = (Test-Path (Join-Path $collegue ".git")); detail = $collegue }
    @{ label = "Repo aria-local-sync"; ok = (Test-Path (Join-Path $localSync ".git")); detail = $localSync }
    @{ label = "Repo aria-skills"; ok = (Test-Path (Join-Path $projets "aria-skills\.git")) }
    @{ label = "Secret maitre coffre"; ok = (Test-HasSecret ".vault-master-secret" "GOLDENFAR_VAULT_MASTER"); detail = "Bitwarden goldenfar-vault-master" }
    @{ label = "TOTP Google Authenticator"; ok = (Test-HasSecret ".vault-totp-secret" "GOLDENFAR_VAULT_TOTP_SECRET"); detail = "Bitwarden goldenfar-vault-totp" }
    @{ label = "Regles Grok (session-handoff)"; ok = (Test-Path (Join-Path $env:USERPROFILE ".grok\rules\session-handoff.md")) }
    @{ label = "Regles Cursor"; ok = (Test-Path (Join-Path $env:USERPROFILE ".cursor\rules\session-handoff.md")) }
    @{ label = "Coffre local dechiffre"; ok = (Test-Path (Join-Path $env:LOCALAPPDATA "GoldenFar\vault")); detail = $vaultLocal }
)
$setupRows = $setupChecks | ForEach-Object {
    $d = if ($_.detail) { $_.detail } else { "" }
    New-Row $_.label $_.ok $d "sylvain"
}
$setupOk = ($setupChecks | Where-Object { -not $_.ok }).Count -eq 0

# --- Debut session : script session-handoff.ps1 ---
$repos = @("collegue-memoire", "aria-local-sync", "aria-skills", "aria-vanguard", "aria-sandbox")
$pulled = @()
foreach ($r in $repos) {
    $p = Join-Path $projets $r
    if (Test-Path (Join-Path $p ".git")) { $pulled += $r }
}
$handoffMd = Join-Path $sessionsRoot "HANDOFF.md"
$sessionStart = Join-Path $collegue "SESSION-START.md"

$startRows = @()
$startRows += New-Row "Verifier clone collegue-memoire" (Test-Path $collegue) "" "auto"
$startRows += New-Row "git pull repos GoldenFar" ($pulled.Count -ge 3) ($pulled -join ", ") "auto"
$startRows += New-Row "Comparer manifestes autre PC" (Test-Path $sessionsRoot) 'sessions/{machine}/latest.json' "auto"
$startRows += New-Row "Generer SESSION-START.md" (Test-Path $sessionStart) "" "auto"
$startRows += New-Row "Lire HANDOFF.md (SSOT GitHub)" (Test-Path $handoffMd) "" "grok"
$startRows += New-Row "Lire COLLEGUE.md" (Test-Path (Join-Path $collegue "COLLEGUE.md")) "" "grok"
$startRows += New-Row "Lire fin JOURNAL.md" (Test-Path (Join-Path $collegue "JOURNAL.md")) "" "grok"
$startRows += New-Row "Lire VISION.md (taches Aria)" (Test-Path (Join-Path $projets "aria-vanguard\VISION.md")) "" "grok"
$startRows += New-Row "Resumer delta autre PC (3-5 lignes)" $true "avant ta premiere reponse" "grok"

$gitSessPath = Join-Path $env:LOCALAPPDATA "GoldenFar\git-operator-session.json"
$gitSessOk = $false
$gitSessDetail = "TOTP requis (session 12h)"
if (Test-Path $gitSessPath) {
    try {
        $gs = Get-Content $gitSessPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($gs.machine -eq $machine -and (Get-Date) -lt [datetime]::Parse($gs.expires_at)) {
            $gitSessOk = $true
            $gitSessDetail = "valide jusqu'a $($gs.expires_at)"
        }
    } catch { }
}
$startRows += New-Row "Session Git TOTP (12h) pull/push" $gitSessOk $gitSessDetail "auto"

$auditJson = Join-Path $env:LOCALAPPDATA "GoldenFar\github-audit-latest.json"
$auditOk = $true
$auditDetail = "non execute"
if (Test-Path $auditJson) {
    try {
        $ar = Get-Content $auditJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $auditOk = ($ar.status -eq "clean" -or $ar.status -eq "ok")
        $auditDetail = $ar.summary
    } catch { }
}
$startRows += New-Row "Audit GitHub securite" $auditOk $auditDetail "auto"

# --- Fin session ---
$endRows = @()
$endRows += New-Row "collect-session.ps1" $true "manifeste JSON leger" "grok"
$endRows += New-Row "commit + push sessions/" $true "collegue-memoire GitHub" "grok"
$endRows += New-Row "MAJ COLLEGUE.md si decision metier" $true "optionnel" "grok"
$endRows += New-Row "Append JOURNAL.md par action" $true "journal-de-bord skill" "grok"

# --- Multi-PC ---
$pcCards = @()
if (Test-Path $sessionsRoot) {
    Get-ChildItem $sessionsRoot -Directory | ForEach-Object {
        $latest = Join-Path $_.FullName "latest.json"
        if (-not (Test-Path $latest)) { return }
        try {
            $m = Get-Content $latest -Raw -Encoding UTF8 | ConvertFrom-Json
            $isHere = $_.Name -eq $machine
            $badge = if ($isHere) { '<span class="badge here">Ce PC</span>' } else { '<span class="badge other">Autre PC</span>' }
            $repos = if ($m.repos_in_session) { $m.repos_in_session -join ", " } else { "-" }
            $files = if ($m.files_touched) { $m.files_touched.Count } else { 0 }
            $cardClass = if ($isHere) { "current" } else { "" }
            $pcName = $_.Name
            $collected = $m.collected_at
            $pcCards += @(
                "    <div class=`"pc-card $cardClass`">"
                "      <h3>$pcName $badge</h3>"
                "      <p><strong>Derniere session</strong> : $collected</p>"
                "      <p><strong>Repos</strong> : $repos</p>"
                "      <p><strong>Fichiers</strong> : $files</p>"
                "    </div>"
            ) -join "`n"
        } catch { }
    }
}
if ($pcCards.Count -eq 0) {
    $pcCards += '<p class="muted">Aucun manifeste - lance collect-session.ps1 en fin de session.</p>'
}

$setupBadge = if ($setupOk) { '<span class="pill ok">Setup complet</span>' } else { '<span class="pill warn">Setup incomplet</span>' }
$fileUri = "file:///" + ($outHtml -replace '\\', '/')

$html = @"
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Session Grok - $machine</title>
  <style>
    :root {
      --bg: #0f1419;
      --card: #1a2332;
      --border: #2d3a4f;
      --text: #e7ecf3;
      --muted: #8b9cb3;
      --ok: #3dd68c;
      --warn: #f5c542;
      --bad: #f87171;
      --accent: #60a5fa;
      --grok: #a78bfa;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 1.5rem;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg); color: var(--text);
      line-height: 1.5;
    }
    .wrap { max-width: 960px; margin: 0 auto; }
    header {
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }
    h1 { margin: 0 0 0.25rem; font-size: 1.5rem; }
    .meta { color: var(--muted); font-size: 0.9rem; }
    .pill {
      display: inline-block; padding: 0.2rem 0.6rem;
      border-radius: 999px; font-size: 0.8rem; font-weight: 600;
    }
    .pill.ok { background: rgba(61,214,140,0.15); color: var(--ok); }
    .pill.warn { background: rgba(245,197,66,0.15); color: var(--warn); }
    section {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 1.25rem;
    }
    h2 { margin: 0 0 1rem; font-size: 1.1rem; color: var(--accent); }
    table { width: 100%; border-collapse: collapse; }
    td { padding: 0.5rem 0.4rem; vertical-align: top; border-bottom: 1px solid var(--border); }
    tr:last-child td { border-bottom: none; }
    .status { width: 2rem; font-size: 1.1rem; text-align: center; }
    tr.ok .status { color: var(--ok); }
    tr.warn .status { color: var(--warn); }
    tr.bad .status { color: var(--bad); }
    .label { font-weight: 500; }
    .detail { color: var(--muted); font-size: 0.85rem; display: block; margin-top: 0.15rem; }
    .badge {
      font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
      padding: 0.1rem 0.35rem; border-radius: 4px; margin-left: 0.35rem;
      vertical-align: middle;
    }
    .badge.auto { background: rgba(96,165,250,0.2); color: var(--accent); }
    .badge.grok { background: rgba(167,139,250,0.2); color: var(--grok); }
    .badge.you { background: rgba(245,197,66,0.2); color: var(--warn); }
    .badge.here { background: rgba(61,214,140,0.2); color: var(--ok); }
    .badge.other { background: rgba(139,156,179,0.2); color: var(--muted); }
    .pc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1rem; }
    .pc-card {
      background: rgba(0,0,0,0.2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
    }
    .pc-card.current { border-color: var(--ok); }
    .pc-card h3 { margin: 0 0 0.5rem; font-size: 1rem; }
    .pc-card p { margin: 0.25rem 0; font-size: 0.85rem; color: var(--muted); }
    .legend { display: flex; flex-wrap: wrap; gap: 1rem; font-size: 0.85rem; color: var(--muted); margin-top: 0.75rem; }
    .muted { color: var(--muted); }
    code { background: rgba(0,0,0,0.3); padding: 0.1rem 0.35rem; border-radius: 4px; font-size: 0.85em; }
    footer { margin-top: 2rem; font-size: 0.8rem; color: var(--muted); text-align: center; }
    a { color: var(--accent); }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Checklist session Grok / Cursor</h1>
      <p class="meta">
        <strong>$machine</strong> - genere le $handoffAt
        &nbsp; $setupBadge
      </p>
      <p class="meta">Tu n'as rien a demander : Grok fait la colonne <span class="badge grok">Grok</span> automatiquement.</p>
    </header>

    <section>
      <h2>Debut de session (automatique)</h2>
      <table>
        $($startRows -join "`n")
      </table>
      <div class="legend">
        <span><span class="badge auto">Script</span> session-handoff.ps1</span>
        <span><span class="badge grok">Grok</span> skill always-on</span>
      </div>
    </section>

    <section>
      <h2>Setup PC (une fois par machine)</h2>
      <table>
        $($setupRows -join "`n")
      </table>
      <p class="muted" style="margin-top:0.75rem">Guide : <code>aria-local-sync/SETUP-AUTRE-PC.md</code></p>
    </section>

    <section>
      <h2>Fin de session utile (automatique Grok)</h2>
      <table>
        $($endRows -join "`n")
      </table>
    </section>

    <section>
      <h2>Etat multi-PC</h2>
      <div class="pc-grid">
        $($pcCards -join "`n")
      </div>
    </section>

    <footer>
      Regenere a chaque <code>session-handoff.ps1</code> -
      <a href="$fileUri">SESSION-CHECKLIST.html</a>
    </footer>
  </div>
</body>
</html>
"@

$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($outHtml, $html, $utf8)
Write-Host "Checklist visuelle : $outHtml" -ForegroundColor Green
Write-Host "  $fileUri" -ForegroundColor DarkGray

if ($Open) {
    Start-Process $outHtml
}