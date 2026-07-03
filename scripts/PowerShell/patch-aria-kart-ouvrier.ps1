# Patch aria() dans le profil PS : défaut = Ouvrier Letta (plus aria-core pitch)
$ErrorActionPreference = "Stop"
. (Join-Path (Split-Path $PSScriptRoot -Parent) "aria-paths.ps1")

$profilePath = "$HOME\Documents\PowerShell\Microsoft.PowerShell_profile.ps1"
if (-not (Test-Path $profilePath)) {
    throw "Profil introuvable: $profilePath"
}

$content = Get-Content $profilePath -Raw

$old = @'
            elseif ($S.ToLower() -match ($agentTriggers = "exécute|crée|modifie|écris|update|lis |lit |affiche|montre|contenu|read|list|liste|supprime|rename|move|copy|fichier|dossier|powershell|commande|\bagent\b|ouvre|ferme")) {
                Write-Host "`n⚙️ Mode Agent détecté → Lancement de l'orchestrateur v3..." -ForegroundColor Magenta
                Invoke-AriaAgent -TaskPrompt $S
            }
            elseif ($Intent -eq "GROK") { Invoke-Grok -Prompt $S }
            elseif ($Intent -eq "GROQ") { Invoke-Groq -Prompt $S }
            elseif ($Intent -eq "CHAT") { Invoke-Ollama -Prompt $S }
            else { Invoke-AriaBrain -Message $S }
'@

$new = @'
            elseif ($S.StartsWith("/cerveau") -or $S.StartsWith("/brain")) {
                $rest = ($S -replace '^(?i)/(cerveau|brain)\s*', '').Trim()
                if ($rest) { Invoke-AriaBrain -Message $rest }
                else { Write-Host "Usage: /cerveau <message>" -ForegroundColor Yellow }
            }
            else { Invoke-AriaKartDefault -Message $S -Intent $Intent }
'@

if ($content -notmatch [regex]::Escape($old.Trim())) {
    Write-Host "Bloc routing introuvable — profil deja patche ou structure differente" -ForegroundColor Yellow
    exit 1
}

$content = $content.Replace($old.Trim(), $new.Trim())
$content = $content -replace 'ARIA v2\.5 \(cerveau\)', 'ARIA v2.6 (ouvrier Letta defaut)'

[System.IO.File]::WriteAllText($profilePath, $content)
Write-Host "Profil patche : defaut = Invoke-AriaKartDefault (Ouvrier Letta)" -ForegroundColor Green
Write-Host "Recharge : rc" -ForegroundColor Cyan