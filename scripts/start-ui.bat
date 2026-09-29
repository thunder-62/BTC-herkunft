@echo off
REM BTC-Herkunft - start Vite UI on 127.0.0.1:5173 (Windows cmd)
setlocal
cd /d "%~dp0..\frontend"
echo BTC-Herkunft UI - %CD%

if not exist "node_modules\" (
  echo npm install ...
  call npm install
)

echo Starting Vite on http://127.0.0.1:5173 ...
call npm run dev
