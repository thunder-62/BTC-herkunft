# BTC-Herkunft — Anleitung für Einsteiger (Windows)

Diese Anleitung führt Schritt für Schritt vom Herunterladen bis zum fertigen
Bericht fürs Finanzamt. Du brauchst keine Computer-Vorkenntnisse.
Unter Linux oder macOS startest du die App mit `./scripts/start-linux.sh`
(siehe [`README.md`](README.md), Abschnitt „Linux / macOS“).

> **Wichtig, bevor es losgeht**
>
> - Gib **niemals** deine Wiederherstellungswörter (Seed, 12/24 Wörter) oder
>   private Schlüssel ein — weder hier noch sonst irgendwo. Die App braucht nur
>   den **öffentlichen** Schlüssel (xpub), siehe Schritt 8.
> - Die App speichert nichts dauerhaft. Schließt du sie, ist alles weg — das ist
>   Absicht (Datenschutz).
> - Die App ist **keine Steuerberatung**.

**Du brauchst:** einen Windows-10- oder Windows-11-PC, Internet, etwa 2 GB
freien Speicher und beim ersten Mal rund 30 Minuten.

---

## Einmalig: Vorbereitung

### Schritt 1 — Python installieren

Python ist ein Programm, das die App im Hintergrund braucht.

1. Öffne im Browser **https://www.python.org/downloads/windows/**
2. Klicke bei „Python 3.12“ (3.11 oder 3.13 gehen auch) auf
   **„Windows installer (64-bit)“**. Die Datei landet im Ordner *Downloads*.
3. Öffne die heruntergeladene Datei per Doppelklick.
4. **Ganz wichtig:** Setze unten im ersten Fenster den Haken bei
   **„Add python.exe to PATH“**.
5. Klicke auf **„Install Now“** und warte, bis „Setup was successful“ erscheint.
   Dann **„Close“**.

### Schritt 2 — Node.js installieren

Node.js wird für die Bedienoberfläche gebraucht.

1. Öffne **https://nodejs.org/de**
2. Klicke auf den Knopf für die **LTS-Version** (die empfohlene) und lade den
   Windows-Installer herunter.
3. Öffne die Datei und klicke dich mit **„Weiter“ / „Next“** durch. Alle
   Voreinstellungen passen. Am Ende **„Installieren“** und **„Fertig“**.
4. Den Haken „Tools for Native Modules“ brauchst du **nicht**.

> Tipp: Nach beiden Installationen den PC einmal neu starten. Dann findet
> Windows die neuen Programme sicher.

---

## Die App herunterladen

### Schritt 3 — ZIP-Datei herunterladen

1. Öffne die Projektseite **https://github.com/thunder-62/BTC-herkunft**
2. Klicke oben rechts auf den grünen Knopf **„Code“**.
3. Klicke im aufklappenden Menü ganz unten auf **„Download ZIP“**.
4. Die Datei **`BTC-herkunft-main.zip`** landet im Ordner *Downloads*.

> Siehst du statt der Projektseite „404 – Page not found“? Dann ist das Projekt
> nicht öffentlich. Melde dich bei GitHub an bzw. bitte den Besitzer, dich
> freizuschalten.

### Schritt 4 — ZIP-Datei „entsperren“

Windows markiert Dateien aus dem Internet und warnt später bei jedem Start.
Das verhinderst du so:

1. Öffne den Ordner *Downloads* (Explorer).
2. **Rechtsklick** auf `BTC-herkunft-main.zip` → **„Eigenschaften“**.
3. Unten im Reiter „Allgemein“: Haken bei **„Zulassen“** setzen (heißt in manchen
   Versionen „Blockierung aufheben“). Gibt es den Haken nicht, ist nichts zu tun.
4. **„OK“**.

### Schritt 5 — Auspacken

1. **Rechtsklick** auf `BTC-herkunft-main.zip` → **„Alle extrahieren…“**.
2. Als Ziel z. B. `C:\Users\<dein Name>\Documents\BTC-Herkunft` wählen
   (über „Durchsuchen…“) → **„Extrahieren“**.
3. Es entsteht ein Ordner **`BTC-herkunft-main`**. Darin liegen u. a. die Datei
   **`Start-BTC-Herkunft.bat`** und diese Anleitung.

---

## Die App starten

### Schritt 6 — Doppelklick auf `Start-BTC-Herkunft.bat`

