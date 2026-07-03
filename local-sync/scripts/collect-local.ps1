# Rassemble l'état local (hors secrets) dans sync/ - puis git push sur l'autre PC
param(
    [switch]$SkipMetier,
    [switch]$SkipIde,
    [switch]$SkipVault,
    [switch]$SkipTotp,   # rotation nocturne / tache planifiee (PC de confiance)
    [string]$TotpCode,   # code 6 chiffres (si terminal non interactif)
    [switch]$ViaAria     # demande le code via Telegram (ARIA)
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_paths.ps1")
. (Join-Path $PSScriptRoot "totp-gate.ps1")
if (-not $SkipTotp) { Assert-TotpGate -Code $TotpCode -ViaAria:$ViaAria }
Ensure-SyncDirs

$hostname = $env:COMPUTERNAME
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
$inventory = [ordered]@{
    machine   = $hostname
    collected = $ts
    user      = $env:USERNAME
    paths     = @{}
    files     = @()
    missing   = @()
    warnings  = @()
}

Write-Host "=== aria-local-sync : collect ===" -ForegroundColor Cyan
Write-Host "Machine : $hostname"
Write-Host "Cible   : $script:LocalSyncRoot"
Write-Host ""

# --- ARIA DATA_DIR ---
$srcData = Get-AriaDataSource
$inventory.paths["aria_data_source"] = $srcData
if ($srcData) {
    Write-Host "[ARIA] Source: $srcData" -ForegroundColor Green
    $n = Copy-TreeSafe -Source $srcData -Dest $script:SyncAriaData
    Write-Host "       -> $n fichier(s) dans sync/aria-data" -ForegroundColor DarkGray
} else {
    $inventory.missing += "aria_data_dir"
    Write-Host "[ARIA] DATA_DIR introuvable (dev local pas initialise ?)" -ForegroundColor Yellow
    Write-Host "       Defaut attendu: $script:DefaultAriaData" -ForegroundColor DarkGray
}

# --- Coffre : export chiffre (.gfv) -> sync/vault/ (toutes les cles, jamais en clair Git) ---
$vault = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
$inventory.paths["vault"] = $vault
if (-not $SkipVault) {
    if (Test-Path $vault) {
        $vaultFiles = Get-ChildItem $vault -Recurse -File -Force -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName.Substring($vault.Length).TrimStart('\') }
        $inventory.files += @{ area = "vault_inventory"; names = $vaultFiles }
        $crypto = Join-Path $script:OperatorRoot "_vault-crypto.ps1"
        if (-not (Test-Path $crypto)) {
            $inventory.warnings += "operator/_vault-crypto.ps1 introuvable - clone aria-vanguard"
            Write-Host "[VAULT] Script chiffrement absent - clone aria-vanguard" -ForegroundColor Red
        } else {
            . $crypto
            $gfvOut = Join-Path $script:SyncVault $script:VaultGfvName
            Write-Host "[VAULT] Export chiffre ($($vaultFiles.Count) fichiers)..." -ForegroundColor Cyan
            $pass = Get-VaultSyncPassphrase -Confirm
            Export-EncryptedVaultArchive -OutFile $gfvOut -Password $pass
            $sizeKb = [math]::Round((Get-Item $gfvOut).Length / 1KB, 1)
            Write-Host "[VAULT] -> sync/vault/$($script:VaultGfvName) (${sizeKb} KB, AES chiffre)" -ForegroundColor Green
            $inventory.files += @{ area = "vault_export"; file = $script:VaultGfvName; size_kb = $sizeKb }
        }
    } else {
        $inventory.missing += "vault"
        $inventory.warnings += "Coffre absent sur ce PC"
        Write-Host "[VAULT] Absent - rien a exporter" -ForegroundColor Yellow
    }
} else {
    Write-Host "[VAULT] Skip (-SkipVault)" -ForegroundColor DarkGray
}

# --- IDE Cursor ---
if (-not $SkipIde) {
    $cursorSrc = Join-Path $env:USERPROFILE ".cursor\rules"
    if (Test-Path $cursorSrc) {
        Remove-Item "$script:SyncIdeCursor\*" -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $cursorSrc "*.md") $script:SyncIdeCursor -Force
        Write-Host "[IDE] Cursor rules -> sync/ide/cursor-rules" -ForegroundColor Green
    } else {
        $inventory.missing += "cursor_rules"
    }

    $grokSrc = Join-Path $env:USERPROFILE ".grok\rules"
    if (Test-Path $grokSrc) {
        Remove-Item "$script:SyncIdeGrok\*" -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem $grokSrc -Filter "*.md" -File | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $script:SyncIdeGrok $_.Name) -Force
        }
        Write-Host "[IDE] Grok rules -> sync/ide/grok-rules" -ForegroundColor Green
    } else {
        $inventory.missing += "grok_rules"
    }
}

# --- Repos clones (inventaire) ---
$projets = Join-Path $env:USERPROFILE "projets"
if (Test-Path $projets) {
    $repos = Get-ChildItem $projets -Directory | ForEach-Object { $_.Name }
    $inventory.files += @{ area = "projets_clones"; names = $repos }
    Write-Host "[PROJETS] $($repos -join ', ')" -ForegroundColor DarkGray
}

# --- Sauvegarde inventaire machine ---
$machineDir = Join-Path $script:MachinesDir $hostname
if (-not (Test-Path $machineDir)) { New-Item -ItemType Directory -Path $machineDir -Force | Out-Null }
$invPath = Join-Path $machineDir "inventory.json"
$inventory | ConvertTo-Json -Depth 6 | Set-Content $invPath -Encoding UTF8
Write-Host ""
Write-Host "Inventaire : machines\$hostname\inventory.json" -ForegroundColor Cyan
Write-Host ""
Write-Host "Etape suivante :" -ForegroundColor Yellow
Write-Host "  git add -A"
Write-Host "  git status    # OK: .gfv chiffre ; PAS de .env en clair"
Write-Host "  git commit -m ""sync: collect $hostname"""
Write-Host "  git push"
Write-Host ""
Write-Host "Autre PC : git pull puis .\scripts\apply-local.ps1" -ForegroundColor Yellow