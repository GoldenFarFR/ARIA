# Menu rapide ACP v2 — statut + demarrage local
param([switch]$Start)

$repo = $env:ARIA_REPO_ROOT
if (-not $repo) { $repo = Join-Path $env:USERPROFILE "GitHub-Repos\ARIA" }
$op = Join-Path $repo "vanguard\operator"

Write-Host "ACP v2 — Aria Vanguard ZHC" -ForegroundColor Cyan
Write-Host "Repo : $repo"
Write-Host ""

if (Get-Command acp -ErrorAction SilentlyContinue) {
    acp agent list 2>&1 | Select-Object -First 5
} else {
    Write-Host "acp-cli absent — npm i -g @virtuals-protocol/acp-cli" -ForegroundColor Red
}

if ($Start) {
    & (Join-Path $op "start-acp-local.ps1")
} else {
    Write-Host ""
    Write-Host "Demarrer : .\prepare-acp-v2-integration.ps1 -Start" -ForegroundColor Yellow
    Write-Host "Ou       : $op\start-acp-local.ps1" -ForegroundColor Yellow
}