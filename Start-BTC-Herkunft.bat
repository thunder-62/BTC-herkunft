@echo off
REM BTC-Herkunft starten - einfach doppelklicken.
REM Oeffnet zwei Fenster (Server und Oberflaeche) und danach den Browser.
setlocal EnableExtensions
cd /d "%~dp0"
title BTC-Herkunft - Start

echo.
echo  ================================================
echo   BTC-Herkunft wird gestartet ...
echo  ================================================
echo.

where node >nul 2>&1
if errorlevel 1 (
  echo  FEHLER: Node.js ist nicht installiert.
  echo  Bitte zuerst Node.js installieren - siehe ANLEITUNG.md, Schritt 2.
  echo  Die Download-Seite wird jetzt geoeffnet.
  start "" https://nodejs.org/de
  echo.
  pause
  exit /b 1
)

start "BTC-Herkunft Server - bitte offen lassen" cmd /k "scripts\start-api.bat"
start "BTC-Herkunft Oberflaeche - bitte offen lassen" cmd /k "scripts\start-ui.bat"

echo  Zwei neue Fenster sind aufgegangen - bitte beide offen lassen.
echo  Beim ersten Start werden Programmteile geladen: 5 bis 10 Minuten.
echo  Der Browser oeffnet sich automatisch, sobald alles bereit ist.
echo.

set /a TRIES=0
:WAIT
timeout /t 3 /nobreak >nul
set /a TRIES+=1
curl.exe -s -o nul http://127.0.0.1:8000/api/health
if errorlevel 1 goto NOTYET
curl.exe -s -o nul http://127.0.0.1:5173/
if errorlevel 1 goto NOTYET
goto OPEN
:NOTYET
if %TRIES% LSS 300 goto WAIT
echo  Die App ist nach 15 Minuten noch nicht bereit.
echo  Bitte die Meldungen in den beiden anderen Fenstern pruefen
echo  (siehe ANLEITUNG.md, Abschnitt "Wenn etwas nicht klappt").
echo.
pause
exit /b 1

:OPEN
start "" http://127.0.0.1:5173/
echo  Fertig - die App ist im Browser geoeffnet: http://127.0.0.1:5173
echo  Beenden: die beiden Fenster "Server" und "Oberflaeche" schliessen.
echo  Dieses Fenster schliesst sich in 10 Sekunden.
timeout /t 10 >nul
exit /b 0
