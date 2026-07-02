# Chemins communs collect / apply — aria-local-sync
$ErrorActionPreference = "Stop"

$script:LocalSyncRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$script:SyncAriaData = Join-Path $script:LocalSyncRoot "sync\aria-data"
$script:SyncIdeCursor = Join-Path $script:LocalSyncRoot "sync\ide\cursor-rules"
$script:SyncIdeGrok = Join-Path $script:LocalSyncRoot "sync\ide\grok-rules"
$script:SyncVault = Join-Path $script:LocalSyncRoot "sync\vault"
$script:VaultGfvName = "goldenfar-vault.gfv"
$script:MachinesDir = Join-Path $script:LocalSyncRoot "machines"
. (Resolve-Path (Join-Path $PSScriptRoot "..\..\scripts\aria-paths.ps1"))
$script:DefaultAriaData = $script:AriaDataDir
$script:OperatorRoot = $script:AriaOperatorRoot

function Get-VaultSyncPassphrase {
    param([switch]$Confirm)
    . (Join-Path $PSScriptRoot "daily-vault-key.ps1")
    if (Test-DailyVaultMode) {
        Show-DailyVaultStatus
        return (Get-DailyVaultSecurePass -DateUtc ([datetime]::UtcNow))
    }
    if ($env:GOLDENFAR_VAULT_SYNC_PASS) {
        return (ConvertTo-SecureString $env:GOLDENFAR_VAULT_SYNC_PASS -AsPlainText -Force)
    }
    $pass = Read-Host "Mot de passe coffre (meme sur les 2 PC - note Bitwarden)" -AsSecureString
    if ($Confirm) {
        $confirm = Read-Host "Confirme" -AsSecureString
        $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pass)
        $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($confirm)
        try {
            if ([Runtime.InteropServices.Marshal]::PtrToStringAuto($b1) -ne `
                [Runtime.InteropServices.Marshal]::PtrToStringAuto($b2)) {
                throw "Les mots de passe ne correspondent pas"
            }
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
        }
    }
    return $pass
}

function Get-AriaDataSource {
    if ($env:ARIA_LOCAL_DATA_DIR -and (Test-Path $env:ARIA_LOCAL_DATA_DIR)) {
        return (Resolve-Path $env:ARIA_LOCAL_DATA_DIR).Path
    }
    $vault = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
    $localEnv = Join-Path $vault "local.env"
    if (Test-Path $localEnv) {
        foreach ($line in Get-Content $localEnv -Encoding UTF8) {
            if ($line -match '^\s*DATA_DIR\s*=\s*(.+)\s*$') {
                $p = $Matches[1].Trim().Trim('"')
                if ($p -and (Test-Path $p)) { return (Resolve-Path $p).Path }
            }
        }
    }
    if (Test-Path $script:DefaultAriaData) {
        return (Resolve-Path $script:DefaultAriaData).Path
    }
    return $null
}

function Ensure-SyncDirs {
    foreach ($d in @(
        $script:SyncAriaData,
        $script:SyncIdeCursor,
        $script:SyncIdeGrok,
        $script:SyncVault,
        $script:MachinesDir
    )) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
}

function Copy-TreeSafe {
    param(
        [string]$Source,
        [string]$Dest,
        [string[]]$ExcludePatterns = @("*.env", "*api-key*", "auth.json")
    )
    if (-not (Test-Path $Source)) { return 0 }
    if (-not (Test-Path $Dest)) { New-Item -ItemType Directory -Path $Dest -Force | Out-Null }
    $count = 0
    Get-ChildItem -LiteralPath $Source -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($Source.Length).TrimStart('\')
        $skip = $false
        foreach ($pat in $ExcludePatterns) {
            if ($rel -like $pat -or $_.Name -like $pat) { $skip = $true; break }
        }
        if ($skip) { return }
        $target = Join-Path $Dest $rel
        $parent = Split-Path $target -Parent
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        $count++
    }
    return $count
}