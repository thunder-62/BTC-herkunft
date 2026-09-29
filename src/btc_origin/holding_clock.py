"""Flag inflows held ≥ 365 days (DE Haltefrist hint; BMF 06.03.2025 / Anlage SO).

FIFO lot model (wallet-related):
- Each *external* inflow creates a lot (acquisition date + amount).
- External outflows (and fees) consume lots FIFO per wallet.
- Internal transfers / change do NOT create or reset lots.

Not tax advice. Documentation hint only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from btc_origin.tx_ingestor import Flow


HOLDING_DAYS = 365

def _strengthen_session_internal(flows: list[Flow]) -> None:
    """Defense in depth: mark fully-internal txids using session wallet flows.

    Even if address-set tagging was incomplete, any txid with both session outs
    and session ins and no residual external outputs is treated as internal so
    FIFO transfers lots instead of minting a receive-date acquisition.
    """
    by_txid: dict[str, list[Flow]] = {}
    for f in flows:
        by_txid.setdefault(f.txid, []).append(f)
    for group in by_txid.values():
        outs = [f for f in group if f.direction == "out"]
        inns = [f for f in group if f.direction == "in" and f.wallet_id is not None]
        if not outs or not inns:
            continue
        sum_in = sum(f.amount_sats for f in inns)
        tx_total = None
        for f in group:
            if f.tx_total_output_sats is not None:
                tx_total = f.tx_total_output_sats
                break
        if tx_total is not None:
            external = max(0, tx_total - sum_in)
        else:
            foreign = sum(
                f.amount_sats
                for f in group
                if f.direction == "in" and f.wallet_id is None
            )
            external = foreign
        if external != 0:
            # Still mark session ins as change when outs exist (payment+change).
            for f in inns:
                f.is_internal = True
            continue
        fee = None
        sum_out = sum(f.amount_sats for f in outs)
        if tx_total is not None and sum_out >= tx_total:
            fee = sum_out - tx_total
        elif sum_out >= sum_in:
            fee = sum_out - sum_in
        for f in outs + inns:
            f.is_internal = True
            f.external_amount_sats = 0
            if fee is not None:
                f.fee_sats = fee




def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # ISO date or datetime string
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return date.fromisoformat(value[:10])


@dataclass
class HoldingFlag:
    """Result of checking one inflow / remaining lot against the 365-day threshold."""

    inflow_id: str
    inflow_date: date
    as_of: date
    days_held: int
    qualifies_haltefrist_hint: bool
    note: str
    amount_sats: int | None = None
    wallet_id: int | None = None
    kind: str = "remaining_lot"  # remaining_lot | disposal


@dataclass
class LotMove:
    """One hop in a lot's genealogy (acquire or internal transfer)."""

    wallet_id: int | None
    txid: str | None = None
    date: date | None = None
    kind: str = "transfer"  # inflow | transfer
    address: str | None = None


@dataclass
class Lot:
    """One external acquisition lot (FIFO unit)."""

    lot_id: str
    wallet_id: int | None
    acquisition_date: date
    amount_sats: int
    remaining_sats: int
    txid: str
    address: str
    origin_wallet_id: int | None = None
    origin_address: str = ""
    original_amount_sats: int | None = None
    path: list[LotMove] = field(default_factory=list)
    # Acquisition from an exchange export (BMF 06.03.2025 Rz. 20): purchase
    # price per BTC incl. fees and a note; None/"" = Cloud-Eintritt on-chain.
    price_eur: float | None = None
    acq_source: str = ""


@dataclass
class AcqPiece:
    """Part of a Cloud-Eintritt with its real acquisition (exchange export)."""

    sats: int
    acquisition_date: date
    price_eur: float | None = None  # EUR per BTC incl. fees; None = Tageskurs
    source: str = ""


@dataclass
class LotConsumption:
    """FIFO consumption of a lot by an external outflow or fee."""

    lot_id: str
    wallet_id: int | None
    acquisition_date: date
    consumed_sats: int
    disposal_date: date
    days_held: int
    qualifies_haltefrist_hint: bool
    txid: str
    kind: str  # outflow | fee
    # Snapshot at disposal for Wallet-Cloud Flows UI (genealogy until Austritt).
    path: list[LotMove] = field(default_factory=list)
    origin_wallet_id: int | None = None
    origin_address: str = ""
    origin_txid: str = ""
    address_at_disposal: str = ""
    original_amount_sats: int | None = None
    price_eur: float | None = None
    acq_source: str = ""


@dataclass
class LotLedgerResult:
    lots: list[Lot] = field(default_factory=list)
    # Every external acquisition (Cloud-Eintritt) as created — including lots
    # that were later spent completely. Snapshot, never mutated.
    acquisitions: list[Lot] = field(default_factory=list)
    consumptions: list[LotConsumption] = field(default_factory=list)
    remaining_flags: list[HoldingFlag] = field(default_factory=list)
    disposal_flags: list[HoldingFlag] = field(default_factory=list)


def days_held(inflow_date: date | datetime | str, as_of: date | datetime | str | None = None) -> int:
    start = _as_date(inflow_date)
    end = _as_date(as_of) if as_of is not None else datetime.now(timezone.utc).date()
    return (end - start).days


