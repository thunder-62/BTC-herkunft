"""Post-sync enrichment: ownership inference → internal tags → labels → FIFO.

Runs entirely in session RAM / SQLite ``:memory:``. Never writes to disk.
Never sets cost basis to 0.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from btc_origin.db import list_flows
from btc_origin.holding_clock import HoldingClock, HoldingFlag, LotLedgerResult
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.label_service import LabelAssignment, LabelService
from btc_origin.config import get_settings
from btc_origin.ownership_inference import (
    OwnershipInferenceResult,
    OwnershipInferencer,
    TxIoView,
)
from btc_origin.tx_ingestor import Flow


@dataclass
class EnrichmentResult:
    flows_tagged: int = 0
    internal_count: int = 0
    labels_applied: int = 0
    holding_flags: list[HoldingFlag] = field(default_factory=list)
    haltefrist_qualified: int = 0
    lot_result: LotLedgerResult | None = None
    ownership: OwnershipInferenceResult | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        ownership_payload = None
        if self.ownership is not None:
            ownership_payload = self.ownership.as_dict(include_full=True)
        return {
            "flows_tagged": self.flows_tagged,
            "internal_count": self.internal_count,
            "labels_applied": self.labels_applied,
            "haltefrist_qualified": self.haltefrist_qualified,
            "holding_flags": [
                {
                    "inflow_id": h.inflow_id,
                    "inflow_date": h.inflow_date.isoformat(),
                    "days_held": h.days_held,
                    "qualifies_haltefrist_hint": h.qualifies_haltefrist_hint,
                    "amount_sats": h.amount_sats,
                    "kind": h.kind,
                }
                for h in self.holding_flags
            ],
            "ownership": ownership_payload,
            "notes": list(self.notes),
        }


def own_addresses_from_db(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT address FROM addresses").fetchall()
    return {str(r[0]) for r in rows if r[0]}


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def flows_from_db_rows(rows: list[dict[str, Any]]) -> list[Flow]:
    """Restore Flow objects including vout/vin/height and net/FIFO fields."""
    out: list[Flow] = []
    for r in rows:
        out.append(
            Flow(
                txid=str(r.get("txid") or ""),
                address=str(r.get("address") or ""),
                direction=str(r.get("direction") or "in"),
                amount_sats=int(r.get("amount_sats") or 0),
                wallet_id=r.get("wallet_id"),
                is_internal=bool(r.get("is_internal")),
                block_height=_optional_int(r.get("block_height")),
                block_time=r.get("block_time"),
                vout=_optional_int(r.get("vout")),
                vin_index=_optional_int(r.get("vin_index")),
                tx_total_output_sats=_optional_int(r.get("tx_total_output_sats")),
                external_amount_sats=_optional_int(r.get("external_amount_sats")),
                fee_sats=_optional_int(r.get("fee_sats")),
                lot_date=r.get("lot_date"),
                holding_days=_optional_int(r.get("holding_days")),
                prev_txid=r.get("prev_txid") or None,
                prev_vout=_optional_int(r.get("prev_vout")),
            )
        )
    return out


def persist_flow_enrichment(conn: sqlite3.Connection, flows: list[Flow]) -> None:
    """Persist internal flags + net/FIFO annotations back to session flows."""
    for f in flows:
        conn.execute(
            "UPDATE flows SET is_internal=?, external_amount_sats=?, fee_sats=?, "
            "lot_date=?, holding_days=?, tx_total_output_sats=? "
            "WHERE txid=? AND address=? AND direction=? "
            # One address can spend/receive several outpoints in one tx.
            "AND COALESCE(vout, -1)=COALESCE(?, -1) "
            "AND COALESCE(vin_index, -1)=COALESCE(?, -1)",
            (
                1 if f.is_internal else 0,
                f.external_amount_sats,
                f.fee_sats,
                f.lot_date,
                f.holding_days,
                f.tx_total_output_sats,
                f.txid,
                f.address,
                f.direction,
                f.vout,
                f.vin_index,
            ),
        )
    conn.commit()


# Backward-compatible alias
def persist_flow_internal_flags(conn: sqlite3.Connection, flows: list[Flow]) -> None:
    persist_flow_enrichment(conn, flows)


def persist_labels(conn: sqlite3.Connection, assignments: Iterable[LabelAssignment]) -> int:
    n = 0
    for a in assignments:
        # Never persist a fabricated cost basis — schema has no cost_basis column
        # by design (evidence-gap status only).
        conn.execute(
            "INSERT INTO labels (target_type, target_id, label, status, source) "
            "VALUES (?,?,?,?,?)",
            (a.target_type, a.target_id, a.label, a.status, a.source),
        )
        n += 1
    conn.commit()
    return n


def list_labels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, target_type, target_id, label, status, source FROM labels ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def label_map_for_addresses(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """address → {label, status} from session labels table."""
    out: dict[str, dict[str, str]] = {}
    for row in list_labels(conn):
        if row.get("target_type") == "address":
            out[str(row["target_id"])] = {
                "label": str(row.get("label") or ""),
                "status": str(row.get("status") or ""),
            }
    return out


class SessionEnricher:
    """Orchestrate ownership inference + internal tagging + labels + FIFO."""

    def __init__(
        self,
        *,
        tagger: InternalTransferTagger | None = None,
        labels: LabelService | None = None,
        clock: HoldingClock | None = None,
        ownership: OwnershipInferencer | None = None,
    ) -> None:
        self.tagger = tagger or InternalTransferTagger()
        self.labels = labels or LabelService()
        self.clock = clock or HoldingClock()
        self.ownership = ownership or OwnershipInferencer()

    def enrich(
        self,
        conn: sqlite3.Connection,
        *,
        own_addresses: set[str] | None = None,
        tx_graph: Sequence[TxIoView] | dict[str, Any] | None = None,
        as_of: date | datetime | str | None = None,
        clear_prior_labels: bool = True,
        run_ownership_inference: bool | None = None,
    ) -> EnrichmentResult:
        result = EnrichmentResult()
        if run_ownership_inference is None:
            run_ownership_inference = get_settings().ownership_inference
        seed = own_addresses if own_addresses is not None else own_addresses_from_db(conn)
        rows = list_flows(conn)
        flows = flows_from_db_rows(rows)
        if not flows:
            result.notes.append("No flows to enrich.")
            if run_ownership_inference:
                own_inf = self.ownership.infer(seed, tx_graph, flows=None)
                result.ownership = own_inf
                result.notes.extend(own_inf.notes)
            return result

        own = set(seed)
        if run_ownership_inference:
            own_inf = self.ownership.infer(seed, tx_graph, flows=flows)
            result.ownership = own_inf
            own = set(own_inf.own)
            result.notes.extend(own_inf.notes)
            result.notes.append(
                f"Eigene Adressen: {own_inf.seed_count} registriert, "
                f"{own_inf.inferred_count} heuristisch aus Co-Spends/Change "
                f"(abgeleitet / heuristisch — keine Steuerberatung)."
            )
        else:
            result.notes.append(
                f"Eigene Adressen: {len(own)} — nur aus eingefügten xpubs/Adressen "
                "(keine heuristische Ableitung)."
            )

        self.tagger.tag_inplace(flows, own)
        result.flows_tagged = len(flows)
        result.internal_count = sum(1 for f in flows if f.is_internal)

        if clear_prior_labels:
            conn.execute("DELETE FROM labels")
            conn.commit()
        assignments = self.labels.apply_to_flows(flows)
        result.labels_applied = persist_labels(conn, assignments)

        end = as_of
        if end is None:
            end = datetime.now(timezone.utc).date()

        lot_result = self.clock.apply_fifo_lots(flows, as_of=end)
        result.lot_result = lot_result
        result.holding_flags = list(lot_result.remaining_flags)
        result.haltefrist_qualified = sum(
            1 for h in result.holding_flags if h.qualifies_haltefrist_hint
        )

        persist_flow_enrichment(conn, flows)

        result.notes.append(
            "Enrichment complete: tx-net internal tags, "
            "evidence-gap labels (never silent cost basis 0), "
            "FIFO lot holding-clock hints."
        )
        return result
