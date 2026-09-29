# local/ — deine privaten Dateien (werden NICHT ins Git übernommen)

Die App **liest** diesen Ordner beim Start, bei jedem Sync und per
„Lokale Dateien neu laden“. Sie **schreibt nie** hinein. Alles außer dieser
README ist in `.gitignore` — deine Namen und Börsen-Exporte landen nie auf GitHub.

## `externe-adressen.csv`

Eigene Namen für externe Gegenstellen, Format wie der Export
„Externe Adressen speichern“:

```csv
name,adresse
Relai,bc1q0wym7hvxuy9dvqdesczkscwy6cpv77cavyla55
Bitvavo,3PxY3oQaaxnmPhEcsCkHuCHBYzPFb5ME8k
```

Komma oder Semikolon, mit oder ohne Kopfzeile. Tipp: Export herunterladen,
in Excel die `ext-NNN` umbenennen, als `local/externe-adressen.csv` speichern.
Eigene Namen stehen im Bericht mit Fußnote (Angabe des Steuerpflichtigen). Weißt du nicht,
wohin etwas ging, nenne es z. B. „Dienst unbekannt 2024“ — Namen mit „unbekannt“ sind
keine Zuordnung und bekommen keine Fußnote.

## `zuordnung.csv` (auch `zuordnungen.csv`) — Datumsregel für unbenannte Gegenstellen

Für Zu- und Abflüsse, die sich keiner Börse zuordnen lassen, kannst du eine
eigene Angabe machen: Jede noch unbenannte Gegenstelle (`ext-NNN`), deren
Bewegungen **alle bis einschließlich** dem Datum liegen, bekommt den Namen.

```csv
name,bis
FTX,2022-11-07
```

Namen aus `externe-adressen.csv`, Börsen-Exporten und Labels gehen vor. Im
Finanzamt-Bericht sind solche Namen mit **³** markiert („Zuordnung nach Angabe
des Steuerpflichtigen, nicht belegt“), in der App mit „Angabe“.

## `pruef-xpubs.txt` — xpubs nur für den Adressabgleich

Optional. Je Zeile eine xpub/ypub/zpub, wahlweise mit Namen: `Altes Ledger;xpub…`.
Die Matching-Diagnose prüft damit, ob eine Zieladresse aus einem Börsen-Export (z. B.
ein Relai-Kauf mit Direktversand) zu einer Wallet gehört, die du nicht im Bericht
führst. Diese xpubs erscheinen weder im Bericht noch in der Diagnose — dort steht nur
der Name und der Pfad (z. B. `m/0/5`). Nur öffentliche Schlüssel; private Schlüssel
und Wortlisten werden abgelehnt.

## `check-pp.csv` — Abgleich mit Portfolio Performance (nur Browser)

Exportiere in Portfolio Performance die Umsätze deines Bitcoin-Wertpapiers als
CSV und speichere sie als `local/check-pp.csv`. Existiert die Datei, zeigt die
App die aufklappbare Karte **„Abgleich Portfolio Performance“** — eine
**Korrekturliste** zur Bereinigung des PP-Depots (nicht im Finanzamt-Bericht):

- Oben: PP-Bestand, Blockchain-Bestand, Δ und das Δ nach allen Korrekturen.
- Was stimmt, wird nur gezählt: exakt, Datum verschoben (±7 Tage, gleiche Menge),
  **Sammelbuchung** (mehrere PP-Käufe bis 45 Tage vor einer Auszahlung = eine
  Auszahlung, oder eine PP-Buchung = mehrere Transaktionen), Abgang inkl.
  Netzwerkgebühr.
- Jede offene Zeile ist eine Korrektur mit Vorschlag und **Wirkung** auf den
  PP-Bestand: Menge ändern bzw. Auszahlungs-/Netzwerkgebühr als Auslieferung
  buchen; fehlende Bewegungen (je Jahr und Gegenstelle gebündelt) buchen;
  PP-Buchungen ohne Blockchain-Gegenstück prüfen. ▸ klappt die einzelnen
  PP-Zeilen und Transaktionen auf.
