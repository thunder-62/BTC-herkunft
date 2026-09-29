# Regelwerk für BTC-Herkunft

Diese Datei beschreibt, wie die steuerrechtlichen Parameter und die Kategorien
für das Tool **BTC-Herkunft** abgelegt sind, wie sie aufgebaut sind und wie sie
jedes Jahr gepflegt werden.

> Die Dateien bilden die Rechtslage nach bestem Wissen ab. Sie sind eine
> Arbeitsgrundlage und keine Steuerberatung.

---

## 1. Grundidee

Alles, was sich durch Gesetz oder Verwaltungsauffassung ändern kann, steht in
YAML-Dateien und **nicht im Code**. Eine Änderung der Rechtslage ist damit in der
Regel eine Datenänderung, keine Programmänderung.

Daraus folgen drei Regeln:

1. **Ein Jahr, eine Datei.** Vorgänge eines Kalenderjahres werden ausschließlich
   mit der Regeldatei dieses Jahres bewertet – auch innerhalb eines Berichts,
   der mehrere Jahre umfasst (eine Veräußerung aus 2022 mit `regeln/2022.yaml`,
   eine aus 2024 mit `regeln/2024.yaml`).
2. **Keine stillen Annahmen.** Fehlt die Regeldatei für ein benötigtes Jahr,
   bricht das Tool ab. Es übernimmt nie automatisch die Werte eines anderen
   Jahres.
3. **Nachvollziehbarkeit.** Jede Regeldatei trägt einen Stand. Der Report nennt
   für jedes verwendete Jahr „Regelwerk VZ <JJJJ>, Stand <Datum>“, das
   Prüfprotokoll zusätzlich die Hashes der Dateien.

---

## 2. Dateien und Ablage

```
btc-regeln/               ← im Repository (öffentlich)
├── REGELWERK.md          ← diese Dokumentation
├── kategorien.yaml       ← jahresunabhängig: Kategorien und Zuordnungen
└── regeln/
    ├── 2022.yaml         ← je Veranlagungsjahr eine Datei
    ├── 2023.yaml
    ├── 2024.yaml
    ├── 2025.yaml
    └── 2026.yaml
```

Dazu kommt optional `local/kategorien.yaml` mit persönlichen Zuordnungen (nicht
im Git). Ein anderer Ablageort für `btc-regeln/` lässt sich mit der
Umgebungsvariablen `BTC_ORIGIN_RULES_DIR` angeben.

Alle Dateien sind UTF-8, YAML 1.2, Einrückung mit zwei Leerzeichen.
Kommentare (`#`) sind erlaubt und erwünscht, wo eine Zahl einer Erklärung bedarf.

---

## 3. Aufbau einer Regeldatei `regeln/<JJJJ>.yaml`

| Schlüssel | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `veranlagungsjahr` | Ganzzahl | ja | Muss mit dem Dateinamen übereinstimmen. |
| `stand` | Datum `JJJJ-MM-TT` | ja | Datum der letzten inhaltlichen Prüfung. |
| `geprueft_von` | Text | ja | Wer geprüft hat, z. B. „Recherche (keine Steuerberatung)“ oder „Steuerberater XY“. |
| `quelle.bmf_schreiben.datum` | Datum | ja | Datum des maßgeblichen BMF-Schreibens zu Kryptowerten. |
| `quelle.bmf_schreiben.gz` | Text | ja | Geschäftszeichen des BMF-Schreibens. |
| `quelle.bmf_schreiben.titel` | Text | ja | Titel des BMF-Schreibens. |
| `quelle.bmf_schreiben.anwendung` | Text | ja | Anwendungsregel (z. B. „alle offenen Fälle (Rz. 106)“). |
| `quelle.vorgaenger` | Text | nein | Vorgängerschreiben, zur Information. |
| `paragraph_23.haltefrist_monate` | Ganzzahl | ja | Haltefrist für private Veräußerungsgeschäfte. |
| `paragraph_23.freigrenze_eur` | Ganzzahl | ja | Freigrenze nach § 23 Abs. 3 Satz 5 EStG für dieses Jahr. |
| `paragraph_23.verlustverrechnung` | Text | ja | Kurzbeschreibung der Verlustverrechnung für den Report. |
| `paragraph_22_nr_3.freigrenze_eur` | Ganzzahl | ja | Freigrenze für sonstige Einkünfte aus Leistungen. |
| `kursregel.id` | Text | ja | Kennung der im Programm umgesetzten Kursregel (derzeit `binance_tagesschluss_utc`). Unbekannte Kennung → Abbruch. Die übrigen `kursregel`-Texte sind nur Anzeige. |
| `kursregel.primaer` | Text | ja | Kursquelle für Anschaffung und Veräußerung (Rz. 43, 91). |
| `kursregel.vor_2020_01_03` | Text | nein | Ersatzquelle für frühere Daten. |
| `kursregel.vor_2017_08_17` | Text | nein | Ersatzquelle für frühere Daten. |
| `kursregel.wochenende_feiertag_ezb` | Text | ja | Regel für fehlende EZB-Kurse. |
| `reform.beschlossen` | Boolesch | ja | `true` erst nach Verkündung im Bundesgesetzblatt. |
| `reform.stichtag_altbestand` | Datum | nein | Grenze Alt-/Neubestand. |
| `reform.hinweis` | Text | ja | Stand des Gesetzgebungsverfahrens. |
| `reform.ausblick` | Text | ja | Ausblick auf die Reform im Bericht (interne Fassung) und im Nachweis Altbestand; nur Information. |
| `texte` | Liste | ja | Textbausteine für den Abschnitt „Rechtsgrundlagen und Quellen“. |

