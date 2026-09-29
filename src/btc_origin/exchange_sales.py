"""Real purchase and sale data from exchange exports (BMF 06.03.2025 Rz. 20, 55).

Coins are usually bought/sold on a central exchange and only later moved
to/from the own wallet. For the tax the time of the trade on the exchange is
decisive (Rz. 20, 55) — not the Cloud-Eintritt/-Austritt on the chain. With
an export in ``local/boersen/`` the account of each exchange is replayed:

* Pool per exchange: coins bought there (date, EUR price incl. fee) and coins
  deposited from the wallets (acquisition date from the Einzelbetrachtung).
* Sells and withdrawals take coins from the pool in order of acquisition
  (FiFo, Rz. 61 — inside an exchange account coins cannot be told apart).
* **Kauf-Seite:** a withdrawal matched to a Cloud-Eintritt (same exchange,
  date ±7 days, amount incl. withdrawal fee) gives that entry the purchase
  date(s) and price(s) of the withdrawn coins (``acquisition_overrides``).
* **Verkauf-Seite:** a deposit to an exchange whose export has sell rows is
  valued with the sale day, the EUR amount and the exchange fee of the sells;
  deposits not sold stay on the exchange (no disposal).

Everything without an export keeps the chain dates: Cloud-Eintritt =
Anschaffung, Abfluss an fremde Adresse = Veräußerung (Annahme).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

from btc_origin.holding_clock import AcqPiece
from btc_origin.local_files import ExchangeTrade
from btc_origin.year_summary import allocate_fee_shares

SATS_PER_BTC = 100_000_000
MATCH_MAX_DAYS = 7
# Rücklauf: Auszahlung, die binnen 1 Tag als Einzahlung (Menge − Gebühr) zurückkommt
RETURN_MAX_DAYS = 1
RETURN_MAX_DIFF_SATS = 1_000
RETURN_MAX_DIFF_RATIO = 0.01
WITHDRAW_FEE_MAX_SATS = 100_000
WITHDRAW_FEE_MAX_RATIO = 0.03
DEPOSIT_MAX_DIFF_SATS = 100  # Einzahlung ↔ Abfluss: Rundung im Export
DEPOSIT_MAX_DIFF_RATIO = 0.001


# Satoshi-Test: kleiner Betrag aus der eigenen Wallet an die Börse als Nachweis der
# Wallet-Inhaberschaft (EU-Geldtransferverordnung 2023/1113, ab 30.12.2024 für
# Übertragungen über 1.000 € zwischen Börse und eigener Wallet).
TFR_START = date(2024, 12, 30)
SATOSHI_TEST_MAX_SATS = 100_000  # 0,001 BTC
SATOSHI_TEST_MAX_DAYS = 7
SATOSHI_TEST_STATUS = "Satoshi-Test (Nachweis der Wallet-Inhaberschaft, VO (EU) 2023/1113)"


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


@dataclass
class _Coins:
    acq: date | None
    sats: int
    origin: str  # wallet | exchange | unknown
    row: dict[str, Any] | None = None  # deposit row (wallet origin)
    fee_sats: int = 0  # network fee of the deposit (wallet origin)
    fee_eur: float = 0.0  # deposit fee in EUR at the deposit day
    price_eur: float | None = None  # EUR per BTC incl. fees (exchange purchase)
    deposit_day: date | None = None
    source: str = ""  # Herkunftstext eines Börsenkaufs (buy_source)

    def take(self, sats: int) -> tuple[int, int, float]:
        """Take ``sats`` → (sats, fee_sats, fee_eur) shares; exact integers."""
        fee = self.fee_sats * sats // self.sats if sats < self.sats else self.fee_sats
        fee_eur = self.fee_eur * sats / self.sats
        self.fee_sats -= fee
        self.fee_eur -= fee_eur
        self.sats -= sats
        return sats, fee, fee_eur


@dataclass
class Withdrawal:
    """Coins leaving an exchange (export row or buy sent to an address)."""

    exchange: str
    day: date
    sats: int
    addresses: tuple[str, ...]
    pieces: list[AcqPiece] = field(default_factory=list)
    matched_entry: dict[str, Any] | None = None
    fee_sats: int = 0  # withdrawal fee in BTC laut Export
    # True: export amount = received, fee charged on top (net); False: the
    # export amount includes the fee (gross). Set when matched.
    net_fee: bool = False
    direct: bool = False  # Kauf mit Direktversand an eine Adresse (z. B. Relai)
    trade_id: int = 0  # id() der Export-Zeile (gleiche Menge/Tag bleiben unterscheidbar)
    entry_key: str = ""  # "txid:vout" des zugeordneten Zuflusses
    # Teil der Auszahlung, den kein Kauf/Bestand im Export davor deckt (z. B. per
    # Lightning erhaltene BTC) → beim Zufluss „ohne Kaufbeleg“ (Zuflusstag, Tageskurs)
    uncovered_sats: int = 0

    def recon(self) -> dict[str, Any]:
        """Käufe − Auszahlungsgebühr − Transaktionskosten = Eingang (exact)."""
        received = int((self.matched_entry or {}).get("amount_sats") or 0)
        bought = sum(p.sats for p in self.pieces)
        # Fee charged on top and not among the listed coins → not subtracted.
        on_top = self.net_fee and bought == received
        fee = 0 if on_top else self.fee_sats
        rest = bought - fee - received
        return {
            "bought_sats": bought,
            "fee_sats": self.fee_sats,
            "fee_on_top": on_top,
            "rest_sats": rest,
            "received_sats": received,
            "ok": rest >= 0,
        }


@dataclass
class ExchangeReport:
    """Abgleich of one exchange export — documented in the Finanzamt report."""

    exchange: str
    buys: int = 0
    sells: int = 0
    withdrawals: int = 0
    deposits_export: int = 0
    deposits_wallet: int = 0  # Cloud-Austritte to this exchange
    deposits_sold_sats: int = 0
    deposits_held_sats: int = 0
    deposits_back_sats: int = 0
    unknown_sold_sats: int = 0
    # Einzahlungen laut Export: einem Abfluss aus den Wallets zugeordnet / unbekannte Quelle
    deposits_matched: int = 0
    satoshi_tests: int = 0  # davon Satoshi-Tests (Nachweis der Wallet-Inhaberschaft)
    # Verkäufe laut Export (Tag, sats) → Herkunft der verkauften BTC (wallet/exchange/unknown)
    sale_origins: dict[tuple[date, int], set[str]] = field(default_factory=dict)
    # Kauftag auf der Börse → Tage der Verkäufe, die diese Käufe (FiFo) veräußerten
    sold_buys: dict[date, set[date]] = field(default_factory=dict)
    entries_total: int = 0  # Cloud-Eintritte from this exchange
    matched: list[Withdrawal] = field(default_factory=list)
    unmatched: list[Withdrawal] = field(default_factory=list)
    unmatched_deposits: list[ExchangeTrade] = field(default_factory=list)  # Quelle unbekannt
    # Rückläufe: id(Auszahlung) → Einzahlung, mit der sie zurückkam (beides kein Vorgang)
    returns: dict[int, ExchangeTrade] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "buys": self.buys,
            "sells": self.sells,
            "withdrawals": self.withdrawals,
            "deposits_export": self.deposits_export,
            "deposits_wallet": self.deposits_wallet,
            "deposits_sold_sats": self.deposits_sold_sats,
            "deposits_held_sats": self.deposits_held_sats,
            "deposits_back_sats": self.deposits_back_sats,
            "unknown_sold_sats": self.unknown_sold_sats,
            "deposits_matched": self.deposits_matched,
            "entries_total": self.entries_total,
            "entries_matched": len(self.matched),
            "withdrawals_unmatched": len(self.unmatched),
        }


_FIAT = ("", "EUR", "USD", "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "KARTE")


def is_swap(t: ExchangeTrade) -> bool:
    """Kauf/Verkauf gegen einen anderen Kryptowert (Tausch, Rz. 54)."""
    return t.eur is None and str(getattr(t, "quote", "") or "").upper() not in _FIAT


def buy_source(t: ExchangeTrade) -> str:
    """Herkunftstext eines Kaufs; beginnt immer mit „Kauf auf <Börse> lt. Export“."""
    src = f"Kauf auf {t.exchange} lt. Export"
    if is_swap(t):
        return f"{src} (Tausch gegen {t.quote}, Rz. 54 — nicht bewertet)"
    if t.einkunft:
        return f"{src} ({t.kategorie}, Einkunft § 22 Nr. 3 EStG — Tageskurs)"
    if t.eur is None:
        return f"{src} (kein Betrag im Export — Tageskurs)"
    return src


def is_satoshi_test(c: _Coins, t: ExchangeTrade) -> bool:
    """Kleine Einzahlung aus den Wallets (≤ 0,001 BTC), ab 30.12.2024, die binnen
    7 Tagen mit einer mindestens zehnmal größeren Auszahlung zurückgeht."""
    dep = int((c.row or {}).get("amount_sats") or 0)
    if not dep or c.deposit_day is None or c.deposit_day < TFR_START:
        return False
    days = (t.day - c.deposit_day).days
    return 0 <= days <= SATOSHI_TEST_MAX_DAYS and dep <= SATOSHI_TEST_MAX_SATS and t.sats >= 10 * dep


def _buy_price(t: ExchangeTrade, price_eur: Callable[[date], float | None]) -> float | None:
    """EUR per BTC incl. fees of a buy row. Without an amount in the export the
    Tageskurs of the purchase day (documented rule, Rz. 91); a Tausch against
    another crypto asset stays unvalued (None, only marked)."""
    if t.sats <= 0 or is_swap(t):
        return None
    if t.eur is None:
        return price_eur(t.day)
    fee = t.fee_eur or 0.0
    if t.fee_sats:
        px = price_eur(t.day)
        fee += t.fee_sats / SATS_PER_BTC * px if px is not None else 0.0
    return (t.eur + fee) / (t.sats / SATS_PER_BTC)


def replay_exchanges(
    out_rows: Iterable[dict[str, Any]],
    trades: Iterable[ExchangeTrade],
    price_eur: Callable[[date], float | None],
    net_fee_keys: set[tuple[str, date, int]] | None = None,
) -> tuple[list[dict[str, Any]], list[Withdrawal], dict[str, ExchangeReport]]:
    """Replay every exchange account of the exports.

    Returns (rows for ``yearly_summary``, withdrawals with acquisition pieces,
    report per exchange). ``net_fee_keys`` = withdrawals whose fee was charged
    on top of the exported amount (known after matching) — their fee coins
    also leave the pool. Rows to an exchange with sell rows are replaced by
    sale pieces / „auf der Börse“ remainders; other outflow rows pass through
    with their explicit network-fee share. Fee rows are consumed.
    """
    rows = [r for r in out_rows if r.get("direction") in (None, "out")]
    trades = list(trades)
    shares = allocate_fee_shares(rows)
    reports: dict[str, ExchangeReport] = {}
    for t in trades:
        rep = reports.setdefault(t.exchange, ExchangeReport(t.exchange))
        if t.kind == "buy":
            rep.buys += 1
        elif t.kind == "sell":
            rep.sells += 1
        elif t.kind == "withdraw":
            rep.withdrawals += 1
        elif t.kind == "deposit":
            rep.deposits_export += 1
    selling = {t.exchange for t in trades if t.kind == "sell"}
    # Einzahlungen laut Export ↔ Abflüsse aus den Wallets (gleiche Börse, ±7 Tage,
    # Menge bis auf Gebührentoleranz): diese Abflüsse sind Umbuchungen auf das
    # eigene Börsenkonto (Rz. 54), keine Veräußerung.
    export_deposits = {
        ex: sorted((t for t in trades if t.kind == "deposit" and t.exchange == ex), key=lambda t: t.day)
        for ex in reports
    }
    used_deposits: set[int] = set()

    def deposit_in_export(ex: str, day: date | None, sats: int) -> bool:
        if day is None:
            return False
        best = None
        for t in export_deposits.get(ex, []):
            if id(t) in used_deposits:
                continue
            gap = abs((t.day - day).days)
            diff = abs(t.sats - sats)
            # Einzahlung: die Börse schreibt den empfangenen Betrag gut (keine Auszahlungsgebühr)
            # → nur Rundung zulassen, nicht die Gebührentoleranz der Auszahlungen
            if gap <= MATCH_MAX_DAYS and diff <= max(DEPOSIT_MAX_DIFF_SATS, int(sats * DEPOSIT_MAX_DIFF_RATIO)):
                cand = (gap, diff, t)
                if best is None or cand[:2] < best[:2]:
                    best = cand
        if best is None:
            return False
        used_deposits.add(id(best[2]))
        reports[ex].deposits_matched += 1
        return True

    result: list[dict[str, Any]] = []
    pools: dict[str, list[_Coins]] = {ex: [] for ex in reports}
    events: list[tuple[date, float, str, Any]] = []  # (day, order, exchange, item)
    withdrawals: list[Withdrawal] = []

    for i, r in enumerate(rows):
        if r.get("kind") == "fee":
            continue
        fee_sats = shares.get(i, 0)
        ex = str(r.get("external_name") or "").replace("³", "")  # ³ = eigene Zuordnung
        dep_day = _day(r.get("time"))
        if ex in reports:
            reports[ex].deposits_wallet += 1
        in_export = ex in reports and deposit_in_export(ex, dep_day, int(r.get("amount_sats") or 0))
        if (ex not in selling and not in_export) or dep_day is None:
            result.append({**r, "fee_sats": fee_sats})
            continue
        px = price_eur(dep_day)
        coins = _Coins(
            acq=_day(r.get("lot_date")),
            sats=int(r.get("amount_sats") or 0),
            origin="wallet",
            row=r,
            fee_sats=fee_sats,
            fee_eur=fee_sats / SATS_PER_BTC * px if px is not None else 0.0,
            price_eur=r.get("acq_price_eur"),
            deposit_day=dep_day,
        )
        events.append((dep_day, 0, ex, coins))
    for t in trades:
        if t.kind == "buy":
            price = _buy_price(t, price_eur)
            if t.destination:
                # Bought and sent straight to an address (e.g. Relai).
                withdrawals.append(
                    Withdrawal(
                        t.exchange, t.day, t.sats, t.addresses,
                        [AcqPiece(t.sats, t.day, price, buy_source(t))],
                        fee_sats=t.fee_sats,
                        direct=True,
                        trade_id=id(t),
                    )
                )
            else:
                events.append((t.day, 0, t.exchange, _Coins(acq=t.day, sats=t.sats, origin="exchange", price_eur=price,
                                                            source=buy_source(t))))
        elif t.kind in ("sell", "withdraw"):
            events.append((t.day, 1, t.exchange, t))
    # Rücklauf: Eine Auszahlung, die binnen eines Tages als Einzahlung aus unbekannter Quelle
    # (Menge − Gebühr) wieder gutgeschrieben wird, hat die Börse nicht verlassen — z. B.
    # Auszahlung an eine eigene Einzahlungsadresse. Beide zählen nicht; nur die Gebühr geht ab.
    for ex, deps in export_deposits.items():
        outs = [t for t in trades if t.exchange == ex and t.kind == "withdraw"]
        for d in deps:
            if id(d) in used_deposits:
                continue
            cands = [
                w for w in outs
                if id(w) not in reports[ex].returns
                and 0 <= (d.day - w.day).days <= RETURN_MAX_DAYS
                # zurück kommt weniger (Gebühr); exakt gleich = eher die erneute Auszahlung
                and 0 < w.sats - d.sats <= max(RETURN_MAX_DIFF_SATS, int(w.sats * RETURN_MAX_DIFF_RATIO))
            ]
            if cands:
                w = min(cands, key=lambda w: ((d.day - w.day).days, w.sats - d.sats))
                reports[ex].returns[id(w)] = d
                used_deposits.add(id(d))
    returned = {k for r in reports.values() for k in r.returns}
    # Rückläufe zuerst: die spätere echte Auszahlung desselben Tages bekommt die Käufe
    events = [(e[0], 0.5, e[2], e[3]) if isinstance(e[3], ExchangeTrade) and id(e[3]) in returned else e
              for e in events]
    events.sort(key=lambda e: (e[0], e[1]))

    def piece_row(c: _Coins, sats: int, fee_sats: int, fee_eur: float,
                  sale: ExchangeTrade | None, status: str, not_disposal: bool, ex: str) -> dict[str, Any]:
        base = dict(c.row or {})
        day = sale.day if sale else (c.deposit_day or c.acq)
        row: dict[str, Any] = {
            **base,
            "direction": "out",
            "kind": "exchange_sale" if sale else "exchange_held",
            "origin": c.origin,
            "time": day.isoformat() if day else None,
            "lot_date": c.acq.isoformat() if c.acq else None,
            "amount_sats": sats,
            "haltefrist_days": (day - c.acq).days if day and c.acq else None,
            "fee_sats": fee_sats if c.origin == "wallet" else 0,
            "external_name": base.get("external_name") or ex,
            "status": status,
            "not_disposal": not_disposal,
            "deposit_date": c.deposit_day.isoformat() if c.deposit_day else None,
            "acq_price_eur": c.price_eur,
        }
        if c.origin == "exchange":
            row["acq_source"] = f"Kauf auf {ex} lt. Export"
            row.pop("txid", None)
            row["address"] = None
        if sale is not None:
            frac = sats / sale.sats
            px = price_eur(sale.day)
            proceeds = sale.eur * frac if sale.eur is not None else (
                sats / SATS_PER_BTC * px if px is not None else None
            )
            sale_fee = (sale.fee_eur or 0.0) * frac
            if sale.fee_sats and px is not None:
                sale_fee += sale.fee_sats * frac / SATS_PER_BTC * px
            row["proceeds_eur"] = proceeds
            row["proceeds_estimated"] = sale.eur is None  # kein Erlös im Export → Tageskurs
            row["fee_eur"] = sale_fee + fee_eur
        return row

    for _day_, _order, ex, item in events:
        pool = pools[ex]
        rep = reports[ex]
        if isinstance(item, _Coins):
            pool.append(item)
            pool.sort(key=lambda c: (c.acq or date.max))
            continue
        t: ExchangeTrade = item
        if id(t) in rep.returns:
            # nur die Gebühr verlässt das Börsenkonto (FiFo, ohne Veräußerung)
            fee_left = t.sats - rep.returns[id(t)].sats
            while fee_left > 0 and pool:
                n = min(fee_left, pool[0].sats)
                pool[0].take(n)
                fee_left -= n
                if pool[0].sats == 0:
                    pool.pop(0)
            continue
        need = t.sats
        w = (
            Withdrawal(ex, t.day, t.sats, t.addresses, fee_sats=t.fee_sats, trade_id=id(t))
            if t.kind == "withdraw"
            else None
        )
        if w is not None and net_fee_keys and (ex, t.day, t.sats) in net_fee_keys:
            need += t.fee_sats  # fee charged on top: those coins leave the pool too
            w.net_fee = True
        while need > 0 and pool:
            c = pool[0]
            n = min(need, c.sats)
            acq, price, origin, src = c.acq, c.price_eur, c.origin, c.source
            sats, fee, fee_eur = c.take(n)
            need -= n
            if t.kind == "sell":
                rep.sale_origins.setdefault((t.day, t.sats), set()).add(origin)
                if origin == "exchange" and acq is not None:
                    rep.sold_buys.setdefault(acq, set()).add(t.day)
                if origin == "wallet":
                    rep.deposits_sold_sats += sats
                result.append(piece_row(
                    c, sats, fee, fee_eur, t,
                    f"Verkauf am {t.day.strftime('%d.%m.%Y')} lt. Export", False, ex,
                ))
            else:
                assert w is not None
                sat_test = origin == "wallet" and is_satoshi_test(c, t)
                if acq is not None:
                    if price is None and origin == "wallet":
                        price = price_eur(acq)  # eingezahlte Teilbestände: Kurs am Anschaffungstag
                    w.pieces.append(AcqPiece(
                        sats, acq, price,
                        (src or f"Kauf auf {ex} lt. Export") if origin == "exchange"
                        else "Satoshi-Test, zurück ausgezahlt" if sat_test
                        else "Einzahlung aus den Wallets, zurück ausgezahlt",
                    ))
                if origin == "wallet":
                    rep.deposits_back_sats += sats
                    if sat_test:
                        rep.satoshi_tests += 1
                    result.append(piece_row(
                        c, sats, fee, fee_eur, None,
                        (SATOSHI_TEST_STATUS + ", " if sat_test else "")
                        + f"wieder ausgezahlt am {t.day.strftime('%d.%m.%Y')} lt. Export",
                        True, ex,
                    ))
            if c.sats == 0:
                pool.pop(0)
        if w is not None:
            w.uncovered_sats = max(need, 0)
            withdrawals.append(w)
        if need > 0 and t.kind == "sell":
            rep.sale_origins.setdefault((t.day, t.sats), set()).add("unknown")
            rep.unknown_sold_sats += need
            unknown = _Coins(acq=None, sats=need, origin="unknown")
            result.append(piece_row(
                unknown, need, 0, 0.0, t,
                "Verkauf lt. Export — Herkunft unbekannt (nicht aus den Wallets)", False, ex,
            ))
    for ex, deps in export_deposits.items():
        reports[ex].unmatched_deposits = [t for t in deps if id(t) not in used_deposits]
    for ex, pool in pools.items():
        for c in pool:
            if c.origin == "wallet" and c.sats > 0:
                reports[ex].deposits_held_sats += c.sats
                sats, fee, fee_eur = c.take(c.sats)
                result.append(piece_row(
                    c, sats, fee, fee_eur, None,
                    "lt. Export nicht verkauft (auf der Börse)", True, ex,
                ))
    return result, withdrawals, reports


def resolve_exchange_sales(
    out_rows: Iterable[dict[str, Any]],
    trades: Iterable[ExchangeTrade],
    price_eur: Callable[[date], float | None],
) -> list[dict[str, Any]]:
    """Rows for ``yearly_summary`` (sale side only)."""
    return replay_exchanges(out_rows, trades, price_eur)[0]


def match_withdrawals(
    in_rows: Iterable[dict[str, Any]],
    withdrawals: list[Withdrawal],
    reports: dict[str, ExchangeReport] | None = None,
) -> dict[str, list[AcqPiece]]:
    """Withdrawal ↔ Cloud-Eintritt → ``"txid:vout"`` → purchase pieces.

    A Cloud-Eintritt matches a withdrawal of the same exchange (sender name,
    or the withdrawal row names the receiving address) within ±7 days whose
    export amount is the received amount plus at most the withdrawal fee.
    Each entry and each withdrawal is used once; closest date wins.
    """
    entries = []
    for r in in_rows:
        if r.get("direction") != "in":
            continue
        lot_id = str(r.get("lot_id") or "")
        vout = lot_id.split(":")[-1].split("#")[0] if lot_id else ""
        day = _day(r.get("time"))
        if not vout or day is None:
            continue
        entries.append((r, f"{r.get('txid')}:{vout}", day))
    if reports is not None:
        for r, _k, _d in entries:
            name = str(r.get("source_name") or "")
            if name in reports:
                reports[name].entries_total += 1

    def fits(export_sats: int, received: int) -> bool:
        diff = export_sats - received
        return 0 <= diff <= max(WITHDRAW_FEE_MAX_SATS, int(received * WITHDRAW_FEE_MAX_RATIO))

    used: set[str] = set()
    overrides: dict[str, list[AcqPiece]] = {}
    for w in sorted(withdrawals, key=lambda w: w.day):
        best = None
        for r, key, day in entries:
            if key in used:
                continue
            name = str(r.get("source_name") or "")
            exact = name == w.exchange or (
                bool(w.addresses) and str(r.get("address") or "") in w.addresses
            )
            # A receipt beats a mere statement: unnamed (ext-NNN) or rule-named
            # senders may also match an export withdrawal (exact names first).
            loose = not name or name.startswith("ext-") or bool(r.get("source_declared"))
            gap = abs((day - w.day).days)
            if not (exact or loose) or gap > MATCH_MAX_DAYS or not fits(w.sats, int(r.get("amount_sats") or 0)):
                continue
            cand = (0 if exact else 1, gap, w.sats - int(r.get("amount_sats") or 0), key, r)
            if best is None or cand[:3] < best[:3]:
                best = cand
        rep = reports.get(w.exchange) if reports is not None else None
        if best is None or not w.pieces:
            if best is not None:
                # Menge und Datum passen, aber im Export steht davor kein Kauf (z. B. per
                # Lightning erhaltene BTC): Zufluss benennen, Anschaffung bleibt Zuflusstag.
                used.add(best[3])
                w.matched_entry = best[4]
                w.entry_key = best[3]
            if rep is not None:
                rep.unmatched.append(w)
            continue
        best = best[1:]  # drop the exact/loose rank
        used.add(best[2])
        w.matched_entry = best[3]
        w.entry_key = best[2]
        received = int(best[3].get("amount_sats") or 0)
        if w.fee_sats and w.sats == received:
            w.net_fee = True  # export amount already net, fee on top
        if w.uncovered_sats:
            # nicht durch Käufe gedeckt: nicht die Käufe hochrechnen, sondern Rest ohne
            # Kaufbeleg — Anschaffung = Zuflusstag, Tageskurs (wie jeder unbelegte Zufluss)
            w.pieces.append(AcqPiece(w.uncovered_sats, _day(best[3].get("time")) or w.day, None, ""))
        overrides[best[2]] = list(w.pieces)
        if rep is not None:
            rep.matched.append(w)
    # Export ohne Uhrzeit: Mehrere Auszahlungen am selben Tag haben keine erkennbare
    # Reihenfolge. Hat eine Auszahlung mit passendem Zufluss keine Käufe erhalten, weil
    # FiFo sie einer Auszahlung desselben Tages ohne Zufluss (z. B. Lightning) gab, gehen
    # diese Käufe an die Auszahlung mit Zufluss.
    for rep in (reports or {}).values():
        for w in [w for w in rep.unmatched if w.matched_entry is not None and not w.pieces]:
            donors = [
                d for d in rep.unmatched
                if d is not w and d.day == w.day and d.matched_entry is None and d.pieces
            ]
            # nur umverteilen, wenn die Käufe der anderen Auszahlungen die ganze Auszahlung decken
            if sum(p.sats for d in donors for p in d.pieces) < w.sats:
                continue
            need = w.sats
            for d in donors:
                while need > 0 and d.pieces:
                    p = d.pieces[0]
                    n = min(need, p.sats)
                    w.pieces.append(AcqPiece(n, p.acquisition_date, p.price_eur, p.source))
                    if n == p.sats:
                        d.pieces.pop(0)
                    else:
                        d.pieces[0] = AcqPiece(p.sats - n, p.acquisition_date, p.price_eur, p.source)
                    need -= n
            if w.pieces and need <= 0:
                rep.unmatched.remove(w)
                rep.matched.append(w)
                received = int(w.matched_entry.get("amount_sats") or 0)
                if w.fee_sats and w.sats == received:
                    w.net_fee = True
                overrides[w.entry_key] = list(w.pieces)
    return overrides


_KIND_DE = {"buy": "Kauf", "sell": "Verkauf", "withdraw": "Auszahlung", "deposit": "Einzahlung"}


def trade_ledger(
    trades: Iterable[ExchangeTrade], reports: dict[str, ExchangeReport]
) -> list[dict[str, Any]]:
    """All export rows in one uniform shape (Anhang C) with their Zuordnung:
    which Cloud-Eintritt a buy / withdrawal ended up in."""

    def entry_text(w: Withdrawal) -> str:
        e = w.matched_entry or {}
        day = str(e.get("time") or "")[:10]
        d = f"{day[8:10]}.{day[5:7]}.{day[:4]}" if len(day) == 10 else day
        wallet = e.get("wallet_name") or "Wallet"
        return f"→ Zufluss {d} in {wallet}"

    matched: dict[int, Withdrawal] = {}
    buys_in: dict[tuple[str, date], list[str]] = {}
    unmatched: dict[int, Withdrawal] = {}
    for rep in reports.values():
        for w in rep.matched:
            matched[w.trade_id] = w
            for p in w.pieces:
                if p.source.startswith("Kauf auf"):
                    buys_in.setdefault((w.exchange, p.acquisition_date), []).append(entry_text(w))
        for w in rep.unmatched:
            unmatched[w.trade_id] = w
    out: list[dict[str, Any]] = []
    for t in sorted(trades, key=lambda t: (t.day, t.exchange, t.kind)):
        key = id(t)
        rep_r = reports.get(t.exchange)
        back = rep_r.returns.get(key) if rep_r is not None else None
        back_of = next((w_id for w_id, d in (rep_r.returns.items() if rep_r else []) if id(d) == key), None)
        if back is not None:
            note = (f"Rücklauf: am {back.day.strftime('%d.%m.%Y')} als Einzahlung zurück (abzüglich Gebühr) — "
                    "hat das Börsenkonto nicht verlassen, keine Auszahlung")
        elif back_of is not None:
            note = "Rücklauf einer Auszahlung (siehe dort) — keine Einzahlung von außen"
        elif t.kind in ("withdraw",) or (t.kind == "buy" and t.destination):
            if key in matched:
                note = entry_text(matched[key])
            elif key in unmatched and unmatched[key].matched_entry is None and any(
                m.exchange == t.exchange and m.day == t.day and m.sats == t.sats for m in matched.values()
            ):
                note = "gleiche Menge wie eine zugeordnete Auszahlung desselben Tages — vermutlich doppelt"
            elif key in unmatched:
                w = unmatched[key]
                note = (
                    entry_text(w) + " (kein zuordenbarer Kauf im Export davor — Anschaffung = Zuflusstag)"
                    if w.matched_entry is not None
                    else "keinem Zufluss in die Wallets zugeordnet"
                )
            else:
                note = ""
        elif t.kind == "buy":
            rep_b = reports.get(t.exchange)
            sold = sorted(rep_b.sold_buys.get(t.day, set())) if rep_b is not None else []
            targets = sorted(set(buys_in.get((t.exchange, t.day), []))) + [
                f"→ Verkauf {d.strftime('%d.%m.%Y')} (Abschnitt 3.1)" for d in sold
            ]
            rep_t = reports.get(t.exchange)
            rest = (
                "auf der Börse verblieben, verkauft oder mit einer nicht zugeordneten Auszahlung abgeflossen"
                if rep_t is not None and rep_t.sells
                else "auf der Börse verblieben oder mit einer nicht zugeordneten Auszahlung abgeflossen (Anhang B)"
            )
            note = "; ".join(targets) if targets else rest
        elif t.kind == "sell":
            rep = reports.get(t.exchange)
            origins = rep.sale_origins.get((t.day, t.sats), set()) if rep is not None else set()
            note = "Veräußerung lt. Export (Abschnitte 2 und 3)"
            if origins and "wallet" not in origins:
                note += " — verkaufte BTC stammen nicht aus den betrachteten Wallets"
            elif "unknown" in origins or "exchange" in origins:
                note += " — teilweise nicht aus den betrachteten Wallets"
        else:
            note = "Einzahlung auf die Börse"
        if t.kind in ("buy", "sell") and t.eur is None:
            if is_swap(t):
                note += f" · Tausch gegen {t.quote} (Rz. 54) — nicht bewertet"
            elif getattr(t, "usd", None) is None:
                note += " · kein Betrag im Export" + (" (Kartenkauf)" if t.quote == "Karte" else "") + " — Tageskurs"
        price = t.eur / (t.sats / SATS_PER_BTC) if t.eur is not None and t.sats else None
        out.append(
            {
                "exchange": t.exchange,
                "day": t.day.isoformat(),
                "kind": _KIND_DE.get(t.kind, t.kind),
                "sats": t.sats,
                "eur": t.eur,
                "fee_eur": t.fee_eur,
                "fee_sats": t.fee_sats,
                "price_eur": price,
                "art": t.art,
                "kategorie": t.kategorie,
                "einkunft": t.einkunft,
                "file": t.file,
                "note": note
                + (
                    f" · {t.usd:,.2f} USD zum EZB-Referenzkurs".replace(",", "X").replace(".", ",").replace("X", ".")
                    if getattr(t, "usd", None) is not None
                    else ""
                ),
            }
        )
    return out


DIAG_WINDOW_DAYS = 30


def matching_diagnosis(
    in_rows: Iterable[dict[str, Any]],
    reports: dict[str, ExchangeReport],
    withdrawals: list[Withdrawal],
    matched_keys: set[str],
) -> list[dict[str, Any]]:
    """Beinahe-Treffer der Zuordnung Auszahlung ↔ Zufluss — nur Diagnose, die Regel
    (±7 Tage, Menge bis 0,001 BTC bzw. 3 % Gebühr) bleibt unverändert.

    Je nicht zugeordneter Export-Auszahlung und je Zufluss ohne Börsen-Kaufdaten der
    beste Kandidat der Gegenseite (±30 Tage) mit Abstand in Tagen, Abweichung in BTC
    bzw. % und dem Grund, warum die Regel nicht greift."""
    entries = []
    for r in in_rows:
        if r.get("direction") != "in":
            continue
        lot_id = str(r.get("lot_id") or "")
        vout = lot_id.split(":")[-1].split("#")[0] if lot_id else ""
        day = _day(r.get("time"))
        if day is None:
            continue
        entries.append((r, f"{r.get('txid')}:{vout}", day))

    def reasons(w: Withdrawal, r: dict[str, Any], key: str, day: date) -> list[str]:
        out = []
        name = str(r.get("source_name") or "")
        exact = name == w.exchange or (bool(w.addresses) and str(r.get("address") or "") in w.addresses)
        loose = not name or name.startswith("ext-") or bool(r.get("source_declared"))
        if not (exact or loose):
            out.append(f"Absender heißt „{name}“, nicht {w.exchange}")
        gap = abs((day - w.day).days)
        if gap > MATCH_MAX_DAYS:
            out.append(f"{gap} Tage Abstand (erlaubt ±{MATCH_MAX_DAYS})")
        received = int(r.get("amount_sats") or 0)
        diff = w.sats - received
        tol = max(WITHDRAW_FEE_MAX_SATS, int(received * WITHDRAW_FEE_MAX_RATIO))
        if diff < 0:
            out.append("Zufluss größer als Auszahlung laut Export")
        elif diff > tol:
            out.append(f"Mengendifferenz {diff / SATS_PER_BTC:.8f} BTC über Toleranz {tol / SATS_PER_BTC:.8f}")
        if key in matched_keys:
            out.append("Zufluss ist bereits einer anderen Auszahlung zugeordnet")
        same = w.matched_entry is not None and (
            w.matched_entry.get("txid"), str(w.matched_entry.get("time"))[:10]
        ) == (r.get("txid"), str(r.get("time"))[:10])
        if w.matched_entry is not None and not same:
            other = str(w.matched_entry.get("time") or "")[:10]
            out.append(f"Auszahlung ist bereits dem Zufluss vom {other} zugeordnet")
        if not w.pieces:
            out.append("kein zuordenbarer Kauf/Bestand auf der Börse vor der Auszahlung "
                       "(keiner oder schon mit anderen Auszahlungen abgeflossen)")
        return out or ["Regel erfüllt — bei gleichwertigen Kandidaten nicht gewählt"]

    def score(w: Withdrawal, r: dict[str, Any], day: date) -> float:
        received = int(r.get("amount_sats") or 0)
        rel = abs(w.sats - received) / max(w.sats, 1)
        return abs((day - w.day).days) + 20 * rel

    def row(kind: str, w: Withdrawal | None, r: dict[str, Any] | None, key: str, day: date | None) -> dict[str, Any]:
        received = int((r or {}).get("amount_sats") or 0)
        diff = (w.sats - received) if (w is not None and r is not None) else None
        return {
            "art": kind,
            "boerse": w.exchange if w is not None else "",
            "auszahlung_tag": w.day.isoformat() if w is not None else "",
            "auszahlung_btc": w.sats / SATS_PER_BTC if w is not None else None,
            "zufluss_tag": day.isoformat() if day is not None else "",
            "zufluss_btc": received / SATS_PER_BTC if r is not None else None,
            "zufluss_wallet": str((r or {}).get("wallet_name") or ""),
            "zufluss_absender": str((r or {}).get("source_name") or ""),
            "abstand_tage": abs((day - w.day).days) if (w is not None and day is not None) else None,
            "diff_btc": diff / SATS_PER_BTC if diff is not None else None,
            "diff_prozent": 100 * diff / w.sats if (diff is not None and w is not None and w.sats) else None,
            "grund": "; ".join(reasons(w, r, key, day)) if (w is not None and r is not None and day is not None)
            else "keine Auszahlung dieser Börse innerhalb ±30 Tage — deckt der Export den Zeitraum ab?",
        }

    out: list[dict[str, Any]] = []
    for rep in reports.values():
        for w in sorted(rep.unmatched, key=lambda w: w.day):
            cands = [(score(w, r, d), r, k, d) for r, k, d in entries if abs((d - w.day).days) <= DIAG_WINDOW_DAYS]
            if cands:
                _s, r, k, d = min(cands, key=lambda c: c[0])
                out.append(row("Auszahlung ohne Zufluss", w, r, k, d))
            else:
                out.append(row("Auszahlung ohne Zufluss", w, None, "", None))
    for r, k, d in entries:
        if k in matched_keys:
            continue
        name = str(r.get("source_name") or "")
        if name in reports or not name or name.startswith("ext-") or r.get("source_declared"):
            # benannte Börse: nur deren Auszahlungen; unbenannt/eigene Angabe: alle (±14 Tage)
            window = DIAG_WINDOW_DAYS if name in reports else 14
            cands = [
                (score(w, r, d), w)
                for w in withdrawals
                if abs((d - w.day).days) <= window and (name not in reports or w.exchange == name)
            ]
            if cands:
                _s, w = min(cands, key=lambda c: c[0])
                out.append(row("Zufluss ohne Kaufdaten", w, r, k, d))
            elif name in reports:
                out.append(row("Zufluss ohne Kaufdaten", None, r, k, d))  # Export deckt den Zeitraum nicht ab?
    return out


def open_exchange_items(
    reports: dict[str, ExchangeReport],
    explanations: Iterable[tuple[str, date | None, str, str]] = (),
) -> list[dict[str, Any]]:
    """Export-Zeilen ohne Wallet-Vorgang (Anhang „Nicht zugeordnete Börsenvorgänge“):
    Auszahlungen und Käufe mit Direktversand ohne Zufluss, Einzahlungen ohne Abfluss aus
    den Wallets. Erläuterung aus ``erlaeuterungen.csv`` (Börse, Datum, Art — leeres
    Datum/Art gilt für alle), sonst „offen“."""
    rules = list(explanations)

    def explain(ex: str, day: date, art: str) -> str:
        for r_ex, r_day, r_art, text in rules:
            if r_ex.strip().lower() != ex.lower():
                continue
            if r_day is not None and r_day != day:
                continue
            if r_art and not art.lower().startswith(r_art.strip().lower()):
                continue
            return text
        return "offen"

    out: list[dict[str, Any]] = []
    for ex in sorted(reports):
        rep = reports[ex]
        for w in rep.unmatched:
            art = "Kauf mit Direktversand" if w.direct else "Auszahlung"
            # Für die Regel-Suche: „Auszahlung mit/ohne Zufluss“ — eine Regel „Auszahlung“
            # gilt für beide, „Auszahlung ohne Zufluss“ nur für die ohne passenden Zufluss.
            match_art = f"{art} {'mit' if w.matched_entry is not None else 'ohne'} Zufluss"
            if w.matched_entry is not None:
                e = w.matched_entry
                d = str(e.get("time") or "")[:10]
                status = (
                    f"Zufluss {d[8:10]}.{d[5:7]}.{d[:4]} in {e.get('wallet_name') or 'Wallet'}; kein "
                    "zuordenbarer Kauf im Export davor — Anschaffung = Zuflusstag"
                )
            elif any(m.day == w.day and m.sats == w.sats for m in rep.matched):
                status = (
                    "gleicher Tag und gleiche Menge wie eine zugeordnete Auszahlung — vermutlich "
                    "doppelte Zeile im Export"
                )
            else:
                status = "keinem Zufluss in die Wallets zugeordnet"
            out.append({"exchange": ex, "day": w.day, "kind": art, "sats": w.sats,
                        "status": status, "note": explain(ex, w.day, match_art),
                        "entry_txid": str((w.matched_entry or {}).get("txid") or "")})
        for t in rep.unmatched_deposits:
            note = explain(ex, t.day, "Einzahlung")
            out.append({"exchange": ex, "day": t.day, "kind": "Einzahlung", "sats": t.sats,
                        "status": "keinem Abfluss aus den Wallets zugeordnet"
                        + (" (Quelle unbekannt)" if note == "offen" else " (Quelle: siehe Erläuterung)"),
                        "note": note, "entry_txid": ""})
    return sorted(out, key=lambda r: (r["day"], r["exchange"], r["kind"]))


def destination_check(
    reports: dict[str, ExchangeReport],
    own_wallet_of: dict[str, str],
    inflows_by_address: dict[str, list[tuple[date, int]]],
) -> list[dict[str, Any]]:
    """Für jede nicht zugeordnete Auszahlung bzw. jeden Kauf mit Direktversand, deren
    Export-Zeile eine Zieladresse nennt: Gehört die Adresse zu einer betrachteten Wallet,
    und welche Zuflüsse gab es dort? Die Adresse selbst wird nicht ausgegeben."""
    out: list[dict[str, Any]] = []
    for ex in sorted(reports):
        for w in reports[ex].unmatched:
            if not w.addresses:
                continue
            art = "Kauf mit Direktversand" if w.direct else "Auszahlung"
            for addr in w.addresses:
                wallet = own_wallet_of.get(addr)
                ins = sorted(inflows_by_address.get(addr, []))
                if wallet is None:
                    result = ("keine Adresse der betrachteten Wallets (andere Wallet, fremde Adresse "
                              "oder außerhalb der abgeleiteten Adressen der xpub)")
                elif not ins:
                    result = f"Adresse gehört zu „{wallet}“, dort aber kein Zufluss"
                else:
                    parts = [
                        f"{d:%d.%m.%Y} ({(d - w.day).days:+d} Tage, {100 * (s_ - w.sats) / w.sats:+.2f} %)"
                        for d, s_ in ins
                    ]
                    result = f"Adresse gehört zu „{wallet}“; Zuflüsse dort: " + "; ".join(parts)
                # „address“ nur für die Weiterverarbeitung im Speicher — nie ausgeben
                out.append({"exchange": ex, "day": w.day, "kind": art, "sats": w.sats, "result": result,
                            "address": addr, "own": wallet is not None})
    return out
