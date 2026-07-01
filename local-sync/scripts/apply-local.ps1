# Restaure sync/ vers le PC courant (apres git pull sur l'autre machine)
param(
    [switch]$SkipMetier,
    [switch]$SkipIde,
    [switch]$SkipVault,
    [switch]$Force,
    [string]$TotpCode,
    [switch]$ViaAria
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_paths.ps1")
. (Join-Path $PSScriptRoot "totp-gate.ps1")
Assert-TotpGate -Code $TotpCode -ViaAria:$ViaAria

$destData = if ($env:ARIA_LOCAL_DATA_DIR) { $env:ARIA_LOCAL_DATA_DIR } else { $script:DefaultAriaData }

Write-Host "=== aria-local-sync : apply ===" -ForegroundColor Cyan
Write-Host "Cible DATA_DIR : $destData"
Write-Host ""

# --- Coffre chiffre (cles API, production.env, etc.) ---
if (-not $SkipVault) {
    $gfv = Join-Path $script:SyncVault $script:VaultGfvName
    if (Test-Path $gfv) {
        $crypto = Join-Path $script:OperatorRoot "_vault-crypto.ps1"
        if (-not (Test-Path $crypto)) {
            Write-Host "[VAULT] Clone aria-vanguard puis relance apply-local" -ForegroundColor Red
            exit 1
        }
        . $crypto
        . (Join-Path $PSScriptRoot "daily-vault-key.ps1")
        Write-Host "[VAULT] Import coffre chiffre..." -ForegroundColor Cyan
        $imported = $false
        $candidates = @(Get-DailyVaultDecryptCandidates)
        if ($candidates.Count -eq 0) {
            $candidates = @(Get-VaultSyncPassphrase)
        }
        foreach ($pass in $candidates) {
            try {
                Import-EncryptedVaultArchive -InFile $gfv -Password $pass -Force:$Force
                $imported = $true
                break
            } catch {
                continue
            }
        }
        if (-not $imported) {
            throw "Impossible de dechiffrer le .gfv (cle du jour / hier / secret maitre ?)"
        }
        if (Test-DailyVaultMode) {
            Write-Host "[VAULT] Dechiffre (rotation quotidienne)" -ForegroundColor Green
        }
        Write-Host "[VAULT] Coffre restaure -> $(Get-GoldenFarVaultRoot)" -ForegroundColor Green
        $syncLocal = Join-Path $script:OperatorRoot "sync-local.ps1"
        if (Test-Path $syncLocal) {
            & $syncLocal
            Write-Host "[VAULT] backend/.env genere via sync-local.ps1" -ForegroundColor Green
        }
    } else {
        Write-Host "[VAULT] Pas de sync/vault/$($script:VaultGfvName) - lance collect-local sur l autre PC" -ForegroundColor Yellow
    }
}

if (-not (Test-Path $script:SyncAriaData)) {
    Write-Host "sync/aria-data vide - rien a restaurer (git pull fait ?)" -ForegroundColor Yellow
} elseif ((Get-ChildItem $script:SyncAriaData -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
    Write-Host "sync/aria-data sans fichiers - le PC source n avait peut-etre pas de DATA_DIR local" -ForegroundColor Yellow
} else {
    if ((Test-Path $destData) -and -not $Force) {
        $existing = (Get-ChildItem $destData -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
        if ($existing -gt 0) {
            Write-Host "DATA_DIR deja rempli ($existing fichiers). Utilise -Force pour ecraser." -ForegroundColor Red
            exit 1
        }
    }
    if (-not (Test-Path $destData)) { New-Item -ItemType Directory -Path $destData -Force | Out-Null }
    $n = Copy-TreeSafe -Source $script:SyncAriaData -Dest $destData
    Write-Host "[ARIA] $n fichier(s) -> $destData" -ForegroundColor Green
}

if (-not $SkipIde) {
    $cursorDst = Join-Path $env:USERPROFILE ".cursor\rules"
    if (Test-Path $script:SyncIdeCursor) {
        if (-not (Test-Path $cursorDst)) { New-Item -ItemType Directory -Path $cursorDst -Force | Out-Null }
        Copy-Item (Join-Path $script:SyncIdeCursor "*.md") $cursorDst -Force
        Write-Host "[IDE] Cursor rules restaurees" -ForegroundColor Green
    }
    $grokDst = Join-Path $env:USERPROFILE ".grok\rules"
    if (Test-Path $script:SyncIdeGrok) {
        if (-not (Test-Path $grokDst)) { New-Item -ItemType Directory -Path $grokDst -Force | Out-Null }
        Copy-Item (Join-Path $script:SyncIdeGrok "*.md") $grokDst -Force
        Write-Host "[IDE] Grok rules restaurees" -ForegroundColor Green
    }
}

if (-not $SkipMetier) {
    $ddcSrc = $script:SyncMetierDdc
    if (Test-Path $ddcSrc) {
        $dl = Join-Path $env:USERPROFILE "Downloads"
        Get-ChildItem $ddcSrc -File | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $dl $_.Name) -Force
            Write-Host "[METIER] $($_.Name) -> Downloads" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Verification finale :" -ForegroundColor Yellow
Write-Host "  cd %USERPROFILE%\projets\aria-vanguard\operator"
Write-Host "  .\check-aria-status.ps1"