"""Merge flows from unlimited multi-xpub wallets into one ledger."""

from __future__ import annotations

from dataclasses import dataclass, field

from btc_origin.tx_ingestor import Flow


@dataclass
class Ledger:
    """Unified ledger across any number of xpubs/wallets — no hard cap."""

    flows: list[Flow] = field(default_factory=list)
    wallet_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def inflow_count(self) -> int:
        return sum(1 for f in self.flows if f.direction == "in")

    @property
    def outflow_count(self) -> int:
        return sum(1 for f in self.flows if f.direction == "out")


class Merger:
    """Combine per-wallet flow lists into a single ledger.

    Supports an **unlimited** number of source wallets/xpubs. A soft note may
    be attached when the wallet count is large; merging always proceeds.
    Deduplicates identical (txid, address, direction, vout/vin) rows.
    """

    def merge(
        self,
        flows_by_wallet: dict[int, list[Flow]],
        *,
        warn_threshold: int = 50,
    ) -> Ledger:
        wallet_ids = list(flows_by_wallet.keys())
        combined: list[Flow] = []
        seen: set[tuple] = set()
        for wid in wallet_ids:
            for flow in flows_by_wallet.get(wid, []):
                key = (
                    flow.txid,
                    flow.address,
                    flow.direction,
                    flow.vout,
                    flow.vin_index,
                    flow.amount_sats,
                )
                if key in seen:
                    continue
                seen.add(key)
                combined.append(flow)
        # Stable chronological-ish order: height then txid then direction
        combined.sort(
            key=lambda f: (
                f.block_height is None or (f.block_height or 0) <= 0,
                f.block_height or 0,
                f.txid,
                0 if f.direction == "in" else 1,
            )
        )
        notes: list[str] = []
        if len(wallet_ids) >= warn_threshold:
            notes.append(
                f"{len(wallet_ids)} wallets in merge — may be slower; no hard limit."
            )
        return Ledger(flows=combined, wallet_ids=wallet_ids, notes=notes)