### 3.1 Textbausteine (`texte`)

Jeder Eintrag hat drei Felder:

```yaml
- id: p23_abs3_s5                 # eindeutig innerhalb der Datei, nur a–z, 0–9, _
  titel: "§ 23 Abs. 3 Satz 5 EStG"
  text: "Gewinne bleiben steuerfrei, wenn … weniger als 1000 € beträgt."
```

- Die `id` bleibt über die Jahre gleich, damit der Report denselben Baustein
  findet. Ändert sich der Inhalt, ändert sich nur `text`.
- Zahlen im Text (z. B. Freigrenzen) müssen zu den Werten derselben Datei passen.
  Der Loader prüft das für die Freigrenzen.
- Neue Bausteine bekommen eine neue `id`; entfallene werden gelöscht, nicht
  auskommentiert.

### 3.2 Reform-Schalter

Solange `reform.beschlossen: false` gilt, kennzeichnet das Tool Teilbestände nur
als Alt- oder Neubestand. Es rechnet nicht anders. Eine abweichende Berechnung
wird erst eingebaut, wenn das Gesetz beschlossen ist – und dann mit einem
eigenen Regelblock in den Dateien ab dem ersten betroffenen Jahr.

---

## 4. Aufbau von `kategorien.yaml`

Die Datei ordnet Zuflüsse und Export-Zeilen steuerlichen Kategorien zu. Sie ist
jahresunabhängig; die Freigrenzen und Rechtstexte kommen immer aus der
Regeldatei des jeweiligen Jahres.

### 4.1 `kategorien`

```yaml
kategorien:
  <name>:                             # technischer Schlüssel, nur a–z, 0–9, _
    anzeigename: "…"                  # Pflicht; Report und Geltungsbereich zeigen nur diesen Namen
    behandlung: anschaffung | einkunft_22_3 | nicht_unterstuetzt
    anschaffung_zum_tageskurs: true   # nur bei einkunft_22_3
    beschreibung: "…"                 # optional, erscheint im Report
```

| `behandlung` | Wirkung im Tool |
|---|---|
| `anschaffung` | Zufluss ist eine Anschaffung; Anschaffungskosten laut Beleg, sonst Tageskurs. |
| `einkunft_22_3` | Wert zum Tageskurs am Zuflusstag wird als Einkunft nach § 22 Nr. 3 EStG ausgewiesen (mit Freigrenzen-Hinweis des Jahres). Mit `anschaffung_zum_tageskurs: true` gilt derselbe Wert als Anschaffungskosten, Anschaffungsdatum = Zuflusstag. |
| `nicht_unterstuetzt` | Kein Wert, keine Summe. Ausweis unter „Offene Punkte“ als „nicht unterstützt – manuell prüfen“, Hinweis auf Seite 1, Prüfprotokoll endet mit Fehler (quittiert: nur Warnung, Abschnitt 4.4). Die Mengenabstimmung bleibt unberührt. |

Aktuell definiert: `kauf`, `empfehlung`, `cashback` (unterstützt) sowie
`mining`, `staking`, `lending`, `airdrop`, `fork`, `tausch_krypto`,
`andere_kryptowerte`, `defi`, `schenkung_erbschaft`, `betriebsvermoegen`,
`lightning_zahlung` (nicht unterstützt).

Der Abschnitt „Geltungsbereich“ in der Methodik jedes Reports wird aus den
Anzeigenamen gebildet: unterstützt sind Bitcoin on-chain im Privatvermögen,
Anschaffungen der Kategorien mit `anschaffung`, Veräußerung durch Verkauf oder
Abfluss an Dritte, Umbuchungen und Zuflüsse der Kategorien mit `einkunft_22_3`;
nicht unterstützt sind alle Kategorien mit `nicht_unterstuetzt` und jede nicht
zugeordnete Zeilenart.

