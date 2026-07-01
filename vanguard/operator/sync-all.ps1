# Sync local + Render en une commande

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

& (Join-Path $Root "sync-local.ps1")
& (Join-Path $Root "sync-render.ps1")
& (Join-Path $Root "sync-vanguard.ps1")