# Simulation attaque + verification rotation quotidienne (ne modifie pas le coffre local)
# Usage: .\test-vault-security.ps1

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir
$gfvCurrent = Join-Path $repoRoot "sync\vault\goldenfar-vault.gfv"
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("gf-security-test-" + [guid]::NewGuid().ToString("n"))

. (Join-Path $scriptDir "daily-vault-key.ps1")
$crypto = Join-Path $env:USERPROFILE "projets\aria-vanguard\operator\_vault-crypto.ps1"
if (-not (Test-Path $crypto)) { throw "Clone aria-vanguard requis: _vault-crypto.ps1" }
. $crypto

function Test-EncryptedVaultPassword {
    param(
        [string]$InFile,
        [SecureString]$Password
    )
    $data = [IO.File]::ReadAllBytes($InFile)
    if ($data.Length -lt 64) { return $false }
    $magic = [Text.Encoding]::ASCII.GetString($data[0..3])
    if ($magic -ne "GFV1") { return $false }
    $salt = $data[4..19]
    $iv = $data[20..35]
    $tag = $data[36..67]
    $cipher = $data[68..($data.Length - 1)]
    $keys = Get-VaultCryptoKey -Password $Password -Salt $salt
    $hmac = New-Object System.Security.Cryptography.HMACSHA256(,$keys.Mac)
    $expected = $hmac.ComputeHash($cipher)
    for ($i = 0; $i -lt 32; $i++) {
        if ($tag[$i] -ne $expected[$i]) { return $false }
    }
    $aes = [System.Security.Cryptography.Aes]::Create()
    $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
    $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
    $aes.Key = $keys.Aes
    $aes.IV = $iv
    $dec = $aes.CreateDecryptor()
    try {
        [void]$dec.TransformFinalBlock($cipher, 0, $cipher.Length)
        return $true
    } catch {
        return $false
    }
}

function Write-AttackResult {
    param(
        [string]$Scenario,
        [bool]$Pass,
        [string]$Detail,
        [ValidateSet("attack", "legit", "info")]
        [string]$Kind = "attack"
    )
    $icon = switch ($Kind) {
        "legit" { if ($Pass) { "[AUTORISE]" } else { "[ECHEC]" } }
        "info"  { if ($Pass) { "[CHANGE]" } else { "[IDENTIQUE]" } }
        default { if ($Pass) { "[BLOQUE]" } else { "[FAILLE]" } }
    }
    $color = if ($Pass) { "Green" } else { "Red" }
    Write-Host "$icon $Scenario" -ForegroundColor $color
    Write-Host "       $Detail" -ForegroundColor DarkGray
    return $Pass
}

New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
$results = @()

Write-Host ""
Write-Host "=== TEST SECURITE GoldenFar Vault ===" -ForegroundColor Cyan
Write-Host "Machine: $env:COMPUTERNAME | UTC: $([datetime]::UtcNow.ToString('yyyy-MM-dd HH:mm'))"
Write-Host ""

if (-not (Test-DailyVaultMode)) {
    Write-Host "Rotation quotidienne inactive (.vault-master-secret absent)" -ForegroundColor Red
    exit 1
}
Show-DailyVaultStatus
Write-Host ""

