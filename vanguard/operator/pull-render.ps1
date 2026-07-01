# Telecharge les variables Render vers production.env

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$ServiceName = "aria-api"
$OutFile = Get-ProductionEnvPath -ScriptsRoot $Root
$ExampleFile = Join-Path $Root "production.env.example"

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) {
    Write-Host "Cle Render manquante (coffre keys\render.api-key)." -ForegroundColor Red
    Write-Host "Render Dashboard > Account Settings > API Keys > Create"
    Write-Host "Colle rnd_... dans : $(Get-RenderApiKeyPath -ScriptsRoot $Root)"
    exit 1
}

$headers = Get-RenderHeaders -ApiKey $apiKey
Write-Host "Recherche du service Render..." -ForegroundColor Cyan
$serviceId = Resolve-RenderServiceId -Headers $headers -Root $Root -FallbackName $ServiceName
if (-not $serviceId) {
    Write-Host "Service Render introuvable." -ForegroundColor Red
    exit 1
}

Write-Host "Service ID: $serviceId" -ForegroundColor Green
Write-Host "Telechargement des variables d'environnement..." -ForegroundColor Cyan
$remote = Get-RenderEnvVars -Headers $headers -ServiceId $serviceId

$template = Read-EnvFile -Path $ExampleFile
$local = Read-EnvFile -Path $OutFile
$merged = @{}

foreach ($key in $template.Keys) { $merged[$key] = $template[$key] }
foreach ($key in $local.Keys) {
    if ($local[$key]) { $merged[$key] = $local[$key] }
}
foreach ($key in $remote.Keys) {
    if ($remote[$key]) { $merged[$key] = $remote[$key] }
}

$header = @(
    "# DEXPulse - production Render (genere par pull-render.ps1)"
    "# Sync vers Render : .\sync-render.ps1"
    ""
)
$keyOrder = @(
    "DEBUG", "SERVE_FRONTEND", "DATA_DIR", "ACCESS_CODE_ENABLED", "SESSION_TTL_HOURS",
    "ALERT_COOLDOWN_HOURS", "ARIA_AUTONOMOUS", "ARIA_JUNO_OUTREACH", "ARIA_PUBLIC_MODE",
    "ARIA_GROUNDED_MODE", "ARIA_LLM_ENABLED", "ARIA_LLM_TEMPERATURE", "ARIA_LLM_ENHANCE_SKILLS",
    "ARIA_PROACTIVE_IDEAS", "ARIA_CHAT_RATE_LIMIT_PER_HOUR",
    "ARIA_X_HANDLE", "TELEGRAM_BOT_USERNAME", "TELEGRAM_ADMIN_USERNAME", "SITE_BASE_URL",
    "HOLDING_DOMAIN", "CORS_ORIGINS", "LLM_PROVIDER", "LLM_MODEL",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_IDS", "LLM_API_KEY", "ADMIN_API_SECRET",
    "PRIVY_APP_ID", "PRIVY_JWT_VERIFICATION_KEY",
    "X_BEARER_TOKEN", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET", "ARIA_EMAIL",
    "GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_SANDBOX_REPO", "GITHUB_TOKEN_REPO",
    "GITHUB_READ_REPOS", "GITHUB_WRITE_REPOS", "GITHUB_EXCLUDED_REPOS", "GITHUB_PROTECTED_REPOS"
)

Write-EnvFile -Path $OutFile -HeaderLines $header -Vars $merged -KeyOrder $keyOrder
Write-Host ""
Write-Host "$($remote.Count) variable(s) Render -> production.env" -ForegroundColor Green
Write-Host "Edite production.env si besoin, puis .\sync-render.ps1 pour pousser." -ForegroundColor Cyan