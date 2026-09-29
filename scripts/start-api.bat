@echo off
REM BTC-Herkunft - start FastAPI on 127.0.0.1:8000 (Windows cmd)
setlocal EnableExtensions
cd /d "%~dp0.."

REM Repo on OneDrive: disable Git auto-cleanup (gc), which cannot delete folders
REM OneDrive holds open and asks "Should I try again? (y/n)" dozens of times.
if exist ".git" (
  where git >nul 2>&1 && git config gc.auto 0
)
echo BTC-Herkunft API - %CD%

REM Prefer explicit override, then Python 3.12 under LocalAppData,
REM then py -3.12 launcher, then where python.
set "HOST_PYTHON="
if defined BTC_ORIGIN_PYTHON if exist "%BTC_ORIGIN_PYTHON%" set "HOST_PYTHON=%BTC_ORIGIN_PYTHON%"
if not defined HOST_PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "HOST_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined HOST_PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "HOST_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined HOST_PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "HOST_PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined HOST_PYTHON if exist "C:\Python312\python.exe" set "HOST_PYTHON=C:\Python312\python.exe"
if not defined HOST_PYTHON (
  where py >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%I in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "HOST_PYTHON=%%I"
  )
)
if not defined HOST_PYTHON (
  for /f "delims=" %%I in ('where python 2^>nul') do (
    if not defined HOST_PYTHON set "HOST_PYTHON=%%I"
  )
)
if not defined HOST_PYTHON (
  echo ERROR: Python 3.12 python.exe not found.
  echo Install from python.org or set BTC_ORIGIN_PYTHON to the full path.
  echo Expected e.g. %%LOCALAPPDATA%%\Programs\Python\Python312\python.exe
  exit /b 1
)

echo Using Python: %HOST_PYTHON%

if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  "%HOST_PYTHON%" -m venv .venv
  if errorlevel 1 (
    echo ERROR: venv creation failed.
    exit /b 1
  )
)

REM pip install only when pyproject.toml changed since the last install (copy
REM kept in .venv). Editable install: code changes need no reinstall.
REM Force a reinstall: delete .venv\pyproject.installed.toml
set "NEED_INSTALL=1"
if exist ".venv\pyproject.installed.toml" if exist ".venv\Scripts\btc-origin.exe" (
  fc /b "pyproject.toml" ".venv\pyproject.installed.toml" >nul 2>&1 && set "NEED_INSTALL="
)
if defined NEED_INSTALL (
  echo Installing dependencies ^(pyproject.toml changed^) ...
  call .venv\Scripts\python.exe -m pip install -q -e ".[dev]"
  if errorlevel 1 call .venv\Scripts\python.exe -m pip install -q -e .
  if not errorlevel 1 copy /y "pyproject.toml" ".venv\pyproject.installed.toml" >nul
) else (
  echo Dependencies up to date ^(skip pip install^)
)

echo Starting btc-origin on http://127.0.0.1:8000 ...
if exist ".venv\Scripts\btc-origin.exe" (
  call .venv\Scripts\btc-origin.exe
) else (
  call .venv\Scripts\python.exe -m uvicorn btc_origin.api.app:app --host 127.0.0.1 --port 8000
)