def qualifies_haltefrist(
    inflow_date: date | datetime | str,
    as_of: date | datetime | str | None = None,
    *,
    threshold_days: int = HOLDING_DAYS,
) -> bool:
    """True if inflow is at least ``threshold_days`` old (default 365)."""
    return days_held(inflow_date, as_of) >= threshold_days


class HoldingClock:
    """Evaluate inflows / lots for DE Haltefrist hint (≥ 365 days)."""

    def __init__(self, threshold_days: int = HOLDING_DAYS) -> None:
        self.threshold_days = threshold_days
        # "txid:vout" of a Cloud-Eintritt → real purchases (exchange export).
        self.acquisition_overrides: dict[str, list[AcqPiece]] = {}

    def flag_inflow(
        self,
        inflow_id: str,
        inflow_date: date | datetime | str,
        as_of: date | datetime | str | None = None,
        *,
        amount_sats: int | None = None,
        wallet_id: int | None = None,
        kind: str = "remaining_lot",
    ) -> HoldingFlag:
        end = _as_date(as_of) if as_of is not None else datetime.now(timezone.utc).date()
        start = _as_date(inflow_date)
        held = (end - start).days
        ok = held >= self.threshold_days
        note = (
            f"Haltefrist-Hinweis: {held} Tage gehalten "
            f"(Schwelle {self.threshold_days} Tage; BMF 06.03.2025 / Anlage SO — keine Steuerberatung)."
        )
        return HoldingFlag(
            inflow_id=inflow_id,
            inflow_date=start,
            as_of=end,
            days_held=held,
            qualifies_haltefrist_hint=ok,
            note=note,
            amount_sats=amount_sats,
            wallet_id=wallet_id,
            kind=kind,
        )

    def flag_many(
        self,
        inflows: list[tuple[str, date | datetime | str]],
        as_of: date | datetime | str | None = None,
    ) -> list[HoldingFlag]:
        return [self.flag_inflow(i, d, as_of=as_of) for i, d in inflows]

    def apply_fifo_lots(
        self,
        flows: list[Flow],
        *,
        as_of: date | datetime | str | None = None,
    ) -> LotLedgerResult:
        """Build FIFO lots from tagged flows; annotate flows with lot_date / holding_days.

        Prerequisites: ``InternalTransferTagger`` has already set ``is_internal``,
        ``external_amount_sats``, and ``fee_sats`` where applicable.

        Mutates flows in place for lot_date / holding_days annotations.

        Verwendungsreihenfolge per BMF-Schreiben vom 06.03.2025 (Rz. 61):
        **Einzelbetrachtung** — when every spend knows the coin (outpoint) it
        consumed, lots follow the actual coins (``apply_specific_lots``).
        Wallet-FIFO below is only the fallback when that is not possible
        (e.g. hand-built flows without outpoints).
        """
        outs = [f for f in flows if f.direction == "out"]
        if outs and all(f.prev_txid for f in outs):
            return self.apply_specific_lots(flows, as_of=as_of)
        end = _as_date(as_of) if as_of is not None else datetime.now(timezone.utc).date()
        result = LotLedgerResult()

        # Re-link cloud-internal moves even if prior tagging used a sparse own set.
        _strengthen_session_internal(flows)

        # Chronological order (unknown times last, stable by original order).
        indexed = list(enumerate(flows))

        def _sort_key(item: tuple[int, Flow]) -> tuple:
            i, f = item
            bt = f.block_time or ""
            return (bt, i)

        ordered = [f for _, f in sorted(indexed, key=_sort_key)]

        # wallet_id → open lots (FIFO queue)
        open_lots: dict[int | None, list[Lot]] = {}
        fee_seen_txids: set[str] = set()
        # Group fully-internal txids so cross-xpub moves can transfer lots.
        internal_txids = {
            f.txid
            for f in ordered
            if f.is_internal and f.direction == "out"
        } & {
            f.txid
            for f in ordered
            if f.is_internal and f.direction == "in"
        }
        transferred_txids: set[str] = set()

        for f in ordered:
            wid = f.wallet_id
            if f.direction == "in" and not f.is_internal and f.amount_sats > 0:
                if not f.block_time:
                    continue
                try:
                    acq = _as_date(f.block_time)
                except (ValueError, TypeError):
                    continue
                lot = Lot(
                    lot_id=f"{f.txid}:{f.address}:{f.vout if f.vout is not None else 'in'}",
                    wallet_id=wid,
                    acquisition_date=acq,
                    amount_sats=f.amount_sats,
                    remaining_sats=f.amount_sats,
                    txid=f.txid,
                    address=f.address,
                    origin_wallet_id=wid,
                    origin_address=f.address,
                    original_amount_sats=f.amount_sats,
                    path=[
                        LotMove(
                            wallet_id=wid,
                            txid=f.txid,
                            date=acq,
                            kind="inflow",
                            address=f.address,
                        )
                    ],
                )
                open_lots.setdefault(wid, []).append(lot)
                result.acquisitions.append(replace(lot, path=list(lot.path)))
                f.lot_date = acq.isoformat()
                held = (end - acq).days
                f.holding_days = held
                continue

            if f.direction == "in" and f.is_internal:
                # Change / internal receive: do not create a new lot.
                if f.txid in internal_txids and f.txid not in transferred_txids:
                    self._transfer_lots_for_internal_tx(
                        open_lots, ordered, f.txid, end=end
                    )
                    transferred_txids.add(f.txid)
                queue = open_lots.get(wid) or []
                if queue:
                    f.lot_date = queue[0].acquisition_date.isoformat()
                    f.holding_days = (end - queue[0].acquisition_date).days
                continue

            if f.direction == "out" and f.is_internal:
                # Fully internal out: no external consumption; lot transfer on matching in.
                if f.txid in internal_txids and f.txid not in transferred_txids:
                    self._transfer_lots_for_internal_tx(
                        open_lots, ordered, f.txid, end=end
                    )
                    transferred_txids.add(f.txid)
                queue = open_lots.get(wid) or []
                if queue:
                    f.lot_date = queue[0].acquisition_date.isoformat()
                    f.holding_days = (end - queue[0].acquisition_date).days
                continue

            if f.direction == "out" and not f.is_internal:
                # External disposal: consume external_amount (fallback: full amount).
                dispose_amount = (
                    f.external_amount_sats
                    if f.external_amount_sats is not None
                    else f.amount_sats
                )
                try:
                    disposal_date = (
                        _as_date(f.block_time) if f.block_time else end
                    )
                except (ValueError, TypeError):
                    disposal_date = end

                consumed_meta = self._consume(
                    open_lots,
                    wid,
                    dispose_amount,
                    disposal_date=disposal_date,
                    txid=f.txid,
                    kind="outflow",
                    result=result,
                )
                self._move_spent_lots_to_change(open_lots, ordered, f)
                if consumed_meta:
                    # Annotate outflow with earliest consumed lot date + days held.
                    earliest = min(c.acquisition_date for c in consumed_meta)
                    f.lot_date = earliest.isoformat()
                    f.holding_days = max(c.days_held for c in consumed_meta)

                # Fees consume lots once per txid (attach to first external out).
                if (
                    f.fee_sats
                    and f.fee_sats > 0
                    and f.txid not in fee_seen_txids
                ):
                    fee_seen_txids.add(f.txid)
                    self._consume(
                        open_lots,
                        wid,
                        f.fee_sats,
                        disposal_date=disposal_date,
                        txid=f.txid,
                        kind="fee",
                        result=result,
                    )

        # Remaining lots → holding flags (only unspent balances).
        for queue in open_lots.values():
            for lot in queue:
                if lot.remaining_sats <= 0:
                    continue
                result.lots.append(lot)
                flag = self.flag_inflow(
                    lot.lot_id,
                    lot.acquisition_date,
                    as_of=end,
                    amount_sats=lot.remaining_sats,
                    wallet_id=lot.wallet_id,
                    kind="remaining_lot",
                )
                result.remaining_flags.append(flag)

        for c in result.consumptions:
            flag = self.flag_inflow(
                f"{c.txid}:{c.lot_id}:{c.kind}",
                c.acquisition_date,
                as_of=c.disposal_date,
                amount_sats=c.consumed_sats,
                wallet_id=c.wallet_id,
                kind="disposal",
            )
            result.disposal_flags.append(flag)

        return result


    def apply_specific_lots(
        self,
        flows: list[Flow],
        *,
        as_of: date | datetime | str | None = None,
    ) -> LotLedgerResult:
        """Einzelbetrachtung je Coin (UTXO), BMF-Schreiben 06.03.2025 Rz. 61.

        Every own coin (outpoint) carries its lot slices (acquisition date,
        origin, path). A transaction consumes exactly the coins it spends.
        Only where coins are merged inside ONE transaction can the individual
        unit no longer be followed — there the first-acquired slices are
        allocated first (FIFO): external disposal, then fee, then the own
        outputs (change / transfer) in output order. Transfers between own
        wallets are no disposal and keep the acquisition date.
        """
        end = _as_date(as_of) if as_of is not None else datetime.now(timezone.utc).date()
        result = LotLedgerResult()
        _strengthen_session_internal(flows)

        by_tx: dict[str, list[Flow]] = {}
        for f in flows:
            by_tx.setdefault(f.txid, []).append(f)

        def tx_key(txid: str) -> tuple:
            group = by_tx[txid]
            bt = min((f.block_time or "9999") for f in group)
            hs = [f.block_height for f in group if f.block_height and f.block_height > 0]
            return (bt, min(hs) if hs else 10**12, txid)

        utxo: dict[str, list[Lot]] = {}
        done: set[str] = set()

        def day_of(group: list[Flow]) -> date:
            for f in group:
                if f.block_time:
                    try:
                        return _as_date(f.block_time)
                    except (ValueError, TypeError):
                        continue
            return end

        def slice_of(lot: Lot, sats: int, **changes: Any) -> Lot:
            changes.setdefault("path", list(lot.path))
            return replace(lot, remaining_sats=sats, **changes)

        def process(txid: str, stack: set[str]) -> None:
            if txid in done or txid in stack:
                return
            stack.add(txid)
            group = by_tx[txid]
            for f in group:  # parents first (same block / missing times)
                if f.direction == "out" and f.prev_txid in by_tx:
                    process(str(f.prev_txid), stack)
            self._process_specific_tx(txid, group, utxo, result, day_of(group), end, slice_of)
            done.add(txid)
            stack.discard(txid)

        for txid in sorted(by_tx, key=tx_key):
            process(txid, set())

        for slices in utxo.values():
            for lot in slices:
                if lot.remaining_sats <= 0:
                    continue
                result.lots.append(lot)
                result.remaining_flags.append(
                    self.flag_inflow(
                        lot.lot_id,
                        lot.acquisition_date,
                        as_of=end,
                        amount_sats=lot.remaining_sats,
                        wallet_id=lot.wallet_id,
                        kind="remaining_lot",
                    )
                )
        for c in result.consumptions:
            result.disposal_flags.append(
                self.flag_inflow(
                    f"{c.txid}:{c.lot_id}:{c.kind}",
                    c.acquisition_date,
                    as_of=c.disposal_date,
                    amount_sats=c.consumed_sats,
                    wallet_id=c.wallet_id,
                    kind="disposal",
                )
            )
        return result

    def _entry_from_pieces(
        self,
        txid: str,
        f: Flow,
        pieces: list[AcqPiece],
        utxo: dict[str, list[Lot]],
        result: LotLedgerResult,
        day: date,
        end: date,
    ) -> None:
        """Cloud-Eintritt split by real purchases (exchange export, Rz. 20):
        each part keeps its purchase date and price; amounts are scaled to
        the received sats (exact integer split, withdrawal fee excluded)."""
        total = sum(p.sats for p in pieces) or 1
        slices: list[Lot] = []
        cum = 0
        for k, p in enumerate(sorted(pieces, key=lambda p: p.acquisition_date)):
            before = f.amount_sats * cum // total
            cum += p.sats
            sats = f.amount_sats * cum // total - before
            if sats <= 0:
                continue
            lot = Lot(
                lot_id=f"{txid}:{f.address}:{f.vout if f.vout is not None else 'in'}#{k + 1}",
                wallet_id=f.wallet_id,
                acquisition_date=p.acquisition_date,
                amount_sats=sats,
                remaining_sats=sats,
                txid=txid,
                address=f.address,
                origin_wallet_id=f.wallet_id,
                origin_address=f.address,
                original_amount_sats=sats,
                path=[LotMove(wallet_id=f.wallet_id, txid=txid, date=day, kind="inflow", address=f.address)],
                price_eur=p.price_eur,
                acq_source=p.source,
            )
            result.acquisitions.append(replace(lot, path=list(lot.path)))
            slices.append(lot)
        utxo[f"{txid}:{f.vout}"] = slices
        first = min((s.acquisition_date for s in slices), default=day)
        f.lot_date = first.isoformat()
        f.holding_days = (end - first).days

    def _process_specific_tx(
        self,
        txid: str,
        group: list[Flow],
        utxo: dict[str, list[Lot]],
        result: LotLedgerResult,
        day: date,
        end: date,
        slice_of: Callable[..., Lot],
    ) -> None:
        outs = sorted(
            (f for f in group if f.direction == "out"),
            key=lambda f: (f.vin_index is None, f.vin_index or 0),
        )
        ins = sorted(
            (f for f in group if f.direction == "in"),
            key=lambda f: (f.vout is None, f.vout or 0),
        )

        if not outs:
            # No own coin spent → every own output is a Cloud-Eintritt (new lot).
            for f in ins:
                if f.amount_sats <= 0:
                    continue
                pieces = self.acquisition_overrides.get(f"{txid}:{f.vout}")
                if pieces:
                    self._entry_from_pieces(txid, f, pieces, utxo, result, day, end)
                    continue
                lot = Lot(
                    lot_id=f"{txid}:{f.address}:{f.vout if f.vout is not None else 'in'}",
                    wallet_id=f.wallet_id,
                    acquisition_date=day,
                    amount_sats=f.amount_sats,
                    remaining_sats=f.amount_sats,
                    txid=txid,
                    address=f.address,
                    origin_wallet_id=f.wallet_id,
                    origin_address=f.address,
                    original_amount_sats=f.amount_sats,
                    path=[
                        LotMove(
                            wallet_id=f.wallet_id, txid=txid, date=day, kind="inflow", address=f.address
                        )
                    ],
                )
                result.acquisitions.append(replace(lot, path=list(lot.path)))
                utxo[f"{txid}:{f.vout}"] = [lot]
                f.lot_date = day.isoformat()
                f.holding_days = (end - day).days
            return

        # Pool the spent coins; inside one tx the unit can no longer be
        # followed → first-acquired first (FIFO).
        pool: list[Lot] = []
        own_in = 0
        for f in outs:
            own_in += f.amount_sats
            pool.extend(utxo.pop(f"{f.prev_txid}:{f.prev_vout}", []))
        pool.sort(key=lambda s: (s.acquisition_date, s.lot_id))
        own_out = sum(f.amount_sats for f in ins)
        tx_total = next(
            (f.tx_total_output_sats for f in group if f.tx_total_output_sats is not None), None
        )
        fee = own_in - tx_total if tx_total is not None and own_in >= tx_total else 0
        external = max(0, own_in - own_out - fee)

        def take(amount: int, kind: str) -> list[LotConsumption]:
            taken: list[LotConsumption] = []
            while amount > 0 and pool:
                s = pool[0]
                n = min(s.remaining_sats, amount)
                if n > 0:
                    held = (day - s.acquisition_date).days
                    c = LotConsumption(
                        lot_id=s.lot_id,
                        wallet_id=s.wallet_id,
                        acquisition_date=s.acquisition_date,
                        consumed_sats=n,
                        disposal_date=day,
                        days_held=held,
                        qualifies_haltefrist_hint=held >= self.threshold_days,
                        txid=txid,
                        kind=kind,
                        path=list(s.path),
                        origin_wallet_id=s.origin_wallet_id,
                        origin_address=s.origin_address,
                        origin_txid=s.txid,
                        address_at_disposal=s.address,
                        original_amount_sats=s.original_amount_sats,
                        price_eur=s.price_eur,
                        acq_source=s.acq_source,
                    )
                    taken.append(c)
                    result.consumptions.append(c)
                    s.remaining_sats -= n
                    amount -= n
                if s.remaining_sats <= 0:
                    pool.pop(0)
            return taken

        disposed = take(external, "outflow")
        take(fee, "fee")
        if disposed:
            first = min(c.acquisition_date for c in disposed)
            for f in outs:
                f.lot_date = first.isoformat()
                f.holding_days = max(c.days_held for c in disposed)

        for f in ins:  # change / transfer: keep acquisition date, follow the coin
            need = f.amount_sats
            parts: list[Lot] = []
            while need > 0 and pool:
                s = pool[0]
                n = min(s.remaining_sats, need)
                path = list(s.path)
                if not path or path[-1].wallet_id != f.wallet_id:
                    path.append(
                        LotMove(wallet_id=f.wallet_id, txid=txid, date=day, kind="transfer", address=f.address)
                    )
                parts.append(slice_of(s, n, wallet_id=f.wallet_id, address=f.address, path=path))
                s.remaining_sats -= n
                need -= n
                if s.remaining_sats <= 0:
                    pool.pop(0)
            utxo[f"{txid}:{f.vout}"] = parts
            if parts:
                f.lot_date = min(p.acquisition_date for p in parts).isoformat()
                f.holding_days = (end - min(p.acquisition_date for p in parts)).days

    def _transfer_lots_for_internal_tx(
        self,
        open_lots: dict[int | None, list[Lot]],
        flows: list[Flow],
        txid: str,
        *,
        end: date,
    ) -> None:
        """Move lot balances across wallets for a fully internal tx (no date reset).

        Records each hop on ``Lot.path`` so genealogy (Inflow → wallets → current)
        is available for the UI — still in-memory only.
        """
        outs = [f for f in flows if f.txid == txid and f.direction == "out" and f.is_internal]
        inns = [f for f in flows if f.txid == txid and f.direction == "in" and f.is_internal]
        if not outs or not inns:
            return

        move_date: date | None = None
        for f in outs + inns:
            if f.block_time:
                try:
                    move_date = _as_date(f.block_time)
                    break
                except (ValueError, TypeError):
                    continue

        # Peel lots FIFO from source wallets in out order; push onto dest wallets.
        peeled: list[Lot] = []
        for out in outs:
            need = out.amount_sats
            queue = open_lots.setdefault(out.wallet_id, [])
            peeled_here: list[Lot] = []
            while need > 0 and queue:
                lot = queue[0]
                take = min(lot.remaining_sats, need)
                if take <= 0:
                    queue.pop(0)
                    continue
                lot.remaining_sats -= take
                need -= take
                orig = (
                    lot.original_amount_sats
                    if lot.original_amount_sats is not None
                    else lot.amount_sats
                )
                slice_lot = Lot(
                    lot_id=lot.lot_id,
                    wallet_id=None,  # reassigned below
                    acquisition_date=lot.acquisition_date,
                    amount_sats=orig,
                    remaining_sats=take,
                    txid=lot.txid,
                    address=lot.address,
                    origin_wallet_id=lot.origin_wallet_id
                    if lot.origin_wallet_id is not None
                    else lot.wallet_id,
                    origin_address=lot.origin_address or lot.address,
                    original_amount_sats=orig,
                    path=list(lot.path),
                )
                peeled.append(slice_lot)
                peeled_here.append(slice_lot)
                if lot.remaining_sats <= 0:
                    queue.pop(0)
            # Annotate out with earliest peeled date if any
            if peeled_here:
                out.lot_date = peeled_here[0].acquisition_date.isoformat()
                out.holding_days = (end - peeled_here[0].acquisition_date).days

        # Best effort: if source wallets had no open lots (e.g. ordering gap) but
        # this txid is clearly internal, peel FIFO across the whole cloud so we
        # never mint a fresh acquisition_date = receive day for the dest.
        if not peeled:
            need_total = sum(f.amount_sats for f in inns)
            # Flatten open lots oldest-first across wallets (stable by acq date).
            cloud: list[tuple[int | None, Lot]] = []
            for wid, queue in list(open_lots.items()):
                for lot in queue:
                    if lot.remaining_sats > 0:
                        cloud.append((wid, lot))
            cloud.sort(key=lambda t: (t[1].acquisition_date, t[1].lot_id))
            for wid, lot in cloud:
                if need_total <= 0:
                    break
                if lot.remaining_sats <= 0:
                    continue
                take = min(lot.remaining_sats, need_total)
                lot.remaining_sats -= take
                need_total -= take
                orig = (
                    lot.original_amount_sats
                    if lot.original_amount_sats is not None
                    else lot.amount_sats
                )
                peeled.append(
                    Lot(
                        lot_id=lot.lot_id,
                        wallet_id=None,
                        acquisition_date=lot.acquisition_date,
                        amount_sats=orig,
                        remaining_sats=take,
                        txid=lot.txid,
                        address=lot.address,
                        origin_wallet_id=lot.origin_wallet_id
                        if lot.origin_wallet_id is not None
                        else lot.wallet_id,
                        origin_address=lot.origin_address or lot.address,
                        original_amount_sats=orig,
                        path=list(lot.path),
                    )
                )
                if lot.remaining_sats <= 0:
                    q = open_lots.get(wid) or []
                    if q and q[0] is lot:
                        q.pop(0)
                    elif lot in q:
                        q.remove(lot)

        # Distribute peeled slices onto destination wallets proportional to in amounts.
        dest_queue = list(inns)
        for slice_lot in peeled:
            while slice_lot.remaining_sats > 0 and dest_queue:
                dest = dest_queue[0]
                # How much this dest still needs? track via mutating a side map
                if not hasattr(dest, "_lot_need"):
                    dest._lot_need = dest.amount_sats  # type: ignore[attr-defined]
                need_d = dest._lot_need  # type: ignore[attr-defined]
                if need_d <= 0:
                    dest_queue.pop(0)
                    continue
                take = min(slice_lot.remaining_sats, need_d)
                # Same-wallet change/internal: keep path; only append hop on wallet change.
                new_path = list(slice_lot.path)
                if not new_path or new_path[-1].wallet_id != dest.wallet_id:
                    new_path.append(
                        LotMove(
                            wallet_id=dest.wallet_id,
                            txid=txid,
                            date=move_date,
                            kind="transfer",
                            address=dest.address,
                        )
                    )
                open_lots.setdefault(dest.wallet_id, []).append(
                    Lot(
                        lot_id=slice_lot.lot_id,
                        wallet_id=dest.wallet_id,
                        acquisition_date=slice_lot.acquisition_date,
                        amount_sats=slice_lot.amount_sats,
                        remaining_sats=take,
                        txid=slice_lot.txid,
                        address=dest.address,
                        origin_wallet_id=slice_lot.origin_wallet_id,
                        origin_address=slice_lot.origin_address,
                        original_amount_sats=slice_lot.original_amount_sats,
                        path=new_path,
                    )
                )
                slice_lot.remaining_sats -= take
                dest._lot_need = need_d - take  # type: ignore[attr-defined]
                if dest._lot_need <= 0:  # type: ignore[attr-defined]
                    dest_queue.pop(0)

    @staticmethod
    def _move_spent_lots_to_change(
        open_lots: dict[int | None, list[Lot]],
        flows: list[Flow],
        spend: Flow,
    ) -> None:
        """After an external spend, remaining sats of the spent address sit on
        the change output of the same tx (same wallet) — update lot addresses so
        „wo liegen die Sats jetzt“ never points at a spent address."""
        change = next(
            (
                f
                for f in flows
                if f.txid == spend.txid
                and f.direction == "in"
                and f.is_internal
                and f.wallet_id == spend.wallet_id
                and f.address
            ),
            None,
        )
        if change is None:
            return
        for lot in open_lots.get(spend.wallet_id) or []:
            if lot.remaining_sats > 0 and lot.address == spend.address:
                lot.address = change.address

    def _consume(
        self,
        open_lots: dict[int | None, list[Lot]],
        wallet_id: int | None,
        amount: int,
        *,
        disposal_date: date,
        txid: str,
        kind: str,
        result: LotLedgerResult,
    ) -> list[LotConsumption]:
        if amount <= 0:
            return []
        queue = open_lots.setdefault(wallet_id, [])
        left = amount
        consumed: list[LotConsumption] = []
        while left > 0 and queue:
            lot = queue[0]
            take = min(lot.remaining_sats, left)
            if take <= 0:
                queue.pop(0)
                continue
            lot.remaining_sats -= take
            left -= take
            held = (disposal_date - lot.acquisition_date).days
            orig = (
                lot.original_amount_sats
                if lot.original_amount_sats is not None
                else lot.amount_sats
            )
            c = LotConsumption(
                lot_id=lot.lot_id,
                wallet_id=wallet_id,
                acquisition_date=lot.acquisition_date,
                consumed_sats=take,
                disposal_date=disposal_date,
                days_held=held,
                qualifies_haltefrist_hint=held >= self.threshold_days,
                txid=txid,
                kind=kind,
                path=list(lot.path),
                origin_wallet_id=(
                    lot.origin_wallet_id
                    if lot.origin_wallet_id is not None
                    else (lot.path[0].wallet_id if lot.path else lot.wallet_id)
                ),
                origin_address=lot.origin_address or lot.address,
                origin_txid=lot.txid,
                address_at_disposal=lot.address,
                original_amount_sats=orig,
            )
            consumed.append(c)
            result.consumptions.append(c)
            if lot.remaining_sats <= 0:
                queue.pop(0)
        return consumed


