# Threat Model — BTC-Herkunft (btc-origin)

Lokale App für die Herkunfts- und Haltefrist-Analyse aus **öffentlichen** xpubs.
Keine Seeds, keine privaten Schlüssel, **keine dauerhafte Speicherung der Sitzung**.

## Assets

| Asset | Sensitivität | Speicherung |
|-------|--------------|-------------|
| xpub/ypub/zpub | mittel — offenbaren den ganzen Adressraum | nur RAM / SQLite `:memory:` |
| Adressen, Transaktionen, Flows, Lots | mittel (finanzielle Historie) | nur RAM |
| Name und Steuer-ID für den Bericht | hoch (personenbezogen) | nur in der Anfrage; nie gespeichert |
| Eigene Namen externer Adressen | mittel | RAM; optional `local/externe-adressen.csv` (vom Nutzer gepflegt) |
| Börsen-Exporte | hoch (Kontoauszüge) | nur `local/boersen/` (vom Nutzer abgelegt, von der App nur gelesen) |
| Portfolio-Performance-Export | hoch (Depotumsätze) | nur `local/check-pp.csv` (nur gelesen; Abgleich nur im Browser, nie im Bericht) |
| Öffentliche Label-Packs | niedrig (öffentlich) | Git, `data/label_packs/` |
| Kurse | niedrig (öffentlich) | RAM-Cache |

**Wird nie angenommen:** Seeds, private Schlüssel, Passphrasen, Signiermaterial.

## Bedrohungen und Gegenmaßnahmen

| Bedrohung | Gegenmaßnahme |
|-----------|---------------|
| Eingabe von Seeds/Keys | API/UI nehmen nur xpubs und Adressen an; Validierung lehnt anderes ab. Kein USB/HID. |
| Dauerhafte Spuren auf Platte | SQLite `:memory:` erzwungen; keine Session-Dateien; kein LocalStorage/IndexedDB für Wallet-Daten; Berichte nur als Bytes auf Klick. `.gitignore` für `*.db` als Netz. |
| Versehentliches Committen privater Daten | `local/` ist in `.gitignore` (außer `local/README.md`); Label-Packs enthalten nur öffentliche Adressen. |
| Cloud-Sync privater Dateien | Liegt das Projekt in OneDrive o. ä., werden `local/` und Berichte, die der Nutzer speichert, mitsynchronisiert — Hinweis in `local/README.md`. |
| Netz-Leck an Electrum-Server | Server konfigurierbar (eigener Server möglich); Default öffentlicher Server mit Failover. Der Server sieht die abgefragten Adressen. |
| Netz-Leck an Kursquellen | Binance, EZB, mempool.space, CoinGecko werden nur mit Datumsbereichen abgefragt — nie mit Adressen oder xpubs. |
| Öffentlich erreichbare API | Bindung an `127.0.0.1`; CORS nur localhost; keine Authentifizierung vorgesehen. |
| Falsche Steueraussagen (stille Kostenbasis 0, falsche Zuordnung) | „Kaufnachweis fehlt“ statt 0; Kurse als Referenzwerte gekennzeichnet; Mengenabstimmung und Wallet-Überleitung auf den Satoshi mit Warnung; Annahmen im Bericht ausgewiesen; Hinweis „keine Steuerberatung“. |
| Fremde Adressen als eigen gewertet | Eigen sind nur die eingefügten xpubs; Ownership-Heuristik standardmäßig aus und als heuristisch markiert. |
| Große/unerwartete Dateien in `local/` | Größenlimit 20 MB je Datei; Adressen werden validiert; es wird nur gelesen. |
| Supply-Chain (pip/npm) | wenige Abhängigkeiten; Versionen prüfen. |

## Außerhalb des Umfangs

- Steuer- oder Investmentberatung
- Signieren, Senden von Transaktionen, Custody
- Automatischer Versand an Behörden oder Clouds
- Dauerhafte Datenbank, Sitzungswiederherstellung
- Hardware-Wallet-Anbindung per USB

## Annahme

Die App läuft auf einem vertrauenswürdigen lokalen Gerät. **Schließen = Daten
weg.** Wer Ergebnisse behalten will, speichert Bericht oder CSV bewusst selbst.
