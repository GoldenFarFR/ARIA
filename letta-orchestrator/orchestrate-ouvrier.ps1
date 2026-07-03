# ARIA-Ouvrier direct — copie conforme Grok/Cursor (local, économie jetons)
param(
    [Parameter(Mandatory)]
    [string]$Message,
    [switch]$ShowTrace,
    [switch]$Quiet
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
if (-not $env:XAI_API_KEY) {
    foreach ($src in @("GROK_API_KEY", "IMAGE_API_KEY", "XAI_API_KEY")) {
        $c = [Environment]::GetEnvironmentVariable($src, "User")
        if (-not $c) { $c = [Environment]::GetEnvironmentVariable($src, "Process") }
        if (-not $c) { $c = (Get-Item "env:$src" -ErrorAction SilentlyContinue).Value }
        if ($c -and $c.Length -ge 20) { $env:XAI_API_KEY = $c; break }
    }
}
if (-not $env:OLLAMA_KEEP_ALIVE) { $env:OLLAMA_KEEP_ALIVE = "30m" }
if ($ShowTrace) { $env:ARIA_OUVRIER_VERBOSE = "1" }
if ($Quiet) { $env:ARIA_OUVRIER_VERBOSE = "" }

$py = Join-Path $Here "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv absent — .\install.ps1 puis .\setup-ouvrier.py" }

$pyArgs = @((Join-Path $Here "orchestrate_ouvrier.py"), "--message", $Message)
if ($ShowTrace) { $pyArgs += "--verbose" }
elseif ($Quiet) { $pyArgs += "--quiet" }
elseif ($env:ARIA_OUVRIER_VERBOSE -eq "1") { $pyArgs += "--verbose" }

& $py @pyArgs 2>&1