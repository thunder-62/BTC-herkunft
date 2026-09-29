# Regeln für dieses Repository

BTC-Herkunft ist Open Source. Das Repository ist öffentlich lesbar; echte
Wallet-Daten des Nutzers dürfen hier **nie** auftauchen — weder im Code noch in
Tests, Doku, Commit-Nachrichten oder PR-Texten.

## Echte Daten bleiben draußen

- Nie ins Repo: xpubs/ypub/zpub, Adressen, Transaktions-IDs, Beträge, Zeitstempel,
  Wallet-Namen oder Erläuterungen aus Dateien, Diagnosen, PDFs oder Nachrichten
  des Nutzers — auch nicht gekürzt (`3Abc…Xyz9`) oder als „Real case“.
- Aus echten Exporten wird nur die **Struktur** übernommen: Spaltennamen, Formate,
  Zeilenarten, Vorzeichen, Anzahl Nachkommastellen.
- Ein echter Fehlerfall wird mit erfundenen Werten nachgebaut; im Test steht, was
  er prüft, nicht, woher er kommt.
- Nutzerdateien (`local/`, Berichte, Diagnosen) werden nur gelesen, nie
  committet; `local/` ist per `.gitignore` ausgeschlossen (außer `local/README.md`).
- Nie Seeds oder private Schlüssel, auch keine erfundenen, die wie echte aussehen.

## So sehen Testdaten aus

- Beträge rund (`0.01`, `250_000`, `€900.00`) oder offensichtliche Muster
  (`0.12345678`, `1_111_111`).
- Uhrzeiten auf volle Minuten (`10:05:00`).
- Adressen nur aus `tests/privacy_allowlist.txt` (BIP-Testvektoren oder synthetisch
  aus `sha256("btc-origin synthetic {i}")`) oder offensichtlich ungültige
  Platzhalter (`bc1qownaddress`); öffentliche Börsenadressen nur aus
  `data/label_packs/`.
- Transaktions-IDs als Platzhalter (`"c" * 64`, `"t1"`), Wallet-Namen generisch
  (`W`, `Ledger`, `Wallet A`).

## Automatische Prüfung

`tests/test_privacy_guard.py` läuft mit jedem `pytest` und in der CI. Er meldet
gültige Adressen/xpubs außerhalb der Erlaubt-Liste, 64-stellige Hex-Werte, krumme
Beträge, Uhrzeiten mit Sekunden und persönliche Namen. Bei einem Treffer: den
Wert durch einen erfundenen ersetzen. Nur nachweislich öffentliche oder
abgeleitete Werte (z. B. Unix-Zeit `1_700_000_000`) in die Erlaubt-Liste
eintragen oder die Zeile mit `privacy: ok` und Begründung markieren.

## Sonstiges

- Hinweis „keine Steuerberatung“ in Berichten beibehalten.
- Sitzungsdaten nur im Arbeitsspeicher.
- Keine Modellbezeichnungen in Commits oder PRs.
