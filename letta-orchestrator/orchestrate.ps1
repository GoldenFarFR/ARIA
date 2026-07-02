# Wrapper PowerShell — charge le coffre puis lance orchestrate.py
param(
    [ValidateSet("simple", "moyen", "complexe")]
    [string]$Niveau,
    [Parameter(Mandatory)]
    [string]$Message
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$VaultDir = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $k = $Matches[1]; $v = $Matches[2].Trim()
            if ($v -match '^"(.*)"$') { $v = $Matches[1] }
            if (-not [string]::IsNullOrWhiteSpace($v)) { Set-Item -Path "env:$k" -Value $v }
        }
    }
}

Import-DotEnv (Join-Path $VaultDir "local.env")
Import-DotEnv (Join-Path $VaultDir "production.env")
if (-not $env:XAI_API_KEY) {
    foreach ($src in @("GROK_API_KEY", "IMAGE_API_KEY")) {
        $candidate = [Environment]::GetEnvironmentVariable($src, "Process")
        if (-not $candidate) { $candidate = [Environment]::GetEnvironmentVariable($src, "User") }
        if ($candidate) { $env:XAI_API_KEY = $candidate; break }
    }
}

$py = Join-Path $Here "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv absent — lance .\install.ps1" }

$args = @((Join-Path $Here "orchestrate.py"), "--message", $Message)
if ($Niveau) { $args += @("--niveau", $Niveau) }
& $py @args