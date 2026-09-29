# Herkunftsanalyse Bitcoin — Bericht fürs Finanzamt

PDF, im Browser geöffnet über die Karte „Finanzamt: Herkunftsanalyse Bitcoin“
bzw. `GET /api/report/herkunft.pdf?stichtag=YYYY-MM-DD&name=…&steuer_id=…&privacy=true`.
Erzeugt nur auf Klick, nur im Speicher; Name und Steuer-ID werden nirgends
gespeichert. Code: `src/btc_origin/origin_report.py` (Aufbau, Layout-Hilfen) und
`src/btc_origin/report_sections/` (je Abschnitt ein Modul) für die Darstellung,
`api/routes/report.py::_build_herkunft_report` (Daten).

**Keine Steuerberatung.** Der Bericht ist eine Arbeitsgrundlage; er richtet sich
nach dem BMF-Schreiben vom 06.03.2025 (GZ IV C 1 - S 2256/00042/064/043,
„Einzelfragen zur ertragsteuerrechtlichen Behandlung bestimmter Kryptowerte“).

## Aufbau — vom Einfachen zum Detail

| Teil | Inhalt |
|------|--------|
| Kopf | Titel „Herkunftsanalyse Bitcoin – Name“ (ohne Name nur der Titel), Stand (Datum/Uhrzeit), Stichtag, optional Name und Steuer-ID, Anzahl Wallets. Fußzeile: „Erstellt mit BTC-Herkunft <Commit-ID>“ (aus `.git` bzw. `BTC_ORIGIN_COMMIT`) und Seitenzahl. |
| **Zusammenfassung** (Seite 1) | Die wichtigsten Ergebnisse mit Verweis auf den Abschnitt: Wallets (davon mit Bestand), Bestand zum Stichtag (€), Abstimmung mit der Blockchain, unbelegte Teilbestände in der Haltefrist mit „steuerfrei spätestens ab“ (`summary_rows`). |
| **Stichtag in der Zukunft** | Liegt der Stichtag nach dem Erstellungstag, heißt es in Zusammenfassung, Abschnitt 1 und allen Überleitungen „Bestand am <Erstellungstag>“ mit dem Hinweis „Stichtag … liegt in der Zukunft; Bestand bis dahin fortgeschrieben ohne weitere Bewegungen“. |
| **Inhaltsverzeichnis** (Seite 2) | Alle Abschnitte und Anhänge mit Seitenzahl, anklickbar; darunter „So ist der Bericht aufgebaut“ (`aufbau_text`, je Fassung); dieselben Einträge als Lesezeichen im PDF (`start_section`, `insert_toc_placeholder`). |
| **1 Betrachtete Wallets** | Wallet, xpub, Aktivitätszeitraum, Transaktionen. **Bestand zum Stichtag** nur für Wallets mit Bestand (Wert zum Tageskurs des Stichtags; liegt er in der Zukunft, zum heutigen Kurs); übrige Wallets als Satz „Bestand 0 BTC“. **Mengenabstimmung:** Zuflüsse − Abflüsse − Transaktionsgebühren (Abflüsse / Umbuchungen) = Bestand = unverbrauchte Einzelbeträge (UTXO) laut Blockchain. |
| **2 Zu- und Abflüsse je Wallet** (neue Seite) | Wallets nach erstem Zufluss sortiert (älteste zuerst). Je Wallet jeder Zufluss von einer fremden Adresse: Zuflusstag, Anschaffung, Herkunft, Tx, Menge, Kurs, Anschaffungswert, heute vorhanden → **Zwischensumme**. Dann jeder **Abfluss an eine fremde Adresse** im gleichen Aufbau (Abfluss, Veräußerung, Empfänger, Tx, Menge, Kurs, Veräußerungspreis, Gewinn/Verlust; darunter „+ Gebühr“ und „= Abgang laut Blockchain“) → Zwischensumme (Abgang inkl. Gebühren). Danach die **Überleitung**: + Umbuchung von „X“ / − Umbuchung an „Y“ (je Gegen-Wallet eine Zeile) − Zwischensumme Abflüsse (inkl. Gebühren) − Gebühren der Umbuchungen = Bestand am Stichtag (✓ = stimmt mit Blockchain). Menge, Kurs und Anschaffungswert von Zuflüssen **ohne Kaufbeleg** stehen in **Blau**. Überleitungszeilen mit 0 BTC entfallen (Zwischensumme und Bestand bleiben). Wallets nur mit Umbuchungen und am Stichtag leer heißen **Durchgangs-Wallet** (keine Anschaffung/Veräußerung, Haltefrist läuft weiter). Am Kapitelende **2.1 Übersicht: Fluss zwischen den Wallets** — statische Fassung der Lot-Fluss-Grafik mit denselben Zahlen: Kreise = Wallets (Größe = Bestand am Stichtag, nach Alter im Uhrzeigersinn), links Zuflüsse (grün mit / blau ohne Kaufbeleg), graue Pfeile = Umbuchungen, rechts rote Abflüsse; Menge an jeder Linie. |
| **3 Mögliche Gewinne und Verluste** | Möglicher Gewinn/Verlust je Jahr, getrennt < 1 Jahr / ≥ 1 Jahr: Veräußerungspreis, Anschaffungskosten, Werbungskosten, Freigrenze als Hinweis. |
| **3.1 Veräußerungen auf Börsen ohne Abfluss aus den Wallets** | Nur wenn vorhanden: Verkäufe laut Börsen-Export, deren BTC nicht aus den Wallets stammen (dort gekauft oder aus anderer Quelle eingezahlt) — Verkauf, Anschaffung, Tage, Menge, Veräußerungspreis, Anschaffungskosten, Werbungskosten, Gewinn/Verlust, Börse · Herkunft. Sonst ist die Jahresübersicht 3.1. |
| **3.2 Jahresübersicht für die Anlage SO** | Eine Zeile je Kalenderjahr mit Bewegungen (lückenlos): Anzahl Veräußerungsgeschäfte < 1 Jahr (ein Abfluss bzw. ein Verkauf laut Export, ggf. mehrere Teilbestände), Gewinn/Verlust < 1 Jahr, Hinweis („keine Veräußerung“, „nur nach Ablauf der Haltefrist“, Freigrenze erreicht/nicht erreicht). Hinweis: Freigrenze gilt für alle privaten Veräußerungsgeschäfte zusammen — andere Geschäfte nicht erfasst. |
| **4 Belege und Ersatzwerte** | Kurz und sachlich: Haltefrist (§ 23 EStG), Rechtslage (BFH IX R 3/22; BMF 10.05.2022/06.03.2025, Rz. 87 ff., 106), Aufbewahrung (§ 147a AO, Rz. 105), Ersatzwerte ohne Beleg (Rz. 91) und Schätzung (Rz. 92, BFH VI R 11/07) — je ein Satz (`EXPLAIN_EVIDENCE`). Kommen insolvente Plattformen als Gegenstelle vor (FTX, Mt. Gox, Celsius, Voyager, BlockFi — auch per `zuordnung.csv`), folgt je Plattform ein Hinweis mit Auszahlungsstopp und Insolvenzantrag (`DEFUNCT_PLATFORMS`). |
| **5 Nicht durch Belege nachgewiesene Transaktionen** | Zuflüsse ohne Kaufdaten aus einem Börsen-Export (Anschaffung = Zuflusstag, Tageskurs) mit Wert und heute vorhandener Menge. Abflüsse ohne Verkaufsbeleg stehen in Abschnitt 2 (Veräußerung „angenommen“) und Abschnitt 3. |
| **6 Rechtsgrundlagen und Quellen** | § 23 EStG und die verwendeten Randnummern des BMF-Schreibens mit Kurztext; am Ende „Ausblick: Kryptosteuer-Reform (kein geltendes Recht)“. |
| **7 Methodik und Annahmen** | Datenquelle, Verwendungsreihenfolge, Umbuchungen, Gebühren, Kursregel (mit Anzahl Tage je Quelle), Annahmen. |
| **8 Unklare Transaktionen: unbelegte Käufe innerhalb der Haltefrist** | **8.1** Noch gehaltene Teilbestände ohne Kaufbeleg, deren Haltefrist zum Stichtag nicht abgelaufen ist: Zufluss (= Anschaffung), Herkunft, Wallet, Menge, Tage, **steuerfrei ab** (Tag nach dem Jahrestag, § 23 Abs. 1 Satz 1 Nr. 2 EStG), Wert. **8.2** Hinweis auf unbelegte Teilbestände im Altbestand (Kryptosteuer-Reform, siehe unten). |
| **9 Erläuterungen für Leser ohne Bitcoin-Vorkenntnisse** (neue Seite, vor den Anhängen) | Allgemeinverständlich: Blockchain, Wallet/Adresse/xpub, Bitcoin-Beträge wie Geldscheine (Wechselgeld), Zufluss/Umbuchung/Abfluss, Gebühren, Börse und Wallet, Lightning, Haltefrist — mit Randnummern (Text: `EXPLAIN_BASICS`). |
| **Anhang A** | Abgleich mit Börsen-Exporten (nur mit Exporten in `local/boersen/`): Dateitabelle mit Zahlen **je Datei** (mehrere Dateien einer Börse werden gemeinsam ausgewertet) und, falls angegeben, die Herkunft der Datei unter dem Dateinamen (Art „Datei“ in `erlaeuterungen.csv`); je Auszahlung: Käufe − Auszahlungsgebühr − Transaktionskosten = Eingang Wallet (✓). |
| **Anhang B** | Nicht zugeordnete Börsenvorgänge (nur wenn vorhanden): Auszahlungen und Käufe mit Direktversand ohne Zufluss, Einzahlungen ohne Abfluss aus den Wallets — Börse, Datum, Art, Menge, Status, Erläuterung aus `local/erlaeuterungen.csv` (sonst „offen“). |
| **Anhang C** | Belegte Käufe und Verkäufe: alle Export-Zeilen einheitlich (Datum, Art, Menge, EUR, Gebühr, Kurs) mit Zuordnung zum Zufluss. |

