# Wallet-Cloud — Begriffe und Semantik

Verwandt: [`HERKUNFTSANALYSE.md`](HERKUNFTSANALYSE.md) · [`API.md`](API.md) ·
[`DATA_MODEL.md`](DATA_MODEL.md) · [`../README.md`](../README.md)

**Keine Steuerberatung. Keine Seeds/Keys. Sitzung nur im RAM.**

## 1. Was ist die Wallet-Cloud?

Alle in der Sitzung eingefügten xpubs (und Einzeladressen) bilden zusammen
eine Cloud. **Eigen** sind nur deren Adressen (Gap-Scan je Kette und Adresstyp).

| Bewegung | Bedeutung |
|----------|-----------|
| **Zufluss** (Cloud-Eintritt) | Sats kommen von einer fremden Adresse |
| **Umbuchung** | zwischen eigenen Adressen — auch über verschiedene xpubs; keine Veräußerung |
| **Abfluss** (Cloud-Austritt) | Sats gehen an eine fremde Adresse |
| **Gebühr** | Netzwerkgebühr; bei Abflüssen Werbungskosten, bei Umbuchungen nur Bestandsminderung |

Die Tagger-Logik rechnet je Transaktion netto: eigene Inputs, eigene Outputs,
fremde Outputs, Gebühr. Eine Zahlung mit Wechselgeld ist ein Abfluss plus
Wechselgeld — keine Umbuchung.

**Ownership-Heuristik** (`ownership_inference.py`, Co-Spend/Konsolidierung) ist
standardmäßig **aus**; `OWNERSHIP_INFERENCE=true` schaltet sie ein. Ergebnisse
sind als „abgeleitet / heuristisch“ markiert.

## 2. Lots — Einzelbetrachtung je Teilbestand

Modul `holding_clock.py` (`apply_specific_lots`):

- Jeder Zufluss erzeugt ein **Lot** mit Anschaffungsdatum (Tag des Zuflusses
  bzw. Kaufdatum laut Börsen-Export) und Herkunft.
- Jeder Abfluss-Flow kennt seinen ausgegebenen Einzelbetrag (`prev_txid:prev_vout`).
  Eine Transaktion verbraucht genau die Lot-Anteile der Bitcoin, die sie ausgibt.
- Werden mehrere Einzelbeträge in einer Transaktion zusammengeführt, gilt dort FiFo:
  zuerst der externe Abfluss, dann die Gebühr, dann die eigenen Ausgänge
  (Wechselgeld/Umbuchung) in Ausgangsreihenfolge.
- Umbuchungen und Wechselgeld behalten Anschaffungsdatum und Herkunft; der
  Weg steht im `path` (Zufluss → Wallets → aktueller Ort).
- Flows ohne Outpoint (nur von Hand gebaute Testdaten) fallen auf FiFo je Wallet zurück.

Rechtlicher Hintergrund: BMF-Schreiben 06.03.2025 Rz. 56, 61 — siehe
[`HERKUNFTSANALYSE.md`](HERKUNFTSANALYSE.md).

## 3. Cloud-Flows (UI-Karte „Flows“)

`GET /api/cloud/flows` — nur Zu- und Abflüsse, keine Zeilen für Umbuchungen:

| | Zufluss (`in`) | Abfluss (`out`) |
|--|----------------|-----------------|
| Zeilen | jeder Zufluss, auch später abgeflossene | je verbrauchtem Lot-Anteil |
| Betrag | beim Eintritt; `remaining_sats` = davon noch da | verbrauchte Menge |
| Zeit | Tag des Zuflusses (`lot_date` = Anschaffung) | Tag des Abflusses |
| Adresse | Eintrittsadresse; `current_locations` = wo der Rest liegt | fremde Zieladresse bzw. „außerhalb Cloud“ |
| Ursprung / Ziel | Absender (`source_name`, gebündelt) | Empfänger (`external_name`) |

Das Roh-Ledger mit allen Zeilen (auch intern) liefert `GET /api/flows`.

## 4. Externe Adressen

- Fremde Gegenstellen heißen `ext-001`, `ext-002` … in Reihenfolge des ersten
  Auftretens — für Absender wie Empfänger.
- **Bündelung (heuristisch):** Absenderadressen, die gemeinsam Inputs einer
  Transaktion sind, gehören zu einer Gegenstelle; Einzahlungsadressen, die
  später gemeinsam weiterbewegt werden, ebenfalls.
- **Namen**, Vorrang von oben: CSV-Import in der Sitzung bzw.
  `local/externe-adressen.csv` → Börsen-Export (TxID, Adresse oder Datum+Betrag)
  → öffentliches Label (`data/label_packs/`) → Datumsregel aus `local/zuordnung.csv`
  (eigene Angabe, im Bericht ³) → `ext-NNN`.
- Hinweise „vermutlich Börse“: Sammelauszahlung (≥ 5 Empfänger) oder wiederholt
  genutzte Hot-Wallet.
