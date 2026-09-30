# Mitmachen bei BTC-Herkunft

Danke fürs Mitmachen! Diese Seite erklärt, wie das Projekt aufgebaut ist, wie du
es lokal startest und testest — und die wichtigste Regel: **echte Wallet-Daten
gehören nie ins Repository.**

## Datenschutz zuerst

BTC-Herkunft verarbeitet Steuer- und Vermögensdaten. Das Repository ist
öffentlich. Deshalb gilt ohne Ausnahme:

- **Nie ins Repo:** xpubs/ypub/zpub, Adressen, Transaktions-IDs, Beträge,
  Zeitstempel, Wallet-Namen oder Erläuterungen aus echten Exporten, Berichten,
  Diagnosen oder Screenshots — auch nicht gekürzt oder als „Beispiel aus der
  Praxis“. Auch nicht in Commit-Nachrichten, Issues oder PR-Texten.
- **Aus echten Exporten nur die Struktur übernehmen:** Spaltennamen, Formate,
  Zeilenarten, Vorzeichen, Anzahl Nachkommastellen. Einen echten Fehlerfall mit
  erfundenen Werten nachbauen; im Test steht, *was* er prüft, nicht woher er kommt.
- **Nie Seeds oder private Schlüssel**, auch keine erfundenen, die echt aussehen.
- Nutzerdateien liegen in `local/` — per `.gitignore` ausgeschlossen (außer
  `local/README.md`). Nie committen.

### So sehen Testdaten aus

- Beträge rund (`0.01`, `250_000`, `€900.00`) oder offensichtliche Muster
  (`0.12345678`, `1_111_111`); Uhrzeiten auf volle Minuten (`10:05:00`).
- Adressen nur aus `tests/privacy_allowlist.txt` (BIP-Testvektoren oder
  synthetisch nach dem dort beschriebenen Rezept) oder offensichtlich ungültige
  Platzhalter (`bc1qownaddress`); öffentliche Börsenadressen nur aus
  `data/label_packs/`.
- Transaktions-IDs als Platzhalter (`"c" * 64`, `"t1"`), Wallet-Namen generisch
  (`W`, `Ledger`, `Wallet A`).

`tests/test_privacy_guard.py` läuft bei jedem `pytest` und in der CI. Er meldet
gültige Adressen/xpubs außerhalb der Erlaubt-Liste, 64-stellige Hex-Werte, krumme
Beträge, Uhrzeiten mit Sekunden und persönliche Namen. Bei einem Treffer den Wert
durch einen erfundenen ersetzen — nur nachweislich öffentliche oder abgeleitete
Werte mit `privacy: ok` und Begründung markieren. Die Erlaubt-Liste wird
nachgerechnet (BIP-Testvektoren, synthetische Adressen, Muster-Platzhalter);
`scripts/privacy_scan.py` prüft in der CI zusätzlich jeden Commit des PR.

## Lokal starten

Voraussetzungen: Python 3.11+, Node.js 18+.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]" pymupdf
cd frontend && npm install && cd ..
./scripts/start-linux.sh                            # Windows: Start-BTC-Herkunft.bat
```

## Prüfen vor dem PR

Dasselbe läuft in der CI (`.github/workflows/tests.yml`):

```bash
ruff check src tests scripts       # Lint
pytest -q                          # alle Tests inkl. Datenschutz-Prüfung
python scripts/privacy_scan.py origin/main..HEAD   # Datenschutz: jeder Commit des Branches
cd frontend && npx tsc --noEmit    # Typecheck der Oberfläche
```

Für PDFs unter Linux die Schrift DejaVu installieren (`fonts-dejavu-core`), sonst
fallen Tests mit Umlauten im Bericht aus.

## Aufbau

| Bereich | Wo | Worum es geht |
|---|---|---|
| Blockchain | `electrum_client.py`, `hd_deriver.py`, `tx_ingestor.py`, `chain_cache.py`, `sync_pipeline.py` | xpub ableiten, Historie über Electrum lesen (nur RAM) |
| Auswertung | `holding_clock.py`, `cloud_summary.py`, `internal_transfer_tagger.py`, `year_summary.py`, `price_oracle.py` | FiFo, Haltefristen, Zu-/Abflüsse der Wallet-Cloud, Kurse |
| Börsen-Exporte | `exchanges/` (ein Modul je Format), `local_files.py`, `exchange_sales.py` | Exporte lesen, Käufe/Verkäufe den Blockchain-Bewegungen zuordnen |
| Bericht | `origin_report.py` (Daten, Layout-Hilfen), `report_sections/` (ein Modul je Abschnitt), `fa_inbound_report.py` | PDF „Herkunftsanalyse Bitcoin“ (intern und Finanzamt-Fassung) |
| Regelwerk | `btc-regeln/` (YAML, siehe `REGELWERK.md`), `regelwerk.py` (Laden, Validierung), `pdf_vergleich.py` | Steuerrechtliche Parameter je Veranlagungsjahr, Kategorien — Daten statt Code |
| Jahressteuerreport | `tax_year.py` (Daten), `tax_report.py` (PDF) | Auszug aus dem Herkunftsnachweis für ein Kalenderjahr (§ 23, § 22 Nr. 3 EStG) |
| PP-Abgleich | `pp_check.py`, `pp_diff.py` | Portfolio Performance ↔ Blockchain, Delta-Import, CLI |
| API | `api/app.py` (App), `api/core.py` (Sitzung, Hilfen), `api/routes/` (je Bereich ein Router) | FastAPI, nur `127.0.0.1` |
| Oberfläche | `frontend/src/`: `App.tsx` (Rahmen), `appState.ts` (Zustand/Aktionen), `cards/` (je Karte eine Komponente) | React/Vite |

Fachliche Details: [`docs/HERKUNFTSANALYSE.md`](docs/HERKUNFTSANALYSE.md),
Datenmodell: [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md), API: [`docs/API.md`](docs/API.md).

## Häufige Beiträge

- **Neue Börse / neues Exportformat:** siehe [`src/btc_origin/exchanges/README.md`](src/btc_origin/exchanges/README.md)
  — ein neues Modul plus Test, ohne bestehende Parser anzufassen.
- **Öffentliche Börsenadressen:** Label-Packs in `data/label_packs/`
  ([`docs/LABEL_PACKS.md`](docs/LABEL_PACKS.md)) — nur nachweislich öffentliche Adressen mit Quelle.
- **Rechtslage:** Änderungen an Freigrenzen, Haltefrist oder Rechtstexten gehören in
  `btc-regeln/regeln/<JJJJ>.yaml`, nicht in den Code — Ablauf in `btc-regeln/REGELWERK.md`.
  **Jede Änderung in `btc-regeln/`** (Regeldateien, `kategorien.yaml`, `REGELWERK.md`) wird vor
  dem Merge mit der steuerlichen Prüfung abgestimmt; der PR nennt, was sich geändert hat und
  wer es freigegeben hat.
- **Bericht:** Formulierungen mit Bezug aufs BMF-Schreiben bitte mit Randnummer
  (Rz.) belegen. Der Hinweis „keine Steuerberatung“ bleibt immer erhalten.

## Grundsätze

- Alles läuft lokal; Sitzungsdaten nur im Arbeitsspeicher, nichts auf Platte außer
  dem, was der Nutzer ausdrücklich herunterlädt.
- Keine Steuerberatung — der Bericht belegt, er bewertet nicht.
- Ein PR = ein Thema; Tests für neues Verhalten; Commit-Nachrichten auf Deutsch
  oder Englisch, ohne echte Daten.
