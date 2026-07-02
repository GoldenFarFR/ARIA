# Pont ARIA <-> Cursor — envoie le dernier message humain/ouvrier a l'API locale, append reponse ARIA.
# Usage: .\aria-cursor-bridge.ps1 [-ApiUrl "http://127.0.0.1:8000"] [-TimeoutSec 120]

param(
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [ValidateSet("auto", "vanguard", "letta")]
    [string]$Provider = "auto",
    [int]$TimeoutSec = 120
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_paths.ps1")

$bridgePath = Join-Path $script:AriaCollegueRoot "sessions\aria-cursor-bridge.jsonl"
if (-not (Test-Path $bridgePath)) {
    throw "Bridge introuvable: $bridgePath"
}

function Read-BridgeEntries {
    Get-Content $bridgePath -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object {
        $_ | ConvertFrom-Json
    }
}

function Append-BridgeEntry($obj) {
    $line = ($obj | ConvertTo-Json -Compress -Depth 5)
    if (Test-Path $bridgePath) {
        $tail = [System.IO.File]::ReadAllText($bridgePath, [System.Text.UTF8Encoding]::new($false))
        if ($tail.Length -gt 0 -and -not $tail.EndsWith("`n")) {
            [System.IO.File]::AppendAllText($bridgePath, "`n", [System.Text.UTF8Encoding]::new($false))
        }
    }
    [System.IO.File]::AppendAllText($bridgePath, "$line`n", [System.Text.UTF8Encoding]::new($false))
}

$entries = @(Read-BridgeEntries)
if ($entries.Count -eq 0) {
    Write-Host "[bridge] vide" -ForegroundColor Yellow
    exit 0
}

$last = $entries[-1]
if ($last.speaker -eq "ARIA") {
    Write-Host "[bridge] dernier message deja ARIA — rien a envoyer" -ForegroundColor DarkGray
    exit 0
}
if ($last.kind -eq "system") {
    Write-Host "[bridge] dernier message systeme — attendre un humain/ouvrier" -ForegroundColor DarkGray
    exit 0
}

$humanSpeakers = @("Sylvain", "Grok")
if ($humanSpeakers -notcontains $last.speaker) {
    Write-Host "[bridge] speaker $($last.speaker) non routable" -ForegroundColor Yellow
    exit 0
}

# Contexte court pour ARIA (derniers echanges)
$contextLines = @()
foreach ($e in ($entries | Where-Object { $_.kind -ne "system" } | Select-Object -Last 8)) {
    $contextLines += "$($e.speaker): $($e.text)"
}
$payloadText = $last.text
if ($contextLines.Count -gt 1) {
    $payloadText = @(
        "Contexte session Cursor (pont C). Reponds en francais sauf demande contraire.",
        ($contextLines -join "`n"),
        "---",
        "Message actuel de $($last.speaker): $($last.text)"
    ) -join "`n"
}

function Test-LettaBridgeUp {
    try {
        Invoke-WebRequest -Uri "http://localhost:8283/v1/agents/" -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Invoke-LettaBridgeReply([string]$Text) {
    $lettaRoot = Join-Path $script:AriaRepoRoot "letta-orchestrator"
    $orch = Join-Path $lettaRoot "orchestrate.ps1"
    if (-not (Test-Path $orch)) { throw "letta-orchestrator absent" }
    $start = Join-Path $lettaRoot "start-letta.ps1"
    if (-not (Test-LettaBridgeUp) -and (Test-Path $start)) { & $start | Out-Null }
    if (-not (Test-LettaBridgeUp)) { throw "Letta :8283 injoignable" }

    $raw = & $orch -Message $Text 2>&1 | Out-String
    if ($raw -match '(?s)--- RÉPONSE ---\s*(.+?)\s*---------------') {
        return $Matches[1].Trim()
    }
    if ($raw -match '\[Échec\]') { throw "tous les agents Letta ont echoue" }
    return $raw.Trim()
}

$envProvider = $env:ARIA_BRIDGE_PROVIDER
if ($Provider -eq "auto" -and $envProvider -in @("letta", "vanguard")) {
    $Provider = $envProvider
}

$useLetta = ($Provider -eq "letta") -or ($Provider -eq "auto" -and (Test-LettaBridgeUp))
$skill = $null
$reply = ""

if ($useLetta) {
    try {
        $reply = Invoke-LettaBridgeReply -Text $payloadText
        $skill = "letta_orchestrator"
    } catch {
        if ($Provider -eq "letta") {
            $reply = "[bridge-letta-error] $($_.Exception.Message)"
        } else {
            Write-Host "[bridge] Letta indisponible, fallback Vanguard API" -ForegroundColor DarkYellow
            $useLetta = $false
        }
    }
}

if (-not $useLetta) {
    $body = @{ message = $payloadText } | ConvertTo-Json -Compress
    $uri = "$($ApiUrl.TrimEnd('/'))/api/aria/chat"
    try {
        $resp = Invoke-RestMethod -Uri $uri -Method POST -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
            -ContentType "application/json; charset=utf-8" -TimeoutSec $TimeoutSec
        $reply = [string]$resp.reply
        $skill = $resp.skill_used
    }
    catch {
        $reply = "[bridge-error] $($_.Exception.Message)"
        $skill = $null
    }
}

$ariaId = "aria-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Append-BridgeEntry @{
    id        = $ariaId
    speaker   = "ARIA"
    text      = $reply
    at        = (Get-Date).ToUniversalTime().ToString("o")
    reply_to  = $last.id
    skill     = $skill
    kind      = "message"
}

Write-Host "[bridge] ARIA -> $($reply.Substring(0, [Math]::Min(120, $reply.Length)))..." -ForegroundColor DarkYellow
exit 0