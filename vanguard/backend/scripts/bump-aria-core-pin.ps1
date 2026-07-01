# Bump aria-core git pin in requirements.txt after pushing aria-sandbox
param(
    [string]$AriaSandboxRoot = "$env:USERPROFILE\projets\aria-sandbox"
)

$ErrorActionPreference = "Stop"
$sha = git -C $AriaSandboxRoot rev-parse HEAD
$req = Join-Path $PSScriptRoot "..\requirements.txt"
$text = Get-Content $req -Raw -Encoding UTF8
$pattern = 'aria-core @ git\+https://github\.com/GoldenFarFR/aria-sandbox\.git@[a-f0-9]+#subdirectory=packages/aria-core'
$replacement = "aria-core @ git+https://github.com/GoldenFarFR/aria-sandbox.git@${sha}#subdirectory=packages/aria-core"
if ($text -notmatch $pattern) {
    throw "aria-core pin line not found in requirements.txt"
}
$new = $text -replace $pattern, $replacement
Set-Content $req $new -Encoding UTF8 -NoNewline
$buildFile = Join-Path $AriaSandboxRoot "packages\aria-core\src\aria_core\_build.py"
$short = $sha.Substring(0, 7)
Set-Content $buildFile "# Auto-updated by aria-vanguard/backend/scripts/bump-aria-core-pin.ps1`nARIA_CORE_BUILD = `"$short`"`n" -Encoding UTF8 -NoNewline
Write-Host "Pinned aria-core to $sha (build $short)"