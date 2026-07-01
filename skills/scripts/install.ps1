# Link aria-skills into ~/.grok/skills/, ~/.cursor/skills/, and rules (user scope)
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path $PSScriptRoot -Parent
$GrokHome = Join-Path $env:USERPROFILE ".grok"
$CursorHome = Join-Path $env:USERPROFILE ".cursor"

function Link-DirItems {
    param(
        [string]$SrcDir,
        [string]$DstDir,
        [string[]]$Exclude = @()
    )
    if (-not (Test-Path $SrcDir)) {
        Write-Error "Source not found: $SrcDir"
    }
    New-Item -ItemType Directory -Force -Path $DstDir | Out-Null
    Get-ChildItem -Path $SrcDir | ForEach-Object {
        if ($Exclude -contains $_.Name) { return }
        $target = Join-Path $DstDir $_.Name
        if (Test-Path $target) {
            Write-Host "Skip (exists): $($_.Name)"
            return
        }
        if ($_.PSIsContainer) {
            New-Item -ItemType Junction -Path $target -Target $_.FullName | Out-Null
        } else {
            New-Item -ItemType HardLink -Path $target -Target $_.FullName | Out-Null
        }
        Write-Host "Linked: $($_.Name)"
    }
}

$SkillsSrc = Join-Path $RepoRoot ".grok\skills"
$RulesSrc = Join-Path $RepoRoot ".grok\rules"
$SkillsDst = Join-Path $GrokHome "skills"
$RulesDst = Join-Path $GrokHome "rules"

Write-Host "=== Skills ==="
Get-ChildItem -Path $SkillsSrc -Directory | ForEach-Object {
    if ($_.Name -eq "_template") { return }
    $target = Join-Path $SkillsDst $_.Name
    if (Test-Path $target) {
        Write-Host "Skip (exists): $($_.Name)"
        return
    }
    New-Item -ItemType Junction -Path $target -Target $_.FullName | Out-Null
    Write-Host "Linked: $($_.Name)"
}

Write-Host "=== Rules Grok (always-on) ==="
Link-DirItems -SrcDir $RulesSrc -DstDir $RulesDst

$CursorSkillsDst = Join-Path $CursorHome "skills"
$CursorRulesDst = Join-Path $CursorHome "rules"
Write-Host "=== Skills Cursor ==="
Get-ChildItem -Path $SkillsSrc -Directory | ForEach-Object {
    if ($_.Name -eq "_template") { return }
    $target = Join-Path $CursorSkillsDst $_.Name
    if (Test-Path $target) {
        Write-Host "Skip (exists): $($_.Name)"
        return
    }
    New-Item -ItemType Junction -Path $target -Target $_.FullName | Out-Null
    Write-Host "Linked: $($_.Name)"
}

$AriaRoot = if ($env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT } else { Join-Path $env:USERPROFILE "GitHub-Repos\ARIA" }
$CollegueRules = Join-Path $AriaRoot "collegue-memoire\.cursor\rules"
Write-Host "=== Rules Cursor (collegue-memoire) ==="
foreach ($ruleName in @("journal-de-bord.md", "session-handoff.md", "collegue-memoire.md", "consommation-grok.md")) {
    $src = Join-Path $CollegueRules $ruleName
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $CursorRulesDst $ruleName
    Copy-Item -Path $src -Destination $dst -Force
    Write-Host "Copied: $ruleName"
}
Link-DirItems -SrcDir $RulesSrc -DstDir $CursorRulesDst -Exclude @("vision.md", "vision-enforcer.md")

Write-Host "Done. Restart Grok/Cursor or wait for auto-reload."