# Copie local.env vers aria-vanguard/backend/.env (+ secrets operateur depuis production.env)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")

. (Join-Path $Root "_vault-common.ps1")
$Src = Get-LocalEnvPath -ScriptsRoot $Root
$Prod = Get-ProductionEnvPath -ScriptsRoot $Root
$Dst = Join-Path $Root "..\backend\.env"

if (-not (Test-Path $Src)) {
    Write-Host "Lance d'abord .\setup.ps1 ou .\init-from-local.ps1" -ForegroundColor Red
    exit 1
}

$merged = Read-EnvFile -Path $Src
if (Test-Path $Prod) {
    $prod = Read-EnvFile -Path $Prod
    # LLM local : local.env garde ollama ; production.env fournit la cle si groq/xai
    $overlay = @(
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_IDS", "TELEGRAM_WEBHOOK_SECRET",
        "GITHUB_TOKEN", "GITHUB_READ_REPOS", "GITHUB_WRITE_REPOS",
        "GITHUB_EXCLUDED_REPOS", "GITHUB_PROTECTED_REPOS",
        "X_BEARER_TOKEN", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
        "X_ALLOW_LIKES", "X_ALLOW_REPLIES", "X_CURIOSITY_ENABLED", "X_MENTIONS_LEARN_ENABLED",
        "LLM_API_KEY", "PRIVY_APP_ID"
    )
    foreach ($key in $overlay) {
        if ($prod[$key]) { $merged[$key] = $prod[$key] }
    }
    Write-Host "Secrets operateur fusionnes depuis production.env" -ForegroundColor DarkGray
}

$lines = @(
    "# aria-vanguard API - dev local (sync-local.ps1)",
    "# Secrets operateur: production.env",
    ""
)
foreach ($key in $merged.Keys) {
    $lines += "$key=$($merged[$key])"
}
Set-Content -Path $Dst -Value $lines -Encoding UTF8
Write-Host "-> $Dst" -ForegroundColor Green