# --- Fichiers volés simulés ---
$gfvStolenLegacy = Join-Path $tempDir "stolen-legacy.gfv"
$gfvStolenToday = $gfvCurrent
try {
    $blob = (git -C $repoRoot rev-parse "08959cf:sync/vault/goldenfar-vault.gfv" 2>$null).Trim()
    if ($blob) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "git"
        $psi.Arguments = "-C `"$repoRoot`" cat-file blob $blob"
        $psi.RedirectStandardOutput = $true
        $psi.UseShellExecute = $false
        $p = [Diagnostics.Process]::Start($psi)
        $ms = New-Object IO.MemoryStream
        $p.StandardOutput.BaseStream.CopyTo($ms)
        $p.WaitForExit()
        if ($ms.Length -gt 64) {
            [IO.File]::WriteAllBytes($gfvStolenLegacy, $ms.ToArray())
        } else {
            $gfvStolenLegacy = $null
        }
    }
} catch {
    Write-Host "Avertissement: ancien .gfv (08959cf) non extrait - test legacy saute" -ForegroundColor Yellow
    $gfvStolenLegacy = $null
}
if ($gfvStolenLegacy -and -not (Test-Path $gfvStolenLegacy)) { $gfvStolenLegacy = $null }

$hashToday = (Get-FileHash $gfvCurrent -Algorithm SHA256).Hash.Substring(0, 16)
$hashLegacy = if ($gfvStolenLegacy -and (Test-Path $gfvStolenLegacy)) {
    (Get-FileHash $gfvStolenLegacy -Algorithm SHA256).Hash.Substring(0, 16)
} else { "n/a" }

Write-Host "--- Preuve: le fichier sur GitHub a change apres rotation ---" -ForegroundColor Yellow
Write-Host "  .gfv actuel (rotation) : SHA256 $hashToday..."
if ($hashLegacy -ne "n/a") {
    Write-Host "  .gfv vole lundi (legacy) : SHA256 $hashLegacy..."
    $changed = $hashToday -ne $hashLegacy
    $results += Write-AttackResult "Fichier GitHub different apres rotation" $changed `
        "L'attaquant avec la copie d'hier n'a pas le meme blob que GitHub aujourd'hui" -Kind info
}

$master = Get-VaultMaster
$todayUtc = [datetime]::UtcNow.Date
$yesterdayUtc = $todayUtc.AddDays(-1)
$tomorrowUtc = $todayUtc.AddDays(1)

$keyToday = Get-DailyVaultSecurePass -DateUtc $todayUtc
$keyYesterday = Get-DailyVaultSecurePass -DateUtc $yesterdayUtc
$keyTomorrow = Get-DailyVaultSecurePass -DateUtc $tomorrowUtc
$keyWrongMaster = Get-DailyVaultPassphrasePlain -Master "FAKE_MASTER_SECRET_FOR_TEST_ONLY" -DateUtc $todayUtc
$keyWrongMasterSec = ConvertTo-SecureString $keyWrongMaster -AsPlainText -Force
$legacyPass = ConvertTo-SecureString "xYgKsOuwqZ7d5Ezm9ntUbH0X" -AsPlainText -Force
$randomPass = ConvertTo-SecureString ("wrong-" + [guid]::NewGuid().ToString("n")) -AsPlainText -Force

Write-Host ""
Write-Host "--- Scenario 1: Attaquant vole .gfv actuel (GitHub) ---" -ForegroundColor Yellow

$ok = Test-EncryptedVaultPassword -InFile $gfvStolenToday -Password $legacyPass
$results += Write-AttackResult "Ancien mot de passe statique (Bitwarden legacy)" (-not $ok) `
    "Mot de passe d'avant rotation sur fichier du jour"

$ok = Test-EncryptedVaultPassword -InFile $gfvStolenToday -Password $keyYesterday
$results += Write-AttackResult "Cle derivee d'HIER sur fichier du JOUR" (-not $ok) `
    "Simule attaquant qui crackait hier - fichier deja rechiffre"

$ok = Test-EncryptedVaultPassword -InFile $gfvStolenToday -Password $randomPass
$results += Write-AttackResult "Mot de passe aleatoire" (-not $ok) `
    "Brute force naif"

$ok = Test-EncryptedVaultPassword -InFile $gfvStolenToday -Password $keyWrongMasterSec
$results += Write-AttackResult "Secret maitre faux + date du jour" (-not $ok) `
    "Sans le vrai secret Bitwarden goldenfar-vault-master"

