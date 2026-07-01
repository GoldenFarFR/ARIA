# Bootstrap 2e PC GoldenFar — guide interactif + verifications
# Usage: .\bootstrap-autre-pc.ps1 [-SkipApply] [-SkipNewPc]

param(
    [switch]$SkipApply,
    [switch]$SkipNewPc,
    [switch]$SkipHandoff
)

$ErrorActionPreference = "Stop"
$projets = Join-Path $env:USERPROFILE "projets"
$syncRoot = Join-Path $projets "aria-local-sync"
$scripts = Join-Path $syncRoot "scripts"

function Test-CommandExists {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "=== bootstrap-autre-pc ($env:COMPUTERNAME) ===" -ForegroundColor Cyan
Write-Host ""

$missing = @()
foreach ($cmd in @("git", "python", "node")) {
    if (-not (Test-CommandExists $cmd)) { $missing += $cmd }
}
if ($missing.Count -gt 0) {
    Write-Host "Manquant : $($missing -join ', ')" -ForegroundColor Red
    Write-Host "Installe : winget install Git.Git Python.Python.3.12 OpenJS.NodeJS.LTS"
    exit 1
}

if (-not (Test-Path $syncRoot)) {
    Write-Host "Clone aria-local-sync d'abord." -ForegroundColor Red
    exit 1
}

$master = Join-Path $syncRoot ".vault-master-secret"
$totp = Join-Path $syncRoot ".vault-totp-secret"
if (-not (Test-Path $master) -or -not (Test-Path $totp)) {
    Write-Host "[STOP] Secrets Bitwarden requis AVANT bootstrap." -ForegroundColor Red
    Write-Host "  Voir CHANGEMENT-PC-MAINTENANT.md etape 3"
    Write-Host "  .vault-master-secret  <- Bitwarden goldenfar-vault-master"
    Write-Host "  .vault-totp-secret    <- Bitwarden goldenfar-vault-totp"
    exit 1
}

$vanguard = Join-Path $projets "aria-vanguard"
if (-not (Test-Path (Join-Path $vanguard ".git"))) {
    Write-Host "[CLONE] aria-vanguard..." -ForegroundColor Cyan
    Push-Location $projets
    git clone https://github.com/GoldenFarFR/aria-vanguard.git
    Pop-Location
}

if (-not $SkipApply) {
    Write-Host "[APPLY] apply-local.ps1 (-TotpCode si gate)..." -ForegroundColor Cyan
    Push-Location $scripts
    try {
        & .\apply-local.ps1
    } catch {
        Write-Host "[ERR] apply-local : $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "      Verifie Bitwarden + git pull aria-local-sync"
    }
    Pop-Location
}

$vaultProd = Join-Path $env:LOCALAPPDATA "GoldenFar\vault\production.env"
if (Test-Path $vaultProd) {
    if (-not (Select-String -Path $vaultProd -Pattern "^ADMIN_API_SECRET=" -Quiet)) {
        Write-Host "[!] ADMIN_API_SECRET absent dans production.env" -ForegroundColor Yellow
        Write-Host "    Bitwarden goldenfar-admin-api -> production.env"
    }
}

if (-not $SkipNewPc) {
    $newPc = Join-Path $vanguard "operator\new-pc.ps1"
    if (Test-Path $newPc) {
        Write-Host "[NEW-PC] new-pc.ps1..." -ForegroundColor Cyan
        Push-Location (Split-Path $newPc)
        & .\new-pc.ps1
        Pop-Location
    }
}

$skillsInstall = Join-Path $projets "aria-skills\scripts\install.ps1"
if (Test-Path $skillsInstall) {
    Write-Host "[SKILLS] install.ps1..." -ForegroundColor Cyan
    Push-Location (Split-Path $skillsInstall)
    & .\install.ps1
    Pop-Location
}

$rulesSrc = Join-Path $projets "collegue-memoire\.cursor\rules"
$rulesDst = Join-Path $env:USERPROFILE ".cursor\rules"
if (Test-Path $rulesSrc) {
    New-Item -ItemType Directory -Path $rulesDst -Force | Out-Null
    Copy-Item (Join-Path $rulesSrc "*.md") $rulesDst -Force
    Write-Host "[OK] Regles Cursor copiees" -ForegroundColor Green
}

[Environment]::SetEnvironmentVariable("GOLDENFAR_VAULT_TOTP_VIA_ARIA", "0", "User")
Write-Host "[OK] GOLDENFAR_VAULT_TOTP_VIA_ARIA=0 (TOTP via agent Grok/Cursor)" -ForegroundColor Green

if (-not $SkipHandoff) {
    $handoff = Join-Path $scripts "session-handoff.ps1"
    if (Test-Path $handoff) {
        Write-Host "[HANDOFF] session-handoff.ps1..." -ForegroundColor Cyan
        Push-Location $scripts
        & .\session-handoff.ps1
        Pop-Location
    }
}

Write-Host ""
Write-Host "=== Prochaines etapes ===" -ForegroundColor Cyan
Write-Host "  1. .\simulate-interactive.ps1"
Write-Host "  2. cd aria-vanguard\operator ; .\check-aria-status.ps1"
Write-Host "  3. Ajouter $env:COMPUTERNAME dans security\github-trust.yaml known_machines"
Write-Host "  4. Lire CHANGEMENT-PC-MAINTENANT.md + SETUP-AUTRE-PC.md"
Write-Host "  5. Reconnecter Grok Build (auth locale)"
Write-Host ""
Write-Host "Guide rapide : $syncRoot\CHANGEMENT-PC-MAINTENANT.md" -ForegroundColor Green