def lot_to_genealogy_dict(
    lot: Lot,
    *,
    as_of: date | None = None,
    wallet_names: dict[int, str] | None = None,
    threshold_days: int = HOLDING_DAYS,
) -> dict:
    """Serialize a remaining lot with path suitable for GET /api/cloud/lots."""
    names = wallet_names or {}
    end = as_of or datetime.now(timezone.utc).date()
    held = (end - lot.acquisition_date).days
    orig = (
        lot.original_amount_sats
        if lot.original_amount_sats is not None
        else lot.amount_sats
    )

    def _name(wid: int | None) -> str | None:
        if wid is None:
            return None
        return names.get(int(wid))

    path_out = []
    for step in lot.path:
        path_out.append(
            {
                "wallet_id": step.wallet_id,
                "wallet_name": _name(step.wallet_id),
                "txid": step.txid,
                "date": step.date.isoformat() if step.date else None,
                "kind": step.kind,
                "address": step.address,
            }
        )

    return {
        "lot_id": lot.lot_id,
        "lot_date": lot.acquisition_date.isoformat(),
        "acq_price_eur": lot.price_eur,
        "acq_source": lot.acq_source,
        "original_amount_sats": orig,
        "remaining_sats": lot.remaining_sats,
        "origin": {
            "wallet_id": lot.origin_wallet_id
            if lot.origin_wallet_id is not None
            else (lot.path[0].wallet_id if lot.path else lot.wallet_id),
            "wallet_name": _name(
                lot.origin_wallet_id
                if lot.origin_wallet_id is not None
                else (lot.path[0].wallet_id if lot.path else lot.wallet_id)
            ),
            "txid": lot.txid,
            "address": lot.origin_address or lot.address,
        },
        "current_wallet_id": lot.wallet_id,
        "current_wallet_name": _name(lot.wallet_id),
        # Address where remaining sats currently sit (current UTXO; path hops
        # only record wallet changes and may point at since-spent addresses).
        "current_address": (
            lot.address
            or (lot.path[-1].address if lot.path and lot.path[-1].address else None)
            or lot.origin_address
            or ""
        ),
        "path": path_out,
        "qualifies_haltefrist": held >= threshold_days,
        "days_held": held,
    }



