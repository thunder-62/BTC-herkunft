#!/usr/bin/env bash
# BTC-Herkunft starten (Linux, auch macOS): API und Oberfläche in einem Terminal,
# danach öffnet sich der Browser. Beenden: Strg+C (stoppt beides).
#
#   ./scripts/start-linux.sh
#
# Beim ersten Start werden .venv und node_modules eingerichtet (einige Minuten).
# pip install läuft danach nur, wenn sich pyproject.toml geändert hat
# (Neuinstallation erzwingen: .venv/pyproject.installed.toml löschen).
# Python wählen: BTC_ORIGIN_PYTHON=/pfad/zu/python3.12 ./scripts/start-linux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_URL="http://127.0.0.1:8000/api/health"
UI_URL="http://127.0.0.1:5173/"

say() { printf '\033[36m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

# --- Voraussetzungen --------------------------------------------------------
find_python() {
  if [[ -n "${BTC_ORIGIN_PYTHON:-}" ]]; then
    command -v "$BTC_ORIGIN_PYTHON" && return
  fi
  for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 &&
       "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      command -v "$p"
      return
    fi
  done
  return 1
}

PY="$(find_python)" || fail "Python 3.11 oder neuer nicht gefunden (z. B. sudo apt install python3 python3-venv)."
command -v node >/dev/null 2>&1 || fail "Node.js nicht gefunden (Version 18 oder neuer, https://nodejs.org)."
command -v npm >/dev/null 2>&1 || fail "npm nicht gefunden (gehört zu Node.js)."
command -v curl >/dev/null 2>&1 || fail "curl nicht gefunden (z. B. sudo apt install curl)."

if [[ "$(uname)" == "Linux" && ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]]; then
  say "Hinweis: Schrift DejaVu fehlt — die PDF-Berichte nutzen dann eine Ersatzschrift ohne Umlaute/Sonderzeichen."
  say "         Installieren z. B. mit: sudo apt install fonts-dejavu-core"
fi

# --- API (Python) -----------------------------------------------------------
say "BTC-Herkunft — $ROOT"
say "Python: $PY"
if [[ ! -x .venv/bin/python ]]; then
  say "Richte .venv ein ..."
  "$PY" -m venv .venv || fail "venv konnte nicht angelegt werden (Debian/Ubuntu: sudo apt install python3-venv)."
fi
STAMP=".venv/pyproject.installed.toml"
if [[ ! -x .venv/bin/btc-origin ]] || ! cmp -s pyproject.toml "$STAMP"; then
  say "Installiere Python-Pakete (pyproject.toml geändert) ..."
  .venv/bin/python -m pip install -q -e ".[dev]" || .venv/bin/python -m pip install -q -e .
  cp pyproject.toml "$STAMP"
else
  say "Python-Pakete aktuell (pip install übersprungen)"
fi

# --- Oberfläche (Node) ------------------------------------------------------
if [[ ! -d frontend/node_modules ]]; then
  say "npm install ..."
  (cd frontend && npm install)
fi

# --- Start ------------------------------------------------------------------
# Jeder Teil in eigener Prozessgruppe: beim Beenden die ganze Gruppe stoppen
# (npm startet vite als Enkelprozess, der sonst weiterliefe).
set -m
PIDS=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]}"; do
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT

say "Starte API auf http://127.0.0.1:8000 ..."
.venv/bin/btc-origin &
PIDS+=($!)
say "Starte Oberfläche auf http://127.0.0.1:5173 ..."
(cd frontend && exec npm run dev) &
PIDS+=($!)

for _ in $(seq 1 300); do
  if curl -fs -o /dev/null "$API_URL" && curl -fs -o /dev/null "$UI_URL"; then
    say "Fertig — die App läuft: $UI_URL  (Beenden: Strg+C)"
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$UI_URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
      open "$UI_URL" || true
    fi
    while :; do  # läuft, bis Strg+C oder ein Teil sich beendet (ohne wait -n: macOS-bash 3.2)
      for pid in "${PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null || fail "API oder Oberfläche wurde beendet — Meldungen oben prüfen."
      done
      sleep 2
    done
  fi
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || fail "API oder Oberfläche ist beim Start abgebrochen — Meldungen oben prüfen."
  done
  sleep 3
done
fail "Die App ist nach 15 Minuten noch nicht bereit — Meldungen oben prüfen."
