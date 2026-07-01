# Envoie un message informatif a l'admin via ARIA Telegram
# Usage: .\notify-aria-telegram.ps1 -Text "..." [-Source github-audit]

param(
    [Parameter(Mandatory = $true)]
    [string]$Text,

    [string]$Source = "operator"
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

function Send-ViaAriaApi {
    param([string]$Message, [string]$Src)
    $bridge = Join-Path $PSScriptRoot "totp-aria-bridge.ps1"
    if (-not (Test-Path $bridge)) { return $false }
    . $bridge
    $cfg = Get-AriaApiConfig
    if (-not $cfg.Secret) { return $false }
    $headers = @{
        "X-Admin-Secret" = $cfg.Secret
        "Content-Type"   = "application/json"
    }
    $body = @{ text = $Message; source = $Src } | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Method Post `
            -Uri "$($cfg.Base)/api/aria/operator/notify" `
            -Headers $headers -Body $body
        return [bool]$r.telegram_notified
    } catch {
        return $false
    }
}

function Send-ViaTelegramDirect {
    param([string]$Message)
    $token = Get-VaultEnvValue "TELEGRAM_BOT_TOKEN"
    $admins = Get-VaultEnvValue "TELEGRAM_ADMIN_IDS"
    if (-not $token -or -not $admins) { return $false }
    $chatId = ($admins -split ',')[0].Trim()
    if (-not $chatId) { return $false }
    $uri = "https://api.telegram.org/bot$token/sendMessage"
    $payload = @{ chat_id = $chatId; text = $Message } | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Method Post -Uri $uri -Body $payload -ContentType "application/json"
        return [bool]$r.ok
    } catch {
        return $false
    }
}

$full = "[$Source] $Text"
$ok = Send-ViaAriaApi -Message $Text -Src $Source
if (-not $ok) {
    $ok = Send-ViaTelegramDirect -Message $full
}

if ($ok) {
    Write-Host "[TELEGRAM] Alerte envoyee ($Source)" -ForegroundColor Green
} else {
    Write-Host "[TELEGRAM] Echec envoi (API ou TELEGRAM_BOT_TOKEN manquant)" -ForegroundColor Yellow
}
return $ok