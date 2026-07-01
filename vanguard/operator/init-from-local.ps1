# Genere local.env (+ base production.env) depuis dexpulse/backend/.env

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")

$BackendEnv = Join-Path $Root "..\backend\.env"
. (Join-Path $Root "_vault-common.ps1")
Initialize-GoldenFarVault | Out-Null
$LocalOut = Get-LocalEnvPath -ScriptsRoot $Root
$ProdOut = Get-ProductionEnvPath -ScriptsRoot $Root
$ProdExample = Join-Path $Root "production.env.example"
$LocalExample = Join-Path $Root "local.env.example"

if (-not (Test-Path $BackendEnv)) {
    Write-Host "backend/.env introuvable : $BackendEnv" -ForegroundColor Red
    exit 1
}

$src = Read-EnvFile -Path $BackendEnv
$localTpl = Read-EnvFile -Path $LocalExample
$prodTpl = Read-EnvFile -Path $ProdExample

$local = @{}
foreach ($key in $localTpl.Keys) { $local[$key] = $localTpl[$key] }
foreach ($key in $src.Keys) {
    if ($src[$key]) { $local[$key] = $src[$key] }
}
if (-not $local["ARIA_X_HANDLE"]) { $local["ARIA_X_HANDLE"] = "Aria_ZHC" }

$localHeader = @(
    "# DEXPulse - dev local (genere par init-from-local.ps1)"
    "# Sync : .\sync-local.ps1"
    ""
)
$localOrder = @(
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME", "TELEGRAM_ADMIN_USERNAME", "TELEGRAM_ADMIN_IDS",
    "ACCESS_CODE_ENABLED", "ARIA_AUTONOMOUS", "ARIA_JUNO_OUTREACH",
    "SITE_BASE_URL", "LLM_PROVIDER", "LLM_MODEL", "OLLAMA_BASE_URL", "ARIA_X_HANDLE"
)
Write-EnvFile -Path $LocalOut -HeaderLines $localHeader -Vars $local -KeyOrder $localOrder
Write-Host "[OK] local.env" -ForegroundColor Green

if (-not (Test-Path $ProdOut)) {
    $prod = @{}
    foreach ($key in $prodTpl.Keys) { $prod[$key] = $prodTpl[$key] }
    foreach ($key in @("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_IDS", "ARIA_EMAIL")) {
        if ($src[$key]) { $prod[$key] = $src[$key] }
    }
    $prodHeader = @(
        "# DEXPulse - production Render"
        "# Completer via .\pull-render.ps1 (recommande) ou edition manuelle"
        "# Sync : .\sync-render.ps1"
        ""
    )
    $prodOrder = @(
        "DEBUG", "SERVE_FRONTEND", "DATA_DIR", "ACCESS_CODE_ENABLED", "SESSION_TTL_HOURS",
        "ALERT_COOLDOWN_HOURS", "ARIA_AUTONOMOUS", "ARIA_JUNO_OUTREACH", "ARIA_PUBLIC_MODE",
        "ARIA_GROUNDED_MODE", "ARIA_LLM_ENABLED", "ARIA_LLM_TEMPERATURE", "ARIA_LLM_ENHANCE_SKILLS",
        "ARIA_X_HANDLE", "TELEGRAM_BOT_USERNAME", "TELEGRAM_ADMIN_USERNAME", "SITE_BASE_URL",
        "HOLDING_DOMAIN", "CORS_ORIGINS", "LLM_PROVIDER", "LLM_MODEL",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_IDS", "LLM_API_KEY", "ADMIN_API_SECRET",
        "PRIVY_APP_ID", "PRIVY_JWT_VERIFICATION_KEY",
        "X_BEARER_TOKEN", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
        "X_ACCESS_TOKEN_SECRET", "ARIA_EMAIL",
        "GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_SANDBOX_REPO", "GITHUB_TOKEN_REPO",
        "GITHUB_READ_REPOS", "GITHUB_WRITE_REPOS"
    )
    Write-EnvFile -Path $ProdOut -HeaderLines $prodHeader -Vars $prod -KeyOrder $prodOrder
    Write-Host "[OK] production.env (base - lance pull-render.ps1 pour les secrets Render)" -ForegroundColor Yellow
}