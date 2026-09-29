# BTC-Herkunft

Lokales Open-Source-Tool für **Herkunftsnachweis** und **Haltefrist-Analyse** von
Bitcoin aus öffentlichen xpubs — mit einem Bericht fürs Finanzamt
(„Herkunftsanalyse Bitcoin“), der sich am BMF-Schreiben vom 06.03.2025 orientiert.

> **Repo / Package:** `btc-origin` / `btc_origin` · **Produktname:** BTC-Herkunft ·
> **Lizenz:** MIT — https://github.com/thunder-62/BTC-herkunft
>
> **Keine Steuerberatung, keine Investmentberatung.**

**Mitentwickeln?** → [CONTRIBUTING.md](CONTRIBUTING.md) (Aufbau, Tests, Datenschutzregeln).

**Neu hier und kein IT-Profi?** → [**ANLEITUNG.md**](ANLEITUNG.md): Schritt für
Schritt vom ZIP-Download bis zum PDF fürs Finanzamt (Windows).

## Inhalt

1. [Was die App macht](#was-die-app-macht)
2. [Grundsätze (Hard Boundaries)](#grundsätze-hard-boundaries)
3. [Starten](#starten)
4. [Bedienung](#bedienung)
5. [Konfiguration](#konfiguration)
6. [Architektur](#architektur)
7. [Tests](#tests)
8. [Dokumentation](#dokumentation)

## Was die App macht

Du fügst die **öffentlichen** xpubs deiner Wallets ein (z. B. Ledger, BitBox,
Trezor — jede Wallet mit Standard-xpub/ypub/zpub). Die App liest die Blockchain
über einen Electrum-Server und betrachtet alle eingefügten Wallets zusammen als
**Wallet-Cloud**:

- **Zufluss (Cloud-Eintritt):** Sats kommen von einer fremden Adresse — Börse,
  Dienst, Dritte. Wer der Absender war, zeigt die Spalte „Ursprung“.
- **Umbuchung:** Bewegung zwischen deinen Wallets — keine Veräußerung, das
  Anschaffungsdatum bleibt erhalten.
- **Abfluss (Cloud-Austritt):** Sats gehen an eine fremde Adresse.

Darauf aufbauend:

| Funktion | Kurz |
|----------|------|
| **Einzelbetrachtung je Coin** | Jeder Coin (UTXO) wird über alle Umbuchungen bis zum Abfluss verfolgt (BMF 06.03.2025 Rz. 61, Wechselgeld Rz. 56). FiFo nur innerhalb einer Transaktion, die mehrere Coins zusammenführt. |
| **Jahres-Resümee** | Möglicher Gewinn/Verlust je Jahr, getrennt nach Haltedauer < / ≥ 1 Jahr, Gebühren als Werbungskosten (Rz. 57, 59), Freigrenze als Hinweis. |
| **Börsen-Exporte** | CSV-Exporte (Relai, Bison, Bitvavo, Strike, BitGo, 21bitcoin, Coinbase, Binance …) in `local/boersen/` liefern echte Kauf- und Verkaufsdaten (Rz. 20, 55) und benennen Absender/Empfänger. |
| **Abgleich Portfolio Performance** | Liegt `local/check-pp.csv` (PP-Export des Bitcoin-Wertpapiers), zeigt die App Bestand, Jahressummen und einzelne Buchungen mit Δ zur Blockchain — nur im Browser, zur Bereinigung von PP. |
| **Externe Adressen** | Fremde Gegenstellen heißen `ext-001` …; gebündelt nach gemeinsamen Inputs; eigene Namen per CSV oder `local/externe-adressen.csv`; Datumsregel `local/zuordnung.csv` (z. B. `FTX,2022-11-07`: unbenannte Gegenstellen bis dahin = FTX, als eigene Angabe markiert). |
| **Herkunftsanalyse (PDF)** | Bericht fürs Finanzamt vom Einfachen zum Detail — siehe [`docs/HERKUNFTSANALYSE.md`](docs/HERKUNFTSANALYSE.md). |
| **Kryptosteuer-Reform (Entwurf)** | Coins werden als Alt- (angeschafft bis 31.12.2026) oder Neubestand gekennzeichnet; unbelegte Altbestands-Coins sind hervorgehoben; eigenes PDF „Nachweis Altbestand“ je Coin mit Anschaffung, Nachweis, Tx und Adresse. Grundlage ist ein Referentenentwurf, kein geltendes Recht. |
| **Nacherklärung (Entwurf, PDF)** | Bei möglichem steuerpflichtigem Gewinn: Anschreiben mit Platzhaltern (§ 153 / § 371 AO), Werte je Jahr, Einzelaufstellung, Checkliste — keine fertige Selbstanzeige, mit Steuerberatung klären. |
| **Lot-Fluss** | Netzgrafik: Wallets im Raum verteilt (verschiebbar), Zuflüsse (grün), Umbuchungen (weiß), Abflüsse (rot) als Pfeile. |
| **Privacy-Ansicht** | Maskiert xpubs, Adressen, TxIDs und Beträge — auch im PDF; Namen bleiben lesbar. |
| **Trace / Labels** | Rückverfolgung einer Transaktion; öffentliche Label-Packs (FTX, Mt. Gox, …). |

**Kurse** folgen einer festen Regel (Rz. 91): Binance BTC/EUR-Tagesschlusskurs;
vor dem 03.01.2020 Binance BTC/USDT ÷ EZB-Referenzkurs USD/EUR; vor dem
17.08.2017 mempool.space.

## Grundsätze (Hard Boundaries)

1. **Nie** Seeds, private Schlüssel oder Signieren — nur öffentliche xpubs/Adressen per Einfügen.
2. **Keine Persistenz:** Sitzung nur im RAM (SQLite `:memory:`). Prozessende = alles weg. Kein LocalStorage/IndexedDB für Wallet-Daten.
3. **Ausnahmen, nur lesend:** öffentliche Label-Packs in [`data/label_packs/`](data/label_packs/) (Git) und dein privater Ordner [`local/`](local/README.md) (in `.gitignore`, die App schreibt nie hinein).
4. **Ausgaben nur auf Klick:** CSV/PDF/Bericht werden im Speicher erzeugt, nie automatisch auf Platte geschrieben. Name und Steuer-ID im Bericht werden nirgends gespeichert.
5. **Eigen sind nur die eingefügten xpubs/Adressen.** Die heuristische Ownership-Erweiterung ist aus (`OWNERSHIP_INFERENCE=true` schaltet sie ein).
6. **Keine stille Kostenbasis 0:** fehlt ein Nachweis, steht „Kaufnachweis fehlt“; Kurse sind Referenzwerte.
7. API bindet nur an `127.0.0.1`.

Details: [`THREAT_MODEL.md`](THREAT_MODEL.md).

## Starten

Voraussetzungen: Python 3.11+, Node.js 18+.

### Windows

Am einfachsten: Doppelklick auf **`Start-BTC-Herkunft.bat`** im Hauptordner —
öffnet Server- und UI-Fenster und den Browser, sobald beides bereit ist
(Schritt für Schritt: [`ANLEITUNG.md`](ANLEITUNG.md)).

```powershell
.\scripts\start-windows.ps1     # öffnet API- und UI-Fenster
# einzeln: .\scripts\start-api.ps1 / .\scripts\start-ui.ps1 (oder die .bat-Varianten)
```

Blockiert die Execution Policy das Skript: `powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1`.
Die Skripte richten beim ersten Start `.venv` und `npm install` ein. Danach läuft `pip install` nur, wenn sich `pyproject.toml` geändert hat (Neuinstallation erzwingen: `.venv\pyproject.installed.toml` löschen).

**Repo in OneDrive:** `start-api` setzt `git config gc.auto 0` — sonst fragt
`git pull` dutzendfach „Deletion of directory … failed (y/n)“, weil OneDrive
Ordner in `.git` sperrt.

### Linux / macOS

Am einfachsten: **`./scripts/start-linux.sh`** — richtet beim ersten Start `.venv`
und `npm install` ein, startet API und UI in einem Terminal und öffnet den Browser,
sobald beides bereit ist. Beenden mit Strg+C. Für die PDF-Berichte unter Linux die
Schrift DejaVu installieren (`sudo apt install fonts-dejavu-core`).

Von Hand:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
btc-origin                                  # API: http://127.0.0.1:8000/api/health

cd frontend && npm install && npm run dev   # UI:  http://127.0.0.1:5173
```

Vite leitet `/api` an die API weiter.

## Bedienung

1. **Wallets einfügen:** je Zeile `Name<TAB>xpub` (oder nur xpub). Bei Plain-`xpub`
   werden alle Adresstypen geprüft (bc1q, 3…, bc1p, 1…) und jeder mit Historie übernommen.
2. **Sync:** Ableitung mit Gap-Limit, Abruf über Electrum (gebündelte Anfragen,
   RAM-Cache), Auswertung. Fortschritt in der Karte „Sync-Status“.
3. **Auswerten:** Wallet-Cloud-Übersicht, Flows (aufklappbar), Jahres-Resümee,
   Lot-Fluss (aufklappbar), Externe Adressen (aufklappbar), Trace, Labels.
4. **Optional `local/` pflegen:** eigene Namen und Börsen-Exporte →
   „Lokale Dateien neu laden“. Siehe [`local/README.md`](local/README.md).
5. **Bericht:** Karte „Finanzamt: Herkunftsanalyse Bitcoin“ — Name, Steuer-ID,
   Stichtag, optional maskiert und „nur Vorgänge bis zum Stichtag“ → „Interne Fassung
   (Full-Detail) öffnen“ (vollständig, PDF im Browser) oder „Finanzamt-Fassung öffnen“ (ohne
   Bestände, xpubs, Adressen und Transaktions-IDs ohne Bezug zu einer Veräußerung) samt
   Kontrolldatei (Prüfprotokolle, Freigabeliste). Transaktionen stehen in beiden Fassungen als
   T-Nummern; die Hashes im Transaktionsverzeichnis (Anhang D).

`DELETE /api/session` bzw. Schließen der API löscht die Sitzung.

## Konfiguration

`.env` (Vorlage `.env.example`, keine Geheimnisse):

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `ELECTRUM_HOST` / `ELECTRUM_PORT` / `ELECTRUM_SSL` | `electrum.blockstream.info` / `50002` / `true` | Bevorzugter Server; danach Failover über die Default-Liste |
| `GAP_LIMIT` | `20` | Unbenutzte Adressen in Folge je Kette |
| `OWNERSHIP_INFERENCE` | aus | Heuristische Erweiterung der eigenen Adressen |
| `BIND_HOST` / `BIND_PORT` | `127.0.0.1` / `8000` | API-Bindung |
| `TRACE_MAX_DEPTH` | `5` | Tiefe der Rückverfolgung |
| `BTC_ORIGIN_LOCAL_DIR` | `<repo>/local` | Ort des privaten Ordners |
| `BTC_ORIGIN_LABEL_PACKS` | `<repo>/data/label_packs` | Ort der Label-Packs |

## Architektur

React/Vite-UI (`frontend/`) ↔ FastAPI (`src/btc_origin/api/`: `app.py` bindet die Router in `routes/` ein, gemeinsamer Zustand in `core.py`) ↔ Module:

```
xpub (Paste) → wallet_registry → hd_deriver → electrum_client (+ chain_cache)
            → sync_pipeline / tx_ingestor → merger → internal_transfer_tagger
            → holding_clock (Lots je Coin) → cloud_summary · year_summary
            → exchange_sales (Börsen-Exporte) · external_book · label_service
            → origin_report (PDF) · report_builder (CSV/PDF)
            price_oracle (Kurse, RAM-Cache) · local_files (local/, nur lesen)
            db = SQLite :memory:
```

| Modul | Rolle |
|-------|-------|
| `wallet_registry`, `hd_deriver` | xpubs im RAM; Adressableitung (embit, Gap-Limit, alle Adresstypen) |
| `electrum_client`, `chain_cache` | Electrum JSON-RPC (SSL, Failover, gebündelt); Tx/Header-Cache im RAM |
| `sync_pipeline`, `tx_ingestor`, `merger` | Transaktionen → Flows (mit Outpoint je Abfluss) → ein Ledger |
| `internal_transfer_tagger` | Umbuchung vs. Abfluss je Transaktion (Netto-Rechnung, Gebühr) |
| `holding_clock` | Lots je Coin (Einzelbetrachtung), Haltefrist, Cloud-Flows |
| `cloud_summary` | Zuflüsse / Abflüsse / Gebühren / Bestand + Plausibilitätsprüfung gegen UTXO |
| `year_summary` | Jahres-Resümee inkl. Werbungskosten |
| `exchange_sales` | Börsenkonten aus Exporten nachspielen: Kauf- und Verkaufsdaten, FiFo auf der Börse |
| `external_book`, `local_files` | `ext-NNN`-Namen, CSV-Import/-Export; Lesen von `local/` |
| `price_oracle` | Kursregel (Binance, EZB, mempool), RAM-Cache, Laden im Hintergrund |
| `origin_report` | PDF „Herkunftsanalyse Bitcoin“ |
| `report_builder`, `fa_inbound_report` | CSV/PDF des Roh-Ledgers; älterer FA-Inbound-Bericht (nur API) |
| `trace_engine`, `label_service`, `ownership_inference` | Rückverfolgung, Label-Packs, optionale Ownership-Heuristik |

## Tests

```bash
pytest                    # ohne Netz: Electrum und Kursquellen sind gemockt
cd frontend && npm run build
```

## Dokumentation

| Datei | Inhalt |
|-------|--------|
| [`docs/HERKUNFTSANALYSE.md`](docs/HERKUNFTSANALYSE.md) | Aufbau des Finanzamt-Berichts, Rechtsgrundlagen, Berechnungsregeln |
| [`docs/WALLET_CLOUD.md`](docs/WALLET_CLOUD.md) | Wallet-Cloud, Zufluss/Umbuchung/Abfluss, Lots, Externe Adressen |
| [`docs/API.md`](docs/API.md) | Endpunkte und Antwortfelder |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Sitzungs-Schema (RAM) und abgeleitete Sichten |
| [`docs/LABEL_PACKS.md`](docs/LABEL_PACKS.md) | Öffentliche Label-Packs |
| [`ANLEITUNG.md`](ANLEITUNG.md) | Einsteiger-Anleitung Windows: Download, Installation, Start, Bericht |
| [`local/README.md`](local/README.md) | Privater Ordner: Namen, Börsen-Exporte, PP-Abgleich |
| [`THREAT_MODEL.md`](THREAT_MODEL.md) | Bedrohungen und Gegenmaßnahmen |

## Lizenz

MIT — siehe [`LICENSE`](LICENSE).
