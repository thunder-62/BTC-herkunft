"""Jahres-Resümee: hypothetischer Gewinn/Verlust der Cloud-Austritte je Jahr.

Annahme (ausdrücklich hypothetisch): Alles, was die Cloud an eine fremde
Adresse (Börse, Dienst, Dritte) verlassen hat, wurde **am Tag des Austritts
zum Tageskurs veräußert**. Je Austritt (Coin-Anteil):

    Veräußerungswert = BTC × EUR-Kurs am Austrittstag
    Anschaffungswert = BTC × EUR-Kurs am Anschaffungstag (Cloud-Eintritt)
    Werbungskosten   = Netzwerkgebühr der Veräußerungs-Transaktion × Kurs am
                       Austrittstag (BMF-Schreiben 06.03.2025, Rz. 59), anteilig
                       nach Betrag auf die Coins der Transaktion verteilt
    Gewinn/Verlust   = Veräußerungswert − Anschaffungswert − Werbungskosten

Die Anschaffung je Coin folgt der Einzelbetrachtung (Rz. 61). Gebühren reiner
Umbuchungen zwischen eigenen Wallets sind keine Werbungskosten einer
Veräußerung und bleiben außen vor.

Getrennt nach Haltedauer < 1 Jahr (privates Veräußerungsgeschäft, § 23 EStG —
steuerlich relevant) und ≥ 1 Jahr (Haltefrist erfüllt). Die Freigrenze wird
nur als Hinweis gezeigt. Kurse = Referenzkurse, keine echten Kauf-/Verkaufs-
preise. **Keine Steuerberatung.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

SATS_PER_BTC = 100_000_000
HOLDING_DAYS = 365


def freigrenze_eur(year: int) -> int:
    """Freigrenze private Veräußerungsgeschäfte (§ 23 Abs. 3 Satz 5 EStG) aus dem Regelwerk
    des Jahres (``regeln/<JJJJ>.yaml``; fehlt es → RegelwerkFehler). Gilt für ALLE privaten
    Veräußerungsgeschäfte des Jahres zusammen — nur Hinweis."""
    from btc_origin.regelwerk import rules_for

    return rules_for(year).freigrenze_23_eur


@dataclass
class YearBucket:
    btc_sats: int = 0
    proceeds_eur: float = 0.0
    cost_eur: float = 0.0
    fees_eur: float = 0.0  # Werbungskosten (Rz. 59)
    priced_sats: int = 0  # sats with both prices known
    missing_price: int = 0  # disposals without a price on either day

    @property
    def gain_eur(self) -> float:
        return self.proceeds_eur - self.cost_eur - self.fees_eur

    def as_dict(self) -> dict[str, Any]:
        return {
            "btc_sats": self.btc_sats,
            "proceeds_eur": round(self.proceeds_eur, 2),
            "cost_eur": round(self.cost_eur, 2),
            "fees_eur": round(self.fees_eur, 2),
            "gain_eur": round(self.gain_eur, 2),
            "priced_sats": self.priced_sats,
            "missing_price": self.missing_price,
        }


@dataclass
class YearSummary:
    year: int
    disposals: int = 0
    short: YearBucket = field(default_factory=YearBucket)  # < 1 Jahr gehalten
    long: YearBucket = field(default_factory=YearBucket)  # ≥ 1 Jahr gehalten
    details: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        # Regelwerk nur für Jahre mit Veräußerung (sonst wird nichts bewertet)
        fg = freigrenze_eur(self.year) if self.disposals else None
        gain_short = round(self.short.gain_eur, 2)
        return {
            "year": self.year,
            "disposals": self.disposals,
            "short": self.short.as_dict(),
            "long": self.long.as_dict(),
            "freigrenze_eur": fg,
            # Hinweis only: Freigrenze counts all private sales of the year.
            "over_freigrenze": gain_short >= fg if fg is not None and self.short.missing_price == 0 else None,
            "complete": self.short.missing_price == 0 and self.long.missing_price == 0,
            "details": list(self.details),
        }


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def allocate_fee_shares(rows: list[dict[str, Any]]) -> dict[int, int]:
    """Index of each outflow row → its integer share of the tx network fee.

    Fee rows (``kind == "fee"``) of a tx are split by amount across the tx's
    outflow rows; cumulative rounding makes the shares add up exactly to the
    fee (Zuflüsse − Abflüsse − Gebühren = Bestand stays exact). Fees of txs
    without an outflow (Umbuchungen) get no share.
    """
    fee_by_tx: dict[str, int] = {}
    out_by_tx: dict[str, int] = {}
    for r in rows:
        tx = str(r.get("txid") or "")
        sats = int(r.get("amount_sats") or 0)
        if r.get("kind") == "fee":
            fee_by_tx[tx] = fee_by_tx.get(tx, 0) + sats
        else:
            out_by_tx[tx] = out_by_tx.get(tx, 0) + sats
    shares: dict[int, int] = {}
    cum_by_tx: dict[str, int] = {}
    for i, r in enumerate(rows):
        if r.get("kind") == "fee":
            continue
        tx = str(r.get("txid") or "")
        total, fee = out_by_tx.get(tx, 0), fee_by_tx.get(tx, 0)
        if not total or not fee:
            continue
        before = cum_by_tx.get(tx, 0)
        after = before + int(r.get("amount_sats") or 0)
        cum_by_tx[tx] = after
        shares[i] = fee * after // total - fee * before // total
    return shares


def yearly_summary(
    out_rows: Iterable[dict[str, Any]],
    price_eur: Callable[[date], float | None],
    *,
    threshold_days: int = HOLDING_DAYS,
) -> list[YearSummary]:
    """Group Cloud-Austritte (OUT rows of /api/cloud/flows) by year of exit.

    Rows with ``kind == "fee"`` are not disposals; their sats count as
    Werbungskosten of the disposal in the same transaction (Rz. 59) — fees of
    transactions without an external outflow (Umbuchungen) are ignored.

    Optional explicit row fields (from exchange exports, see exchange_sales):
    ``fee_sats``, ``fee_eur``, ``proceeds_eur``, ``cost_eur`` override the
    derived values; ``not_disposal`` rows (e.g. still held on the exchange)
    are listed in ``details`` but counted in no bucket; ``origin`` / ``status``
    are passed through.
    """
    rows = [r for r in out_rows if r.get("direction") in (None, "out")]
    fee_share = allocate_fee_shares(rows)

    years: dict[int, YearSummary] = {}
    for i, r in enumerate(rows):
        if r.get("kind") == "fee":
            continue
        exit_day, acq_day = _day(r.get("time")), _day(r.get("lot_date"))
        sats = int(r.get("amount_sats") or 0)
        if exit_day is None or sats <= 0:
            continue
        days = r.get("haltefrist_days")
        if days is None and acq_day is not None:
            days = (exit_day - acq_day).days
        # Unknown acquisition → Haltefrist not provable → counted as < 1 Jahr.
        short = days is None or int(days) < threshold_days
        ys = years.setdefault(exit_day.year, YearSummary(year=exit_day.year))
        fee_sats = int(r["fee_sats"]) if r.get("fee_sats") is not None else fee_share.get(i, 0)
        detail: dict[str, Any] = {
            "exit_date": exit_day.isoformat(),
            "acquisition_date": acq_day.isoformat() if acq_day else None,
            "days_held": days,
            "short_term": short,
            "btc_sats": sats,
            "fee_sats": fee_sats,
            "price_exit_eur": None,
            "price_acquisition_eur": None,
            "proceeds_eur": None,
            "cost_eur": None,
            "fee_eur": None,
            "gain_eur": None,
            "counterparty": r.get("external_name") or r.get("address") or "außerhalb Cloud",
            "address": r.get("address") or None,
            "txid": r.get("txid"),
            "origin_txid": r.get("origin_txid") or "",  # Zufluss, aus dem der Teilbestand stammt
            "origin": r.get("origin") or "wallet",
            "status": r.get("status") or "",
            "disposal": not r.get("not_disposal"),
            "deposit_date": r.get("deposit_date"),
            "acq_source": r.get("acq_source") or "",
            "proceeds_estimated": bool(r.get("proceeds_estimated")),
        }
        ys.details.append(detail)
        if r.get("not_disposal"):
            continue
        ys.disposals += 1
        bucket = ys.short if short else ys.long
        bucket.btc_sats += sats
        px_exit = price_eur(exit_day)
        px_acq = price_eur(acq_day) if acq_day else None
        btc = sats / SATS_PER_BTC
        proceeds = r.get("proceeds_eur")
        if proceeds is None and px_exit is not None:
            proceeds = btc * px_exit
        cost = r.get("cost_eur")
        if cost is None and r.get("acq_price_eur") is not None:
            cost = btc * float(r["acq_price_eur"])  # purchase price lt. Export (Rz. 20)
        if cost is None and px_acq is not None:
            cost = btc * px_acq
        fee = r.get("fee_eur")
        if fee is None and px_exit is not None:
            fee = fee_sats / SATS_PER_BTC * px_exit
        gain = None
        if proceeds is not None and cost is not None and fee is not None:
            gain = proceeds - cost - fee
            bucket.proceeds_eur += proceeds
            bucket.cost_eur += cost
            bucket.fees_eur += fee
            bucket.priced_sats += sats
        else:
            bucket.missing_price += 1
        detail.update(
            {
                "price_exit_eur": px_exit,
                "price_acquisition_eur": px_acq,
                "proceeds_eur": round(proceeds, 2) if proceeds is not None else None,
                "cost_eur": round(cost, 2) if cost is not None else None,
                "fee_eur": round(fee, 2) if fee is not None else None,
                "gain_eur": round(gain, 2) if gain is not None else None,
            }
        )
    return [years[y] for y in sorted(years)]
