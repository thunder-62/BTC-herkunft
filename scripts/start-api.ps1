# BTC-Herkunft - start FastAPI on 127.0.0.1:8000 (Windows PowerShell)
# Run from repo root or any cwd; script resolves its own location.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Repo on OneDrive: Git's auto-cleanup (gc) cannot delete folders OneDrive
# holds open and asks "Should I try again? (y/n)" dozens of times. Turn it off
# for this repo (harmless for a project this size). No-op without git/.git.
if ((Test-Path ".git") -and (Get-Command git -ErrorAction SilentlyContinue)) {
    git config gc.auto 0 2>$null
}

function Resolve-BtcOriginPython {
    if ($env:BTC_ORIGIN_PYTHON -and (Test-Path -LiteralPath $env:BTC_ORIGIN_PYTHON)) {
        return (Resolve-Path -LiteralPath $env:BTC_ORIGIN_PYTHON).Path
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    try {
        $pyOut = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $pyOut) {
            $p = ($pyOut | Select-Object -First 1).ToString().Trim()
            if ($p -and (Test-Path -LiteralPath $p)) { return $p }
        }
    } catch {}
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) {
        return $cmd.Source
    }
    throw "Python 3.12 not found. Install from python.org or set BTC_ORIGIN_PYTHON to python.exe"
}

Write-Host "BTC-Herkunft API - $Root" -ForegroundColor Cyan

$HostPython = Resolve-BtcOriginPython
Write-Host "Using Python: $HostPython" -ForegroundColor DarkGray

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    & $HostPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed with $HostPython" }
}

# pip install only when dependencies changed (pyproject.toml differs from the
# copy stored in .venv after the last install). The package is installed
# editable, so code changes need no reinstall. Saves the pip run on every start,
# especially in OneDrive folders. Force a reinstall: delete .venv\pyproject.installed.toml
$Stamp = ".venv\pyproject.installed.toml"
$NeedInstall = -not (Test-Path $Stamp) -or -not (Test-Path ".venv\Scripts\btc-origin.exe")
if (-not $NeedInstall) {
    $NeedInstall = (Get-FileHash "pyproject.toml").Hash -ne (Get-FileHash $Stamp).Hash
}
if ($NeedInstall) {
    Write-Host "Installing dependencies (pyproject.toml changed) ..."
    & .\.venv\Scripts\python.exe -m pip install -q -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { & .\.venv\Scripts\python.exe -m pip install -q -e . }
    if ($LASTEXITCODE -eq 0) { Copy-Item "pyproject.toml" $Stamp -Force }
} else {
    Write-Host "Dependencies up to date (skip pip install)" -ForegroundColor DarkGray
}

Write-Host "Starting btc-origin on http://127.0.0.1:8000 ..." -ForegroundColor Green
& .\.venv\Scripts\btc-origin.exe
if ($LASTEXITCODE -ne 0) {
    & .\.venv\Scripts\python.exe -m uvicorn btc_origin.api.app:app --host 127.0.0.1 --port 8000
}