### 4.2 `zuordnung_export`

Ordnet die Werte der Art-Spalte einer Börsen-CSV einer Kategorie zu:

```yaml
zuordnung_export:
  <boerse>:
    spalte_art: "<Spaltenname in der CSV>"
    arten:
      "<Wert laut CSV>": <kategorie>
    anschaffungskosten: "<Rechenregel als Text>"   # optional, zur Dokumentation
    hinweis: "…"                                   # optional
```

- Nur Arten eintragen, die in echten Export-Dateien vorkommen. Nichts vorsorglich
  erfinden.
- Eine Art, die in der CSV auftaucht, aber hier fehlt, gilt automatisch als
  **nicht unterstützt**. So fällt jede neue Zeilenart einer Börse sofort auf.
- Technische Arten des Parsers (Kauf, Verkauf, Auszahlung, Einzahlung) sind keine
  steuerlichen Kategorien. Sie dürfen hier genannt werden, wenn der Parser sie
  als Zwischenstufe braucht.

**Erlaubte Zuordnungsziele** und die Richtung laut Export, zu der sie passen
müssen (sonst gilt die Zeile als nicht unterstützt):

| Ziel | Art | passt zu |
|---|---|---|
| eine Kategorie mit `behandlung: anschaffung` (z. B. `kauf`) | Kategorie | Kauf |
| eine Kategorie mit `behandlung: einkunft_22_3` (z. B. `empfehlung`) | Kategorie | Kauf, Einzahlung oder vom Parser nicht erkannte Richtung (eine Einkunft ist immer ein Zufluss) |
| eine Kategorie mit `behandlung: nicht_unterstuetzt` | Kategorie | jede Richtung — die Zeile wird nicht bewertet |
| `verkauf` | technische Art | Verkauf |
| `auszahlung` | technische Art | Auszahlung von der Börse |
| `einzahlung` | technische Art | Einzahlung auf die Börse |

`kauf` ist zugleich Kategorie und Richtung; die technischen Arten
`verkauf`, `auszahlung`, `einzahlung` sind keine Kategorien und brauchen keinen
Eintrag unter `kategorien`. Ein Tausch gegen einen anderen Kryptowert ist immer
nicht unterstützt, auch wenn die Art zugeordnet ist.

**Angehängte Lesart des Parsers.** Legt die Art-Spalte allein die Richtung nicht
fest, hängt der Parser seine Lesart an den Wert an; zugeordnet wird dieser
zusammengesetzte Wert:

- **21bitcoin** (`transaction_type`): „trade“ steht für Kauf und Verkauf. Der
  Parser liest die Richtung aus `buy_asset`/`sell_asset` und meldet
  `trade – Kauf` bzw. `trade – Verkauf`.
- **BitGo** (`TX_TYPE`): Ein Kauf über das Settlement-Konto erscheint als
  „Deposit“, ein Verkauf als „Withdrawal“. Der Parser erkennt die Gegenbuchung
  des Settlement-Kontos und meldet `Deposit – Kauf über Settlement-Konto` bzw.
  `Withdrawal – Verkauf über Settlement-Konto`; echte Ein- und Auszahlungen
  bleiben `Deposit` bzw. `Withdrawal`.
- **Binance Transaktionshistorie** (`Operation`): „Binance Convert“ steht für Kauf
  und Verkauf. Der Parser meldet `Binance Convert – Kauf` bzw.
  `Binance Convert – Verkauf` (allgemein: jede Operation ohne Richtungswort wie
  Buy/Sell/Sold bekommt die Lesart angehängt).
- **Binance Order-Historie** (`Side`): Bei Paaren mit BTC als Gegenwert
  (z. B. ETH/BTC) meldet der Parser `BUY (BTC als Gegenwert)` bzw.
  `SELL (BTC als Gegenwert)` — ein Tausch.

Die Oberfläche (Karte „Regelwerk & Kategorien“) zeigt alle vorkommenden Arten
je Börse mit Anzahl und Richtung, ohne Beträge und Daten, und schreibt neue
Zuordnungen nach Bestätigung in `local/kategorien.yaml`.

Beispiel Relai: Der Export enthält nur die Art `Buy`. Rewards erscheinen dort
nicht und werden manuell zugeordnet.

### 4.3 `zuordnung_manuell`

