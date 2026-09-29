"""Allgemeine Exporte mit Kopfzeile — Rückfall, wenn kein spezieller Parser passt."""

from __future__ import annotations

from datetime import date

from btc_origin.exchanges.common import (
    ExchangeTrade,
    SATS_PER_BTC,
    _BTC_ASSETS,
    _CANCELLED_TYPE_RE,
    _NOT_DONE_RE,
    _REVERSAL_RE,
    _amount_to_sats,
    _header_roles,
    _parse_decimal,
    _row_addresses,
    _row_dates,
    _trade_roles,
    trade_kind,
)


# Spalten mit der Vorgangs-ID laut Export (erste vorhandene gilt)
_ID_COLUMNS = ("operation id", "transaction id", "referenceid", "reference id", "id")


def parse(rows: list[list[str]], name: str, delim: str) -> list[ExchangeTrade]:
    """Allgemeines Kopfzeilen-Format (Relai, Bison, Bitvavo, Strike …): Spaltenrollen
    anhand der Spaltennamen; Kauf/Verkauf/Ein-/Auszahlung je Zeile (nur BTC)."""
    roles = _header_roles(rows[0])
    extra = _trade_roles(rows[0])
    if not {"date", "amount", "type"} <= roles.keys():
        return []
    head = [c.strip().lower() for c in rows[0]]
    id_col = next((head.index(c) for c in _ID_COLUMNS if c in head), None)
    out: list[ExchangeTrade] = []
    reversals: list[tuple[str, date, int]] = []  # (kind, Tag, sats) zurückgebuchter Vorgänge
    for row in rows[1:]:
        def cell(role: str, src: dict[str, int] = roles) -> str:
            i = src.get(role)
            return row[i] if i is not None and i < len(row) else ""

        if "asset" in roles and cell("asset").strip().lower() not in _BTC_ASSETS:
            continue
        kind = trade_kind(cell("type"))
        dates = _row_dates(cell("date"))
        sats = _amount_to_sats(cell("amount"))
        if not dates or not sats:
            continue
        if _NOT_DONE_RE.search(cell("status", extra)) or _CANCELLED_TYPE_RE.search(cell("type")):
            continue  # abgebrochen/fehlgeschlagen/offen — kein Vorgang
        art = cell("type").strip()
        ref = row[id_col].strip() if id_col is not None and id_col < len(row) else ""
        if kind is None and _REVERSAL_RE.search(delim.join(row)):
            continue  # Rückbuchung ohne erkennbare Richtung — wie bisher kein Vorgang
        if kind is None:
            # Art unbekannt → nicht raten, als nicht unterstützt melden
            out.append(ExchangeTrade(exchange=name, day=dates[0], kind="unknown", sats=sats, art=art, ref=ref))
            continue
        if _REVERSAL_RE.search(delim.join(row)):
            # Rückbuchung einer fehlgeschlagenen Auszahlung/Einzahlung (z. B. Strike „Reversal“):
            # hebt den ursprünglichen Vorgang auf, ist selbst kein Vorgang
            reversals.append((kind, dates[0], sats))
            continue
        fiat_ccy = cell("fiat_ccy", extra).strip().upper()
        eur = _parse_decimal(cell("fiat", extra)) if fiat_ccy in ("", "EUR") else None
        fee_ccy = cell("fee_ccy", extra).strip().upper()
        fee_val = _parse_decimal(cell("fee", extra))
        fee_eur, fee_sats = None, 0
        fee_btc = _amount_to_sats(cell("fee_btc", extra)) if "fee_btc" in extra else None
        if fee_btc:
            fee_sats = fee_btc
        if fee_val:
            if fee_ccy in ("BTC", "XBT"):
                fee_sats = round(fee_val * SATS_PER_BTC)
            elif fee_ccy in ("SAT", "SATS"):
                fee_sats = round(fee_val)
            elif fee_ccy in ("", "EUR"):
                fee_eur = fee_val
        out.append(
            ExchangeTrade(
                exchange=name,
                day=dates[0],
                kind=kind,
                sats=sats,
                eur=eur,
                fee_eur=fee_eur,
                fee_sats=fee_sats,
                destination=bool(_row_addresses(cell("dest", extra))),
                addresses=tuple(_row_addresses(delim.join(row))),
                art=art,
                ref=ref,
            )
        )
    for kind, day, sats in reversals:
        cand = [
            t for t in out
            if t.kind == kind and abs((t.day - day).days) <= 1 and abs(t.sats - sats) <= t.fee_sats
        ]
        if cand:
            out.remove(min(cand, key=lambda t: (abs(t.sats - sats), abs((t.day - day).days))))
    return out
