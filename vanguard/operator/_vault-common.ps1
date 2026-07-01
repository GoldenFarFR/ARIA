# Coffre local GoldenFar — hors Git, hors dossier projets/
# Chemin: %LOCALAPPDATA%\GoldenFar\vault (variable GOLDENFAR_VAULT)

function Get-GoldenFarVaultRoot {
    if ($script:GoldenFarVaultRoot) { return $script:GoldenFarVaultRoot }
    $fromEnv = [Environment]::GetEnvironmentVariable("GOLDENFAR_VAULT", "User")
    if ($fromEnv) {
        $script:GoldenFarVaultRoot = $fromEnv.TrimEnd('\')
        return $script:GoldenFarVaultRoot
    }
    $script:GoldenFarVaultRoot = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
    return $script:GoldenFarVaultRoot
}

function Initialize-GoldenFarVault {
    $vault = Get-GoldenFarVaultRoot
    foreach ($sub in @("", "keys", "stripe")) {
        $p = if ($sub) { Join-Path $vault $sub } else { $vault }
        if (-not (Test-Path $p)) {
            New-Item -ItemType Directory -Path $p -Force | Out-Null
        }
    }
    try {
        $user = "$env:USERDOMAIN\$env:USERNAME"
        icacls $vault /inheritance:r /grant:r "${user}:(OI)(CI)F" 2>$null | Out-Null
    } catch { }
    try { attrib +h $vault 2>$null | Out-Null } catch { }
    [Environment]::SetEnvironmentVariable("GOLDENFAR_VAULT", $vault, "User")
    $readme = Join-Path $vault "LISEZMOI.txt"
    if (-not (Test-Path $readme)) {
        @(
            "Coffre GoldenFar - NE PAS synchroniser ni committer."
            "Scripts operateur: projets\aria-vanguard\operator"
            "Variable: GOLDENFAR_VAULT=$vault"
        ) | Set-Content -Path $readme -Encoding UTF8
    }
    return $vault
}

function Resolve-VaultOrLegacyPath {
    param(
        [string]$ScriptsRoot,
        [string]$VaultRelative,
        [string]$LegacyLeaf
    )
    $vaultPath = Join-Path (Get-GoldenFarVaultRoot) $VaultRelative
    if (Test-Path $vaultPath) { return $vaultPath }
    if ($ScriptsRoot -and $LegacyLeaf) {
        $legacy = Join-Path $ScriptsRoot $LegacyLeaf
        if (Test-Path $legacy) { return $legacy }
    }
    return $vaultPath
}

function Get-ProductionEnvPath {
    param([string]$ScriptsRoot = "")
    Resolve-VaultOrLegacyPath -ScriptsRoot $ScriptsRoot -VaultRelative "production.env" -LegacyLeaf "production.env"
}

function Get-LocalEnvPath {
    param([string]$ScriptsRoot = "")
    Resolve-VaultOrLegacyPath -ScriptsRoot $ScriptsRoot -VaultRelative "local.env" -LegacyLeaf "local.env"
}

function Get-VanguardEnvPath {
    param([string]$ScriptsRoot = "")
    Resolve-VaultOrLegacyPath -ScriptsRoot $ScriptsRoot -VaultRelative "vanguard.env" -LegacyLeaf "vanguard.env"
}

function Get-RenderApiKeyPath {
    param([string]$ScriptsRoot = "")
    Resolve-VaultOrLegacyPath -ScriptsRoot $ScriptsRoot -VaultRelative "keys\render.api-key" -LegacyLeaf ".render-api-key"
}

function Get-IonosApiKeyPath {
    param([string]$ScriptsRoot = "")
    Resolve-VaultOrLegacyPath -ScriptsRoot $ScriptsRoot -VaultRelative "keys\ionos.api-key" -LegacyLeaf ".ionos-api-key"
}

function Get-StripeRecoveryCodesPath {
    Join-Path (Get-GoldenFarVaultRoot) "stripe\recovery-codes.txt"
}