**Begriffe:** *Teilbestand* = der Teil eines Zuflusses mit eigenem
Anschaffungsdatum und eigener Herkunft (eine Zeile in Abschnitt 2 und im Nachweis
Altbestand). *Einzelbetrag (UTXO)* = technischer Baustein der Blockchain
(„Geldschein“), aus dem Zahlungen bestehen. „Coin“ wird bewusst nicht verwendet.

**Zeichenlegende je Seite:** Jedes Zeichen, das auf einer Seite vorkommt (¹ belegt laut Export, ² nach dem Stichtag, ³ eigene Angabe, ⁴ Alt-/Neubestand, ↳ einzelner Kauf, ✓/✗ Abgleich mit der Blockchain, Jahr mit * fehlender Kurs), wird am Fuß genau dieser Seite erklärt (`_mark_legend`).

`privacy=true` maskiert xpubs, Adressen, TxIDs, Beträge und die Steuer-ID. Namen (Person, Wallets, Gegenstellen, Börsen) bleiben lesbar — außer ein „Name“ ist selbst eine (gekürzte) Adresse.

## Zwei Fassungen: intern und Finanzamt

Aus denselben Daten entstehen zwei PDF-Fassungen (`GET /api/report/herkunft.pdf?fassung=intern|finanzamt`,
Standard `intern`). Die Datei heißt `herkunftsanalyse-bitcoin-<Stand>[-bis-<Datum>]-<fassung>.pdf`.
Die Fußzeile trägt in beiden Fassungen denselben Build-Hash.

- **`bis=<Datum>` (beide Fassungen, in der Oberfläche „nur Vorgänge bis zum Stichtag“):** Der
  Bericht enthält nur Vorgänge bis zu diesem Tag (Tagesende UTC). Das gilt für die Blockchain
  und für die Börsen-Exporte. Abstimmung und Überleitungen erfolgen zu diesem Tag. Wallets
  ohne Vorgänge bis zu diesem Tag werden nicht aufgeführt.
