# Chiffrement AES-CBC + HMAC du coffre (compatible PowerShell 5.1 / tous PC Windows)

$script:VaultCryptoRoot = $PSScriptRoot

function Get-VaultCryptoKey {
    param([SecureString]$Password, [byte[]]$Salt)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes($plain, $Salt, 200000)
    return @{
        Aes = $derive.GetBytes(32)
        Mac = $derive.GetBytes(32)
    }
}

function Export-EncryptedVaultArchive {
    param(
        [string]$OutFile,
        [SecureString]$Password
    )
    . (Join-Path $script:VaultCryptoRoot "_vault-common.ps1")
    $vault = Get-GoldenFarVaultRoot
    if (-not (Test-Path $vault)) { throw "Coffre introuvable: $vault" }

    $tempZip = Join-Path ([IO.Path]::GetTempPath()) ("goldenfar-vault-" + [guid]::NewGuid().ToString("n") + ".zip")
    try {
        Push-Location -LiteralPath $vault
        try {
            $items = @(Get-ChildItem -Force)
            if ($items.Count -eq 0) { throw "Coffre vide: $vault" }
            Compress-Archive -Path ($items.Name) -DestinationPath $tempZip -Force
        } finally {
            Pop-Location
        }
        if (-not (Test-Path -LiteralPath $tempZip)) { throw "Echec compression archive" }
        $payload = [IO.File]::ReadAllBytes($tempZip)
        $salt = New-Object byte[] 16
        (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($salt)
        $keys = Get-VaultCryptoKey -Password $Password -Salt $salt
        $iv = New-Object byte[] 16
        (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($iv)

        $aes = [System.Security.Cryptography.Aes]::Create()
        $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
        $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
        $aes.Key = $keys.Aes
        $aes.IV = $iv
        $enc = $aes.CreateEncryptor()
        $cipher = $enc.TransformFinalBlock($payload, 0, $payload.Length)

        $hmac = New-Object System.Security.Cryptography.HMACSHA256(,$keys.Mac)
        $tag = $hmac.ComputeHash($cipher)

        $magic = [Text.Encoding]::ASCII.GetBytes("GFV1")
        $out = New-Object System.Collections.Generic.List[byte]
        $out.AddRange($magic)
        $out.AddRange($salt)
        $out.AddRange($iv)
        $out.AddRange($tag)
        $out.AddRange($cipher)
        [IO.File]::WriteAllBytes($OutFile, $out.ToArray())
    } finally {
        if (Test-Path $tempZip) { Remove-Item $tempZip -Force }
    }
}

function Import-EncryptedVaultArchive {
    param(
        [string]$InFile,
        [SecureString]$Password,
        [switch]$Force
    )
    . (Join-Path $script:VaultCryptoRoot "_vault-common.ps1")
    $vault = Initialize-GoldenFarVault
    $data = [IO.File]::ReadAllBytes($InFile)
    if ($data.Length -lt 64) { throw "Fichier invalide ou corrompu" }
    $magic = [Text.Encoding]::ASCII.GetString($data[0..3])
    if ($magic -ne "GFV1") { throw "Format non reconnu (attendu GFV1)" }
    $salt = $data[4..19]
    $iv = $data[20..35]
    $tag = $data[36..67]
    $cipher = $data[68..($data.Length - 1)]
    $keys = Get-VaultCryptoKey -Password $Password -Salt $salt

    $hmac = New-Object System.Security.Cryptography.HMACSHA256(,$keys.Mac)
    $expected = $hmac.ComputeHash($cipher)
    $macOk = $true
    for ($i = 0; $i -lt 32; $i++) {
        if ($tag[$i] -ne $expected[$i]) { $macOk = $false; break }
    }
    if (-not $macOk) { throw "Mot de passe incorrect ou fichier altere" }

    $aes = [System.Security.Cryptography.Aes]::Create()
    $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
    $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
    $aes.Key = $keys.Aes
    $aes.IV = $iv
    $dec = $aes.CreateDecryptor()
    try {
        $plain = $dec.TransformFinalBlock($cipher, 0, $cipher.Length)
    } catch {
        throw "Mot de passe incorrect ou fichier altere"
    }

    $tempZip = Join-Path ([IO.Path]::GetTempPath()) ("goldenfar-restore-" + [guid]::NewGuid().ToString("n") + ".zip")
    $staging = $null
    try {
        [IO.File]::WriteAllBytes($tempZip, $plain)
        $staging = Join-Path ([IO.Path]::GetTempPath()) ("goldenfar-staging-" + [guid]::NewGuid().ToString("n"))
        Expand-Archive -Path $tempZip -DestinationPath $staging -Force
        if ((Get-ChildItem $vault -Force -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0 -and -not $Force) {
            throw "Coffre non vide. Relance avec -Force pour ecraser."
        }
        Get-ChildItem -LiteralPath $vault -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
        Get-ChildItem -LiteralPath $staging -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $vault -Recurse -Force
        }
    } finally {
        if (Test-Path $tempZip) { Remove-Item $tempZip -Force }
        if ($staging -and (Test-Path $staging)) { Remove-Item $staging -Recurse -Force }
    }
}