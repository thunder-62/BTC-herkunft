"""Buy/Sell-Spaltenformat (z. B. 21bitcoin): je Zeile Kauf- und Verkaufsseite."""

from __future__ import annotations


from btc_origin.exchanges.common import (
    ExchangeTrade,
    SATS_PER_BTC,
    _FIAT,
    _amount_to_sats,
    _parse_decimal,
    _row_addresses,
    _row_dates,
)


_KIND_LABEL = {"buy": "Kauf", "sell": "Verkauf"}


def _is_buy_sell(header: list[str]) -> bool:
    """Buy/Sell-Spaltenformat (z. B. 21bitcoin): je Zeile Kauf- und Verkaufsseite."""
    h = {c.strip().lower() for c in header}
    return {"buy_asset", "buy_amount", "sell_asset", "sell_amount", "transaction_date"} <= h


def _parse_buy_sell(rows: list[list[str]], name: str) -> list[ExchangeTrade]:
    """``buy_asset/buy_amount`` = was hereinkommt, ``sell_asset/sell_amount`` =
    was hinausgeht (z. B. 21bitcoin).

    * BTC gegen EUR/USD → ``buy``; EUR/USD gegen BTC → ``sell``.
    * Nur BTC hinaus (Auszahlung) → ``withdraw``; nur BTC herein → ``deposit``
      (``transaction_type`` mit „withdraw“/„deposit“ entscheidet im Zweifel).
    * ``fee_asset`` BTC → Gebühr in sats, EUR → Gebühr in EUR.
    """
    head = [c.strip().lower() for c in rows[0]]
    out: list[ExchangeTrade] = []
    for row in rows[1:]:
        r = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(head)}
        dates = _row_dates(r.get("transaction_date", ""))
        if not dates:
            continue
        buy_a, sell_a = r.get("buy_asset", "").upper(), r.get("sell_asset", "").upper()
        fee_a = r.get("fee_asset", "").upper()
        art_csv = r.get("transaction_type", "").strip()
        typ = art_csv.lower()
        btc_in = _amount_to_sats(r.get("buy_amount", "")) if buy_a in ("BTC", "XBT") else None
        btc_out = _amount_to_sats(r.get("sell_amount", "")) if sell_a in ("BTC", "XBT") else None
        fee = _parse_decimal(r.get("fee_amount", "")) or 0.0
        fee_sats = round(fee * SATS_PER_BTC) if fee_a in ("BTC", "XBT") else 0
        fee_eur = fee if fee_a == "EUR" and fee else None
        fiat_amount = None
        if btc_in and sell_a in _FIAT:
            kind, fiat_ccy, sats = "buy", sell_a, btc_in
            fiat_amount = _parse_decimal(r.get("sell_amount", ""))
        elif btc_out and buy_a in _FIAT:
            kind, fiat_ccy, sats = "sell", buy_a, btc_out
            fiat_amount = _parse_decimal(r.get("buy_amount", ""))
        elif btc_out and not btc_in:
            kind, fiat_ccy, sats = "withdraw", "", btc_out
        elif btc_in and not btc_out:
            kind, fiat_ccy, sats = ("withdraw" if "withdraw" in typ else "deposit"), "", btc_in
        else:
            continue  # andere Coins, reine Gebührenzeilen …
        if "withdraw" in typ and kind == "deposit":
            kind = "withdraw"
        out.append(
            ExchangeTrade(
                exchange=name,
                day=dates[0],
                kind=kind,
                sats=sats,
                eur=fiat_amount if fiat_ccy == "EUR" else None,
                usd=fiat_amount if fiat_ccy == "USD" else None,
                fee_eur=fee_eur,
                fee_sats=fee_sats,
                addresses=tuple(_row_addresses(",".join(row))),
                # „trade“ legt die Richtung nicht fest → Lesart anhängen
                art=f"{art_csv} – {_KIND_LABEL[kind]}" if kind in ("buy", "sell") else art_csv,
                ref=r.get("id", ""),
            )
        )
    return out