- **Kurzreferenzen (beide Fassungen):** In allen Tabellen steht statt der Transaktions-ID eine
  Kurzreferenz T-001, T-002, … Die Nummern werden chronologisch nach Blockzeit vergeben, bei
  gleicher Blockzeit nach TxID (`build_tx_refs`). Bei unveränderten Daten bleiben sie in jedem
  Lauf gleich; ein `bis` behält die Nummern früherer Vorgänge. Der letzte Anhang **D
  „Transaktionsverzeichnis“** nennt je Transaktion Referenz, Datum, Wallet, Art (Zufluss,
  Abfluss, Umbuchung) und die vollständige Transaktions-ID. Sie steht in Monospace, einzeilig,
  ohne Trennzeichen und ist direkt aus dem PDF kopierbar.

### Finanzamt-Fassung

Die Finanzamt-Fassung belegt jedes Veräußerungsgeschäft prüfbar. Bestände und Kennungen ohne
Bezug zu einer Veräußerung zeigt sie nicht:

- **Seite 1 und Kopfzeile:** Seite 1 trägt einen Pflichthinweis (was fehlt, Vorlage auf
  Anforderung, Rz. 87, 101–104), jede Seite „Finanzamt-Fassung“ in der Kopfzeile.
- **Freigabeliste:** Im Transaktionsverzeichnis stehen nur die Transaktionen jedes Abflusses,
  der als Veräußerung gilt (angenommen oder belegt), und jedes Zuflusses, aus dem ein
  veräußerter Teilbestand stammt. Alle übrigen erscheinen in den Tabellen als „T-042°“
  (Fußnote „° Transaktions-Hash auf Anforderung“) und fehlen im Verzeichnis. Gekürzte Hashes werden nie abgedruckt. Verkäufe auf
  einer Börse ohne Abfluss brauchen keine TxID; Beleg ist der Export.
- **Entfernt:**
  - xpubs und Adressen („auf Anforderung“);
  - die Tabelle „Bestand am …“ und die Bestandszeile der Zusammenfassung;
  - die Mengen der Mengenabstimmung (Positionen und ✓ bleiben);
  - die Spalte „Heute vorhanden“ in den Abschnitten 3 und 6;
  - die Zeile „= Bestand“ jeder Überleitung (stattdessen ✓/✗);
  - in der Flussgrafik (2.1) Zahlen und Bestand: Die Linienstärke folgt weiter der Menge,
    die Kreisgröße dem Durchfluss der Wallet (alles, was hineinkam) statt dem Bestand;
  - Abschnitt 8.2 und der Reform-Ausblick;
  - in Abschnitt 8.1 die Mengen und Werte.
- **Bleibt:** Zu- und Abflussmengen in Abschnitt 2 bleiben bewusst stehen, sonst wäre die
  Herkunft der Veräußerungen nicht prüfbar. Unverändert bleiben alle Daten, Kurse, Werte,
  Gewinne, Gebühren, Fußnoten, die Abschnitte 3 bis 3.2, 4, 6 (ohne Ausblick) und 7 sowie die
  Anhänge A–C.
- **Echte Entfernung:** Entfernte Inhalte werden gar nicht erst gezeichnet. Zusätzlich
  ersetzt ein Filter vor dem Zeichnen jede Adresse, jeden Schlüssel und jede nicht
  freigegebene (auch gekürzte) Kennung durch „auf Anforderung“. Es gibt keine Links auf
  Block-Explorer.

### Kontrolldatei

`GET /api/report/herkunft-kontrolle.txt` (nur Download) erzeugt beide Fassungen aus
demselben Lauf und prüft sie. Die Kontrolldatei enthält die Prüfprotokolle und die
Freigabeliste (T-Nummer und TxID); sie ist nicht Teil des PDF. Geprüft werden:

- **(1)** Jede T-Nummer ist im Verzeichnis aufgelöst oder mit ° („auf Anforderung“) markiert.
- **(2)** Jede Transaktions-ID des Verzeichnisses ist im Text eine zusammenhängende Zeichenkette.
- **Nur Finanzamt-Fassung:**
  - (3a) keine xpubs oder Adressen;
  - (3b) keine 64-stelligen Hashes außerhalb der Freigabeliste;
  - (3c) die abgedruckten TxIDs entsprechen genau der Freigabeliste;
  - (3d) keine Bestandsangaben;
  - (3e) Metadaten ohne Wallet-Daten, keine externen Links, Anhänge oder Ebenen.
- **(4)** Abschnitt 3 bis 3.2 ist in beiden Fassungen identisch; wiederholte Tabellenköpfe
  nach einem Seitenumbruch zählen nicht.
- **(5)** Jede Fußnote ist auf ihrer Seite erklärt, das Inhaltsverzeichnis stimmt.

Mit installiertem `pymupdf` liest die Kontrolldatei den Text aus dem fertigen PDF, sonst
aus den gezeichneten Texten.

## Nacherklärung (Entwurf)

Erreicht in einem Jahr der Gewinn aus Veräußerungen innerhalb eines Jahres die
Freigrenze, zeigt die Karte „Finanzamt“ einen Hinweis mit Jahresauswahl und
dem Link „Nacherklärung (Entwurf) öffnen“
(`GET /api/report/nacherklaerung.pdf?jahre=…`, `render_nacherklaerung_pdf`).
Das PDF ist bewusst **keine fertige Selbstanzeige**:

1. **Anschreiben** mit Platzhaltern (Anschrift, Finanzamt, Steuernummer, Datum)
   und zwei Textvarianten — § 153 AO (Berichtigung) oder § 371 AO
   (Selbstanzeige) —, verweist auf die beigefügte Herkunftsanalyse.
2. **Einkünfte je Jahr** (Zuarbeit Anlage SO): Veräußerungspreis,
   Anschaffungskosten, Werbungskosten, Gewinn/Verlust, Freigrenze, Ergebnis.
