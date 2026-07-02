# ACP events listener — append JSONL pour heartbeat acp_provider_poll
# SSOT pitfalls : mode legacy (v2 HTTP 500 Virtuals)

param(
    [ValidateSet("legacy", "v2", "all")]
    [string]$Mode = "legacy",
    [switch]$Background,
    [string]$OutputFile = "",
    [string]$LogDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutputFile) {
    $OutputFile = Join-Path $env:LOCALAPPDATA "GoldenFar\acp-events.jsonl"
}
if (-not $LogDir) {
    $LogDir = Join-Path $env:LOCALAPPDATA "GoldenFar"
}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
New-Item -ItemType File -Path $OutputFile -Force | Out-Null

$outLog = Join-Path $LogDir "acp-listener.log"
$errLog = Join-Path $LogDir "acp-listener.log.err"

$acpCmd = Join-Path $env:APPDATA "npm\acp.cmd"
if (-not (Test-Path $acpCmd)) {
    $acpCmd = (Get-Command acp -ErrorAction SilentlyContinue).Source
}
if (-not $acpCmd -or -not (Test-Path $acpCmd)) {
    Write-Host "acp-cli introuvable — npm i -g @virtuals-protocol/acp-cli" -ForegroundColor Red
    exit 1
}

$listenArgs = @("events", "listen", "--output", $OutputFile)
switch ($Mode) {
    "legacy" { $listenArgs += "--legacy" }
    "v2"     { }
    "all"    { $listenArgs += "--all" }
}

Write-Host "ACP listener — mode $Mode" -ForegroundColor Cyan
Write-Host "  -> $OutputFile" -ForegroundColor DarkGray

if ($Background) {
    $argStr = ($listenArgs | ForEach-Object {
        if ($_ -match '\s') { "`"$_`"" } else { $_ }
    }) -join " "
    $cmdLine = "`"$acpCmd`" $argStr"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmdLine `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -WindowStyle Hidden
    Write-Host "Listener lance en arriere-plan (logs : $outLog)" -ForegroundColor Green
    exit 0
}

& $acpCmd @listenArgs 2>&1 | Tee-Object -FilePath $outLog