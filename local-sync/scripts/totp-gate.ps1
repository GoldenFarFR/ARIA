# Verification TOTP (Google Authenticator / Bitwarden) - RFC 6238
# Usage: . .\totp-gate.ps1 ; Assert-TotpGate

function ConvertFrom-Base32 {
    param([string]$Encoded)
    $alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    $clean = ($Encoded -replace '\s|=', '').ToUpper()
    $bits = [System.Collections.Generic.List[bool]]::new()
    foreach ($c in $clean.ToCharArray()) {
        $idx = $alphabet.IndexOf($c)
        if ($idx -lt 0) { throw "Base32 invalide: $c" }
        for ($i = 4; $i -ge 0; $i--) { [void]$bits.Add(([bool](($idx -band (1 -shl $i)) -ne 0))) }
    }
    $bytes = [System.Collections.Generic.List[byte]]::new()
    for ($i = 0; $i + 8 -le $bits.Count; $i += 8) {
        $val = 0
        for ($j = 0; $j -lt 8; $j++) { if ($bits[$i + $j]) { $val = $val -bor (1 -shl (7 - $j)) } }
        [void]$bytes.Add([byte]$val)
    }
    return [byte[]]$bytes.ToArray()
}

function Get-TotpCode {
    param(
        [string]$SecretBase32,
        [int]$Digits = 6,
        [int]$Period = 30,
        [int64]$Timestamp = [math]::Floor([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() / $Period)
    )
    $key = ConvertFrom-Base32 $SecretBase32
    $msg = [BitConverter]::GetBytes([int64]$Timestamp)
    if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($msg) }
    $hmac = New-Object System.Security.Cryptography.HMACSHA1(,$key)
    $hash = $hmac.ComputeHash($msg)
    $offset = $hash[-1] -band 0x0f
    $code = (($hash[$offset] -band 0x7f) -shl 24) -bor (($hash[$offset + 1] -band 0xff) -shl 16) `
        -bor (($hash[$offset + 2] -band 0xff) -shl 8) -bor ($hash[$offset + 3] -band 0xff)
    $mod = [math]::Pow(10, $Digits)
    return ([int]($code % $mod)).ToString().PadLeft($Digits, '0')
}

function Test-TotpCode {
    param(
        [string]$SecretBase32,
        [string]$Code,
        [int]$Window = 1
    )
    $clean = ($Code -replace '\s', '')
    if ($clean -notmatch '^\d{6}$') { return $false }
    $step = [math]::Floor([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() / 30)
    for ($w = -$Window; $w -le $Window; $w++) {
        if ((Get-TotpCode -SecretBase32 $SecretBase32 -Timestamp ($step + $w)) -eq $clean) {
            return $true
        }
    }
    return $false
}

function Get-TotpSecretPath {
    Join-Path (Split-Path -Parent $PSScriptRoot) ".vault-totp-secret"
}

function Get-TotpSecret {
    if ($env:GOLDENFAR_VAULT_TOTP_SECRET) { return $env:GOLDENFAR_VAULT_TOTP_SECRET.Trim() }
    $path = Get-TotpSecretPath
    if (Test-Path $path) { return (Get-Content $path -Raw -Encoding UTF8).Trim() }
    return $null
}

function Assert-TotpGate {
    param(
        [string]$Code,
        [switch]$ViaAria  # obsolete - ignore (TOTP IDE uniquement)
    )
    $secret = Get-TotpSecret
    if (-not $secret) { return }
    if (-not $Code -and $env:GOLDENFAR_VAULT_TOTP_CODE) {
        $Code = $env:GOLDENFAR_VAULT_TOTP_CODE.Trim()
    }
    if ($ViaAria -or $env:GOLDENFAR_VAULT_TOTP_VIA_ARIA -eq "1") {
        Write-Warning "[TOTP] Telegram desactive - donne les 6 chiffres dans le chat Grok/Cursor (-TotpCode)."
    }
    if (-not $Code) {
        Write-Host "[TOTP] Code Google Authenticator requis (6 chiffres)" -ForegroundColor Cyan
        try {
            $Code = Read-Host "Code Authenticator"
        } catch {
            throw "[TOTP_REQUIRED] Demande le code a Sylvain dans le chat Grok/Cursor (6 chiffres GoldenFar Vault), puis relance avec -TotpCode ou `$env:GOLDENFAR_VAULT_TOTP_CODE."
        }
    }
    if (-not (Test-TotpCode -SecretBase32 $secret -Code $Code)) {
        throw "Code TOTP invalide ou expire (verifie heure du telephone)"
    }
    Write-Host "[TOTP] OK" -ForegroundColor Green
    if ($env:GOLDENFAR_VAULT_TOTP_CODE) {
        Remove-Item Env:\GOLDENFAR_VAULT_TOTP_CODE -ErrorAction SilentlyContinue
    }
}