- Filter je Jahr; erledigte Punkte abhaken (merkt sich der Browser).
- **Delta-CSV für PP** (Download nur auf Klick, die App schreibt nie nach
  `local/`): enthält nur, was PP noch fehlt — fehlende Bewegungen, Auszahlungs-
  und Netzwerkgebühren (auch interner Umbuchungen, je Jahr eine Zeile) und
  Mengenkorrekturen als Differenz (Notiz „Korrektur Stück zu Zeile …“), jeweils
  als Ein-/Auslieferung zum Tageskurs. Deine bestehenden PP-Buchungen bleiben
  unverändert — samt Verrechnungskonten und Fremdwährungen; neue Zeilen tragen
  kein Konto (sonst „Buchungswährung passt nicht zu Kontowährung“). PP-Zeilen,
  die nahe (±30 Tage) bei einer ungebuchten Blockchain-Bewegung liegen, werden
  mit der Differenz am Blockchain-Tag korrigiert. PP-Zeilen ohne jedes
  Gegenstück lassen sich per Import nicht löschen: Sie stehen in der Liste
  („in PP löschen“), außer „behalten“ ist angehakt (z. B. Coins noch auf einer
  Börse). Nach dem Download nennt die Karte die Wirkung der Datei und den
  PP-Bestand nach Import und Löschen. Ablauf: PP-Datei sichern → *Datei →
  Importieren → CSV-Dateien → Depotumsätze* (Depot wählen) → gelistete Zeilen
  löschen → neu exportieren als `check-pp.csv` → „neu laden“. Umbuchungen
  zwischen PP-Depots sind nicht enthalten. Die Kopfzeile trägt die Namen des
  PP-Imports (`Wert`, `Wertpapiername`), auch wenn der Export „Alle Buchungen“
  sie `Gesamtpreis`/`Betrag` und `Wertpapier` nennt.
- **Diagnose kopieren (ohne Bestände)**: Text zur Fehlersuche zum Weitergeben —
  nur Zähler, Jahre, Richtung und Abweichung in % des Blockchain-Bestands, keine
  BTC-Mengen, Beträge, Daten, Adressen oder TxIDs. Nach Import und neuem Export
  vergleicht sie die Abweichung mit der zuletzt erzeugten Delta-Datei (nur im
  Arbeitsspeicher) und nennt die wahrscheinliche Ursache (doppelt importiert,
  veralteter Export, Zeilen nicht gelöscht).
- **Vergleich auf der Kommandozeile** (nur lokal, schreibt nichts): PP-Export
  vorher und nachher gegen den Ist-Stand der Blockchain. Den Ist-Stand lädt der
  Link „Ist-Stand (Blockchain) als CSV“ in der PP-Karte herunter. Aufruf im
  Projektordner (Windows):

  ```powershell
  .\.venv\Scripts\python.exe -m btc_origin.pp_diff --vorher alt.csv --nachher neu.csv --ist ist-stand-blockchain.csv --delta pp-import-bitcoin.csv
  ```

  (Linux/macOS: `.venv/bin/btc-origin-pp-diff …`). Zeigt die Bestände, welche
  Zeilen in PP neu sind bzw. entfernt wurden, ob die Delta-Datei genau einmal
  importiert ist, und was nachher noch von der Blockchain abweicht. `--teilen`
  gibt nur Zähler und Anteile in % aus — ohne Beträge, Daten, Adressen.
- In PP separat gebuchte Netzwerkgebühren (kleine Auslieferungen) werden der
  Gebühr eines Abgangs bzw. einer internen Umbuchung zugeordnet.
