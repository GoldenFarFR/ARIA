# Lie les skills et rules aria-skills dans ~/.grok/ et ~/.cursor/
# Usage : .\scripts\link-aria-skills.ps1

$ErrorActionPreference = "Stop"
$SkillsRepo = Join-Path $env:USERPROFILE "projets\aria-skills"
$InstallScript = Join-Path $SkillsRepo "scripts\install.ps1"

if (-not (Test-Path $InstallScript)) {
    Write-Host "aria-skills absent. Clone d'abord :" -ForegroundColor Yellow
    Write-Host '  git clone https://github.com/GoldenFarFR/aria-skills.git "%USERPROFILE%\projets\aria-skills"'
    exit 1
}

& $InstallScript
Write-Host "Skills Aria lies. Relance Cursor/Grok si besoin." -ForegroundColor Green