1. Öffne den Ordner `BTC-herkunft-main` und **doppelklicke auf
   `Start-BTC-Herkunft.bat`**. (Je nach Einstellung heißt sie nur
   `Start-BTC-Herkunft` mit einem Zahnrad-Symbol.)
2. Erscheint ein blaues Fenster **„Der Computer wurde durch Windows geschützt“**:
   auf **„Weitere Informationen“** klicken, dann **„Trotzdem ausführen“**.
3. Es gehen **zwei schwarze Fenster** auf („Server“ und „Oberfläche“).
   **Beide offen lassen** — sie *sind* die App. Zuschauen musst du nicht.
4. **Beim ersten Mal** lädt die App ihre Bausteine aus dem Internet. Das dauert
   **5 bis 10 Minuten**, in den Fenstern läuft dabei Text durch. Das ist normal.
5. Sobald alles bereit ist, **öffnet sich der Browser von selbst** mit der App
   (Adresse `http://127.0.0.1:5173`). Ab dem zweiten Start geht es in einer
   Minute.

> Fragt Windows, ob Python bzw. Node.js „auf das Netzwerk zugreifen“ darf:
> **„Zulassen“** — die App braucht Internet, um die Blockchain abzufragen.

### Schritt 7 — Die App beenden

Einfach die beiden schwarzen Fenster („Server“ und „Oberfläche“) schließen.
Alle eingegebenen Daten sind dann gelöscht. Beim nächsten Mal wieder mit
Schritt 6 starten.

---

## Die App benutzen

### Schritt 8 — Deine xpubs heraussuchen

Eine **xpub** (auch *ypub* oder *zpub*) ist der **öffentliche Schlüssel** einer
Wallet: eine lange Zeichenkette, die mit `xpub`, `ypub` oder `zpub` beginnt.
Damit kann man Kontostände und Bewegungen **ansehen**, aber **nichts
ausgeben**. Du findest sie in deiner Wallet-App meist in den
Konto-Einstellungen unter „Erweitert“, „Kontoinformationen“ oder „Extended
public key“, zum Beispiel:

| Wallet | Wo die xpub steht (Bezeichnungen je nach Version) |
|--------|---------------------------------------------------|
| Ledger Live | Konto öffnen → Schraubenschlüssel „Konto bearbeiten“ → „Erweitert“ |
| BitBoxApp | Konto öffnen → „Kontoinformationen“ |
| Trezor Suite | Konto öffnen → „Details“ → „Öffentlichen Schlüssel (XPUB) anzeigen“ |
| Sparrow | Wallet öffnen → „Settings“ → Abschnitt „Keystore“ |

Kopiere die xpub (markieren, **Strg + C**). Beginnt eine Zeichenkette mit
`xprv`, `yprv`, `zprv` oder sind es 12/24 Wörter: **Finger weg, das ist geheim.**
Die App würde sie ohnehin ablehnen.

> Tipp: Lege dir eine Liste deiner xpubs mit Namen in einer Textdatei an einem
> sicheren Ort an. Die App merkt sich nichts — so kannst du sie beim nächsten
> Start schnell wieder einfügen. Eine xpub ist nicht geheim wie ein Seed, verrät
> aber deine ganze Kontohistorie: nicht weitergeben.

### Schritt 9 — Wallets einfügen

Im Browser, Abschnitt **„Wallets — xpub einfügen“**, für jede Wallet:

1. Bei **„Anzeigename“** einen Namen eintippen, z. B. `Ledger Sparen`.
2. Ins große Feld darunter klicken und die xpub einfügen (**Strg + V**).
3. Auf **„Zur Sitzung hinzufügen“** klicken.
4. Mit der nächsten Wallet wiederholen.

### Schritt 10 — Daten abrufen

Im Abschnitt **„Sync-Status“** auf **„Sync starten“** klicken. Die App fragt nun
die Blockchain ab. Je nach Anzahl der Transaktionen dauert das einige Sekunden
bis einige Minuten. Danach füllen sich die Abschnitte darunter:

- **Wallet-Cloud — Übersicht:** Zuflüsse, heutiger Bestand, Abflüsse.
- **Jahres-Resümee:** möglicher Gewinn/Verlust je Jahr (Annahmen beachten).
- **Herkunftsnachweis je Wallet:** wie viel davon mit Kaufbeleg belegt ist.

Aufklappbare Abschnitte erkennst du am kleinen Dreieck ▸ — draufklicken.

### Schritt 11 — Bericht fürs Finanzamt