- Erkannte Spalten (deutsch oder englisch): `Datum`/`Date`, `Typ`/`Type`,
  `Stück`/`Shares`, optional `Wert`/`Value`, `Gebühren`/`Fees`,
  `Wertpapiername`/`Security Name`, `Notiz`/`Note`. `Kauf`/`Einlieferung` =
  Zugang, `Verkauf`/`Auslieferung` = Abgang; `Umbuchung` entfällt; bei
  mehreren Wertpapieren zählen nur Zeilen des Coins selbst („Bitcoin“, „BTC“,
  „Bitcoin (BTC)“) — Fonds, ETP/ETF/ETN und Aktien mit „Bitcoin“ im Namen sowie
  Kontobuchungen ohne Wertpapier nicht (ihre Stückzahl ist keine BTC-Menge). Die
  Karte nennt, was gezählt und was übergangen wurde.

## `erlaeuterungen.csv` und `steuer.csv` — Erläuterungen und Angaben zum Steuerreport

`erlaeuterungen.csv` (`Börse;Datum;Art;Erläuterung`) erläutert Börsenvorgänge, Zuflüsse
ohne Beleg und Abflüsse ohne Verkaufsbeleg. Empfehlungsprämien u. Ä. (§ 22 Nr. 3 EStG) ordnet
`kategorien.yaml` ein (unten).
`steuer.csv` nennt weitere Veräußerungsgeschäfte je Jahr:

```csv
2025;Weitere Veräußerungsgeschäfte;0
```

Einzelheiten: `docs/HERKUNFTSANALYSE.md`.

## `kategorien.yaml` — persönliche Zuordnungen zum Regelwerk

Ergänzt `btc-regeln/kategorien.yaml` um Zuordnungen, die nur dich betreffen: Zeilenarten
deiner Börsen-Exporte, die dort noch fehlen (`zuordnung_export`), und einzelne Zuflüsse, z. B.
eine Empfehlungsprämie, die nicht im Börsen-Export steht (`zuordnung_manuell`: Transaktions-Hash
des Zuflusses, Datum zur Kontrolle). Am einfachsten in der Karte „Regelwerk“ einordnen — sie zeigt
die T-Nummer und schreibt den Hash. Fehlt eine Zeilenart, erscheint die Zeile
im Bericht als „nicht unterstützt“ (Abschnitt 8.3) und wird nicht bewertet.

```yaml
zuordnung_manuell:
  <Transaktions-Hash des Zuflusses>:   # 64 Hex-Zeichen (bei mehreren Zuflüssen: txid:vout)
    kategorie: empfehlung
    datum: 2025-03-01
    erlaeuterung: "Empfehlungsprämie; nicht im Export enthalten"
```

Der Hash bleibt gleich, auch wenn sich T-Nummern verschieben (z. B. nach Hinzufügen einer Wallet
mit älteren Transaktionen). Ältere Einträge per T-Nummer werden noch gelesen und beim nächsten
Übernehmen in der Karte in den Hash umgeschrieben; passt das Datum nicht, bricht der Bericht mit
einer Meldung ab. Aufbau: `btc-regeln/REGELWERK.md`.

## `boersen/` — Konto-Exporte deiner Börsen

Lege die CSV-Exporte deiner Börsen/Broker hier ab, **Dateiname = Name**. Mehrere Dateien einer Börse sind möglich; Zusätze wie `-auszahlungen`, `-orders`, `_2024` gehören nicht zum Namen (`binance-orders.csv` und `binance-auszahlungen.csv` = beide „Binance“):

```
local/boersen/relai.csv
local/boersen/bison.csv
local/boersen/bitvavo.csv
```

Die App sucht in jeder Datei nach Transaktions-IDs (64 Hex-Zeichen) und
benennt die passenden Absender (Auszahlungen an dich) bzw. Ziele
(Einzahlungen von dir) nach dem Dateinamen — egal wie die Spalten heißen.

**Kopfzeile erkannt (getestet mit den Formaten von Relai, Bison, Bitvavo, Strike, BitGo, 21bitcoin, Coinbase und Binance):** Die App liest dann gezielt die Spalten
Datum (`Date…`/`Datum`), BTC-Menge (`BTC Amount`, `Asset (amount)`, `Menge`),
Typ (`Transaction type`) und Asset. Zeilen anderer Coins (ETH …) werden
übersprungen; `Withdraw`/`Auszahlung` passt nur zu Zuflüssen bei dir,
`Deposit`/`Einzahlung` nur zu Abflüssen. Bison-/Relai-interne IDs sind keine
Bitcoin-TxIDs und werden ignoriert.

