# Cle de chiffrement quotidienne — derivee d'un secret maitre (change chaque jour UTC)
# Un attaquant avec un .gfv d'hier ne peut pas le ouvrir avec la cle d'aujourd'hui.

function Get-VaultMasterPath {
    Join-Path (Split-Path -Parent $PSScriptRoot) ".vault-master-secret"
}

function Get-VaultMaster {
    if ($env:GOLDENFAR_VAULT_MASTER) { return $env:GOLDENFAR_VAULT_MASTER.Trim() }
    $path = Get-VaultMasterPath
    if (Test-Path $path) { return (Get-Content $path -Raw -Encoding UTF8).Trim() }
    return $null
}

function Get-DailyVaultPassphrasePlain {
    param(
        [string]$Master,
        [datetime]$DateUtc
    )
    $day = $DateUtc.ToUniversalTime().ToString("yyyy-MM-dd")
    $label = "goldenfar-vault-daily-v1:$day"
    $keyBytes = [Text.Encoding]::UTF8.GetBytes($Master)
    $hmac = New-Object System.Security.Cryptography.HMACSHA256(,$keyBytes)
    $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($label))
    return [Convert]::ToBase64String($hash).Substring(0, 32)
}

function Get-DailyVaultSecurePass {
    param([datetime]$DateUtc)
    $master = Get-VaultMaster
    if (-not $master) { return $null }
    $plain = Get-DailyVaultPassphrasePlain -Master $master -DateUtc $DateUtc
    return (ConvertTo-SecureString $plain -AsPlainText -Force)
}

function Get-DailyVaultDecryptCandidates {
    $master = Get-VaultMaster
    if (-not $master) { return @() }
    $today = [datetime]::UtcNow.Date
    $candidates = @()
    foreach ($offset in 0, -1, -2) {
        $day = $today.AddDays($offset)
        $candidates += Get-DailyVaultSecurePass -DateUtc $day
    }
    return $candidates
}

function Test-DailyVaultMode {
    return [bool](Get-VaultMaster)
}

function Show-DailyVaultStatus {
    if (-not (Test-DailyVaultMode)) {
        Write-Host "Mode: mot de passe statique (legacy)" -ForegroundColor DarkGray
        return
    }
    $today = Get-DailyVaultPassphrasePlain -Master (Get-VaultMaster) -DateUtc ([datetime]::UtcNow)
    Write-Host "Mode: rotation quotidienne ACTIVE" -ForegroundColor Green
    Write-Host "Date UTC: $([datetime]::UtcNow.ToString('yyyy-MM-dd'))"
    Write-Host "Cle du jour (auto - ne pas noter a la main): $($today.Substring(0,8))..." -ForegroundColor DarkGray
}