3. **Einzelaufstellung** jeder Veräußerung < 1 Jahr (Rz. 102) mit Grundlage
   („lt. Export“ / „Verkauf angenommen“).
4. **Checkliste:** § 153 vs. § 371 AO, Vollständigkeit (alle Taten der Steuerart,
   mind. zehn Jahre, § 371 Abs. 1), Sperrgründe (§ 371 Abs. 2, § 398a),
   Nachzahlung mit Hinterziehungszinsen (§ 371 Abs. 3, § 235), Festsetzungsfrist
   (§ 169 Abs. 2), Annahmen der Aufstellung, Verlustverrechnung.

Vor dem Einreichen mit Steuerberatung bzw. Fachanwalt für Steuerrecht klären.

## Kryptosteuer-Reform: Alt- und Neubestand (Entwurf)

Nach dem Referentenentwurf des BMF (September 2026, **nicht beschlossen**) sollen
Gewinne aus Kryptowerten, die nach dem 31.12.2026 angeschafft werden, mit 25 %
Abgeltungsteuer ohne Haltefrist besteuert werden. Für den **Altbestand**
(angeschafft bis einschließlich 31.12.2026) soll die bisherige Haltefrist
bleiben. Ohne Nachweis von Anschaffungszeitpunkt und -kosten soll laut
Berichten zum Entwurf pauschal 50 % des Erlöses angesetzt werden.

Das Tool rechnet weiterhin nur nach geltendem Recht und kennzeichnet lediglich
(`REFORM_CUTOFF`, `is_altbestand` in `origin_report.py`):

- **App, Karte „Flows“:** Zuflüsse mit Bestand tragen „Altbestand“,
  „Altbestand · unbelegt“ (rot: kein Kaufbeleg) oder „Neubestand“.
- **Herkunftsanalyse:** Abschnitt 8.2 (unbelegte Teilbestände
  im Altbestand), Ausblick am Ende von Abschnitt 6.
- **Nachweis Altbestand (eigenes PDF)** — `GET /api/report/altbestand.pdf`,
  Link „Nachweis Altbestand öffnen“ in der Karte „Finanzamt“
  (`render_altbestand_pdf`): heute gehaltene Bitcoin, angeschafft bis 31.12.2026.
  1 Übersicht (Altbestand gesamt, davon belegt / nur Blockchain, Neubestand,
  Bestand heute), 2 Wallets mit xpub, 3 Einzelaufstellung je Teilbestand in zwei Listen —
  **3.1 mit Kaufbeleg** (Beleg laut Export) und **3.2 ohne Kaufbeleg** (Herkunft;
  Besitz laut Blockchain ab Zufluss) — je mit Anschaffung, Wallet, Menge,
  Anschaffungskosten, steuerfrei ab, Zufluss-Tx, Adresse heute und Summe, 4 Erläuterungen. Vor dem Stichtag
  mit Hinweis „nach dem Stichtag erneut erzeugen“. `privacy=true` maskiert
  xpubs, TxIDs, Adressen und Beträge.

## Berechnungsregeln

### Verwendungsreihenfolge (Rz. 61, 56, 62)

- **Einzelbetrachtung:** Jeder ausgegebene Einzelbetrag (UTXO) ist aus der Blockchain
  bekannt; die App folgt ihm über alle Umbuchungen bis zum Abfluss.
- **Wechselgeld** führt die ursprünglichen Anschaffungsdaten fort (Rz. 56).
- Nur wo mehrere Einzelbeträge in **einer** Transaktion zusammengeführt werden, gilt
  innerhalb dieser Transaktion FiFo (zuerst der Abfluss, dann die Gebühr, dann
  die eigenen Ausgänge).
- Einheitlich für alle Wallets und Jahre (Rz. 62, 103).

### Umbuchungen (Rz. 54)

Bewegungen zwischen den betrachteten Wallets sind keine Veräußerung (keine
Übertragung auf Dritte); das Anschaffungsdatum bleibt erhalten. Ihre Gebühren
mindern den Bestand, sind aber keine Werbungskosten.

### Gewinn/Verlust (§ 23 Abs. 3 Satz 1 EStG, Rz. 57, 59)

- Veräußerungspreis − Anschaffungskosten − Werbungskosten.
- **Werbungskosten** = Netzwerkgebühr der Veräußerungstransaktion, nach Menge
  ganzzahlig auf die Teilbestände der Transaktion verteilt (Aufteilung auf steuerbare
  und nicht steuerbare Teile, Rz. 57); bei Börsenverkäufen zusätzlich die
  Börsengebühr laut Export.
- Haltedauer < 1 Jahr: privates Veräußerungsgeschäft (§ 23 Abs. 1 Satz 1 Nr. 2
  EStG); ≥ 1 Jahr: nicht steuerbar; keine 10-Jahres-Frist für Bitcoin (Rz. 63).
- **Unbekannte Anschaffung** (Verkauf laut Export ohne bekannte Bitcoin) zählt
  vorsichtshalber als < 1 Jahr.
- Freigrenze (§ 23 Abs. 3 Satz 5 EStG): 600 € bis 2023, 1.000 € ab 2024 — nur
  Hinweis; sie gilt für alle privaten Veräußerungsgeschäfte des Jahres zusammen.

### Zeitpunkte (Rz. 20, 55)

| Fall | Anschaffung | Veräußerung |
|------|-------------|-------------|
| Ohne Börsen-Export | Tag des Zuflusses in die Wallets | Tag des Abflusses an eine fremde Adresse (**Annahme**: Abfluss = Verkauf) |
| Export mit Käufen und Auszahlungen | Kaufdatum(e) und Kaufpreis inkl. Gebühren laut Export | — |
| Export mit Verkäufen | — | Verkaufstag, Erlös und Börsengebühr laut Export; nicht verkaufte Einzahlungen sind keine Veräußerung |

Abflüsse an eigene, hier nicht erfasste Wallets würden fälschlich als Verkauf
gezählt — deshalb alle eigenen xpubs einfügen.

