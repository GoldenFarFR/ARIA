# Branche aria-letta-integration.ps1 dans le profil PowerShell utilisateur
$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path $PSScriptRoot -Parent) "aria-paths.ps1")

$candidates = @(
    $PROFILE.CurrentUserCurrentHost,
    $PROFILE.CurrentUserAllHosts,
    (Join-Path $HOME "Documents\PowerShell\Microsoft.PowerShell_profile.ps1"),
    (Join-Path $HOME "Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1")
) | Where-Object { $_ } | Select-Object -Unique
$profilePath = ($candidates | Where-Object { Test-Path $_ } | Select-Object -First 1)
if (-not $profilePath) { $profilePath = $PROFILE.CurrentUserCurrentHost }
$profileDir = Split-Path $profilePath -Parent
if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir -Force | Out-Null }
if (-not (Test-Path $profilePath)) { New-Item -ItemType File -Path $profilePath -Force | Out-Null }

$marker = "# ARIA Letta v2.4 (monorepo GoldenFar)"
$repo = $script:AriaRepoRoot
$snippet = @"

$marker
`$__ariaLettaHook = Join-Path '$repo' 'scripts\PowerShell\aria-letta-integration.ps1'
if (Test-Path `$__ariaLettaHook) { . `$__ariaLettaHook }
"@

$content = Get-Content $profilePath -Raw -ErrorAction SilentlyContinue
if ($content -notmatch [regex]::Escape($marker)) {
    Add-Content -Path $profilePath -Value $snippet -Encoding UTF8
    Write-Host "Profil mis a jour : $profilePath" -ForegroundColor Green
} else {
    Write-Host "Profil deja branche ($marker)" -ForegroundColor DarkGray
}

# Commande /letta dans la boucle aria()
$lettaHandler = @'
        elseif ($S.StartsWith("/letta")) {
            $rest = $S.Substring(6).Trim()
            if ($rest -eq "" -or $rest -match '^(?i)status?$') {
                Get-AriaLettaStatus
                continue
            }
            $lvl = $null
            if ($rest -match '^(simple|moyen|complexe)\s+(.+)$') {
                $lvl = $Matches[1]; $rest = $Matches[2]
            }
            if ([string]::IsNullOrWhiteSpace($rest)) {
                Write-Host "Usage: /letta status | /letta [simple|moyen|complexe] <message>" -ForegroundColor Yellow
            } else {
                if ($lvl) { Invoke-AriaLetta -Niveau $lvl -Message $rest }
                else { Invoke-AriaLetta -Message $rest }
            }
        }
'@

$applyHandler = @'
        elseif ($S.StartsWith("/apply-lessons")) {
            $rest = ($S -replace '^/apply-lessons\s*', '').Trim()
            if ($rest -eq "" -or $rest -match '^(?i)list$') {
                Invoke-AriaApplyLessons -Action list
            } elseif ($rest -match '^(?i)approved$') {
                Invoke-AriaApplyLessons -Action approved
            } elseif ($rest -match '^(?i)approve\s+(\d+)$') {
                Invoke-AriaApplyLessons -Action approve -Index ([int]$Matches[1])
            } elseif ($rest -match '^(?i)apply\s+(\d+)$') {
                Invoke-AriaApplyLessons -Action apply -Index ([int]$Matches[1])
            } else {
                Write-Host "Usage: /apply-lessons list | approved | approve N | apply N" -ForegroundColor Yellow
            }
        }
'@

$content = Get-Content $profilePath -Raw
if ($content -notmatch 'StartsWith\("/apply-lessons"\)') {
    $needleApply = 'elseif ($S.StartsWith("/letta"))'
    if ($content -notmatch [regex]::Escape($needleApply)) {
        $needleApply2 = 'elseif ($S -eq "/clear") { $Global:ActiveContextText = ""; $Global:ActiveContextName = "Aucun"; dash }'
        if ($content -match [regex]::Escape($needleApply2)) {
            $content = $content.Replace($needleApply2, ($needleApply2 + "`n" + $applyHandler.TrimEnd()))
            [System.IO.File]::WriteAllText($profilePath, $content)
            Write-Host "Commande /apply-lessons ajoutee dans aria()" -ForegroundColor Green
        } else {
            Write-Host "Structure aria() inattendue — ajoute /apply-lessons manuellement" -ForegroundColor Yellow
        }
    } else {
        $idx = $content.IndexOf($needleApply)
        $content = $content.Insert($idx, $applyHandler.TrimEnd() + "`n        ")
        [System.IO.File]::WriteAllText($profilePath, $content)
        Write-Host "Commande /apply-lessons ajoutee dans aria()" -ForegroundColor Green
    }
}

$content = Get-Content $profilePath -Raw
if ($content -notmatch 'StartsWith\("/letta"\)') {
    $needle = 'elseif ($S -eq "/clear") { $Global:ActiveContextText = ""; $Global:ActiveContextName = "Aucun"; dash }'
    if ($content -notmatch [regex]::Escape($needle)) {
        Write-Host "Structure aria() inattendue — ajoute /letta manuellement" -ForegroundColor Yellow
    } else {
        $content = $content.Replace($needle, ($needle + "`n" + $lettaHandler.TrimEnd()))
        [System.IO.File]::WriteAllText($profilePath, $content)
        Write-Host "Commande /letta ajoutee dans aria()" -ForegroundColor Green
    }
}

Write-Host "Recharge :  . `$PROFILE   ou   rc" -ForegroundColor Cyan