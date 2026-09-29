"""Cloud-of-wallets aggregation: external in/out, Bestand, internal fees (+ EUR).

All registered session wallets form one "cloud". Fully internal transfers
(between addresses/wallets inside the cloud) are net-zero for inflow/outflow;
their fees accumulate as interne Transaktionskosten.

EUR lines (optional, when a PriceOracle is supplied):
* Inflow  — each external inflow × EUR spot on cloud-entry / lot / block date
* Outflow — each external outflow × EUR on outflow date
* Fees    — fee sats × EUR on fee/tx date
* Bestand — current holdings × **current** EUR spot

Never invents prices: missing rates → null / „Kurs fehlt“.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from btc_origin.price_oracle import PriceOracle


SATS_PER_BTC = 100_000_000


@dataclass
class CloudSummary:
    inflow_sats: int = 0
    bestand_sats: int = 0
    outflow_sats: int = 0
    internal_fees_sats: int = 0
    external_fees_sats: int = 0
    flow_count: int = 0
    # Independent check: unspent own outputs straight from the raw ledger
    # (all ins − all outs). Must equal bestand_sats.
    chain_balance_sats: int = 0
    consistent: bool = True
    # EUR valuations (None = Kurs fehlt / not computed)
    inflow_eur: float | None = None
    bestand_eur: float | None = None
    outflow_eur: float | None = None
    fees_eur: float | None = None  # internal fees in EUR
    external_fees_eur: float | None = None
    spot_eur: float | None = None
    eur_note: str | None = None
    eur_partial: bool = False  # True if some legs missing a rate
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "inflow_sats": self.inflow_sats,
            "bestand_sats": self.bestand_sats,
            "outflow_sats": self.outflow_sats,
            "internal_fees_sats": self.internal_fees_sats,
            "external_fees_sats": self.external_fees_sats,
            "flow_count": self.flow_count,
            "chain_balance_sats": self.chain_balance_sats,
            "consistent": self.consistent,
            "inflow_eur": self.inflow_eur,
            "bestand_eur": self.bestand_eur,
            "outflow_eur": self.outflow_eur,
            "fees_eur": self.fees_eur,
            "internal_fees_eur": self.fees_eur,  # alias for UI
            "external_fees_eur": self.external_fees_eur,
            "spot_eur": self.spot_eur,
            "eur_note": self.eur_note,
            "eur_partial": self.eur_partial,
            "labels": {
                "inflow": "Inflow",
                "bestand": "Bestand",
                "outflow": "Outflow",
                "internal_fees": "Interne Transaktionskosten",
            },
            "note": (
                "Cloud = alle Wallets der Sitzung. Interne Transfers (inkl. "
                "Cross-Wallet) sind netto null; nur Gebühren zählen als "
                "interne Transaktionskosten. EUR: Inflow/Outflow/Fees zum "
                "historischen EUR-Kurs am jeweiligen Tag; Bestand × Spot. "
                "Keine Steuerberatung. Kurse nie erfunden."
            ),
            "ephemeral": True,
            "notes": list(self.notes),
        }


def _as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _truthy_internal(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _parse_day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _sats_to_eur(sats: int, eur_per_btc: float | None) -> float | None:
    if eur_per_btc is None:
        return None
    return (sats / SATS_PER_BTC) * float(eur_per_btc)


def _flow_as_dict(f: Any) -> dict[str, Any]:
    if isinstance(f, dict):
        return f
    return {
        "txid": getattr(f, "txid", None),
        "direction": getattr(f, "direction", None),
        "amount_sats": getattr(f, "amount_sats", 0),
        "is_internal": getattr(f, "is_internal", False),
        "external_amount_sats": getattr(f, "external_amount_sats", None),
        "fee_sats": getattr(f, "fee_sats", None),
        "block_time": getattr(f, "block_time", None),
        "lot_date": getattr(f, "lot_date", None),
    }


def summarize_cloud(
    flows: Iterable[dict[str, Any] | Any],
    *,
    oracle: PriceOracle | None = None,
) -> CloudSummary:
    """Aggregate session flows into cloud Inflow / Bestand / Outflow / fees.

    When ``oracle`` is provided, also compute EUR fields (fail-open).
    """
    rows: list[dict[str, Any]] = [_flow_as_dict(f) for f in flows]

    summary = CloudSummary(flow_count=len(rows))
    internal_fee_by_txid: dict[str, tuple[int, date | None]] = {}
    external_fee_by_txid: dict[str, tuple[int, date | None]] = {}
    # Legs for EUR: (sats, day)
    inflow_legs: list[tuple[int, date | None]] = []
    outflow_legs: list[tuple[int, date | None]] = []

    for r in rows:
        direction = str(r.get("direction") or "")
        if direction == "in":
            summary.chain_balance_sats += _as_int(r.get("amount_sats"))
        elif direction == "out":
            summary.chain_balance_sats -= _as_int(r.get("amount_sats"))
        internal = _truthy_internal(r.get("is_internal"))
        amount = _as_int(r.get("amount_sats"))
        ext_amt = r.get("external_amount_sats")
        fee = _as_int(r.get("fee_sats"))
        txid = str(r.get("txid") or "")
        # Prefer lot_date (cloud entry / acquisition) for inflows; else block_time.
        day = _parse_day(r.get("lot_date")) or _parse_day(r.get("block_time"))

        if direction == "in" and not internal:
            summary.inflow_sats += amount
            inflow_legs.append((amount, day))
        elif direction == "out" and not internal:
            leaving = _as_int(ext_amt) if ext_amt is not None else amount
            summary.outflow_sats += leaving
            outflow_legs.append((leaving, day))
            if fee > 0 and txid:
                external_fee_by_txid.setdefault(txid, (fee, day))
        elif direction == "out" and internal:
            if fee > 0 and txid:
                internal_fee_by_txid.setdefault(txid, (fee, day))

    summary.internal_fees_sats = sum(v[0] for v in internal_fee_by_txid.values())
    summary.external_fees_sats = sum(v[0] for v in external_fee_by_txid.values())
    summary.bestand_sats = (
        summary.inflow_sats
        - summary.outflow_sats
        - summary.internal_fees_sats
        - summary.external_fees_sats
    )
    if summary.bestand_sats != summary.chain_balance_sats or summary.bestand_sats < 0:
        summary.consistent = False
        summary.notes.append(
            "Plausibilitätsprüfung fehlgeschlagen: Bestand "
            f"{summary.bestand_sats / SATS_PER_BTC:.8f} BTC ≠ unverbrauchte "
            f"Coins laut Blockchain {summary.chain_balance_sats / SATS_PER_BTC:.8f} BTC. "
            "Zu- und Abflüsse bitte nicht verwenden."
        )

    if oracle is not None:
        _attach_eur(
            summary,
            oracle=oracle,
            inflow_legs=inflow_legs,
            outflow_legs=outflow_legs,
            internal_fees=list(internal_fee_by_txid.values()),
            external_fees=list(external_fee_by_txid.values()),
        )
    return summary


def _attach_eur(
    summary: CloudSummary,
    *,
    oracle: PriceOracle,
    inflow_legs: list[tuple[int, date | None]],
    outflow_legs: list[tuple[int, date | None]],
    internal_fees: list[tuple[int, date | None]],
    external_fees: list[tuple[int, date | None]],
) -> None:
    """Fill EUR fields using historical per-day + current spot. Never invent."""
    oracle.prefetch_historical(
        day
        for legs in (inflow_legs, outflow_legs, internal_fees, external_fees)
        for _, day in legs
    )

    def eur_on(day: date | None) -> float | None:
        if day is None:
            return None
        ref = oracle.get_acquisition_reference(day)
        if ref.available and ref.eur is not None:
            return float(ref.eur)
        return None

    partial = False

    def sum_legs(legs: list[tuple[int, date | None]]) -> float | None:
        nonlocal partial
        if not legs:
            return 0.0
        total = 0.0
        any_ok = False
        for sats, day in legs:
            px = eur_on(day)
            if px is None:
                partial = True
                continue
            part = _sats_to_eur(sats, px)
            if part is None:
                partial = True
                continue
            total += part
            any_ok = True
        if not any_ok:
            return None
        return total

    summary.inflow_eur = sum_legs(inflow_legs)
    summary.outflow_eur = sum_legs(outflow_legs)
    summary.fees_eur = sum_legs(internal_fees)
    summary.external_fees_eur = sum_legs(external_fees)

    spot = oracle.get_spot()
    if spot.available and spot.eur is not None:
        summary.spot_eur = float(spot.eur)
        summary.bestand_eur = _sats_to_eur(summary.bestand_sats, summary.spot_eur)
    else:
        summary.spot_eur = None
        summary.bestand_eur = None
        partial = True

    summary.eur_partial = partial
    if partial:
        summary.eur_note = (
            "Kurs fehlt für mindestens eine Position — Beträge nie erfunden. "
            "Inflow/Outflow/Fees = historischer EUR am jeweiligen Tag; "
            "Bestand = Spot. Keine Steuerberatung."
        )
    else:
        summary.eur_note = (
            "EUR: Inflow/Outflow/Fees zum historischen Kurs am jeweiligen Tag; "
            "Bestand × aktueller Spot. Referenzkurse, keine Steuerberatung."
        )