### Börsen-Exporte (`exchange_sales.py`)

Je Börse wird das Konto aus dem Export nachgespielt. Der Börsenname kommt aus dem
Dateinamen; Zusätze wie `-auszahlungen`, `-orders`, Jahreszahlen oder `-old`/`-alt`
(früheres Konto derselben Börse) werden ignoriert — `bitvavo.csv` und
`bitvavo-old.csv` gehören beide zu „Bitvavo“.

- **Bestand auf der Börse:** dort gekaufte Bitcoin (Kaufdatum, Preis inkl. Gebühr)
  und Einzahlungen aus den Wallets (Anschaffungsdatum aus der Einzelbetrachtung).
- **Verkäufe und Auszahlungen** entnehmen Bitcoin nach **FiFo** (Rz. 61) — auf
  dem Börsenkonto sind Bitcoin nicht einzeln unterscheidbar.
- **Kauf-Seite:** Eine Auszahlung laut Export wird einem Zufluss zugeordnet,
  wenn die Börse übereinstimmt (Absendername oder Zieladresse im Export), das
  Datum höchstens 7 Tage abweicht und die Menge laut Export dem Zufluss plus
  höchstens der Auszahlungsgebühr (max. 0,001 BTC bzw. 3 %) entspricht. Jeder
  Zufluss und jede Auszahlung wird einmal verwendet. Der Zufluss erhält dann die
  Kaufdaten der ausgezahlten Bitcoin; besteht er aus mehreren Käufen, wird er in
  Kauf-Teile aufgeteilt. Die Auszahlungsgebühr ist nicht Teil der
  Anschaffungskosten. Decken die Käufe im Export davor nicht die ganze Auszahlung
  (z. B. zuvor per Lightning erhaltene Bitcoin), werden die Käufe nicht
  hochgerechnet: Der Rest erscheint als „↳ ohne Kaufbeleg — im Export kein Kauf
  davor“ (Anschaffung = Zuflusstag, Tageskurs, blau) und zählt in Abschnitt 5.
- **Plausibilität je belegtem Zufluss:** Abschnitt 2 zeigt unter dem Zufluss jeden
  Kauf mit Menge und Kaufpreis laut Export, dann „− Auszahlungsgebühr lt. Export“
  und „− Transaktionskosten“ (Rest, falls die Börse sie nicht einzeln ausweist) =
  Eingang laut Blockchain (✓). Zwei Buchungsarten werden erkannt: Menge laut Export
  inkl. Gebühr (brutto) oder Gebühr zusätzlich belastet (netto). Die Gebühren
  mindern die Menge, nicht den Kaufpreis je BTC.
- **Einzahlungen:** Ein Abfluss aus den Wallets an eine Börse, deren Export eine
  passende Einzahlung enthält (±7 Tage, Menge bis auf Rundung — höchstens 100 sats bzw. 0,1 %), ist eine
  Umbuchung auf das eigene Börsenkonto (Rz. 54), keine Veräußerung — auch wenn der
  Export keine Verkäufe hat. Die Teilbestände gehen mit ihrem Anschaffungsdatum in
  den FiFo-Bestand der Börse; eine spätere Auszahlung besteht aus diesen alten und
  den neu gekauften Teilbeständen. „Veräußerung angenommen“ bleibt nur für Abflüsse
  an Börsen ohne Export, ohne passende Einzahlung im Export oder an unbekannte
  Gegenstellen. Anhang A nennt Einzahlungen laut Export getrennt nach „aus den
  betrachteten Wallets“ und „aus unbekannter Quelle“; Verkäufe von BTC, die nicht aus
  den Wallets stammen, sind in Anhang C gekennzeichnet.
- **Satoshi-Test** (⁶): Eine kleine Einzahlung aus den Wallets (≤ 0,001 BTC, ab 30.12.2024),
  die binnen 7 Tagen mit einer mindestens zehnmal größeren Auszahlung zurückgeht, ist ein
  Nachweis der Wallet-Inhaberschaft nach der EU-Geldtransferverordnung (EU) 2023/1113
  (Pflicht der Börse ab 1.000 € zwischen Börse und eigener Wallet). Kapitel 3 zeigt beim
  Abfluss „keine — Satoshi-Test ⁶“, beim Zufluss „Satoshi-Test ⁶, zurück“; Anhang A zählt sie.
- **Kaufpreis ohne Betrag im Export** (z. B. Binance-Kartenkauf): Tageskurs des
  Kauftags, gekennzeichnet „(Tageskurs)“. **Tausch** gegen einen anderen Kryptowert
  (z. B. Paar ETH/BTC): gekennzeichnet (⁵, Rz. 54), nicht bewertet.
- **Gesamtstatus:** Zusammenfassung und Abschnitt 1 zeigen ✗, sobald eine
  Einzelprüfung in Abschnitt 2 ✗ ist — mit Anzahl und Fundstellen (Datum, Wallet).
- **Matching-Diagnose** (Button „Matching-Diagnose (.md)“, `GET /api/report/matching-diagnose.md`):
  nur für den Steuerpflichtigen, nicht Teil des PDFs, echte Werte, Datei speichert erst
  der Browser. Enthält den Prüfstatus (Zusammenfassung, Einzelprüfungen ✗, Überleitungen
  je Wallet, Kurse/Tausch, Einzahlungen je Börse) und für jede nicht zugeordnete
  Export-Auszahlung bzw. jeden Zufluss ohne Kaufdaten den besten Kandidaten (±30 Tage)
  mit Abstand, Mengendifferenz und Grund. Abschnitt 3 prüft die Zieladressen nicht zugeordneter
  Auszahlungen/Direktkäufe: eigene Wallet ja/nein und Zuflüsse dort (Tage, % Abweichung) — ohne die
  Adresse zu zeigen. Gehört die Adresse zu keiner Wallet, sucht die Diagnose weiter: in allen xpubs
  bis Index 1000 (hinter dem Gap-Limit), in den Prüf-xpubs aus `local/pruef-xpubs.txt` und auf der
  Blockchain (Eingang wann, dann bis zu 3 Schritte: eigene Wallet, Label-Pack, noch dort). Die
  Zuordnungsregel selbst bleibt unverändert.
