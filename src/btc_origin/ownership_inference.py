"""Session-scoped heuristic ownership inference from transaction patterns.

Expands the seed own-address set (xpub gap + address wallets) using:
1. Common-input ownership (co-spends) — iterative fixed-point
2. Change / consolidation outputs when a tx is fully own-funded and small

Hard boundaries
----------------
* RAM / :memory: only — never persists inferred addresses to disk.
* Never touches seeds/keys; pasted xpubs remain the primary seed of trust.
* Heuristic only — UI must label results as ``abgeleitet / heuristisch``.
* Not tax advice; never certified certainty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from btc_origin.tx_ingestor import Flow

# Caps to avoid swallowing exchange hot wallets / runaway expansion.
DEFAULT_MAX_ROUNDS = 20
DEFAULT_MAX_INFERRED = 500
DEFAULT_MAX_OUTPUTS_FOR_CHANGE = 1  # consolidation only; 2-out peel opt-in
DEFAULT_SKIP_FANOUT_ABOVE = 20


@dataclass
class TxIoView:
    """Addresses seen on one tx (inputs = spends, outputs = receives)."""

    txid: str
    input_addresses: list[str] = field(default_factory=list)
    output_addresses: list[str] = field(default_factory=list)
    # When set and greater than len(input_addresses), some vins are unresolved
    # → skip "all vins own" output expansion for safety.
    vin_count: int | None = None
    vout_count: int | None = None

    @property
    def inputs_fully_resolved(self) -> bool:
        if self.vin_count is None:
            return True
        return self.vin_count <= len(self.input_addresses)


@dataclass
class OwnershipInferenceResult:
    """Result of expanding seed own addresses via graph heuristics."""

    seed: set[str]
    own: set[str]
    inferred: set[str]
    inferred_by: dict[str, str] = field(default_factory=dict)
    rounds: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def seed_count(self) -> int:
        return len(self.seed)

    @property
    def inferred_count(self) -> int:
        return len(self.inferred)

    def as_dict(
        self,
        *,
        sample_limit: int = 50,
        include_full: bool = True,
        privacy_mask: bool = False,
    ) -> dict[str, Any]:
        def _mask(addr: str) -> str:
            if not privacy_mask:
                return addr
            return "************"

        inferred_sorted = sorted(self.inferred)
        sample = [_mask(a) for a in inferred_sorted[:sample_limit]]
        payload: dict[str, Any] = {
            "seed_count": self.seed_count,
            "inferred_count": self.inferred_count,
            "own_count": len(self.own),
            "rounds": self.rounds,
            "inferred_addresses_sample": sample,
            "inferred_by_sample": {
                _mask(k): v
                for k, v in list(sorted(self.inferred_by.items()))[:sample_limit]
            },
            "notes": list(self.notes),
            "heuristic": True,
            "label_de": "abgeleitet / heuristisch",
            "warning_de": (
                "Heuristische Zuordnung aus Co-Spends / Change-Mustern. "
                "Keine Steuerberatung, keine zertifizierte Sicherheit. "
                "Nur Sitzungs-RAM — nichts wird auf Disk geschrieben."
            ),
            "ephemeral": True,
        }
        if include_full and not privacy_mask:
            payload["inferred_addresses"] = inferred_sorted
            payload["seed_addresses_sample"] = sorted(self.seed)[:sample_limit]
        elif include_full and privacy_mask:
            payload["inferred_addresses"] = [_mask(a) for a in inferred_sorted]
            payload["seed_addresses_sample"] = [
                _mask(a) for a in sorted(self.seed)[:sample_limit]
            ]
        return payload


def _norm_addr(a: str | None) -> str | None:
    if not a:
        return None
    s = str(a).strip()
    return s or None


def tx_graph_from_flows(flows: Sequence[Flow]) -> list[TxIoView]:
    """Build a coarse TxIoView graph from session flows.

    Outflows = spending (input) addresses; inflows = receiving (output) addresses.
    Addresses that never produced a flow (unknown co-inputs) are absent — merge
    with an explicit graph from raw-tx parse / Electrum resolution for those.
    """
    by_txid: dict[str, TxIoView] = {}
    for f in flows:
        if not f.txid:
            continue
        view = by_txid.get(f.txid)
        if view is None:
            view = TxIoView(txid=f.txid)
            by_txid[f.txid] = view
        addr = _norm_addr(f.address)
        if not addr:
            continue
        if f.direction == "out":
            if addr not in view.input_addresses:
                view.input_addresses.append(addr)
        elif f.direction == "in":
            if addr not in view.output_addresses:
                view.output_addresses.append(addr)
    return list(by_txid.values())


def merge_tx_graphs(*graphs: Iterable[TxIoView]) -> list[TxIoView]:
    """Merge multiple TxIoView lists by txid (union of addresses, max counts)."""
    by_txid: dict[str, TxIoView] = {}
    for graph in graphs:
        for view in graph:
            if not view.txid:
                continue
            cur = by_txid.get(view.txid)
            if cur is None:
                cur = TxIoView(
                    txid=view.txid,
                    input_addresses=[],
                    output_addresses=[],
                    vin_count=view.vin_count,
                    vout_count=view.vout_count,
                )
                by_txid[view.txid] = cur
            for a in view.input_addresses:
                na = _norm_addr(a)
                if na and na not in cur.input_addresses:
                    cur.input_addresses.append(na)
            for a in view.output_addresses:
                na = _norm_addr(a)
                if na and na not in cur.output_addresses:
                    cur.output_addresses.append(na)
            if view.vin_count is not None:
                cur.vin_count = (
                    view.vin_count
                    if cur.vin_count is None
                    else max(cur.vin_count, view.vin_count)
                )
            if view.vout_count is not None:
                cur.vout_count = (
                    view.vout_count
                    if cur.vout_count is None
                    else max(cur.vout_count, view.vout_count)
                )
    return list(by_txid.values())


def tx_graph_from_mapping(
    mapping: Mapping[str, Any],
) -> list[TxIoView]:
    """Build graph from ``{txid: {input_addresses, output_addresses, ...}}``."""
    out: list[TxIoView] = []
    for txid, raw in mapping.items():
        if isinstance(raw, TxIoView):
            out.append(raw)
            continue
        if not isinstance(raw, Mapping):
            continue
        inputs = [str(a) for a in (raw.get("input_addresses") or raw.get("inputs") or [])]
        outputs = [
            str(a) for a in (raw.get("output_addresses") or raw.get("outputs") or [])
        ]
        vin_count = raw.get("vin_count")
        vout_count = raw.get("vout_count")
        out.append(
            TxIoView(
                txid=str(txid),
                input_addresses=inputs,
                output_addresses=outputs,
                vin_count=int(vin_count) if vin_count is not None else None,
                vout_count=int(vout_count) if vout_count is not None else None,
            )
        )
    return out


class OwnershipInferencer:
    """Expand seed own addresses via co-input + change heuristics."""

    def __init__(
        self,
        *,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        max_inferred: int = DEFAULT_MAX_INFERRED,
        max_outputs_for_change: int = DEFAULT_MAX_OUTPUTS_FOR_CHANGE,
        skip_fanout_above: int = DEFAULT_SKIP_FANOUT_ABOVE,
    ) -> None:
        self.max_rounds = max_rounds
        self.max_inferred = max_inferred
        self.max_outputs_for_change = max_outputs_for_change
        self.skip_fanout_above = skip_fanout_above

    def infer(
        self,
        seed_addresses: Iterable[str],
        tx_graph: Sequence[TxIoView] | Mapping[str, Any] | None = None,
        *,
        flows: Sequence[Flow] | None = None,
    ) -> OwnershipInferenceResult:
        seed = {a for a in (_norm_addr(x) for x in seed_addresses) if a}
        graphs: list[list[TxIoView]] = []
        if flows:
            graphs.append(tx_graph_from_flows(flows))
        if tx_graph is not None:
            if isinstance(tx_graph, Mapping):
                graphs.append(tx_graph_from_mapping(tx_graph))
            else:
                graphs.append(list(tx_graph))
        merged = merge_tx_graphs(*graphs) if graphs else []

        own = set(seed)
        inferred_by: dict[str, str] = {}
        notes: list[str] = [
            "Ownership inference is heuristic (co-input + change). "
            "Label: abgeleitet / heuristisch. Not tax advice.",
        ]
        if not seed:
            notes.append("Empty seed — nothing to expand.")
            return OwnershipInferenceResult(
                seed=seed, own=own, inferred=set(), notes=notes
            )
        if not merged:
            notes.append("No tx graph / flows — seed only.")
            return OwnershipInferenceResult(
                seed=seed, own=own, inferred=set(), notes=notes
            )

        rounds = 0
        capped = False
        for rounds in range(1, self.max_rounds + 1):
            grew = False

            # --- Step 2: common-input ownership ---
            for view in merged:
                inputs = [a for a in (_norm_addr(x) for x in view.input_addresses) if a]
                if not inputs:
                    continue
                if not any(a in own for a in inputs):
                    continue
                for a in inputs:
                    if a in own:
                        continue
                    if len(own) - len(seed) >= self.max_inferred:
                        capped = True
                        break
                    own.add(a)
                    inferred_by.setdefault(a, "co_input")
                    grew = True
                if capped:
                    break

            if capped:
                break

            # --- Step 3: change / consolidation outputs ---
            for view in merged:
                inputs = [a for a in (_norm_addr(x) for x in view.input_addresses) if a]
                outputs = [
                    a for a in (_norm_addr(x) for x in view.output_addresses) if a
                ]
                if not inputs or not outputs:
                    continue
                # All known inputs must be own.
                if not all(a in own for a in inputs):
                    continue
                # Safety: do not expand outputs when unresolved foreign vins remain.
                if not view.inputs_fully_resolved:
                    continue
                n_out = (
                    view.vout_count
                    if view.vout_count is not None
                    else len(outputs)
                )
                # Skip huge fan-outs (exchange hot wallets etc.).
                if n_out > self.skip_fanout_above:
                    continue
                # Default: single-out consolidation only (avoids marking
                # payment destinations on 2-out peels). Opt-in ≤2 via ctor.
                if n_out > self.max_outputs_for_change:
                    continue
                for a in outputs:
                    if a in own:
                        continue
                    if len(own) - len(seed) >= self.max_inferred:
                        capped = True
                        break
                    own.add(a)
                    reason = (
                        "consolidation_out"
                        if n_out == 1
                        else "change_out"
                    )
                    inferred_by.setdefault(a, reason)
                    grew = True
                if capped:
                    break

            if capped or not grew:
                break

        inferred = own - seed
        if capped:
            notes.append(
                f"Expansion capped at max_inferred={self.max_inferred}."
            )
        notes.append(
            f"Expanded seed={len(seed)} → own={len(own)} "
            f"(+{len(inferred)} inferred) in {rounds} round(s), "
            f"{len(merged)} tx view(s)."
        )
        return OwnershipInferenceResult(
            seed=seed,
            own=own,
            inferred=inferred,
            inferred_by=inferred_by,
            rounds=rounds,
            notes=notes,
        )


def infer_own_addresses(
    seed_addresses: Iterable[str],
    tx_graph: Sequence[TxIoView] | Mapping[str, Any] | None = None,
    *,
    flows: Sequence[Flow] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_inferred: int = DEFAULT_MAX_INFERRED,
) -> OwnershipInferenceResult:
    """Convenience wrapper around :class:`OwnershipInferencer`."""
    return OwnershipInferencer(
        max_rounds=max_rounds, max_inferred=max_inferred
    ).infer(seed_addresses, tx_graph, flows=flows)
