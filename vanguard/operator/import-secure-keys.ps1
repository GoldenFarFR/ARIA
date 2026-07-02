# Importe toutes les cles depuis secure-keys/config/secure vers le coffre GoldenFar.
# Ne committe jamais les secrets — production.env / local.env restent hors Git.
#
# Usage:
#   .\import-secure-keys.ps1
#   .\import-secure-keys.ps1 -SyncRender
#   $env:SECURE_KEYS_ROOT = "D:\backup\secure-keys\config\secure"

param(
    [switch]$SyncRender,
    [switch]$SyncLocalOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_vault-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$secureRoot = if ($env:SECURE_KEYS_ROOT) {
    $env:SECURE_KEYS_ROOT.TrimEnd('\')
} else {
    Join-Path $env:USERPROFILE "secure-keys\config\secure"
}

if (-not (Test-Path $secureRoot)) {
    Write-Host "Dossier introuvable: $secureRoot" -ForegroundColor Red
    exit 1
}

Initialize-GoldenFarVault | Out-Null
$prodPath = Get-ProductionEnvPath -ScriptsRoot $Root
$localPath = Get-LocalEnvPath -ScriptsRoot $Root

if (-not (Test-Path $prodPath)) {
    $example = Join-Path $Root "production.env.example"
    if (Test-Path $example) {
        Copy-Item $example $prodPath
        Write-Host "[BOOT] production.env cree depuis example" -ForegroundColor Yellow
    } else {
        throw "production.env absent et pas d'example"
    }
}

if (-not (Test-Path $localPath)) {
    $example = Join-Path $Root "local.env.example"
    if (Test-Path $example) {
        Copy-Item $example $localPath
        Write-Host "[BOOT] local.env cree depuis example" -ForegroundColor Yellow
    }
}

function Read-SecureKeyValue {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $raw = Get-Content $Path -Raw -Encoding UTF8
    if ($raw -match '(xai-[A-Za-z0-9]+)') { return $Matches[1] }
    if ($raw -match '(gsk_[A-Za-z0-9]+)') { return $Matches[1] }
    if ($raw -match '(sk-[A-Za-z0-9]+)') { return $Matches[1] }
    $line = (Get-Content $Path -Encoding UTF8 | Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*#' } | Select-Object -First 1)
    if ($line) { return $line.Trim() }
    return $null
}

function Parse-KeyValueFile {
    param([string]$Path)
    $out = @{}
    if (-not (Test-Path $Path)) { return $out }
    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*#') { return }
        if ($_ -match '^\s*(\w+)\s*=\s*(.+)\s*$') {
            $out[$Matches[1]] = [System.Uri]::UnescapeDataString($Matches[2].Trim())
        }
    }
    return $out
}

function Set-EnvKeys {
    param(
        [string]$Path,
        [hashtable]$Keys,
        [switch]$SkipPlaceholders
    )
    $updated = 0
    foreach ($key in $Keys.Keys) {
        $val = $Keys[$key]
        if ([string]::IsNullOrWhiteSpace($val)) { continue }
        if ($SkipPlaceholders -and ($val -match '^(TON_|YOUR_|placeholder|changeme)' -or $val -match 'WEBHOOK_URL$')) {
            Write-Host "[SKIP] $key (placeholder)" -ForegroundColor DarkGray
            continue
        }
        Update-EnvFileKey -Path $Path -Key $key -Value $val
        $updated++
    }
    return $updated
}

$stats = [ordered]@{ production = 0; local = 0; letta = 0; acp = 0 }

# --- Grok / xAI (IMAGE + GROK) ---
$grokKey = Read-SecureKeyValue (Join-Path $secureRoot "Grok.txt")
if ($grokKey) {
    $n = Set-EnvKeys $prodPath @{ IMAGE_API_KEY = $grokKey; GROK_API_KEY = $grokKey }
    $stats.production += $n
    Write-Host "[OK] Grok/xAI -> IMAGE_API_KEY + GROK_API_KEY" -ForegroundColor Green
}

# --- Groq (LLM prod) ---
$groqKey = Read-SecureKeyValue (Join-Path $secureRoot "Groq.txt")
if ($groqKey) {
    $n = Set-EnvKeys $prodPath @{ LLM_API_KEY = $groqKey }
    $stats.production += $n
    Write-Host "[OK] Groq -> LLM_API_KEY" -ForegroundColor Green
}

# --- Telegram ---
$tg = Parse-KeyValueFile (Join-Path $secureRoot "telegram.txt")
if ($tg.token) {
    $tgKeys = @{ TELEGRAM_BOT_TOKEN = $tg.token }
    if ($tg.chatId) { $tgKeys.TELEGRAM_ADMIN_IDS = $tg.chatId }
    $stats.production += (Set-EnvKeys $prodPath $tgKeys)
    $stats.local += (Set-EnvKeys $localPath $tgKeys)
    Write-Host "[OK] Telegram -> TELEGRAM_BOT_TOKEN + ADMIN_IDS" -ForegroundColor Green
}

# --- GitHub (skip placeholder) ---
$gh = Parse-KeyValueFile (Join-Path $secureRoot "github.txt")
if ($gh.token) {
    $n = Set-EnvKeys $prodPath @{ GITHUB_TOKEN = $gh.token } -SkipPlaceholders
    if ($n -gt 0) { $stats.production += $n; Write-Host "[OK] GitHub -> GITHUB_TOKEN" -ForegroundColor Green }
}

# --- X Aria_ZHC ---
$xSrc = Join-Path $secureRoot "x - Aria.txt"
$xMap = [ordered]@{
    bearerToken  = "X_BEARER_TOKEN"
    apiKey       = "X_API_KEY"
    apiSecret    = "X_API_SECRET"
    accessToken  = "X_ACCESS_TOKEN"
    accessSecret = "X_ACCESS_TOKEN_SECRET"
}
$xParsed = @{}
Get-Content $xSrc -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*(\w+)\s*=\s*(.+)\s*$') {
        $k = $Matches[1]
        if ($xMap.Contains($k)) {
            $xParsed[$xMap[$k]] = [System.Uri]::UnescapeDataString($Matches[2].Trim())
        }
    }
}
if ($xParsed.Count -ge 4) {
    $stats.production += (Set-EnvKeys $prodPath $xParsed)
    Write-Host "[OK] X Aria_ZHC -> $($xParsed.Count) cles" -ForegroundColor Green
}