- **Rückbuchungen** (z. B. Strike „Send … Reversal“ mit positiver Menge, auch „refund“,
  „storno“, „fehlgeschlagen“): Die Rückbuchung und der fehlgeschlagene Vorgang gleicher
  Menge (± Gebühr, ±1 Tag) heben sich auf — beides zählt nicht als Auszahlung.
- **Rückläufe:** Eine Auszahlung, die binnen eines Tages als Einzahlung unbekannter Herkunft
  wieder gutgeschrieben wird (Menge minus Gebühr, höchstens 1.000 sats bzw. 1 %), hat das
  Börsenkonto nicht verlassen (z. B. Auszahlung an eine eigene Einzahlungsadresse). Beide
  zählen nicht; nur die Gebühr geht ab. Die spätere echte Auszahlung erhält die Käufe.
- **Käufe mit Zieladresse** (z. B. Relai) gelten als Kauf und Auszahlung am selben Tag.
- Zwei Durchläufe: Lots ohne Börsendaten → Konten nachspielen → Zuordnung →
  Lots mit Kaufdaten. Zwischengespeichert, bis sich Flows oder `local/` ändern.
- **Anhang A** dokumentiert je Börse: Dateien, Anzahl Käufe/Verkäufe/Aus- und
  Einzahlungen, zugeordnete Auszahlungen (mit Kaufdaten), nicht zugeordnete
  Auszahlungen, Verkaufs-Seite (verkauft, verblieben, zurück ausgezahlt,
  unbekannte Herkunft) und die Regeln.

### Struktur eines Exports prüfen, ohne Werte weiterzugeben (`export_check`)

Liest ein Export nicht so, wie erwartet, zeigt dieses Prüfskript die Struktur —
ohne Beträge, Adressen, IDs oder E-Mail-Adressen (Zahlen nur als Form, z. B.
„−Zahl(8 NK)“; Verhältnisse wie Gebühr/Menge in %). Die Ausgabe kann man weitergeben.

```
.venv\Scripts\python.exe -m btc_origin.export_check local\boersen\strike.csv
.venv\Scripts\python.exe -m btc_origin.export_check local\boersen\strike.csv --datum 2024-03-15
```

Mit `--datum` erscheinen alle Zeilen dieses Tages; am Ende steht, wie das Tool die
Datei liest (Kauf, Auszahlung, … mit Gebühr/Menge in %). Das Skript liest nur.

### Erläuterungen zu nicht zugeordneten Börsenvorgängen (`local/erlaeuterungen.csv`)

Optional, eine Zeile je Erläuterung: `Börse;Datum;Art;Erläuterung`, z. B.
`Kraken;;Auszahlung;Testüberweisung` oder `Kraken;01.03.2024;Auszahlung;zweites Konto,
Abrechnung angefordert`. Leeres Datum bzw. leere Art gilt für alle Vorgänge dieser
Börse; die erste passende Zeile gewinnt. Der Text erscheint in Anhang B; ohne
Eintrag steht dort „offen“. `Auszahlung` gilt für alle Auszahlungen; `Auszahlung mit
Zufluss` bzw. `Auszahlung ohne Zufluss` nur für die mit bzw. ohne passenden Zufluss.
Art **`Zufluss`** erläutert Zuflüsse ohne Beleg in
Abschnitt 5 (Börse = Absender, Datum leer = alle Zuflüsse dieses Absenders), z. B.
`Kraken;;Zufluss;zweites Konto, Abrechnung angefordert`. Art **`Abfluss`** erläutert
Abflüsse ohne Verkaufsbeleg (Veräußerung angenommen), die Abschnitt 5 am Ende eigens
auflistet (Börse = Empfänger, z. B. `ext-007;;Abfluss;Zahlung für eine Dienstleistung`).
Art **`Datei`** nennt die Herkunft einer Exportdatei in Anhang A (Name = Dateiname, Datum =
Bereitstellung), z. B. `kraken-alt.csv;15.09.2026;Datei;von der Börse auf Anfrage per E-Mail
bereitgestellt` — CSV-Dateien sind Belege (Rz. 101–103); die Herkunft macht die Belegkette
nachvollziehbar (Originaldatei und Begleit-E-Mail unverändert aufbewahren).
Einkünfte nach § 22 Nr. 3 EStG (z. B. Empfehlungsprämien) ordnet nicht diese Datei ein, sondern
`kategorien.yaml` (siehe Regelwerk unten).
Trennzeichen `;` oder `,` gelten je Zeile. Die Datei liegt wie alles in `local/` nur bei dir.

### Angaben zum Jahressteuerreport (`local/steuer.csv`)

Optional. Je Zeile `Jahr;Weitere Veräußerungsgeschäfte;Betrag` — Gewinn (oder Verlust mit
`-`) aus anderen privaten Veräußerungsgeschäften des Jahres, die nicht in diesem Report
stehen; sie zählen für die Freigrenze mit. Ohne Zeile: „keine angegeben“.

```csv
2025;Weitere Veräußerungsgeschäfte;0
```

### Regelwerk (`btc-regeln/`)

Steuerrechtliche Parameter stehen nicht im Code, sondern je Veranlagungsjahr in
`btc-regeln/regeln/<JJJJ>.yaml` (Haltefrist, Freigrenzen § 23 und § 22 Nr. 3, BMF-Schreiben,
Kursregel, Stand der Kryptosteuer-Reform mit Stichtag Altbestand, Textbausteine der
Rechtsgrundlagen); Kategorien für Zuflüsse und Export-Zeilen in `btc-regeln/kategorien.yaml`.
Aufbau, Validierung und jährliche Pflege: [`btc-regeln/REGELWERK.md`](../btc-regeln/REGELWERK.md).