Enthält eine Zeile eine **Bitcoin-Adresse** (z. B. Relai-Spalte `Destination`),
wird darüber abgeglichen: deine Adresse → Zufluss von dieser Börse, fremde
Adresse → Einzahlung dorthin (Datum ±2 Tage; bei gleicher Adresse entscheidet
das Datum, notfalls der Betrag).

### Käufe und Verkäufe laut Export (BMF-Schreiben 06.03.2025, Rz. 20, 55)

Maßgebend ist der Zeitpunkt des Handels auf der Börse, nicht der Tag, an dem
Coins in deine Wallet kommen oder sie verlassen. Enthält ein Export mit Kopfzeile
Zeilen vom Typ `Buy`/`Kauf`, `Sell`/`Verkauf`, `Withdraw`/`Auszahlung` oder
`Deposit`/`Einzahlung` (EUR-Betrag z. B. Relai `Fiat Amount (excl. fees)`,
Bison `Eur (amount)`; Gebühr `Fee`, ggf. `Fee Currency`), spielt die App das
Börsenkonto nach — auf der Börse gilt FiFo (Rz. 61):

- **Kauf-Seite:** Eine Auszahlung laut Export, die zu einem Zufluss in deine
  Wallet passt (Börse, Datum ±7 Tage, Menge inkl. Auszahlungsgebühr), gibt diesem
  Zufluss **Kaufdatum und Kaufpreis inkl. Gebühren** laut Export. Käufe mit
  Zieladresse (Relai) gelten als Kauf und Auszahlung am selben Tag.
- **Verkauf-Seite:** Einzahlungen aus deinen Wallets auf eine Börse, deren
  Export Verkäufe enthält, werden mit **Verkaufstag, Erlös und Börsengebühr**
  laut Export bewertet. Nicht verkaufte Einzahlungen sind keine Veräußerung.
- Ohne Export gilt der Tag des Zuflusses in bzw. des Abflusses aus den Wallets.
- Der Finanzamt-Bericht dokumentiert den Abgleich je Börse in **Anhang A**.

Der Export sollte den ganzen Zeitraum abdecken, sonst fehlen Käufe bzw. Verkäufe.

**Plausibilität je Auszahlung:** Käufe (Coins vom Börsenkonto) − Auszahlungsgebühr
laut Export − übrige Transaktionskosten = Eingang auf der Wallet laut Blockchain,
auf den Satoshi. Der Bericht zeigt das unter jedem belegten Zufluss (Abschnitt 2)
und je Auszahlung in Anhang A; Anhang C listet alle Export-Zeilen einheitlich.
Hinweis Strike: `FiatAmount` hat keine Währungsspalte — die App nimmt EUR an.

**21bitcoin (z. B. `boersen/21bitcoin.csv`) und andere Exporte im
Buy/Sell-Spaltenformat** (`buy_asset, buy_amount, sell_asset, sell_amount,
fee_asset, fee_amount, transaction_type …`): BTC gegen EUR/USD = Kauf, EUR/USD
gegen BTC = Verkauf; nur BTC hinaus = Auszahlung, nur BTC herein = Einzahlung.
Gebühr in BTC oder EUR; USD wird zum EZB-Kurs umgerechnet.

