# Append message Sylvain + relais API ARIA (raccourci pont depuis PS ou Cursor terminal).
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Message
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_paths.ps1")

$bridgePath = Join-Path $script:AriaCollegueRoot "sessions\aria-cursor-bridge.jsonl"
if (-not (Test-Path $bridgePath)) {
    New-Item -ItemType File -Path $bridgePath -Force | Out-Null
    $init = @{
        id = "bridge-init"
        speaker = "Grok"
        text = "Pont ARIA-Cursor actif."
        at = (Get-Date).ToUniversalTime().ToString("o")
        kind = "system"
    } | ConvertTo-Json -Compress
    Set-Content $bridgePath $init -Encoding UTF8
}

$id = "sylvain-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$entry = @{
    id = $id
    speaker = "Sylvain"
    text = $Message
    at = (Get-Date).ToUniversalTime().ToString("o")
    kind = "message"
} | ConvertTo-Json -Compress

$tail = if (Test-Path $bridgePath) { [System.IO.File]::ReadAllText($bridgePath) } else { "" }
if ($tail.Length -gt 0 -and -not $tail.EndsWith("`n")) {
    [System.IO.File]::AppendAllText($bridgePath, "`n", [System.Text.UTF8Encoding]::new($false))
}
[System.IO.File]::AppendAllText($bridgePath, "$entry`n", [System.Text.UTF8Encoding]::new($false))

& (Join-Path $PSScriptRoot "aria-cursor-bridge.ps1")
$lines = Get-Content $bridgePath -Encoding UTF8 | Where-Object { $_.Trim() }
$last = ($lines[-1] | ConvertFrom-Json)
if ($last.speaker -eq "ARIA") {
    Write-Host ""
    Write-Host "ARIA :" -ForegroundColor DarkYellow
    Write-Host $last.text
}