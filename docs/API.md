# API-Übersicht — BTC-Herkunft

Lokale FastAPI unter `127.0.0.1:8000`. Alle Endpunkte arbeiten auf der
**flüchtigen Sitzung** (RAM / SQLite `:memory:`); Prozessende = Sitzung weg.
Keine Authentifizierung — deshalb nur localhost.

Quelle der Wahrheit: `src/btc_origin/api/routes/` (ein Modul je Bereich) und der UI-Client
`frontend/src/api.ts`. Verwandt: [`WALLET_CLOUD.md`](WALLET_CLOUD.md) ·
[`HERKUNFTSANALYSE.md`](HERKUNFTSANALYSE.md) · [`DATA_MODEL.md`](DATA_MODEL.md)

## Konventionen

| Thema | Verhalten |
|-------|-----------|
| Persistenz | keine; Antworten tragen oft `"ephemeral": true` |
| Eingabe | nur öffentliche xpubs/Adressen; nie Seeds/Keys |
| Berichte | Bytes im Speicher; Header `X-BTC-Herkunft-Disk-Written: false` |
| Kurse | nur aus dem RAM-Cache für die UI (`prices_pending` / `price_status`); fehlende Tage werden im Hintergrund geladen |
| Steuer | keine Steuerberatung |

## Endpunkte

### Sitzung, Wallets, Sync

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/api/health` | Status, Version, `persistence: memory_only` |
| `GET` | `/api/wallets` | Wallets der Sitzung |
| `POST` | `/api/wallets` | Ein xpub oder eine Adresse (`name`, `xpub` \| `address`) |
| `POST` | `/api/wallets/batch` | Mehrere: `items[]` und/oder `paste` (Zeilen `Name<TAB>xpub`) |
| `DELETE` | `/api/session` | Sitzung leeren (Wallets, Ledger, Caches) |
| `POST` | `/api/sync` | Ableiten → Electrum → Flows → Auswertung |
| `GET` | `/api/sync/progress` | Fortschritt (`running`, `phase`, Zähler, `message`) |
| `POST` | `/api/enrich` | Auswertung ohne neuen Electrum-Abruf wiederholen |
| `GET` | `/api/electrum/servers` | Konfigurierter Server + Failover-Liste |
| `GET` | `/api/electrum/status` | `?probe=1` = kurzer Live-Check |

### Wallet-Cloud

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/api/cloud` | Zuflüsse / Bestand / Abflüsse / Gebühren (+ EUR), Plausibilitätsprüfung |
| `GET` | `/api/cloud/flows` | Zu- und Abflüsse (UI-Karte „Flows“) — Felder unten |
| `GET` | `/api/cloud/lots` | Offene Teilbestände (Lots) mit Pfad (`path`) |
| `GET` | `/api/cloud/years` | Jahres-Resümee: < / ≥ 1 Jahr, Werbungskosten, Freigrenze, Details je Teilbestand; berücksichtigt Börsen-Exporte |
| `GET` | `/api/cloud/ownership` | Eigene Adressen; heuristische Erweiterung nur mit `OWNERSHIP_INFERENCE=true`; `?privacy=true` maskiert |
| `GET` | `/api/flows` | Roh-Ledger: eine Zeile je eigenem Input/Output, inkl. intern |

