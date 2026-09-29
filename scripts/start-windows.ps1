# BTC-Herkunft - open API + UI in two new PowerShell windows
$ErrorActionPreference = "Stop"
$Scripts = $PSScriptRoot

# Start-Process joins -ArgumentList without quoting: paths with spaces
# (e.g. "C:\Users\Max Mustermann\...") must be quoted explicitly.
function Start-BtcOriginWindow([string]$ScriptName) {
    $path = Join-Path $Scripts $ScriptName
    Start-Process powershell -WorkingDirectory $Scripts -ArgumentList (
        '-NoExit -ExecutionPolicy Bypass -File "{0}"' -f $path
    )
}

Write-Host "Opening API and UI in separate windows ..." -ForegroundColor Cyan
Start-BtcOriginWindow "start-api.ps1"
Start-Sleep -Seconds 1
Start-BtcOriginWindow "start-ui.ps1"
Write-Host "API: http://127.0.0.1:8000/api/health" -ForegroundColor Green
Write-Host "UI:  http://127.0.0.1:5173" -ForegroundColor Green
