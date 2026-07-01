# Affiche l'etat local sans copier
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_paths.ps1")

Write-Host "=== Inventaire local (sans collect) ===" -ForegroundColor Cyan
Write-Host "Machine : $env:COMPUTERNAME"
Write-Host ""

$src = Get-AriaDataSource
Write-Host "ARIA DATA_DIR : $(if ($src) { $src } else { 'ABSENT' })"
if ($src) {
    Get-ChildItem $src -Recurse -File -ErrorAction SilentlyContinue |
        Select-Object -First 25 @{N='Rel';E={$_.FullName.Substring($src.Length)}} |
        Format-Table -AutoSize
}

$vault = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
Write-Host "Coffre : $(if (Test-Path $vault) { $vault } else { 'ABSENT' })"
if (Test-Path $vault) {
    Get-ChildItem $vault -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName.Substring($vault.Length) }
}

$synced = Join-Path $script:LocalSyncRoot "sync\aria-data"
if (Test-Path $synced) {
    $c = (Get-ChildItem $synced -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host ""
    Write-Host "Deja dans repo (sync/aria-data) : $c fichier(s)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Collect : .\scripts\collect-local.ps1" -ForegroundColor Yellow