- Jede Veräußerung wird mit der Regeldatei ihres Jahres bewertet; die Rechtsgrundlagen
  (Abschnitt 6) kommen aus der Datei des Berichtsjahres (Stichtag). Die Methodik nennt je
  Jahr „Regelwerk VZ <JJJJ>, Stand <Datum>“, die Kontrolldatei zusätzlich SHA-256 der Dateien.
- Fehlt die Datei eines benötigten Jahres (Jahr mit Veräußerung oder Berichtsjahr) oder ist
  eine Datei ungültig, wird kein Bericht erstellt; mit ungültigen Dateien startet die App nicht.
- Persönliche Zuordnungen einzelner Zuflüsse (`zuordnung_manuell`, per Transaktions-Hash mit Datum)
  gehören in `local/kategorien.yaml`, nicht ins Repository.
- **Kategorien der Export-Zeilen:** Jede Zeilenart einer Börse (Wert der Art-Spalte, z. B.
  `Buy`, `Send`; wo sie die Richtung nicht festlegt mit der Lesart des Parsers, z. B.
  `trade – Kauf`) braucht eine Zuordnung in `zuordnung_export`. Die Zuordnung muss zur
  Richtung passen (`kauf` nur für Käufe, `verkauf` nur für Verkäufe …). Kategorien mit
  `behandlung: einkunft_22_3` (empfehlung, cashback) werden zum Tageskurs angeschafft und im
  Jahressteuerreport als Einkunft nach § 22 Nr. 3 EStG ausgewiesen.
- **Nicht unterstützt** ist eine Zeile, deren Art fehlt, deren Kategorie nicht unterstützt
  ist (mining, staking, …), deren Zuordnung nicht zur Richtung passt oder die einen anderen
  Kryptowert betrifft (Tausch). Sie bekommt keinen steuerlichen Wert und fließt in keine
  Summe ein; der Bericht listet sie in Abschnitt 8.3, die Zusammenfassung zeigt die Anzahl,
  die Kontrolldatei meldet einen Fehler. Die Mengenabstimmung mit der Blockchain bleibt
  unberührt. Die Methodik nennt den Geltungsbereich (aus `kategorien.yaml`).

Nach einer Änderung am Regelwerk lassen sich alte und neue Berichte lokal vergleichen — die
Ausgabe nennt nur Abschnitte und Anzahl geänderter Zeilen, keine Werte:

```powershell
.\.venv\Scripts\python.exe -m btc_origin.pdf_vergleich alt.pdf neu.pdf
```

Eigene Benennungen von Gegenstellen (Oberfläche oder `externe-adressen.csv`) gelten wie
Datumsregeln als Angabe des Steuerpflichtigen und tragen im Bericht ³ — außer der Name
enthält „unbekannt“ (z. B. `Dienst unbekannt 2024`): das ist keine Zuordnung, daher ohne ³.
Fußnoten werden je Bericht fortlaufend nummeriert (nur die verwendeten).

### Eigene Angaben zu Gegenstellen (`local/zuordnung.csv`)

Zeilen `name,bis` (z. B. `FTX,2022-11-07`) benennen unbenannte Gegenstellen
(`ext-NNN`), deren Zu- und Abflüsse alle bis einschließlich zum Datum liegen.
Das ist eine **Angabe des Steuerpflichtigen**, kein Beleg: Solche Namen tragen im
Bericht **³** mit Fußnote (Abschnitte 2 und 5, Methodik), in der App
das Badge „Angabe“. Namen aus `externe-adressen.csv`, Börsen-Exporten und Labels
gehen vor; passt ein solcher Zufluss zu einer Auszahlung laut Export, gilt der
Beleg (Börsenname, Kaufdaten).

### Kurse (Rz. 43, 91)

Eine dokumentierte Regel für Anschaffung und Veräußerung (Tagesschlusskurs, UTC):

1. Binance BTC/EUR (ab 03.01.2020),
2. davor Binance BTC/USDT ÷ EZB-Referenzkurs USD/EUR desselben Tages
   (Wochenende/Feiertag: letzter veröffentlichter EZB-Kurs; ab 17.08.2017),
3. davor mempool.space.

Bei Netzfehlern: Einzeltag über mempool.space, dann CoinGecko. Die Methodik
nennt, wie viele Tage nach welcher Quelle bewertet wurden. Erlöse und
Kaufpreise laut Börsen-Export haben Vorrang vor dem Tageskurs.

### Plausibilität

- **Mengenabstimmung** (Abschnitt 1) und **Überleitung je Wallet** (Abschnitt 2)
  müssen auf den Satoshi aufgehen; sonst roter Hinweis „Zahlen nicht verwenden“.
- Umbuchungen je Wallet = Saldo je Transaktion (Wechselgeld an dieselbe Wallet
  zählt nicht); Abflüsse und Gebühren anteilig nach den ausgegebenen Einzelbeträgen.

## Rechtsgrundlagen (im Bericht Abschnitt 6)

§ 23 Abs. 1 Satz 1 Nr. 2, Abs. 3 Satz 1, Abs. 3 Satz 5 EStG; BMF-Schreiben vom
06.03.2025, Rz. 20, 43, 53–57, 59, 61–63, 87, 91, 92, 101–106. BFH-Urteile vom 14.02.2023 (IX R 3/22) und 29.05.2008 (VI R 11/07), beide im BMF-Schreiben zitiert. Wortlaut am
Original geprüft; die Kurztexte stehen in `origin_report.LEGAL_SOURCES`.


## Jahressteuerreport

Karte „Finanzamt: Jahressteuerreport Bitcoin“ unter der Herkunftsanalyse: Jahr wählen (Vorgabe:
das vergangene Jahr; Jahre ohne Regeldatei sind nicht wählbar), interne oder Finanzamt-Fassung
öffnen. Name und Steuer-ID gelten wie beim Herkunftsnachweis; die Checkbox „maskiert“ ist mit
der Karte Herkunftsanalyse gekoppelt.

