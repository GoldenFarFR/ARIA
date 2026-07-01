# Optimise Ollama + ARIA pour PC portable 8 Go VRAM (ex. RTX 5070 Laptop, 32 Go RAM)
# Usage: .\optimize-ollama-local.ps1 [-Model qwen2.5:14b]

param(
    [string]$Model = "qwen2.5:14b",
    [int]$NumCtx = 8192
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_vault-common.ps1")

$LocalEnv = Get-LocalEnvPath -ScriptsRoot $Root
if (-not (Test-Path $LocalEnv)) {
    Write-Host "local.env absent — lance init-from-local.ps1" -ForegroundColor Red
    exit 1
}

# Variables Ollama (niveau utilisateur Windows — persistant)
$ollamaVars = @{
    OLLAMA_FLASH_ATTENTION = "1"
    OLLAMA_KV_CACHE_TYPE   = "q8_0"
}
foreach ($key in $ollamaVars.Keys) {
    [System.Environment]::SetEnvironmentVariable($key, $ollamaVars[$key], "User")
    Set-Item -Path "Env:$key" -Value $ollamaVars[$key]
    Write-Host "OK $key=$($ollamaVars[$key])" -ForegroundColor DarkGray
}

# Mise a jour local.env (SSOT vault)
$envMap = Read-EnvFile -Path $LocalEnv
$envMap["LLM_PROVIDER"] = "ollama"
$envMap["LLM_MODEL"] = $Model
$envMap["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
$envMap["ARIA_OLLAMA_NUM_CTX"] = "$NumCtx"
$envMap["ARIA_LLM_ENABLED"] = "true"
if (-not $envMap["ARIA_VECTOR_MEMORY"]) { $envMap["ARIA_VECTOR_MEMORY"] = "true" }

$lines = @("# GoldenFar local.env — optimise Ollama $(Get-Date -Format 'yyyy-MM-dd')", "")
foreach ($key in ($envMap.Keys | Sort-Object)) {
    $lines += "$key=$($envMap[$key])"
}
Set-Content -Path $LocalEnv -Value $lines -Encoding UTF8
Write-Host "local.env -> LLM_MODEL=$Model, ARIA_OLLAMA_NUM_CTX=$NumCtx" -ForegroundColor Green

& (Join-Path $Root "sync-local.ps1")

# Verif modele installe
$installed = ollama list 2>&1 | Out-String
if ($installed -notmatch [regex]::Escape($Model.Split(":")[0])) {
    Write-Host "Pull $Model ..." -ForegroundColor Yellow
    ollama pull $Model
}

# Smoke rapide (1 phrase)
Write-Host "Smoke Ollama $Model ..." -ForegroundColor Cyan
$body = @{
    model    = $Model
    messages = @(@{ role = "user"; content = "Reponds OK en un mot." })
    stream   = $false
    options  = @{ num_ctx = $NumCtx }
} | ConvertTo-Json -Depth 5
try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:11434/v1/chat/completions" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 90
    $reply = $r.choices[0].message.content
    Write-Host "Reponse: $reply" -ForegroundColor Green
} catch {
    Write-Host "Smoke echoue: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Verifie qu'Ollama tourne (ollama serve ou app tray)" -ForegroundColor Yellow
}

Write-Host "optimize-ollama-local OK — modele $Model, pas de 30B en routine" -ForegroundColor Green