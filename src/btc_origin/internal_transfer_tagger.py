"""Tag transfers between own wallets/xpubs as internal (tx-centric netting).

A movement is *fully* internal only when, within the same txid, no value is
paid to non-session scripts (external_out_sats == 0). That covers pure
self-spends / consolidations / cross-xpub moves.

When a tx also pays third parties (payment with change):
- own inflows (change) are marked internal;
- own outflows stay external and carry ``external_amount_sats`` / ``fee_sats``.

Session robustness: ``direction=="out"`` flows are always treated as session
spends (the ingestor only emits outs for owned outpoints). Session receives
are ``direction=="in"`` with ``wallet_id`` set and/or address in ``own``.
This avoids late FA acquisition dates when the address set is incomplete but
both wallets' flows are present in the session ledger.
"""

from __future__ import annotations

from btc_origin.tx_ingestor import Flow


def _copy_flow(f: Flow) -> Flow:
    return Flow(
        txid=f.txid,
        address=f.address,
        direction=f.direction,
        amount_sats=f.amount_sats,
        wallet_id=f.wallet_id,
        is_internal=f.is_internal,
        block_height=f.block_height,
        block_time=f.block_time,
        vout=f.vout,
        vin_index=f.vin_index,
        tx_total_output_sats=f.tx_total_output_sats,
        external_amount_sats=f.external_amount_sats,
        fee_sats=f.fee_sats,
        lot_date=f.lot_date,
        holding_days=f.holding_days,
        prev_txid=f.prev_txid,
        prev_vout=f.prev_vout,
    )


class InternalTransferTagger:
    """Mark flows that move between registered own addresses as internal."""

    def tag(self, flows: list[Flow], own_addresses: set[str]) -> list[Flow]:
        """Return a new list of flows with ``is_internal`` set where applicable."""
        tagged = [_copy_flow(f) for f in flows]
        self.tag_inplace(tagged, own_addresses)
        return tagged

    def tag_inplace(self, flows: list[Flow], own_addresses: set[str]) -> None:
        """Mutate ``flows`` in place: net-external tagging + fee/external fields."""
        own = {a.strip() for a in own_addresses if a}
        if not flows:
            return

        by_txid: dict[str, list[Flow]] = {}
        for f in flows:
            by_txid.setdefault(f.txid, []).append(f)

        for _txid, group in by_txid.items():
            self._tag_txid_group(group, own)

    def _tag_txid_group(self, group: list[Flow], own: set[str]) -> None:
        # Ingestor only emits outs for owned outpoints → all outs are session spends.
        own_outs = [f for f in group if f.direction == "out"]
        # Session receives: address in own set OR bound to a session wallet_id.
        own_ins = [
            f
            for f in group
            if f.direction == "in"
            and (f.address in own or f.wallet_id is not None)
        ]
        foreign_outs = [
            f
            for f in group
            if f.direction == "in"
            and f.address not in own
            and f.wallet_id is None
        ]

        if not own_outs and not own_ins:
            return

        sum_own_out = sum(f.amount_sats for f in own_outs)
        sum_own_in = sum(f.amount_sats for f in own_ins)
        sum_foreign = sum(f.amount_sats for f in foreign_outs)

        tx_total = None
        for f in group:
            if f.tx_total_output_sats is not None:
                tx_total = f.tx_total_output_sats
                break

        fee_sats: int | None = None
        external_out_sats: int

        if tx_total is not None:
            # Outputs to non-own = total outputs − own received outputs.
            external_out_sats = max(0, tx_total - sum_own_in)
            # Fee when all spent inputs appear owned (typical wallet spend).
            if own_outs and sum_own_out >= tx_total:
                fee_sats = sum_own_out - tx_total
            elif own_outs and sum_foreign == 0 and sum_own_in <= sum_own_out:
                # No foreign flows recorded; fee = inputs − own outputs
                # only if tx_total equals own_in (pure consolidation).
                if tx_total == sum_own_in:
                    fee_sats = sum_own_out - tx_total
                    external_out_sats = 0
        elif foreign_outs:
            external_out_sats = sum_foreign
            if own_outs:
                fee_sats = max(0, sum_own_out - sum_own_in - external_out_sats)
        else:
            # Synthetic / incomplete flows: own in + own out without foreign
            # metadata → treat as fully internal (consolidation / self-transfer).
            if own_outs and own_ins:
                external_out_sats = 0
                fee_sats = max(0, sum_own_out - sum_own_in)
            elif own_outs and not own_ins:
                # Pure external spend (no change recorded).
                external_out_sats = sum_own_out
                fee_sats = None
            else:
                # Pure external inflow.
                external_out_sats = 0

        fully_internal = (
            bool(own_outs)
            and bool(own_ins)
            and external_out_sats == 0
        )

        if fully_internal:
            for f in own_outs + own_ins:
                f.is_internal = True
                f.external_amount_sats = 0
                if fee_sats is not None:
                    f.fee_sats = fee_sats
            return

        # Payment with change (or external spend): change ins = internal;
        # outs remain external and expose net external amount + fee.
        for f in own_ins:
            if own_outs:
                # Change / internal receive alongside a spend.
                f.is_internal = True
            # Pure external inflows stay external (is_internal False).

        # external_out_sats is a per-TX total. Split it across the spent
        # inputs (in input order) so consumers that sum per flow — cloud
        # outflow, FIFO disposal — count it once, not once per input.
        remaining = external_out_sats
        for f in sorted(
            own_outs,
            key=lambda x: (x.vin_index is None, x.vin_index or 0),
        ):
            share = min(f.amount_sats, remaining)
            remaining -= share
            f.is_internal = False
            f.external_amount_sats = share
            if fee_sats is not None:
                # Fee stays a per-TX value; consumers dedupe it by txid.
                f.fee_sats = fee_sats
