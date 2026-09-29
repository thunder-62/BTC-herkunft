"""Coinbase — Transaktionshistorie."""

from __future__ import annotations

import re

from btc_origin.external_book import is_bitcoin_address
from btc_origin.exchanges.common import (
    ExchangeTrade,
    _amount_to_sats,
    _money,
    _row_addresses,
    _row_dates,
)


def _is_coinbase(header: list[str]) -> bool:
    h = {c.strip().lower() for c in header}
    return {"transaction type", "asset", "quantity transacted", "price currency"} <= h


_CB_CONVERT_TO_BTC = re.compile(r"to\s+([\d.,]+)\s+BTC", re.IGNORECASE)


def _parse_coinbase(rows: list[list[str]], name: str) -> list[ExchangeTrade]:
    """Coinbase-Transaktionsexport.

    * Buy / Advanced Trade Buy, Rewards/Staking/Learning in BTC → ``buy``
      (Subtotal = Kaufbetrag, Fees = Gebühr; Belohnungen zum Marktwert).
    * Sell / Advanced Trade Sell, Convert von BTC → ``sell``.
    * Convert in BTC (Asset ≠ BTC, Notiz „… to 0.01 BTC“) → ``buy``.
    * Send → ``withdraw`` (Empfängeradresse), Receive → ``deposit``.
    * Price Currency EUR → EUR, USD → USD (EZB-Kurs).
    """
    head = [c.strip().lower() for c in rows[0]]
    out: list[ExchangeTrade] = []
    for row in rows[1:]:
        r = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(head)}
        dates = _row_dates(r.get("timestamp", ""))
        if not dates:
            continue
        art = r.get("transaction type", "").strip()
        typ = art.lower()
        asset = r.get("asset", "").upper()
        ccy = r.get("price currency", "").upper()
        subtotal = _money(r.get("subtotal", ""))
        fee = _money(r.get("fees and/or spread", "")) or 0.0
        sats = _amount_to_sats(r.get("quantity transacted", ""))
        notes = r.get("notes", "")
        kind = None
        if asset in ("BTC", "XBT"):
            if "buy" in typ or any(w in typ for w in ("reward", "staking", "income", "learning")):
                kind = "buy"
            elif "sell" in typ or "convert" in typ:
                kind = "sell"
            elif typ.startswith("send") or "withdraw" in typ:
                kind = "withdraw"
            elif typ.startswith("receive") or "deposit" in typ:
                kind = "deposit"
        elif "convert" in typ:
            m = _CB_CONVERT_TO_BTC.search(notes)
            if m:
                kind, sats = "buy", _amount_to_sats(m.group(1))
        if not sats:
            continue
        if kind is None:
            if asset in ("BTC", "XBT"):  # BTC-Zeile unbekannter Art → nicht unterstützt
                out.append(ExchangeTrade(exchange=name, day=dates[0], kind="unknown", sats=abs(sats), art=art,
                                         ref=r.get("id", "")))
            continue
        priced = kind in ("buy", "sell")
        amount = subtotal if priced else None
        if kind == "buy" and amount is None:
            amount = _money(r.get("total (inclusive of fees and/or spread)", ""))
        if ccy == "USD" and amount is not None and fee:
            amount = amount + fee if kind == "buy" else amount - fee  # USD-Gebühr einrechnen
        addrs = [a for a in (r.get("recipient address", ""), r.get("sender address", "")) if is_bitcoin_address(a)]
        out.append(
            ExchangeTrade(
                exchange=name,
                day=dates[0],
                kind=kind,
                sats=sats,
                eur=amount if ccy == "EUR" else None,
                usd=amount if ccy == "USD" else None,
                fee_eur=(fee or None) if priced and ccy == "EUR" else None,
                addresses=tuple(addrs) or tuple(_row_addresses(notes)),
                art=art,
                ref=r.get("id", ""),
            )
        )
    return out