**Binance-Transaktionshistorie (empfohlen, z. B. `boersen/binance.csv`;
Spalten `User ID, Time, Account, Operation, Coin, Change, Remark`):** Zeilen mit
gleichem Zeitpunkt bilden einen Vorgang — „Transaction Buy/Spend/Fee“ bzw.
„Transaction Sold/Revenue/Fee“ = Kauf/Verkauf mit EUR-Betrag und Gebühr;
„Buy Crypto With Card“ = Kauf (Kartenbetrag steht nicht im Auszug → Kaufdatum laut
Export, Preis Tageskurs); „Withdraw“ = Auszahlung (Menge inkl. Gebühr), „Deposit“ =
Einzahlung; Zinsen/Belohnungen = Kauf ohne Betrag; Umbuchungen zwischen
Binance-Konten (Transfer, Earn) entfallen. Liegt zusätzlich die Order-Historie vor,
wird sie ignoriert (sonst doppelte Käufe) — die App meldet das.

**Binance (z. B. `boersen/binance.csv`, Order-Historie):** Nur ausgeführte Orders
(Status FILLED/PARTIALLY_FILLED). BTC/EUR bzw. BTC/USDT…: BUY = Kauf, SELL = Verkauf,
Menge aus „Executed“, Betrag aus „Trading total“ (USD-Stablecoins wie USD, EZB-Kurs).
Paare mit BTC als Gegenwährung (z. B. ETH/BTC): BUY = BTC hinaus, SELL = BTC herein.
Die Order-Historie enthält **keine Gebühren und keine Auszahlungen** — für die
Zuordnung der Auszahlungen in deine Wallets zusätzlich die Auszahlungs-Historie
(„Withdrawal History“) als weitere Datei ablegen, z. B. `boersen/binance-auszahlungen.csv`;
die App erkennt die Transaktions-IDs darin.

**Coinbase (z. B. `boersen/coinbase.csv`, Transaktionsexport):** Die
Vorspann-Zeilen („Transactions“, „User,…“) werden übersprungen. Buy / Advanced
Trade Buy und BTC-Belohnungen = Kauf (Subtotal + Fees), Sell / Advanced Trade Sell
und Convert von BTC = Verkauf, Convert in BTC (Notiz „… to 0.01 BTC“) = Kauf,
Send = Auszahlung (Empfängeradresse), Receive = Einzahlung. Beträge mit €/$-Zeichen;
USD wird zum EZB-Kurs umgerechnet.

**BitGo (Go Account, z. B. `boersen/bitgo.csv`):** Beträge in **USD**. Ein Kauf
läuft über ein Settlement-Konto: USD gehen an eine Konto-ID, die BTC kommen von
derselben ID (TxID `primesettlement…`) — die App bucht das als **Kauf** mit dem
gezahlten USD-Betrag (nicht dem Marktwert `USD_AMOUNT`), umgerechnet in EUR zum
**EZB-Referenzkurs** des Tages (Anhang C nennt den USD-Betrag). Verkäufe
entsprechend umgekehrt. Auszahlungen/Einzahlungen sind die Zeilen mit
Bitcoin-TxID bzw. -Adresse; `FEE` (BTC) ist die Auszahlungsgebühr. „Fee“-Zeilen
mit Betrag 0 und nicht bestätigte Zeilen werden ignoriert.
Der **Dateiname** ist der Name der Börse und muss zum Namen des Absenders bzw.
Empfängers passen (`bison.csv` → „Bison“).

### Namen ohne TxID

Zeilen **ohne TxID und ohne Adresse** benennen Gegenstellen über **Datum und BTC-Betrag**:

- Datum ±2 Tage (Formate `2024-03-01`, `01.03.2024`, `01/03/2024`)
- BTC-Beträge = Zahlen mit 3–8 Nachkommastellen (`0,00123456` / `0.015`);
  Euro-Beträge (2 Stellen) werden ignoriert
- Auszahlung an dich: Export-Betrag darf um die Auszahlungsgebühr der Börse
  größer sein (bis 0,001 BTC bzw. 3 %)
- Einzahlung zur Börse: praktisch gleicher Betrag
- Zugeordnet wird nur bei **eindeutigem** Treffer — sonst lieber gar nicht.

**Hinweis OneDrive:** Liegt das Projekt in OneDrive, wird auch dieser Ordner in
die Microsoft-Cloud synchronisiert.
