# Datenmodell (nur im Speicher)

**Grundsatz:** Es gibt keine Datei-Datenbank. SQLite läuft ausschließlich als
`:memory:` für die Lebensdauer des Prozesses (`db.connect` erzwingt das, auch
wenn `SQLITE_PATH` gesetzt ist). `WalletRegistry`, Caches und abgeleitete
Sichten liegen in Python-Strukturen im RAM. Das Frontend speichert keine
Wallet-Daten im Browser (nur UI-Schalter wie die Privacy-Ansicht in
`sessionStorage`).

Verwandt: [`WALLET_CLOUD.md`](WALLET_CLOUD.md) · [`API.md`](API.md) ·
[`HERKUNFTSANALYSE.md`](HERKUNFTSANALYSE.md) · [`LABEL_PACKS.md`](LABEL_PACKS.md)

## Tabellen der Sitzung (`src/btc_origin/db.py`)

### wallets

| Spalte | Typ | Hinweis |
|--------|-----|---------|
| id | INTEGER PK | |
| name | TEXT | Anzeigename (auch aus `Name<TAB>xpub`) |
| xpub | TEXT NULL | öffentlicher Extended Key — **nie** Seed |
| kind | TEXT | `xpub` \| `address` |
| created_at | TEXT | ISO-8601 |

### addresses

| Spalte | Typ | Hinweis |
|--------|-----|---------|
| id | INTEGER PK | |
| wallet_id | INTEGER FK → wallets | |
| address | TEXT | abgeleitet (alle Adresstypen mit Historie) oder eingefügt |
| derivation_path | TEXT NULL | z. B. `m/0/5` |
| is_change | INTEGER | 0/1 |
| first_seen | TEXT NULL | |

### txs

| Spalte | Typ | Hinweis |
|--------|-----|---------|
| id | INTEGER PK | |
| txid | TEXT UNIQUE | |
| block_height | INTEGER NULL | |
| block_time | TEXT NULL | ISO-8601 UTC |
| raw_json | TEXT NULL | optional |

### flows (Roh-Ledger)

Eine Zeile je eigenem Output (`in`) bzw. eigenem Input (`out`).

| Spalte | Typ | Hinweis |
|--------|-----|---------|
| id | INTEGER PK | |
| txid | TEXT | |
| address | TEXT | eigene Adresse |
| direction | TEXT | `in` \| `out` |
| amount_sats | INTEGER | `out` = Wert des ausgegebenen Einzelbetrags |
| wallet_id | INTEGER NULL | |
| is_internal | INTEGER | 0/1 — Umbuchung/Wechselgeld laut Tagger |
| block_time / block_height | | |
| vout | INTEGER NULL | `in`: Output-Index |
| vin_index | INTEGER NULL | `out`: Input-Index |
| prev_txid / prev_vout | TEXT / INTEGER NULL | `out`: ausgegebener Einzelbetrag (Outpoint) — Grundlage der Einzelbetrachtung |
| tx_total_output_sats | INTEGER NULL | Summe aller Outputs (für die Gebühr) |
| external_amount_sats | INTEGER NULL | `out`: Anteil an fremde Adressen (je Input verteilt) |
| fee_sats | INTEGER NULL | Gebühr der Transaktion |
| lot_date | TEXT NULL | Anschaffungsdatum des Teilbestands |
| holding_days | INTEGER NULL | Haltedauer |

`GET /api/flows` liefert diese Zeilen; CSV/PDF des Roh-Ledgers bauen darauf auf.

### labels, settings

| Tabelle | Spalten | Hinweis |
|---------|---------|---------|
| labels | id, target_type (`address`/`txid`/`flow`), target_id, label, status, source | Status z. B. „Kaufnachweis fehlt, Quelle nicht erreichbar“ |
| settings | key, value | |

## RAM-Strukturen außerhalb der Tabellen

| Struktur | Inhalt |
|----------|--------|
| `last_tx_io` | je Tx: Absender-/Empfängeradressen, Anzahl In-/Outputs — für Ursprung, Ziele, Trace |
| `chain_cache` | Tx-Hex, Block-Header, Adress-Historien einer Sitzung |
| `price_oracle`-Cache | Tageskurse; `DAY_SOURCES` = welche Regel welchen Tag bewertet hat |
| `ExternalBook` | importierte Namen externer Adressen |
| `LocalData` | aus `local/` gelesen: Namen, TxID-Treffer, Zeilen für Datum/Betrag-Abgleich, **Börsen-Trades** (`ExchangeTrade`: Börse, Tag, `buy`/`sell`/`deposit`/`withdraw`, sats, EUR, Gebühr, Adressen) |
| `HoldingClock.acquisition_overrides` | `"txid:vout"` eines Zuflusses → Kauf-Teile laut Börsen-Export (`AcqPiece`: sats, Kaufdatum, Preis/BTC, Quelle) |
| `exchange_reports` | Abgleich je Börse für Anhang A |

## Abgeleitete Sichten

| Sicht | Endpunkt | Basis |
|-------|----------|-------|
| Cloud-Summary | `GET /api/cloud` | Flows; Plausibilität gegen UTXO |
| Lots | `GET /api/cloud/lots` | Einzelbetrachtung je Teilbestand |
| Cloud-Flows | `GET /api/cloud/flows` | Lots + Verbrauch |
| Jahres-Resümee | `GET /api/cloud/years` | Abflüsse (+ Börsen-Exporte) |
| Externe Adressen | `GET /api/external` | Zu-/Abflüsse, Bündelung, Namen |
| Herkunftsanalyse | `GET /api/report/herkunft.pdf` | alles oben |

### Lot (`holding_clock.Lot`)

| Feld | Bedeutung |
|------|-----------|
| `lot_id` | `txid:adresse:vout` (bei Kauf-Teilen `#n`) |
| `acquisition_date` | Anschaffung: Tag des Zuflusses bzw. Kaufdatum laut Export |
| `amount_sats`, `remaining_sats`, `original_amount_sats` | Menge beim Eintritt / noch vorhanden |
| `wallet_id`, `address` | aktueller Ort |
| `origin_wallet_id`, `origin_address`, `txid` | Zufluss |
| `path` | `LotMove`s: `inflow`, `transfer` (Wallet, Tx, Datum, Adresse) |
| `price_eur`, `acq_source` | Kaufpreis/BTC inkl. Gebühren und Quelle laut Börsen-Export (sonst leer → Tageskurs) |

Verbrauch (`LotConsumption`): `kind` `outflow` \| `fee`, Menge, Abfluss-Tag,
Haltedauer, Anschaffung, Preis/Quelle.

## Preisbegriffe

| Begriff | Bedeutung | Nicht |
|---------|-----------|-------|
| **Tageskurs / Anschaffungs-Referenz** | historischer Tagesschlusskurs nach der Kursregel | tatsächlicher Kaufpreis |
| **Kaufpreis / Erlös laut Export** | aus dem Börsen-Export | — |
| **REFERENZWERT** | aktueller Kurs (Spot) | Kaufpreis, Kostenbasis |

Fehlt ein Kurs, steht „Kurs fehlt“ — Kurse werden nie erfunden.

## Regeln

- Keine Felder für Seeds, private Schlüssel oder Signaturen.
- Keine stille Kostenbasis 0; fehlender Nachweis → Label-Status.
- Prozessende = Schema und Daten weg; keine Migration, kein Backup.
- Auf Platte erlaubt (nur lesend): `data/label_packs/*.json` (öffentlich, Git)
  und `local/` (privat, `.gitignore`).
