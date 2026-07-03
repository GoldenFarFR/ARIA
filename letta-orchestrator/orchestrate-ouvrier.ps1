# ARIA-Ouvrier Letta — copie conforme Grok/Cursor (local, économie jetons)
param(
    [Parameter(Mandatory)]
    [string]$Message
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
if (-not $env:ARIA_REPO_ROOT) {
    $env:ARIA_REPO_ROOT = (Resolve-Path (Join-Path $Here "..")).Path
}

$VaultDir = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $k = $Matches[1]; $v = $Matches[2].Trim()
            if ($v -match '^"(.*)"$') { $v = $Matches[1] }
            if ($v) { Set-Item -Path "env:$k" -Value $v }
        }
    }
}
Import-DotEnv (Join-Path $VaultDir "local.env")
Import-DotEnv (Join-Path $VaultDir "production.env")
if (-not $env:OLLAMA_KEEP_ALIVE) { $env:OLLAMA_KEEP_ALIVE = "30m" }

$py = Join-Path $Here "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv absent — .\install.ps1 puis .\setup_ouvrier.py" }

& $py (Join-Path $Here "orchestrate_ouvrier.py") --message $Message