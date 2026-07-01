# Pont ARIA <-> Cursor — envoie le dernier message humain/ouvrier a l'API locale, append reponse ARIA.
# Usage: .\aria-cursor-bridge.ps1 [-ApiUrl "http://127.0.0.1:8000"] [-TimeoutSec 120]

param(
    [string]$ApiUrl = "http://127.0.0.1:8000",
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