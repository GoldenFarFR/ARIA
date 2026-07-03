# Liste les fichiers en attente dans download/ (hors processed, rejected, scripts)
param(
    [string]$Root = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)

$skip = @('processed', 'rejected', 'README.md', 'INBOX-STATE.json', 'triage-inbox.ps1', '.gitignore')
$pending = Get-ChildItem -Path $Root -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notin $skip }

if (-not $pending) {
    Write-Host "INBOX: vide"
    exit 0
}

Write-Host "INBOX: $($pending.Count) fichier(s)"
$pending | ForEach-Object {
    $kind = if ($_.Extension -eq '.url') { 'REJECT-url' } else { $_.Extension }
    Write-Host "  - $($_.Name) ($kind, $([math]::Round($_.Length/1KB, 1)) Ko)"
}