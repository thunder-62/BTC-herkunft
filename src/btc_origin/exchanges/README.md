# Börsen-Parser

Ein Modul je Exportformat. Jeder Parser liefert für die Bitcoin-Zeilen eines Exports
`ExchangeTrade`-Einträge (Kauf, Verkauf, Ein- und Auszahlung aus Sicht der Börse).

| Modul | Format |
|---|---|
| `bitgo.py` | BitGo (Go Account), Beträge in USD |
| `binance.py` | Binance Order-Historie und Transaktionshistorie |
| `coinbase.py` | Coinbase Transaktionshistorie |
| `buy_sell.py` | Buy/Sell-Spalten je Zeile (z. B. 21bitcoin) |
| `generic.py` | Rückfall: übliche Spaltennamen (Relai, Bison, Bitvavo, Strike …) |
| `common.py` | gemeinsame Bausteine: Datum/Zahlen erkennen, Spaltenrollen, `ExchangeTrade` |

## Neue Börse hinzufügen

1. **Erst prüfen, ob `generic.py` den Export schon liest.** Viele Börsen nutzen
   Spalten wie `Date`, `Type`, `Amount`, `Currency`, `Fee`. Dann reicht oft ein
   zusätzlicher Spaltenname in `common._header_roles` oder `common._trade_roles`.
2. Sonst ein Modul `meine_boerse.py` anlegen mit
   - `_is_meine_boerse(header: list[str]) -> bool` — erkennt das Format **eindeutig**
     an der Kopfzeile (Menge von Spaltennamen, nicht nur eine),
   - `_parse_meine_boerse(rows: list[list[str]], name: str) -> list[ExchangeTrade]`
     — `rows[0]` ist die Kopfzeile, `name` der Börsenname aus dem Dateinamen.
3. In `__init__.py` in `PARSERS` eintragen — spezielle Formate vor allgemeinen.
4. Test in `tests/` mit **erfundenen** Zeilen im Format des Exports: Spaltennamen,
   Datumsformat, Vorzeichen und Nachkommastellen wie im Original, Werte rund
   (`0.01`, `500.00`), Uhrzeiten auf volle Minuten, TxIDs als `"c" * 64`.
   Nie Zeilen aus einem echten Export kopieren — siehe `CONTRIBUTING.md`.

## Regeln für Parser

- **Nur BTC-Zeilen**, andere Coins überspringen; Tausch gegen andere Kryptowerte mit
  `quote="ETH"` o. ä. markieren (bleibt unbewertet).
- **Abgebrochene/fehlgeschlagene Zeilen** überspringen (`common._NOT_DONE_RE`).
- **Beträge:** `sats` immer positiv; `eur` = Gegenwert in EUR, sonst `usd`
  (Umrechnung zum EZB-Kurs übernimmt die App).
- **Gebühren:** in BTC → `fee_sats`, in EUR → `fee_eur`.
- **Adressen** in der Zeile → `addresses` (hilft beim Zuordnen zur Blockchain).