### Externe Adressen und `local/`

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/api/external` | Gegenstellen (`ext-NNN` oder Name), Adressen je Bündel, Zu-/Abflüsse, Label, Börsen-Hinweise; Status von `local/` |
| `POST` | `/api/external/import` | Namen importieren: `{"csv": "name,adresse\n…"}` (nur gültige Bitcoin-Adressen) |
| `POST` | `/api/external/trail` | Spurensuche für eine Gegenstelle: `{"name": "ext-007", "max_hops": 3}` → `guess`, `confidence` (hoch/mittel/niedrig/keine), `reason`, `steps[]`, `evidence[]`, `checked`; heuristisch, nur lesend über Electrum |
| `GET` | `/api/external/suchliste.csv` | Suchliste ungeklärter Gegenstellen (Datum, Richtung, BTC, EUR, Wallet, TxID) — nur auf Klick |
| `POST` | `/api/external/csv` | Export `externe-adressen.csv` (nur auf Klick) |
| `GET` | `/api/check/pp` | Abgleich `local/check-pp.csv` (Portfolio Performance) ↔ Blockchain: `bestand` (inkl. `after_sats`/`rest_sats`), `ok` (Zähler), `actions[]` (Korrekturen mit `suggest`, `effect_sats`, PP-Zeilen und Transaktionen), `years[]`; `exists=false` ohne Datei. Nur Browser, nie im Bericht |
| `POST` | `/api/check/pp/import.csv` | Korrigierte PP-CSV für den Neuaufbau (alle BTC-Buchungen, Format wie `check-pp.csv`); Body `{"keep_lines": [..]}` (PP-Zeilen ohne Gegenstück, die bleiben; sonst entfallen sie); Header `X-PP-Import-Stats` (u. a. `bestand_sats` der Datei) |
| `POST` | `/api/local/reload` | `local/externe-adressen.csv` und `local/boersen/*` neu lesen (ohne Sync) |
| `GET` | `/api/regelwerk/kategorien` | Kategorien, Zeilenarten der Börsen-Exporte je Börse (Anzahl, Richtung, Zuordnung, Status, je Jahr Anzahl und erstes Vorkommen), Zuflüsse mit T-Nummer, Änderungszähler |
| `POST` | `/api/regelwerk/kategorien/vorschau` | neuer Inhalt von `local/kategorien.yaml` aus Änderungen `{"export": {Börse: {Art: Kategorie}}, "manuell": {T-Nr: {…}}}` — schreibt nichts |
| `POST` | `/api/regelwerk/kategorien/anwenden` | Änderungen (wie bei `vorschau`) sofort für die Sitzung übernehmen — nur im Arbeitsspeicher, schreibt nichts; Berichte und Prüfprotokoll rechnen damit (Protokoll: „Sitzung, nicht gespeichert“) |
| `POST` | `/api/regelwerk/kategorien/verwerfen` | Änderungen der Sitzung verwerfen — es gilt wieder `local/kategorien.yaml` |
| `POST` | `/api/regelwerk/kategorien/speichern` | (optional) | schreibt `local/kategorien.yaml` (Sicherung `.bak`) — nur mit `{"yaml": …, "bestaetigt": true}` |
| `GET` | `/api/regelwerk/aenderungen.csv` | Export-Zeilen, deren Behandlung sich durch `kategorien.yaml` ändert — nur auf Klick |
| `POST` | `/api/pdf-vergleich` | zwei Berichts-PDFs (Base64 `alt`, `neu`) vergleichen: Abschnitte und Anzahl geänderter Zeilen, keine Werte |

### Labels und Trace

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/api/labels` | Labels der Sitzung; Einträge der Label-Packs |
| `POST` | `/api/trace` | Rückverfolgung: `txid`, optional `max_depth`; `ambiguous=true` = keine Gewissheit |

### Berichte (nur auf ausdrücklichen Aufruf)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/api/report/herkunft.pdf` | **Herkunftsanalyse Bitcoin** (inline im Browser). Query: `stichtag` (ISO, Default 31.12. des laufenden Jahres), `name`, `steuer_id`, `privacy` — siehe [`HERKUNFTSANALYSE.md`](HERKUNFTSANALYSE.md) |
| `GET` | `/api/report/steuer.pdf` | **Jahressteuerreport Bitcoin** (inline). Query: `jahr` (Pflicht), `fassung` (`intern` \| `finanzamt`), `anschluss_jahr` (Anschlussvermerk: Standard bzw. `-1` = Herkunftsnachweis auf Anforderung; Jahr = in der Sitzung erzeugter Herkunftsnachweis mit Stichtag 31.12., sonst 422; `0` = ausdrücklich keiner, Warnung im Prüfprotokoll), `name`, `steuer_id`, `privacy`. Dateiname `steuerreport-bitcoin-<JJJJ>-<fassung>.pdf`; fehlt die Regeldatei eines benötigten Jahres → 422 |
| `GET` | `/api/report/steuer-pruefprotokoll.pdf` | **Prüfprotokoll** als PDF (inline im Browser), Inhalt wie die Textfassung; Query wie unten |
| `GET` | `/api/report/steuer-pruefung` | nur das Ergebnis des Prüfprotokolls `{"ergebnis": "bestanden" \| "warnung" \| "fehler", "fehler": […], "warnungen": n}` — Anzeige am Button; Query wie unten |
| `GET` | `/api/report/steuer-pruefprotokoll.txt` | **Prüfprotokoll** des Jahressteuerreports (Textfassung, Download): Query `jahr`, `anschluss_jahr`; beide Fassungen werden erzeugt und geprüft (Abnahmeprüfungen 1–7, Geltungsbereich), dazu Build, Regeldateien mit Stand und SHA-256, `kategorien.yaml`, Export-Dateien mit SHA-256 und Herkunft, Hash der Rechenergebnisse, Freigabeliste; Header `X-BTC-Herkunft-Pruefergebnis` (`bestanden` \| `warnung` \| `fehler`) |
| `GET` | `/api/report/steuer/jahre` | Jahre mit Transaktionsdaten, je mit Angabe, ob eine Regeldatei vorliegt; `vorgabe` = vergangenes Jahr; `herkunft` = in der Sitzung erzeugte Herkunftsnachweise mit Stichtag 31.12. (Jahr → Stand, Stichtag, Build) |
| `GET` | `/api/report/nacherklaerung.pdf` | **Entwurf einer Nacherklärung** (§ 153 / § 371 AO) für Jahre mit möglichem steuerpflichtigem Gewinn. Query: `jahre` (Kommaliste; nur Jahre, deren Gewinn < 1 Jahr die Freigrenze erreicht — andere werden ignoriert; leer = alle diese Jahre), `stichtag`, `name`, `steuer_id`, `privacy` |
| `GET` | `/api/report/altbestand.pdf` | **Nachweis Altbestand**: heute gehaltene Bitcoin, angeschafft bis 31.12.2026 (Bestandsschutz laut Referentenentwurf zur Kryptosteuer-Reform, nicht beschlossen). Query: `name`, `steuer_id`, `privacy` |
| `GET` | `/api/report` | beschreibt die Berichte, erzeugt nichts |
| `POST` | `/api/report/csv` | Roh-Ledger als CSV |
| `POST` | `/api/report/pdf` | Roh-Ledger als PDF |
| `POST` | `/api/report/pdf/fa-inbound` | älterer FA-Inbound-Bericht (Stichtag, zwei Haltefrist-Tabellen); nur noch per API |

## Cloud-Flows (`GET /api/cloud/flows`)

Oben: `flows[]`, `count`, `cloud` (wie `/api/cloud`), `prices_pending`,
`price_status`, `ephemeral`.

| Feld | Zufluss `in` | Abfluss `out` |
|------|--------------|---------------|
| `kind` | `cloud_entry` | `outflow` |
| `amount_sats` | Betrag beim Eintritt | verbrauchte Menge |
| `remaining_sats`, `status`, `status_de` | davon noch da; `vorhanden` / `teilweise` / `abgeflossen` | — |
| `current_locations` | `[{wallet_id, wallet_name, address, remaining_sats}]` | — |
| `time` | Tag des Zuflusses | Tag des Abflusses |
| `lot_date` | Anschaffung (Kaufdatum laut Export, sonst = `time`) | Anschaffung des verbrauchten Teilbestands |
| `acq_price_eur`, `acq_source` | Kaufpreis/BTC inkl. Gebühren und Quelle, falls laut Börsen-Export | dito für den verbrauchten Teilbestand |
| `address`, `address_display` | Eintrittsadresse | fremde Zieladresse bzw. „außerhalb Cloud“ |
| `source_addresses`, `source_name`, `source_complete`, `source_coinbase` | Absender (gebündelt / benannt) | — |
| `external_name` | — | Name der Gegenstelle (`ext-NNN`, CSV, Export, Label) |
| `wallet_id`, `wallet_name` | Eintritts-Wallet | Wallet vor dem Abfluss |
| `txid`, `lot_id`, `origin_txid` | Zufluss-Tx, Lot | Abfluss-Tx, Lot, Zufluss-Tx |
| `path`, `path_labels` | Umbuchungskette seit Eintritt | Kette bis zum Abfluss |
| `haltefrist_hint`, `haltefrist_days` | ≥ 365 Tage (Rest) | Haltedauer bis Abfluss |
| `btc_price_eur`, `btc_price_note` | Tageskurs | Tageskurs |

Umbuchungen erscheinen nicht als eigene Zeilen, nur im `path`.

## Jahres-Resümee (`GET /api/cloud/years`)

`years[]` je Kalenderjahr mit Veräußerungen: `disposals`, `short` / `long`
(`btc_sats`, `proceeds_eur`, `cost_eur`, `fees_eur`, `gain_eur`, `priced_sats`,
`missing_price`), `freigrenze_eur`, `over_freigrenze`, `complete`, `details[]`
(je Teilbestand: `exit_date`, `acquisition_date`, `days_held`, `btc_sats`, `fee_sats`,
Preise, `counterparty`, `origin`, `status`, `disposal`, `acq_source`).
Dazu `assumption` und `note` als Klartext.

## Beispiele

```bash
curl -s http://127.0.0.1:8000/api/health | jq .
curl -s -X POST http://127.0.0.1:8000/api/wallets/batch \
  -H 'Content-Type: application/json' -d '{"paste":"Ledger\txpub…\nBitBox\tzpub…"}'
curl -s -X POST http://127.0.0.1:8000/api/sync | jq '{status,flows}'
curl -s http://127.0.0.1:8000/api/cloud | jq '{inflow_sats,bestand_sats,consistent}'
curl -s http://127.0.0.1:8000/api/cloud/years | jq '.years[] | {year,short,long}'
curl -s 'http://127.0.0.1:8000/api/report/herkunft.pdf?stichtag=2025-12-31' -o herkunft.pdf
```
