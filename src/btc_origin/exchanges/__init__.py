"""Börsen-Parser — ein Modul je Exportformat.

Jeder Eintrag in ``PARSERS`` besteht aus ``detect(header) -> bool`` (erkennt das Format an
der Kopfzeile) und ``parse(rows, name) -> list[ExchangeTrade]``. Der erste passende Parser
gewinnt; passt keiner, liest ``generic`` Exporte mit üblichen Spaltennamen.

Neue Börse: Modul anlegen, hier eintragen, Test mit erfundenen Zeilen schreiben —
siehe ``README.md`` in diesem Ordner.
"""

from __future__ import annotations

from collections.abc import Callable

from btc_origin.exchanges import binance, bitgo, buy_sell, coinbase, generic
from btc_origin.exchanges.common import ExchangeTrade

Detect = Callable[[list[str]], bool]
Parse = Callable[[list[list[str]], str], list[ExchangeTrade]]

# Reihenfolge = Priorität (spezielle Formate vor allgemeinen)
PARSERS: list[tuple[str, Detect, Parse]] = [
    ("bitgo", bitgo._is_bitgo, bitgo._parse_bitgo),
    ("buy_sell", buy_sell._is_buy_sell, buy_sell._parse_buy_sell),
    ("coinbase", coinbase._is_coinbase, coinbase._parse_coinbase),
    ("binance_orders", binance._is_binance_orders, binance._parse_binance_orders),
    ("binance_statement", binance._is_binance_statement, binance._parse_binance_statement),
]


def parse_rows(rows: list[list[str]], name: str, delim: str) -> list[ExchangeTrade]:
    """Export-Zeilen (Kopfzeile zuerst) → BTC-Zeilen; erster passender Parser gewinnt."""
    for _key, detect, parse in PARSERS:
        if detect(rows[0]):
            return parse(rows, name)
    return generic.parse(rows, name, delim)
