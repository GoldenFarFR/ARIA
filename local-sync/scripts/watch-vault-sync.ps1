# Surveille le coffre - relance collect-local quand une cle change (PC source)
# Usage: .\watch-vault-sync.ps1
# Prerequis: GOLDENFAR_VAULT_SYNC_PASS en variable utilisateur Windows

param([int]$PollSeconds = 30)

$ErrorActionPreference = "Stop"
$vault = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
if (-not (Test-Path $vault)) { throw "Coffre introuvable: $vault" }

$collect = Join-Path $PSScriptRoot "collect-local.ps1"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Get-VaultFingerprint {
    $parts = Get-ChildItem $vault -Recurse -File -Force -ErrorAction SilentlyContinue |
        Sort-Object FullName |
        ForEach-Object { "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)" }
    $raw = $parts -join ";"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($raw))).Replace("-", "")
}

if (-not $env:GOLDENFAR_VAULT_SYNC_PASS) {
    Write-Host "Astuce: GOLDENFAR_VAULT_SYNC_PASS en variable utilisateur = sync auto sans prompt." -ForegroundColor Yellow
}

Write-Host "=== watch-vault-sync ===" -ForegroundColor Cyan
Write-Host "Coffre: $vault (poll ${PollSeconds}s)"
Write-Host "Ctrl+C pour arreter"
Write-Host ""

$last = Get-VaultFingerprint
while ($true) {
    Start-Sleep -Seconds $PollSeconds
    $now = Get-VaultFingerprint
    if ($now -eq $last) { continue }
    $last = $now
    Write-Host "[$(Get-Date -Format HH:mm:ss)] Coffre modifie - collect..." -ForegroundColor Yellow
    & $collect -SkipMetier -SkipIde
    . (Join-Path $PSScriptRoot "git-operator-session.ps1")
    $msg = "sync: auto vault $(Get-Date -Format yyyy-MM-ddTHHmm)"
    $r = Invoke-GoldenFarGitPush -Path $repoRoot -Message $msg -Add @("sync/vault/goldenfar-vault.gfv", "machines/") -SkipGitGate
    if ($r.pushed) {
        Write-Host "[OK] Pousse sur GitHub" -ForegroundColor Green
    }
}