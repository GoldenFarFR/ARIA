# Checklist nouveau PC — clone repos + audit (pas de secrets generes)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Projets = Join-Path $env:USERPROFILE "projets"

Write-Host "=== GoldenFar — setup nouveau PC ===" -ForegroundColor Cyan
Write-Host ""

$repos = @(
    @{ name = "collegue-memoire"; url = "https://github.com/GoldenFarFR/collegue-memoire.git" }
    @{ name = "aria-skills"; url = "https://github.com/GoldenFarFR/aria-skills.git" }
    @{ name = "aria-sandbox"; url = "https://github.com/GoldenFarFR/aria-sandbox.git" }
    @{ name = "aria-vanguard"; url = "https://github.com/GoldenFarFR/aria-vanguard.git" }
    @{ name = "aria-local-sync"; url = "https://github.com/GoldenFarFR/aria-local-sync.git" }
)

if (-not (Test-Path $Projets)) { New-Item -ItemType Directory -Path $Projets | Out-Null }

foreach ($r in $repos) {
    $path = Join-Path $Projets $r.name
    if (Test-Path (Join-Path $path ".git")) {
        Write-Host "[OK] $($r.name) deja clone" -ForegroundColor Green
    } else {
        Write-Host "[CLONE] $($r.name)..." -ForegroundColor Yellow
        git clone $r.url $path
    }
}

$skillsInstall = Join-Path $Projets "aria-skills\scripts\install.ps1"
if (Test-Path $skillsInstall) {
    Write-Host ""
    Write-Host "Installation skills Grok/Cursor..." -ForegroundColor Cyan
    & $skillsInstall
}

$rulesSrc = Join-Path $Projets "collegue-memoire\.cursor\rules"
$rulesDst = Join-Path $env:USERPROFILE ".cursor\rules"
if (Test-Path $rulesSrc) {
    if (-not (Test-Path $rulesDst)) { New-Item -ItemType Directory -Path $rulesDst | Out-Null }
    Copy-Item (Join-Path $rulesSrc "*.md") $rulesDst -Force
    Write-Host "[OK] Regles Cursor copiees" -ForegroundColor Green
}

. (Join-Path $Root "_vault-common.ps1")
$vault = Get-GoldenFarVaultRoot
$apiKeyPath = Get-RenderApiKeyPath -ScriptsRoot $Root
if (-not (Test-Path $apiKeyPath)) {
    Write-Host ""
    Write-Host "MANQUANT: cle Render dans le coffre" -ForegroundColor Red
    Write-Host "Coffre: $vault\keys\render.api-key"
    Write-Host "Sync multi-PC : Syncthing (voir MULTI-PC-VAULT.md)"
    Write-Host "Ou sauvegarde : .\import-vault-encrypted.ps1 -InFile <fichier.gfv>"
}

if (-not (Test-Path (Get-ProductionEnvPath -ScriptsRoot $Root))) {
    Write-Host "MANQUANT: production.env dans le coffre — .\pull-render.ps1 apres cle Render" -ForegroundColor Yellow
}

$localSync = Join-Path $Projets "aria-local-sync\scripts\apply-local.ps1"
if (Test-Path $localSync) {
    Write-Host ""
    Write-Host "Restauration etat local (memoire ARIA, IDE, metier)..." -ForegroundColor Cyan
    & $localSync
}

Write-Host ""
Write-Host "Etape finale : .\check-aria-status.ps1" -ForegroundColor Cyan
if (Test-Path (Join-Path $Root "check-aria-status.ps1")) {
    & (Join-Path $Root "check-aria-status.ps1")
}