EXTERNAL_OUTSIDE_CLOUD = "außerhalb Cloud"


def _path_steps_dict(
    path: list[LotMove],
    wallet_names: dict[int, str] | None = None,
) -> list[dict]:
    names = wallet_names or {}

    def _name(wid: int | None) -> str | None:
        if wid is None:
            return None
        return names.get(int(wid))

    out: list[dict] = []
    for step in path:
        out.append(
            {
                "wallet_id": step.wallet_id,
                "wallet_name": _name(step.wallet_id),
                "txid": step.txid,
                "date": step.date.isoformat() if step.date else None,
                "kind": step.kind,
                "address": step.address,
            }
        )
    return out


def _path_labels(path_dicts: list[dict]) -> list[str]:
    labels: list[str] = []
    for step in path_dicts:
        name = step.get("wallet_name")
        if name:
            labels.append(str(name))
        elif step.get("wallet_id") is not None:
            labels.append(f"Wallet {step['wallet_id']}")
        else:
            labels.append("?")
    return labels


def resolve_external_destination(
    txid: str,
    *,
    own_addresses: set[str] | None = None,
    tx_io: dict[str, dict] | None = None,
) -> str | None:
    """Best-effort external output address for a disposal txid, else None."""
    if not txid or not tx_io:
        return None
    entry = tx_io.get(txid) or {}
    outs = [str(a) for a in (entry.get("output_addresses") or []) if a]
    if not outs:
        return None
    own = {str(a) for a in (own_addresses or set()) if a}
    external = [a for a in outs if a not in own]
    if not external:
        return None
    return external[0]


