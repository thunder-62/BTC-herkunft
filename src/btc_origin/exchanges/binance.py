"""Binance — Order-Historie und Transaktionshistorie (Statement)."""

from __future__ import annotations

import re

from btc_origin.exchanges.common import (
    ExchangeTrade,
    SATS_PER_BTC,
    _amount_to_sats,
    _parse_decimal,
    _parse_signed,
    _plain_header,
    _row_addresses,
    _row_dates,
)


def _is_binance_orders(header: list[str]) -> bool:
    h = {_plain_header(c) for c in header}
    return {"orderno", "pair", "side", "executed", "trading total", "status"} <= h


_UNIT_RE = re.compile(r"^\s*([\d.,]+)\s*([A-Za-z]*)\s*$")


_USD_LIKE = ("USD", "USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def _parse_binance_orders(rows: list[list[str]], name: str) -> list[ExchangeTrade]:
    """Binance-Order-Historie (Spot): nur ausgeführte BTC-Orders.

    * Pair BTC/EUR bzw. BTC/USD(T/C…): BUY → Kauf, SELL → Verkauf; Menge aus
      „Executed“, Betrag aus „Trading total“. USD-Stablecoins werden wie USD
      behandelt (EZB-Kurs).
    * Pair XXX/BTC (BTC ist Gegenwährung): BUY = BTC hinaus (Verkauf), SELL = BTC
      herein (Kauf) — ohne EUR-Wert.
    * Status CANCELED/EXPIRED/REJECTED entfällt. Der Export enthält keine
      Gebühren und keine Ein-/Auszahlungen.
    """
    head = [_plain_header(c) for c in rows[0]]
    times = [i for i, h in enumerate(head) if h in ("time", "date(utc)", "date")]
    idx = {h: i for i, h in enumerate(head)}
    out: list[ExchangeTrade] = []
    for row in rows[1:]:
        def cell(h: str) -> str:
            i = idx.get(h)
            return row[i].strip() if i is not None and i < len(row) else ""

        status = cell("status").upper()
        if status and not any(w in status for w in ("FILLED", "PARTIAL")):
            continue
        dates = []
        for i in reversed(times):  # zweite „Time“-Spalte = Ausführung, sonst die erste
            if i < len(row):
                dates = _row_dates(row[i])
                if dates:
                    break
        if not dates:
            continue
        pair = cell("pair").upper().replace("/", "").replace("-", "").replace("_", "")
        side = cell("side").upper()
        ex = _UNIT_RE.match(cell("executed"))
        tot = _UNIT_RE.match(cell("trading total"))
        if not ex:
            continue
        qty, qty_unit = ex.group(1), ex.group(2).upper()
        total = _parse_decimal(tot.group(1)) if tot else None
        total_unit = tot.group(2).upper() if tot else ""
        if pair.startswith(("BTC", "XBT")):
            quote = pair[3:] or total_unit
            sats = _amount_to_sats(qty) if qty_unit in ("", "BTC", "XBT") else None
            if not sats:
                continue
            kind = "buy" if side == "BUY" else "sell" if side == "SELL" else None
            if kind is None:
                continue
            ccy = total_unit or quote
            out.append(
                ExchangeTrade(
                    exchange=name,
                    day=dates[0],
                    kind=kind,
                    sats=sats,
                    eur=total if ccy == "EUR" else None,
                    usd=total if ccy in _USD_LIKE else None,
                    quote=ccy,
                    art=side,
                )
            )
        elif pair.endswith(("BTC", "XBT")) and tot:
            # BTC ist Gegenwährung: Betrag „Trading total“ ist in BTC
            sats = _amount_to_sats(tot.group(1)) if total_unit in ("", "BTC", "XBT") else None
            if not sats:
                continue
            kind = "sell" if side == "BUY" else "buy" if side == "SELL" else None
            if kind:
                base = pair[: -3] or qty_unit
                out.append(ExchangeTrade(exchange=name, day=dates[0], kind=kind, sats=sats, quote=base,
                                         art=f"{side} (BTC als Gegenwert)"))
    return out


def _is_binance_statement(header: list[str]) -> bool:
    h = {c.strip().lower() for c in header}
    return {"time", "account", "operation", "coin", "change"} <= h


_BN_SKIP = ("subscription", "redemption", "transfer", "staking purchase", "savings purchase")


_BN_FEE = ("fee",)


_BN_REWARD = ("distribution", "reward", "interest", "airdrop", "cashback", "commission", "bonus")


_DIRECTION_WORDS = ("buy", "sell", "sold", "spend", "revenue", "kauf", "verkauf")


def _trade_art(ops: list[str], kind: str) -> str:
    """Art eines Handels laut Export. Legt die Operation die Richtung nicht fest (z. B.
    „Binance Convert“ für Kauf und Verkauf), hängt der Parser seine Lesart an."""
    art = " + ".join(ops)
    if not any(w in art.lower() for w in _DIRECTION_WORDS):
        art += " – Kauf" if kind == "buy" else " – Verkauf"
    return art


def _parse_binance_statement(rows: list[list[str]], name: str) -> list[ExchangeTrade]:
    """Binance-Transaktionshistorie (Kontoauszug: ``Operation``, ``Coin``, ``Change``).

    Zeilen mit gleichem Zeitpunkt gehören zu einem Vorgang (z. B. „Transaction
    Buy“ +BTC, „Transaction Spend“ −EUR, „Transaction Fee“):

    * +BTC mit −EUR/USD… → Kauf, −BTC mit +EUR/USD… → Verkauf; Gebühr in EUR
      bzw. BTC aus den Fee-Zeilen.
    * „Buy Crypto With Card“ → Kauf; der Kartenbetrag steht nicht im Auszug
      (Kaufpreis dann Tageskurs, Kaufdatum laut Export).
    * „Withdraw“ → Auszahlung (Menge inkl. Gebühr), „Deposit“ → Einzahlung.
    * Belohnungen (Distribution, Interest, Rewards …) → Kauf ohne Betrag.
    * Umbuchungen zwischen Binance-Konten (Transfer, Earn-Subscription/
      Redemption) entfallen.
    """
    head = [c.strip().lower() for c in rows[0]]
    groups: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for row in rows[1:]:
        r = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(head)}
        key = r.get("time", "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    out: list[ExchangeTrade] = []
    for key in order:
        dates = _row_dates(key)
        if not dates:
            continue
        btc_trade = btc_fee = 0.0
        fiat: dict[str, float] = {}
        fiat_fee: dict[str, float] = {}
        other: dict[str, float] = {}  # andere Kryptowerte (Tausch)
        card = False
        extra: list[ExchangeTrade] = []
        btc_ops: list[str] = []  # Operation der BTC-Zeilen des Handels (Art laut Export)
        for r in groups[key]:
            op_raw = r.get("operation", "").strip()
            op = op_raw.lower()
            coin = r.get("coin", "").upper()
            change = _parse_signed(r.get("change", ""))
            if change is None or any(w in op for w in _BN_SKIP):
                continue
            is_btc = coin in ("BTC", "XBT")
            if is_btc and "withdraw" in op:
                extra.append(ExchangeTrade(exchange=name, day=dates[0], kind="withdraw",
                                           sats=round(abs(change) * SATS_PER_BTC),
                                           addresses=tuple(_row_addresses(r.get("remark", ""))), art=op_raw))
            elif is_btc and "deposit" in op:
                extra.append(ExchangeTrade(exchange=name, day=dates[0], kind="deposit",
                                           sats=round(abs(change) * SATS_PER_BTC), art=op_raw))
            elif is_btc and any(w in op for w in _BN_REWARD) and change > 0:
                extra.append(ExchangeTrade(exchange=name, day=dates[0], kind="buy",
                                           sats=round(change * SATS_PER_BTC), art=op_raw))
            elif any(w in op for w in _BN_FEE):
                if is_btc:
                    btc_fee += abs(change)
                elif coin in ("EUR",) + _USD_LIKE:
                    fiat_fee[coin] = fiat_fee.get(coin, 0.0) + abs(change)
            elif is_btc:
                btc_trade += change
                card = card or "card" in op
                if op_raw not in btc_ops:
                    btc_ops.append(op_raw)
            elif coin in ("EUR",) + _USD_LIKE:
                fiat[coin] = fiat.get(coin, 0.0) + change
            elif coin:
                other[coin] = other.get(coin, 0.0) + change
        sats = round(abs(btc_trade) * SATS_PER_BTC)
        if sats:
            kind = "buy" if btc_trade > 0 else "sell"
            ccy = next((c for c in ("EUR",) + _USD_LIKE if c in fiat), "")
            amount = abs(fiat[ccy]) if ccy else None
            fee = fiat_fee.get(ccy) if ccy else None
            out.append(
                ExchangeTrade(
                    exchange=name,
                    day=dates[0],
                    kind=kind,
                    sats=sats,
                    eur=amount if ccy == "EUR" else None,
                    usd=(amount + (fee or 0) if kind == "buy" else amount - (fee or 0))
                    if ccy in _USD_LIKE and amount is not None
                    else None,
                    fee_eur=fee if ccy == "EUR" else None,
                    fee_sats=round(btc_fee * SATS_PER_BTC),
                    quote=ccy
                    or ("Karte" if card else "")
                    or next((c for c, v in other.items() if (v < 0) == (kind == "buy") and v), ""),
                    art=_trade_art(btc_ops, kind),
                )
            )
        out.extend(extra)
    return out