1. Ganz unten, Abschnitt **„Finanzamt: Herkunftsanalyse Bitcoin“**:
   Name und Steuer-ID eintragen (optional; werden nicht gespeichert).
2. **„Herkunftsanalyse öffnen“** — das PDF öffnet sich in einem neuen Tab.
3. Im PDF-Tab auf das **Speichern-Symbol** (Pfeil nach unten) klicken und die
   Datei ablegen, z. B. in *Dokumente*.
4. Ebenso: **„Nachweis Altbestand öffnen“** (Coins, die bis 31.12.2026
   gekauft wurden) und — falls angeboten — die **Nacherklärung (Entwurf)**.

---

## Optional: Börsen-Belege einbinden

Kaufbelege machen den Bericht genauer (echtes Kaufdatum und echter Kaufpreis).

1. Lade bei deiner Börse (Relai, Bison, Bitvavo, Strike, BitGo, 21bitcoin, Coinbase, Binance …) die
   **Transaktionsübersicht als CSV** herunter. Wo genau, steht in der Hilfe der
   Börse (meist „Kontoauszug“, „Export“ oder „Steuerreport-Daten“).
2. Speichere sie im App-Ordner unter **`local\boersen\`**, und zwar mit dem
   Namen der Börse, z. B. `local\boersen\bitvavo.csv`.
3. In der App (Abschnitt **„Externe Adressen“**) auf **„Lokale Dateien neu
   laden“** klicken — oder die App neu starten.

Weitere Möglichkeiten des Ordners `local` (eigene Namen für Adressen,
Abgleich mit Portfolio Performance) beschreibt
[`local/README.md`](local/README.md).

---

## Eine neue Version installieren

1. Neue ZIP-Datei herunterladen (Schritt 3), entsperren (Schritt 4) und in einen
   **neuen** Ordner auspacken (Schritt 5).
2. Aus dem **alten** Ordner den Unterordner **`local`** in den neuen Ordner
   kopieren (dort vorhandenen `local` ersetzen). Darin liegen deine Börsen-Belege.
3. Aus dem neuen Ordner starten (Schritt 6). Der erste Start dauert wieder etwas
   länger. Den alten Ordner kannst du danach löschen.

---

## Wenn etwas nicht klappt

| Was du siehst | Was hilft |
|---------------|-----------|
| „Node.js ist nicht installiert“ (Browser öffnet nodejs.org) | Schritt 2 ausführen, PC neu starten, erneut starten. |
| Im Fenster „Server“: „Python 3.12 not found“ | Schritt 1 ausführen — Haken „Add python.exe to PATH“ nicht vergessen. PC neu starten. |
| Im Fenster „Oberfläche“: „npm wird nicht erkannt“ | Node.js fehlt oder der PC wurde nach der Installation nicht neu gestartet. |
| Blaues Fenster „Windows hat den PC geschützt“ | „Weitere Informationen“ → „Trotzdem ausführen“. Dauerhaft vermeiden: Schritt 4. |
| Browser zeigt „Diese Seite ist nicht erreichbar“ | Noch etwas warten — beim ersten Start dauert es. Sind die zwei schwarzen Fenster zu, neu starten (Schritt 6). |
| „Address already in use“ / Port belegt | Die App läuft schon (vielleicht in einem versteckten Fenster). PC neu starten, dann einmal starten. |
| Ein schwarzes Fenster zeigt rote Fehlermeldungen | Fenster nicht schließen, einen Screenshot machen (Windows-Taste + Umschalt + S) und dem Betreuer schicken. |
| Sync dauert sehr lange | Bei vielen Transaktionen normal. Der Fortschritt steht unter „Sync-Status“. |
| Beträge sind als `*.********` verdeckt | Oben rechts ist die **Privacy-Ansicht** eingeschaltet — ausschalten. |

---

## Was mit deinen Daten passiert

- Alles bleibt **auf deinem PC** und nur so lange, wie die App läuft.
- Für die Abfrage der Blockchain sieht ein öffentlicher Electrum-Server die
  abgefragten Adressen (nicht deinen Namen). Für Kurse werden nur Datumsbereiche
  abgefragt.
- Der Ordner `local` gehört dir: Die App liest ihn nur, sie schreibt nie hinein.
  Liegt der App-Ordner in OneDrive, werden deine Börsen-Belege mit in die Cloud
  synchronisiert — wer das nicht möchte, packt die App in einen Ordner außerhalb
  von OneDrive.

Mehr Hintergrund für Interessierte: [`README.md`](README.md).