- **Datenbasis:** derselbe Herkunftsnachweis mit Vorgängen bis 31.12. des Jahres, gefiltert auf
  01.01.–31.12. (UTC); nur die Anschaffungen veräußerter Teilbestände liegen davor. T-Nummern
  sind identisch mit dem Herkunftsnachweis.
- **Seite 1:** § 23 EStG — innerhalb der Haltefrist (Anzahl, Erlös, Anschaffungskosten,
  Werbungskosten, Gewinn/Verlust), nach Ablauf der Haltefrist (zur Information), weitere private
  Veräußerungsgeschäfte aus `local/steuer.csv`, Summe, Freigrenze des Jahres laut Regelwerk,
  bei Verlust der Hinweis auf die Feststellung (§ 23 Abs. 3 Satz 7 und 8 EStG); § 22 Nr. 3 EStG —
  Zuflüsse der Kategorien mit `einkunft_22_3` zum Tageskurs mit Freigrenze. Keine
  Zeilennummern der Steuerformulare.
- **Abschnitte:** 1 Anschluss an den Herkunftsnachweis, Mengenabstimmung, Wallets mit Aktivität;
  2 Veräußerungen je Vorgang mit Teilbeständen (Nachweis: Kauf im Jahr → Beleg und T-Nummer des
  Zuflusses, aus Vorjahren → Verweis auf den Herkunftsnachweis); 3 Anschaffungen (Spalte
  „Haltefrist endet am“ nur intern); 4 sonstige Bewegungen; 5 offene Punkte; 6 Methodik.
- **Anschluss (Abschnitt 1.1):** Standard ist „Herkunftsnachweis auf Anforderung“: „Die Herkunft
  aller Bestände ist in einem Herkunftsnachweis dokumentiert, der aus derselben Datenbasis erzeugt
  wird; er wird auf Anforderung vorgelegt.“ (keine Warnung). Wurde ein bestimmter Herkunftsnachweis
  bereits vorgelegt, ihn in dieser Sitzung erzeugen (Link in der Karte) und seinen Stichtag wählen —
  dann nennt 1.1 Stand, Stichtag und Build dieses Dokuments (derselbe Build wie der Report).
  „Kein Herkunftsnachweis“ nur, wenn ausdrücklich gewählt; dann Warnung im Prüfprotokoll.
- **Anhänge:** A Abgleich mit Börsen-Exporten im Jahr (je Datei mit Herkunftsvermerk, je
  Auszahlung Käufe − Gebühr = Eingang), B Export-Zeilen des Jahres (dazu Einkünfte aus
  `zuordnung_manuell` einzeln, als „keine Export-Zeile“ gekennzeichnet), C Transaktionsverzeichnis der
  im Report verwendeten T-Nummern.
- **Finanzamt-Fassung:** Bestände in der Mengenabstimmung „auf Anforderung“ (✓ bleibt sichtbar),
  keine Spalte „Haltefrist endet am“; vollständige Hashes nur für die Veräußerungen des Jahres und
  die Zuflüsse der veräußerten Teilbestände (auch aus Vorjahren), übrige T-Nummern mit „°“.
  Abschnitt 3 ohne Einzelaufstellung der Käufe — nur „Im Jahr … gab es n Zuflüsse von fremden
  Adressen (Anschaffungen); die vollständige Aufstellung mit Belegen wird auf Anforderung
  vorgelegt.“ (keine Mengen, keine Beträge); Einkünfte nach § 22 Nr. 3 EStG weiterhin einzeln.
  5.1 nennt unbelegte Zuflüsse nur, wenn sie zu einer Veräußerung des Jahres gehören; als
  Einkunft eingeordnete Zuflüsse sind belegt und stehen in keiner Fassung unter 5.1. Anhang A: Dateiliste vollständig, Abgleich Auszahlung → Zufluss nur für Auszahlungen,
  aus denen ein im Jahr veräußerter Teilbestand stammt. Anhang B: nur Verkäufe und Einzahlungen
  des Jahres, Käufe hinter veräußerten Teilbeständen (auch aus Vorjahren; Zuordnung über Börse und
  Kauftag), Zeilen mit Einkunft nach § 22 Nr. 3 EStG und nicht unterstützte Zeilen; je Börse eine
  Abschlusszeile „n weitere Zeile(n) des Jahres ohne Bezug zu einer Veräußerung oder Einkunft; auf
  Anforderung“. Ohne Veräußerung und Einkunft stehen in A nur die Dateiliste und in A/B der Satz
  „keine Vorgänge mit Veräußerung oder Einkunft“. Anhang C enthält nur die danach noch
  vorkommenden T-Nummern mit freigegebenem Hash — Einträge mit „°“ stehen dort nicht, ohne
  Veräußerung steht nur „keine“ (Vorgaben der steuerlichen Prüfung). Seite 1 zeigt bei 0 Geschäften
  „—“ statt Beträgen; Abschnitt 4 ist nach T-Nummer sortiert, Anhang C nennt dieselbe Art. Die interne Fassung bleibt
  vollständig.
- **Prüfprotokoll** (PDF, öffnet im Browser; der Button zeigt das Ergebnis schon vorher: ✓ bestanden,
  ⚠ mit Warnungen, ✗ Fehler): erzeugt beide Fassungen und prüft 1 Summen = Einzel-
  geschäfte, 2 Mengenabstimmung, 3 Werte = Herkunftsnachweis, 4 T-Nummern aufgelöst oder „°“,
  5 nur Vorgänge des Jahres, 6 Finanzamt-Fassung (keine Schlüssel/Adressen, nur freigegebene
  Hashes, keine Bestände, Metadaten), 7 Fußnoten auf derselben Seite erklärt, dazu nicht
  unterstützte Vorgänge (quittiert: Warnung). Es nennt Build, Regeldateien mit Stand und SHA-256,
  `kategorien.yaml`, Export-Dateien mit SHA-256 und Herkunft sowie einen Hash über alle
  Rechenergebnisse (gleiche Eingaben → gleicher Wert) und endet mit `ERGEBNIS: BESTANDEN` (ggf. mit
  Warnungen) oder `ERGEBNIS: FEHLER`.