Für einzelne Zuflüsse, bei denen der Export nichts hergibt. Diese Einträge sind
persönliche Daten und stehen deshalb **nicht** in `btc-regeln/kategorien.yaml`,
sondern in `local/kategorien.yaml` (nicht im Git, siehe Abschnitt 9). Die lokale
Datei darf `zuordnung_manuell` und zusätzliche `zuordnung_export`-Arten enthalten,
aber keine eigenen `kategorien`:

```yaml
zuordnung_manuell:
  <txid>:                      # Transaktions-Hash des Zuflusses (64 Hex-Zeichen) …
    kategorie: empfehlung
    datum: 2025-03-01          # Pflicht, zur Kontrolle; muss zum Zufluss passen
    erlaeuterung: "…"          # erscheint im Report
  <txid>:<vout>:               # … bzw. txid:vout, wenn die Transaktion mehrere Zuflüsse hat
    kategorie: cashback
    datum: 2025-04-01
```

Schlüssel ist der Transaktions-Hash (`txid`) oder `txid:vout`. `txid` allein ist nur
zulässig, wenn die Transaktion genau einen Zufluss in die betrachteten Wallets hat;
sonst bricht der Loader mit Hinweis auf `txid:vout` ab. Der Loader prüft außerdem,
dass die Transaktion ein Zufluss ist und das Datum passt; eine Abweichung führt zum
Abbruch. Der Hash bleibt stabil, auch wenn sich T-Nummern verschieben (z. B. durch
eine zusätzliche Wallet mit älteren Transaktionen). Im Report stehen weiterhin
T-Nummern.

Hashes stehen nur in `local/kategorien.yaml` – nie in `btc-regeln/` oder in der
Dokumentation; der Privacy-Guard lehnt sie dort ab. Ältere Einträge per T-Nummer
(`T-123`) werden noch gelesen; die Oberfläche schreibt sie beim nächsten Übernehmen
in den Hash um.

### 4.4 `geprueft_nicht_unterstuetzt` (Quittierung)

Nur in `local/kategorien.yaml`. Ein geprüfter nicht unterstützter Vorgang bleibt
unter „Offene Punkte“ mit seiner Erläuterung, erzeugt im Prüfprotokoll aber nur
eine Warnung statt eines Fehlers. Nicht quittierte Vorgänge bleiben ein Fehler.

```yaml
geprueft_nicht_unterstuetzt:
  - txid: <txid>               # Wallet-Vorgang per Transaktions-Hash (bzw. txid:vout) …
    datum: 2025-03-01          # Pflicht, zur Kontrolle
    erlaeuterung: "…"          # Pflicht
  - boerse: kraken             # … oder Export-Zeile: Börse + Operation-ID …
    id: "…"
    datum: 2025-03-02
    erlaeuterung: "…"
  - boerse: kraken             # … bzw. Börse + Art, wenn der Export keine ID hat
    art: "Mining Reward"
    datum: 2025-03-03
    erlaeuterung: "…"
```

Für Wallet-Vorgänge gelten dieselben Regeln wie in 4.3 (`txid` allein nur bei genau
einem Zufluss, sonst `txid:vout`; Hashes nur lokal). Export-Zeilen werden weiterhin
per Börse + Operation-ID (bzw. Art) quittiert. Ältere Quittungen per `tnr` werden
noch gelesen.

Eine Quittung ohne passenden Vorgang meldet das Prüfprotokoll als Warnung.

---

## 5. Validierung beim Laden

Das Tool prüft beim Start und bricht bei jedem Fehler mit klarer Meldung ab:

- Datei vorhanden für jedes Jahr, das im Bericht vorkommt: jedes Jahr mit einer
  Veräußerung, jedes Jahr mit einer Einkunft nach § 22 Nr. 3 EStG und das
  Berichtsjahr.
- `kursregel.id` ist eine im Programm umgesetzte Kursregel.
- `veranlagungsjahr` stimmt mit dem Dateinamen überein.
- Alle Pflichtfelder vorhanden, Typen korrekt (Ganzzahl, Datum, Boolesch).
- Freigrenzen in den Textbausteinen stimmen mit den Zahlenwerten überein.
- Jede in `zuordnung_export` und `zuordnung_manuell` verwendete Kategorie ist
  in `kategorien` definiert.
- Jeder Eintrag in `zuordnung_manuell` (txid bzw. txid:vout) ist ein Zufluss in die
  betrachteten Wallets, Datum passt; txid allein nur bei genau einem Zufluss.
- Keine doppelten `id` in `texte`, keine doppelten Arten je Börse.
- `geprueft_nicht_unterstuetzt` nur lokal; jede Quittung mit Datum, Erläuterung
  und txid (bzw. txid:vout) oder Börse mit Operation-ID oder Art.

