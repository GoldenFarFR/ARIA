# Sauvegarde chiffree du coffre (USB, mail a toi-meme, 2e PC sans Syncthing)
# Usage: .\export-vault-encrypted.ps1 [-OutFile Desktop\goldenfar-vault.gfv]

param(
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-crypto.ps1")

if (-not $OutFile) {
    $OutFile = Join-Path ([Environment]::GetFolderPath("Desktop")) ("goldenfar-vault-" + (Get-Date -Format "yyyy-MM-dd") + ".gfv")
}

Write-Host "=== Export coffre chiffre ===" -ForegroundColor Cyan
Write-Host "Cible: $OutFile"
Write-Host "Choisis un mot de passe fort (note-le dans Bitwarden)." -ForegroundColor Yellow
$pass = Read-Host "Mot de passe" -AsSecureString
$confirm = Read-Host "Confirme" -AsSecureString
$b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pass)
$b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($confirm)
try {
    if ([Runtime.InteropServices.Marshal]::PtrToStringAuto($b1) -ne [Runtime.InteropServices.Marshal]::PtrToStringAuto($b2)) {
        throw "Les mots de passe ne correspondent pas"
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
}

Export-EncryptedVaultArchive -OutFile $OutFile -Password $pass
Write-Host "[OK] Sauvegarde creee (ne pas laisser en clair sur OneDrive)." -ForegroundColor Green
Write-Host "Sur l'autre PC: .\import-vault-encrypted.ps1 -InFile <ce fichier>" -ForegroundColor DarkGray