# Active / desactive le pont ARIA <-> Cursor (mode auto dans l'IDE).
param(
    [switch]$Enable,
    [switch]$Disable,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_paths.ps1")

$flag = Join-Path $script:AriaCollegueRoot "sessions\.bridge-active"
$cursorRule = Join-Path $env:USERPROFILE ".cursor\rules\aria-cursor-bridge.md"

function Set-CursorRuleAlwaysApply([bool]$On) {
    if (-not (Test-Path $cursorRule)) {
        Write-Host "Regle Cursor absente: $cursorRule" -ForegroundColor Yellow
        return
    }
    $raw = Get-Content $cursorRule -Raw -Encoding UTF8
    if ($On) {
        $raw = $raw -replace 'alwaysApply:\s*false', 'alwaysApply: true'
    } else {
        $raw = $raw -replace 'alwaysApply:\s*true', 'alwaysApply: false'
    }
    [System.IO.File]::WriteAllText($cursorRule, $raw, [System.Text.UTF8Encoding]::new($false))
}

if ($Status -or (-not $Enable -and -not $Disable)) {
    $on = Test-Path $flag
    Write-Host ("Pont Cursor: " + $(if ($on) { "ACTIF" } else { "inactif" })) -ForegroundColor $(if ($on) { "Green" } else { "DarkGray" })
    if ($on) {
        Get-Content $flag -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
    exit 0
}

if ($Enable) {
    $stamp = (Get-Date).ToUniversalTime().ToString("o")
    @(
        "enabled_at=$stamp"
        "provider=vanguard"
        "api=http://127.0.0.1:8000"
    ) | Set-Content $flag -Encoding UTF8
    Set-CursorRuleAlwaysApply $true
    Write-Host "Pont ARIA-Cursor ACTIVE (mode auto Cursor)." -ForegroundColor Green
    Write-Host "Bot requis: uvicorn :8000 | Desactiver: aria-cursor-bridge-toggle.ps1 -Disable" -ForegroundColor DarkGray
    exit 0
}

if ($Disable) {
    if (Test-Path $flag) { Remove-Item $flag -Force }
    Set-CursorRuleAlwaysApply $false
    Write-Host "Pont ARIA-Cursor DESACTIVE." -ForegroundColor Yellow
    exit 0
}