- Export „Externe Adressen speichern“ (`name,adresse`) nur auf Klick.
- **Spurensuche** („Spur prüfen“ je ungeklärter Gegenstelle, „Alle ungeklärten prüfen“;
  `trail.py`): verfolgt über den Electrum-Server bis zu 3 Schritte **vorwärts** (Abflüsse:
  wohin gingen die Bitcoin von dort?) bzw. **rückwärts** (Zuflüsse: woher hatte der
  Absender sie?). Hinweise, von stark nach schwach: Adresse im öffentlichen Label-Pack
  (Schritt 1 = hohe, später = mittlere Sicherheit) → Weiterleitung in eine eigene Wallet
  (evtl. eigene, nicht erfasste Wallet) → börsentypische Muster (≥ 10 Eingänge
  zusammengeführt, Auszahlung an ≥ 10 Empfänger, Adresse mit ≥ 100 Transaktionen) →
  Bitcoin liegen unbewegt auf kaum genutzter Adresse (private Wallet). Bei Label-Treffer
  „Name übernehmen“ (Sitzung; dauerhaft über „Externe Adressen speichern“). Nur Hinweis,
  kein Beleg. Zuflüsse werden über die eigenen Zufluss-Transaktionen Schritt für Schritt
  zurückverfolgt (ohne Adress-Historie); Adressen, deren Historie der Server wegen
  Größe nicht liefert, gelten als Hinweis auf eine Börse. Das Ergebnis zeigt die
  vollständigen Adressen (kopieren, Explorer-Link nur auf Klick) und bei Zuflüssen
  „Wo vermutlich gekauft“.
- **Suchliste herunterladen:** alle Zu- und Abflüsse ungeklärter Gegenstellen mit
  Datum, BTC, EUR-Tageswert, Wallet und TxID — zum Abgleich mit Kontoauszügen und E-Mails.

## 5. Übersicht, Jahres-Resümee, Lot-Fluss

- **Wallet-Cloud-Übersicht** (`GET /api/cloud`): Zuflüsse, Bestand, Abflüsse,
  interne Transaktionskosten; EUR zum Tageskurs, Bestand zum aktuellen Kurs.
  Plausibilitätsprüfung: Bestand = unverbrauchte Einzelbeträge (UTXO) laut Blockchain.
- **Jahres-Resümee** (`GET /api/cloud/years`): möglicher Gewinn/Verlust je Jahr,
  < / ≥ 1 Jahr, Werbungskosten, Freigrenze; Börsen-Exporte werden berücksichtigt.
- **Lot-Fluss** (aufklappbar): Netzgrafik — die Wallets liegen im Raum verteilt
  (Kräftesimulation: stark verbundene nah beieinander; mit der Maus verschiebbar),
  Kreisgröße = heutiger Bestand. Zufluss (links, grün gestrichelt), Umbuchung
  (weiß), Abfluss (rechts, rot) als gebogene Pfeile, Stärke nach Menge; Details
  per Tooltip, alles auch als Tabelle.

## 5a. Herkunftsnachweis je Wallet (aufklappbar)

- **Verteilung:** Ringdiagramm des heutigen Bestands — innen mit (grün, ✓) /
  ohne Kaufbeleg (gelb, !), außen die Wallets darin; Hover zeigt Wallet, BTC und
  Anteil. Tabelle je Wallet: BTC mit/ohne Nachweis und aus wie vielen Zuflüssen
  (Eingänge, von denen heute noch etwas da ist), Anteil am Bestand.
- **Strategie 2026:** unbelegte Teilbestände nach „steuerfrei ab“ gruppiert (Haltefrist
  erfüllt / endet 2026 / endet 2027) mit Überlegungen, wie sie bis 31.12.2026
  einen belegten Kauf bekommen (verkaufen und zurückkaufen nach Ablauf der
  Haltefrist, Belege beschaffen) — Referentenentwurf, keine Steuerberatung.

## 6. Privacy-Ansicht

Schalter in der Kopfzeile: maskiert xpubs, TxIDs, Beträge und Adressen. Namen (Wallets,
Gegenstellen, Börsen) bleiben lesbar — außer ein „Name“ ist selbst eine Adresse.
Nur Anzeige; die Sitzung bleibt unverändert. Der Bericht kann maskiert erzeugt
werden (`privacy=true`).

## 7. Sync

`POST /api/sync`: Ableitung (alle Adresstypen bei Plain-`xpub`), Electrum-Abruf
in Paketen (bis 100 Anfragen je Round-Trip, Failover über öffentliche Server),
Auswertung. Tx-Hex und Block-Header liegen im RAM-Cache (`chain_cache`), Kurse
im RAM-Cache des `price_oracle` und werden im Hintergrund geladen.
Fortschritt: `GET /api/sync/progress`.

## 8. Retest-Checkliste

1. Eigene xpubs einfügen (`Name<TAB>xpub`) → Sync.
2. Übersicht: Bestand = Blockchain (keine Warnung).
3. Flows: nur Zu- und Abflüsse; Umbuchungen nur im Pfad.
4. Externe Adressen: Absender/Empfänger gebündelt, Namen aus `local/`.
5. Jahres-Resümee: Jahre, < / ≥ 1 Jahr, Werbungskosten.
6. Interne Fassung öffnen: Mengenabstimmung und jede Wallet-Überleitung mit ✓;
   Kontrolldatei: alle Prüfungen beider Fassungen OK.
7. Privacy an → keine Klartext-Adressen/Beträge, auch im PDF.
8. `DELETE /api/session` → alles weg; keine `*.db` auf Platte.
