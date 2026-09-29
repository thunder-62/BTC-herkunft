# BTC-Herkunft - start Vite UI on 127.0.0.1:5173 (Windows PowerShell)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root "frontend")

Write-Host "BTC-Herkunft UI - $(Get-Location)" -ForegroundColor Cyan

if (-not (Test-Path "node_modules")) {
    Write-Host "npm install ..."
    npm install
}

Write-Host "Starting Vite on http://127.0.0.1:5173 ..." -ForegroundColor Green
npm run dev