---

## 6. Jährliche Pflege

Einmal im Jahr, vor der Steuererklärung (Januar/Februar):

1. **Neue Regeldatei anlegen:** `regeln/<Vorjahr>.yaml` nach
   `regeln/<neues Jahr>.yaml` kopieren und `veranlagungsjahr` anpassen.
2. **Rechtslage prüfen:** neue BMF-Schreiben zu Kryptowerten, Jahressteuergesetz,
   Stand der Kryptosteuer-Reform, Freigrenzen. Änderungen eintragen, Quelle im
   Kommentar notieren.
3. **Stand setzen:** `stand` und `geprueft_von` aktualisieren – auch wenn sich
   inhaltlich nichts geändert hat.
4. **Kategorien prüfen:** neue Export-Arten der Börsen? Neue Zuflüsse, die
   manuell zugeordnet werden müssen (Rewards, Prämien)?
5. **Report erzeugen** und das Prüfprotokoll lesen. Meldungen zu nicht
   unterstützten Vorgängen klären, bevor die Erklärung abgegeben wird.
6. **Einchecken:** Regeldateien, Kategorien und Report-Stand in einem Commit mit
   der Nachricht `Regelwerk VZ <JJJJ>, Stand <Datum>`.

Vorlage für den Prüfauftrag an Claude:

```
Jahrescheck BTC-Steuer <JJJJ>:
Bitte prüfe die angehängte regeln/<JJJJ>.yaml und kategorien.yaml gegen die
aktuelle Rechtslage für das Veranlagungsjahr <JJJJ>: neue BMF-Schreiben zu
Kryptowerten, Jahressteuergesetz, Stand der Kryptosteuer-Reform, geänderte
Freigrenzen. Nenne Änderungen mit Quelle und gib mir eine korrigierte YAML.
Danach prüfst du den Jahresreport <JJJJ> (Anhang) auf Konsistenz.
```

---

## 7. Regeln für Änderungen an bestehenden Jahren

- **Abgeschlossene Jahre** (Erklärung abgegeben) werden nur geändert, wenn die
  damalige Rechtslage falsch abgebildet war. Jede solche Änderung bekommt einen
  Kommentar mit Datum und Grund und einen eigenen Commit.
- Nach jeder Änderung an einer Regeldatei für ein abgeschlossenes Jahr werden die
  betroffenen Reports neu erzeugt und mit den abgegebenen Werten verglichen.
  Abweichungen sind zu dokumentieren.
- Rückwirkende Gesetzesänderungen werden in allen betroffenen Jahresdateien
  eingetragen, nicht nur im aktuellen Jahr.

---

## 8. Werte der vorhandenen Dateien (Stand 29.09.2026)

| VZ | Haltefrist | Freigrenze § 23 | Freigrenze § 22 Nr. 3 | BMF-Schreiben | Reform |
|---|---|---|---|---|---|
| 2022 | 12 Monate | 600 € | 256 € | 06.03.2025 (alle offenen Fälle) | – |
| 2023 | 12 Monate | 600 € | 256 € | 06.03.2025 | – |
| 2024 | 12 Monate | 1.000 € | 256 € | 06.03.2025 | – |
| 2025 | 12 Monate | 1.000 € | 256 € | 06.03.2025 | – |
| 2026 | 12 Monate | 1.000 € | 256 € | 06.03.2025 | Entwurf, nicht beschlossen; Stichtag Altbestand 31.12.2026 nur als Kennzeichnung |

Offene Einträge in `kategorien.yaml`:

- Manuelle Zuordnungen (z. B. Empfehlungsprämien, die nicht im Export stehen)
  trägt jeder Nutzer in `local/kategorien.yaml` ein.
- `zuordnung_export` für bitvavo, coinbase, strike, binance, bison, bitgo und
  21bitcoin ist aus dem bestehenden Parser ergänzt; vor dem Merge mit echten
  Daten geprüft (Änderungsliste der Oberfläche). Die Einzahlung per Lightning
  bei Strike braucht noch die Art laut Export.

---

## 9. Was nicht in diese Dateien gehört

- **Technische Abgleichparameter** (Zeitfenster für die Zuordnung von
  Auszahlungen, Mengen-Toleranzen, Schwellen für den Satoshi-Test). Sie sind
  Methodik, nicht Steuerrecht, und gehören in den Code oder eine eigene
  `abgleich.yaml`.
- **Persönliche Daten** wie xpubs, Adressen oder Kontonummern. Diese Dateien
  liegen im Git und sollen ohne Bedenken teilbar sein.