# --- X GoldenFarFR (coffre separe) ---
$goldenSrc = Join-Path $secureRoot "x - Golden.txt"
$goldenOut = Join-Path (Get-GoldenFarVaultRoot) "keys\x-goldenfarfr.env"
$goldenParsed = @{}
Get-Content $goldenSrc -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*(\w+)\s*=\s*(.+)\s*$') {
        $goldenParsed[$Matches[1]] = [System.Uri]::UnescapeDataString($Matches[2].Trim())
    }
}
if ($goldenParsed.Count -gt 0) {
    $lines = @("# X keys GoldenFarFR — import secure-keys $(Get-Date -Format yyyy-MM-dd)")
    foreach ($k in $goldenParsed.Keys) { $lines += "$k=$($goldenParsed[$k])" }
    Set-Content -Path $goldenOut -Value $lines -Encoding UTF8
    $stats.acp++
    Write-Host "[OK] X GoldenFarFR -> vault/keys/x-goldenfarfr.env" -ForegroundColor Green
}

# --- Deepseek (optionnel) ---
$dsKey = Read-SecureKeyValue (Join-Path $secureRoot "Deepseek.txt")
if ($dsKey) {
    $dsPath = Join-Path (Get-GoldenFarVaultRoot) "keys\deepseek.api-key"
    Set-Content -Path $dsPath -Value $dsKey.Trim() -Encoding UTF8 -NoNewline
    $stats.acp++
    Write-Host "[OK] Deepseek -> vault/keys/deepseek.api-key" -ForegroundColor Green
}

# --- Virtual / ACP ---
$virtualSrc = Join-Path $secureRoot "Virtual.txt"
if (Test-Path $virtualSrc) {
    $vraw = Get-Content $virtualSrc -Raw -Encoding UTF8
    $pub = $null
    $priv = $null
    if ($vraw -match '(?s)PUBLIC\s*\r?\n(.+?)\r?\n\r?\nPRIVEE') { $pub = $Matches[1].Trim() }
    if ($vraw -match '(?s)PRIVEE\s*\r?\n(.+)') { $priv = $Matches[1].Trim() }
    $keysDir = Join-Path (Get-GoldenFarVaultRoot) "keys"
    if ($pub) {
        Set-Content -Path (Join-Path $keysDir "acp-public.pem") -Value $pub -Encoding UTF8
        $stats.acp++
    }
    if ($priv) {
        Set-Content -Path (Join-Path $keysDir "acp-private.pem") -Value $priv -Encoding UTF8
        $stats.acp++
    }
    if ($pub -or $priv) {
        Write-Host "[OK] Virtual ACP -> vault/keys/acp-*.pem" -ForegroundColor Green
    }
}

# --- Letta orchestrator .env ---
$lettaEnv = Join-Path (Split-Path $Root -Parent) "..\letta-orchestrator\.env"
$lettaEnv = (Resolve-Path (Join-Path $Root "..\..\letta-orchestrator\.env") -ErrorAction SilentlyContinue)
if (-not $lettaEnv) {
    $lettaEnv = Join-Path $env:USERPROFILE "GitHub-Repos\ARIA\letta-orchestrator\.env"
}
$lettaLines = @(
    "ollama_base_url=http://127.0.0.1:11434"
)
if ($groqKey) { $lettaLines += "groq_api_key=$groqKey" }
if ($grokKey) { $lettaLines += "xai_api_key=$grokKey"; $lettaLines += "grok_api_key=$grokKey" }
if ($dsKey) { $lettaLines += "deepseek_api_key=$dsKey" }
Set-Content -Path $lettaEnv -Value $lettaLines -Encoding UTF8
$stats.letta = $lettaLines.Count
Write-Host "[OK] letta-orchestrator/.env ($($lettaLines.Count) vars)" -ForegroundColor Green

# --- Propagation backend ---
& (Join-Path $Root "sync-local.ps1")
Write-Host "[OK] sync-local -> backend/.env" -ForegroundColor Green

Write-Host ""
Write-Host "Import termine — coffre: $(Get-GoldenFarVaultRoot)" -ForegroundColor Cyan
Write-Host "  production.env : $($stats.production) cles"
Write-Host "  local.env      : $($stats.local) cles"
Write-Host "  letta .env     : $($stats.letta) vars"
Write-Host "  vault/keys     : $($stats.acp) fichiers"

if ($SyncRender -and -not $SyncLocalOnly) {
    Write-Host ""
    & (Join-Path $Root "sync-render.ps1")
} elseif (-not $SyncLocalOnly) {
    Write-Host "Prod: .\sync-render.ps1 apres verification (ou -SyncRender)" -ForegroundColor DarkGray
}