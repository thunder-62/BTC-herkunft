"""BitGo (Go Account) — Beträge und Kurse in USD."""

from __future__ import annotations

from datetime import date

from btc_origin.external_book import is_bitcoin_address
from btc_origin.exchanges.common import (
    _TXID_RE,
    ExchangeTrade,
    SATS_PER_BTC,
    _parse_signed,
    _row_dates,
)


def _is_bitgo(header: list[str]) -> bool:
    h = {c.strip().upper() for c in header}
    return {"WALLET_LABEL", "TX_TYPE", "TO_ADDRESS", "FROM_ADDRESS", "USD_AMOUNT"} <= h


def _parse_bitgo(rows: list[list[str]], name: str) -> list[ExchangeTrade]:
    """BitGo (Go Account) — Beträge und Kurse in USD.

    * Kauf: USD gehen an ein Settlement-Konto (TO_ADDRESS = Konto-ID), BTC
      kommen von derselben ID (FROM_ADDRESS, TXID „primesettlement…“) →
      ``buy`` mit dem gezahlten USD-Betrag.
    * Verkauf: BTC an eine Konto-ID, USD von derselben ID zurück → ``sell``.
    * Auszahlung/Einzahlung: Zeilen mit Bitcoin-TxID bzw. -Adresse.
    * „Fee“-Zeilen mit Betrag 0 und nicht bestätigte Zeilen entfallen.
    """
    head = [c.strip().upper() for c in rows[0]]
    recs = []
    for row in rows[1:]:
        r = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(head)}
        amount = _parse_signed(r.get("AMOUNT", ""))
        if not amount or r.get("STATUS", "").lower() not in ("confirmed", ""):
            continue
        dates = _row_dates(r.get("CONFIRMED_DATE") or r.get("CREATE_DATE") or "")
        if not dates:
            continue
        r["_amount"], r["_day"] = amount, dates[0]
        recs.append(r)

    def chain_side(r: dict[str, str], key: str) -> bool:
        return bool(_TXID_RE.fullmatch(r.get("TXID", ""))) or is_bitcoin_address(r.get(key, ""))

    usd_used: set[int] = set()

    def usd_leg(account: str, day: date, sign: int) -> float | None:
        """USD-Gegenbuchung desselben Settlement-Kontos (±7 Tage, nächste zuerst)."""
        best = None
        for i, u in enumerate(recs):
            if i in usd_used or u.get("COIN", "").upper() != "USD" or (u["_amount"] > 0) != (sign > 0):
                continue
            if account not in (u.get("TO_ADDRESS"), u.get("FROM_ADDRESS")):
                continue
            gap = abs((u["_day"] - day).days)
            if gap <= 7 and (best is None or gap < best[0]):
                best = (gap, i)
        if best is None:
            return None
        usd_used.add(best[1])
        return abs(recs[best[1]]["_amount"])

    out: list[ExchangeTrade] = []
    for r in recs:
        if r.get("COIN", "").upper() not in ("BTC", "XBT"):
            continue
        amount = r["_amount"]
        sats = round(abs(amount) * SATS_PER_BTC)
        fee = _parse_signed(r.get("FEE", "")) or 0.0
        if amount > 0:
            if chain_side(r, "FROM_ADDRESS"):
                kind, usd = "deposit", None
            else:
                kind = "buy"
                usd = usd_leg(r.get("FROM_ADDRESS", ""), r["_day"], -1)
                if usd is None:
                    usd = abs(_parse_signed(r.get("USD_AMOUNT", "")) or 0) or None
            addrs = tuple(a for a in (r.get("FROM_ADDRESS", ""),) if is_bitcoin_address(a))
        else:
            if chain_side(r, "TO_ADDRESS"):
                kind, usd = "withdraw", None
            else:
                kind = "sell"
                usd = usd_leg(r.get("TO_ADDRESS", ""), r["_day"], +1)
                if usd is None:
                    usd = abs(_parse_signed(r.get("USD_AMOUNT", "")) or 0) or None
            addrs = tuple(a for a in (r.get("TO_ADDRESS", ""),) if is_bitcoin_address(a))
        tx_type = r.get("TX_TYPE", "").strip()
        out.append(
            ExchangeTrade(
                exchange=name,
                day=r["_day"],
                kind=kind,
                sats=sats,
                fee_sats=round(abs(fee) * SATS_PER_BTC),
                addresses=addrs,
                usd=usd,
                # TX_TYPE nennt auch Käufe/Verkäufe über das Settlement-Konto „Deposit“/„Withdrawal“
                art=f"{tx_type} – {'Kauf' if kind == 'buy' else 'Verkauf'} über Settlement-Konto"
                if kind in ("buy", "sell") else tx_type,
            )
        )
    return out
