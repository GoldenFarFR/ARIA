# Reduit les fenetres "Selectionner compte GitHub" dans Cursor
# Cause: git HTTPS + compte GitHub integre Cursor + beaucoup de pull/push
# Fix: SSH pour les remotes + un seul compte GitHub dans Cursor

$ErrorActionPreference = "Stop"
$prevEap = $ErrorActionPreference
$Projets = Join-Path $env:USERPROFILE "projets"

Write-Host "=== Fix popups GitHub (Cursor) ===" -ForegroundColor Cyan
Write-Host ""

# 1. Git utilise gh (deja fait si gh auth setup-git execute)
& gh auth setup-git 2>$null
Write-Host "[OK] git credential -> gh" -ForegroundColor Green

# 2. SSH
$key = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
if (-not (Test-Path $key)) {
    Write-Host "Generation cle SSH..." -ForegroundColor Yellow
    ssh-keygen -t ed25519 -C "sylvain.rio.fr@gmail.com" -f $key -N '""'
}
$pub = "$key.pub"
Write-Host "Cle publique : $pub"
Write-Host "Ajoute-la sur GitHub (une fois) :" -ForegroundColor Yellow
Write-Host "  gh auth refresh -h github.com -s admin:public_key"
Write-Host "  gh ssh-key add `"$pub`" --title `"PC GoldenFar`""
Write-Host "  ou GitHub > Settings > SSH keys > New"
Write-Host ""

$ErrorActionPreference = "Continue"
$test = (ssh -T git@github.com -o BatchMode=yes 2>&1 | Out-String)
$ErrorActionPreference = $prevEap
if ($test -notmatch 'successfully authenticated') {
    Write-Host "[ATTENTE] SSH pas encore actif sur GitHub - ajoute la cle puis relance ce script." -ForegroundColor Yellow
    exit 0
}

Write-Host "[OK] SSH GitHub actif" -ForegroundColor Green
$repos = Get-ChildItem $Projets -Directory | Where-Object { Test-Path (Join-Path $_.FullName ".git") }
foreach ($r in $repos) {
    $url = git -C $r.FullName remote get-url origin 2>$null
    if ($url -match '^https://github\.com/([^/]+)/(.+?)(?:\.git)?$') {
        $owner = $Matches[1]
        $name = $Matches[2] -replace '\.git$',''
        $ssh = "git@github.com:${owner}/${name}.git"
        git -C $r.FullName remote set-url origin $ssh
        Write-Host "[OK] $($r.Name) -> SSH"
    }
}

Write-Host ""
Write-Host "Dans Cursor : un seul compte GitHub connecte (GoldenFarFR)." -ForegroundColor Cyan
Write-Host "Cursor - Parametres - Comptes : deconnecter le 2e compte GitHub si present."