def cloud_flows_from_lot_result(
    result: LotLedgerResult,
    *,
    as_of: date | None = None,
    wallet_names: dict[int, str] | None = None,
    threshold_days: int = HOLDING_DAYS,
    own_addresses: set[str] | None = None,
    tx_io: dict[str, dict] | None = None,
    include_fees: bool = False,
) -> list[dict]:
    """Shape Wallet-Cloud Flows: IN = every cloud entry, OUT = external disposals.

    Semantics (German UI):
    - **in** = Cloud-Eintritt: one row per external acquisition — also when
      it was spent later. Betrag = amount at entry; remaining_sats = still in
      the cloud; current_locations = where it sits now. Zeit = acquisition
      date. Adresse = entry address. path = internal transfer genealogy.
    - **out** = Cloud-Austritt: one row per outflow consumption (fees optional).
      Betrag = consumed_sats. Zeit = disposal_date. Adresse = external destination
      if known from tx_io, else „außerhalb Cloud“.
    """
    names = wallet_names or {}
    end = as_of or datetime.now(timezone.utc).date()
    rows: list[dict] = []

    def _name(wid: int | None) -> str | None:
        if wid is None:
            return None
        return names.get(int(wid))

    # Remaining slices per acquisition (a lot can be split across wallets).
    remaining_by_lot: dict[str, list[Lot]] = {}
    for lot in result.lots:
        if lot.remaining_sats > 0:
            remaining_by_lot.setdefault(lot.lot_id, []).append(lot)

    # IN = every Cloud-Eintritt (external acquisition), also when spent later.
    # Fallback for hand-built results without acquisitions: remaining lots.
    entries = result.acquisitions or [
        replace(lot, amount_sats=lot.original_amount_sats or lot.amount_sats)
        for lot in result.lots
        if lot.remaining_sats > 0
    ]
    seen_entries: set[str] = set()
    for acq in entries:
        if acq.lot_id in seen_entries:
            continue
        seen_entries.add(acq.lot_id)
        slices = remaining_by_lot.get(acq.lot_id, [])
        remaining = sum(s.remaining_sats for s in slices)
        entry_amount = acq.original_amount_sats or acq.amount_sats
        held = (end - acq.acquisition_date).days
        # Path: genealogy of the biggest remaining slice, else the entry only.
        biggest = max(slices, key=lambda s: s.remaining_sats) if slices else None
        path = _path_steps_dict(biggest.path if biggest else acq.path, names)
        entry_wallet = acq.origin_wallet_id if acq.origin_wallet_id is not None else acq.wallet_id
        entry_addr = acq.origin_address or acq.address or ""
        wname = _name(entry_wallet)
        current = []
        for s_ in slices:
            gene = lot_to_genealogy_dict(
                s_, as_of=end, wallet_names=names, threshold_days=threshold_days
            )
            current.append(
                {
                    "wallet_id": s_.wallet_id,
                    "wallet_name": gene.get("current_wallet_name"),
                    "address": gene.get("current_address") or s_.address,
                    "remaining_sats": s_.remaining_sats,
                }
            )
        if remaining <= 0:
            status = "abgeflossen"
            status_de = "vollständig abgeflossen"
        elif remaining < entry_amount:
            status = "teilweise"
            status_de = "teilweise abgeflossen"
        else:
            status = "vorhanden"
            status_de = "vollständig vorhanden"
        rows.append(
            {
                "direction": "in",
                "kind": "cloud_entry",
                "amount_sats": entry_amount,
                "original_amount_sats": entry_amount,
                "remaining_sats": remaining,
                "status": status,
                "status_de": status_de,
                "address": entry_addr,
                "address_display": (
                    f"{wname} ({entry_addr})" if wname and entry_addr else entry_addr or wname or "—"
                ),
                "wallet_id": entry_wallet,
                "wallet_name": wname,
                "current_locations": current,
                # Zeit = Cloud-Eintritt (block day); lot_date = Anschaffung
                # (differs only for purchases from an exchange export).
                "time": (acq.path[0].date if acq.path and acq.path[0].date else acq.acquisition_date).isoformat(),
                "txid": acq.txid,
                "lot_id": acq.lot_id,
                "lot_date": acq.acquisition_date.isoformat(),
                "acq_price_eur": acq.price_eur,
                "acq_source": acq.acq_source,
                "path": path,
                "path_labels": _path_labels(path),
                # Haltefrist of what is still held; spent parts → out rows.
                "haltefrist_hint": (held >= threshold_days) if remaining > 0 else None,
                "haltefrist_days": held,
                "amount_basis": "entry_sats",
                "note": (
                    "Cloud-Eintritt: Betrag beim Eintritt; remaining_sats = davon "
                    "noch in der Cloud, current_locations = wo sie jetzt liegen."
                ),
            }
        )

    for c in result.consumptions:
        if c.kind == "fee" and not include_fees:
            continue
        if c.kind not in ("outflow", "fee"):
            continue
        path = _path_steps_dict(c.path, names)
        dest = resolve_external_destination(
            c.txid, own_addresses=own_addresses, tx_io=tx_io
        )
        if dest:
            addr_display = dest
            address = dest
        else:
            address = ""
            addr_display = EXTERNAL_OUTSIDE_CLOUD
        rows.append(
            {
                "direction": "out",
                "kind": c.kind,
                "amount_sats": c.consumed_sats,
                "original_amount_sats": c.original_amount_sats,
                "address": address,
                "address_display": addr_display,
                "wallet_id": c.wallet_id,
                "wallet_name": _name(c.wallet_id),
                "time": c.disposal_date.isoformat(),
                "txid": c.txid,
                "lot_id": c.lot_id,
                "lot_date": c.acquisition_date.isoformat(),
                "acq_price_eur": c.price_eur,
                "acq_source": c.acq_source,
                "path": path,
                "path_labels": _path_labels(path),
                "haltefrist_hint": c.qualifies_haltefrist_hint,
                "haltefrist_days": c.days_held,
                "amount_basis": "consumed_sats",
                "origin_txid": c.origin_txid or None,
                "address_at_disposal": c.address_at_disposal or None,
                "note": (
                    "Cloud-Austritt: externe Veräußerung "
                    f"({'Gebühr' if c.kind == 'fee' else 'Outflow'})."
                ),
            }
        )

    # Chronological: time asc, then in before out, stable by lot_id/txid.
    rows.sort(
        key=lambda r: (
            str(r.get("time") or ""),
            0 if r.get("direction") == "in" else 1,
            str(r.get("lot_id") or ""),
            str(r.get("txid") or ""),
        )
    )
    return rows

def lots_related_to_txid(lots: list[Lot], txid: str) -> list[Lot]:
    """Lots whose origin or any path hop references ``txid``."""
    out: list[Lot] = []
    for lot in lots:
        if lot.txid == txid:
            out.append(lot)
            continue
        if any(step.txid == txid for step in lot.path):
            out.append(lot)
    return out
