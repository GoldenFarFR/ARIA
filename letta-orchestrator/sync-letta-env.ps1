# Génère .env Letta depuis le coffre GoldenFar (secrets hors Git)
$Here = $PSScriptRoot
$VaultDir = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
$lines = @()

function Read-EnvFile([string]$Path) {
    if (-not (Test-Path $Path)) { return @{} }
    $map = @{}
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $v = $Matches[2].Trim()
            if ($v -match '^"(.*)"$') { $v = $Matches[1] }
            if ($v) { $map[$Matches[1]] = $v }
        }
    }
    return $map
}

$local = Read-EnvFile (Join-Path $VaultDir "local.env")
$prod = Read-EnvFile (Join-Path $VaultDir "production.env")

$ollama = if ($local.OLLAMA_BASE_URL) { $local.OLLAMA_BASE_URL } else { "http://127.0.0.1:11434" }
$groq = if ($env:GROQ_API_KEY -and $env:GROQ_API_KEY.Length -ge 20) { $env:GROQ_API_KEY }
         elseif ($prod.LLM_API_KEY -and $prod.LLM_API_KEY.Length -ge 20) { $prod.LLM_API_KEY }
         elseif ($local.GROQ_API_KEY -and $local.GROQ_API_KEY.Length -ge 20) { $local.GROQ_API_KEY }
         else { $null }
$anthropic = if ($env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY } else { $prod.ANTHROPIC_API_KEY }

$lines += "ollama_base_url=$ollama"
if ($groq) {
    $lines += "GROQ_API_KEY=$groq"
    $lines += "groq_api_key=$groq"
}
if ($anthropic) {
    $lines += "ANTHROPIC_API_KEY=$anthropic"
    $lines += "anthropic_api_key=$anthropic"
}

if (-not $env:ARIA_REPO_ROOT) {
    $env:ARIA_REPO_ROOT = (Resolve-Path (Join-Path $Here "..")).Path
}
$lines += "ARIA_REPO_ROOT=$($env:ARIA_REPO_ROOT)"

$out = Join-Path $Here ".env"
[System.IO.File]::WriteAllLines($out, $lines)
Write-Host "OK .env Letta ($($lines.Count) clés)" -ForegroundColor Green