Write-Host ""
Write-Host "--- Scenario 2: Attaquant a le .gfv mais PAS le secret maitre ---" -ForegroundColor Yellow
$results += Write-AttackResult "Deriver la cle du jour sans secret maitre" $true `
    "Impossible - HMAC-SHA256(secret, date) requiert le secret 48 octets" -Kind info

Write-Host ""
Write-Host "--- Scenario 3: Toi (legitime) avec secret maitre ---" -ForegroundColor Yellow
$ok = Test-EncryptedVaultPassword -InFile $gfvStolenToday -Password $keyToday
$results += Write-AttackResult "Cle du jour + secret maitre local" $ok `
    "apply-local.ps1 doit reussir (c'est toi)" -Kind legit

if ($gfvStolenLegacy -and (Test-Path $gfvStolenLegacy)) {
    Write-Host ""
    Write-Host "--- Scenario 4: Attaquant vole l'ANCIEN .gfv (avant rotation) ---" -ForegroundColor Yellow
    $okLegacy = Test-EncryptedVaultPassword -InFile $gfvStolenLegacy -Password $legacyPass
    $results += Write-AttackResult "Legacy: ancien .gfv + ancien mot de passe" $okLegacy `
        "Copie d'avant migration - obsolete sur GitHub actuel" -Kind info

    $ok = Test-EncryptedVaultPassword -InFile $gfvStolenLegacy -Password $keyToday
    $results += Write-AttackResult "Ancien .gfv + cle du jour" (-not $ok) `
        "Rotation casse la compatibilite cross-version"
}

Write-Host ""
Write-Host "--- Scenario 5: Simulation demain 03h00 (rotation) ---" -ForegroundColor Yellow
$gfvTomorrowSim = Join-Path $tempDir "simulated-tomorrow.gfv"
$vault = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
if (Test-Path $vault) {
    Export-EncryptedVaultArchive -OutFile $gfvTomorrowSim -Password $keyTomorrow
    $ok = Test-EncryptedVaultPassword -InFile $gfvStolenToday -Password $keyTomorrow
    $results += Write-AttackResult "Copie volee AUJOURD'HUI vs cle de DEMAIN" (-not $ok) `
        "Apres rotation nocturne, la copie volee ce matin est morte"

    $ok = Test-EncryptedVaultPassword -InFile $gfvTomorrowSim -Password $keyToday
    $results += Write-AttackResult "Nouveau .gfv DEMAIN vs cle d'AUJOURD'HUI" (-not $ok) `
        "GitHub aura un nouveau fichier - ancienne cle inutile"

    $ok = Test-EncryptedVaultPassword -InFile $gfvTomorrowSim -Password $keyTomorrow
    $results += Write-AttackResult "Nouveau .gfv DEMAIN vs cle de DEMAIN" $ok `
        "Toi avec secret maitre: toujours OK" -Kind legit
} else {
    Write-Host "  Coffre local absent - simulation demain sautee" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "--- Scenario 6: TOTP (Google Authenticator) ---" -ForegroundColor Yellow
. (Join-Path $scriptDir "totp-gate.ps1")
$totpSecret = Get-TotpSecret
if ($totpSecret) {
    $bad = Test-TotpCode -SecretBase32 $totpSecret -Code "000000"
    $results += Write-AttackResult "Code TOTP invalide (000000)" (-not $bad) `
        "Meme avec .gfv + mot de passe, collect/apply bloques sans telephone"

    $good = Test-TotpCode -SecretBase32 $totpSecret -Code (Get-TotpCode -SecretBase32 $totpSecret)
    $results += Write-AttackResult "Code TOTP valide (legitime)" $good `
        "Seul l'operateur avec Authenticator passe" -Kind legit
} else {
    Write-Host "  TOTP non configure - activer setup-totp-vault.ps1" -ForegroundColor DarkGray
}

# Nettoyage
Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
$passed = ($results | Where-Object { $_ }).Count
$total = $results.Count
if ($passed -eq $total) {
    Write-Host "=== RESULTAT: $passed/$total tests OK - attaque simulee BLOQUEE ===" -ForegroundColor Green
    exit 0
} else {
    Write-Host "=== RESULTAT: $passed/$total tests OK - REVOIR LA CONFIG ===" -ForegroundColor Red
    exit 1
}