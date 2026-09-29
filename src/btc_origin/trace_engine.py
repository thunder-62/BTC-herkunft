"""Reverse BFS provenance trace — depth-limited, ambiguity flagged explicitly.

Never invents certainty. When a tx has multiple previous inputs (parents), or
when parent resolution fails / is truncated by the depth limit, the result
surfaces ``ambiguous`` / per-step flags and notes.

Each hop can carry **input_addresses** (funding prevout addresses) when the
provider can decode them. Electrum path fetches parent tx hex (cached per
trace) and maps ``vout.script_pubkey`` → address via embit.

Electrum is optional via an injected client; unit tests use a fully mocked
graph or client. No unbounded crawl.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


LabelLookup = Callable[[str], str | None]


class TxParentProvider(Protocol):
    """Minimal interface: map a txid → parent txids (vin prevouts).

    Optional richer resolution via ``hop_of`` (duck-typed); TraceEngine uses it
    when present to attach funding addresses.
    """

    def parents_of(self, txid: str) -> list[str]: ...


@dataclass
class FundingAddress:
    """One decoded funding prevout address for a hop."""

    address: str
    prev_txid: str
    vout: int
    label: str | None = None


@dataclass
class HopResolution:
    """Parents + optional address enrichment for one tx."""

    parents: list[str] = field(default_factory=list)
    input_addresses: list[str] = field(default_factory=list)
    output_addresses: list[str] = field(default_factory=list)
    funding: list[FundingAddress] = field(default_factory=list)


@dataclass
class TraceStep:
    """One reverse-provenance hop."""

    txid: str
    depth: int
    parents: list[str] = field(default_factory=list)
    ambiguous: bool = False
    truncated: bool = False
    note: str | None = None
    # Funding prevout addresses (who funded this tx), deduped, order preserved.
    input_addresses: list[str] = field(default_factory=list)
    # Optional outputs of this tx (secondary; useful for session-wallet match).
    output_addresses: list[str] = field(default_factory=list)
    # address → display label (pack entity or session wallet name).
    address_labels: dict[str, str] = field(default_factory=dict)


@dataclass
class TraceNode:
    """Compatibility alias shape used by earlier stubs / API."""

    txid: str
    depth: int
    parents: list[str] = field(default_factory=list)


@dataclass
class TraceResult:
    root_txid: str
    nodes: list[TraceNode] = field(default_factory=list)
    steps: list[TraceStep] = field(default_factory=list)
    max_depth: int = 0
    depth_reached: int = 0
    ambiguous: bool = False
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_txid": self.root_txid,
            "max_depth": self.max_depth,
            "depth_reached": self.depth_reached,
            "ambiguous": self.ambiguous,
            "truncated": self.truncated,
            "notes": list(self.notes),
            "steps": [
                {
                    "txid": s.txid,
                    "depth": s.depth,
                    "parents": list(s.parents),
                    "ambiguous": s.ambiguous,
                    "truncated": s.truncated,
                    "note": s.note,
                    "input_addresses": list(s.input_addresses),
                    "output_addresses": list(s.output_addresses),
                    "address_labels": dict(s.address_labels),
                }
                for s in self.steps
            ],
            "nodes": [
                {"txid": n.txid, "depth": n.depth, "parents": list(n.parents)}
                for n in self.nodes
            ],
        }


def _spk_to_address(script: Any, *, network: str = "main") -> str | None:
    """Decode a script_pubkey to a Bitcoin address when possible.

    Supports P2PKH / P2SH / P2WPKH / P2WSH / P2TR via embit. Returns None for
    undecodable scripts (OP_RETURN, bare multisig, etc.).
    """
    try:
        from embit.networks import NETWORKS

        net = NETWORKS.get(network) or NETWORKS["main"]
        addr = script.address(net)
        return str(addr) if addr else None
    except Exception:  # noqa: BLE001 — undecodable / exotic scripts
        return None


def _output_addresses_from_tx(tx: Any, *, network: str = "main") -> list[str]:
    """Decode all vout script_pubkeys; dedupe preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for vout in tx.vout:
        addr = _spk_to_address(vout.script_pubkey, network=network)
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def _prev_txids_from_hex(tx_hex: str) -> list[str]:
    """Parse prevout txids from raw transaction hex via embit."""
    try:
        from embit.transaction import Transaction

        tx = Transaction.from_string(tx_hex)
        parents: list[str] = []
        for vin in tx.vin:
            prev = bytes(vin.txid).hex()
            # coinbase: null txid
            if prev == "0" * 64:
                continue
            parents.append(prev)
        return parents
    except Exception:  # noqa: BLE001
        return []


def _hop_from_hex(
    tx_hex: str,
    *,
    fetch_parent_hex: Callable[[str], str | None] | None = None,
    network: str = "main",
) -> HopResolution:
    """Build HopResolution from a tx hex; optionally resolve funding addresses.

    When ``fetch_parent_hex`` is provided, each vin's prevout is fetched and
    its script_pubkey decoded to an address (skipped for coinbase / missing).
    """
    try:
        from embit.transaction import Transaction

        tx = Transaction.from_string(tx_hex)
    except Exception:  # noqa: BLE001
        return HopResolution()

    parents: list[str] = []
    funding: list[FundingAddress] = []
    input_seen: set[str] = set()
    input_addresses: list[str] = []

    for vin in tx.vin:
        prev = bytes(vin.txid).hex()
        if prev == "0" * 64:
            continue
        parents.append(prev)
        vout_n = int(vin.vout)
        if fetch_parent_hex is None:
            continue
        parent_hex = fetch_parent_hex(prev)
        if not parent_hex:
            continue
        try:
            from embit.transaction import Transaction as Tx

            parent_tx = Tx.from_string(parent_hex)
            if vout_n < 0 or vout_n >= len(parent_tx.vout):
                continue
            addr = _spk_to_address(
                parent_tx.vout[vout_n].script_pubkey, network=network
            )
            if not addr:
                continue
            funding.append(
                FundingAddress(address=addr, prev_txid=prev, vout=vout_n)
            )
            if addr not in input_seen:
                input_seen.add(addr)
                input_addresses.append(addr)
        except Exception:  # noqa: BLE001 — skip bad parent / decode
            continue

    unique_parents = list(dict.fromkeys(parents))
    return HopResolution(
        parents=unique_parents,
        input_addresses=input_addresses,
        output_addresses=_output_addresses_from_tx(tx, network=network),
        funding=funding,
    )


def _resolve_hop(provider: TxParentProvider, txid: str) -> HopResolution:
    """Prefer ``hop_of`` when available; else fall back to ``parents_of``."""
    hop_of = getattr(provider, "hop_of", None)
    if callable(hop_of):
        hop = hop_of(txid)
        if isinstance(hop, HopResolution):
            return hop
        if isinstance(hop, dict):
            return HopResolution(
                parents=[str(p) for p in hop.get("parents", [])],
                input_addresses=[str(a) for a in hop.get("input_addresses", [])],
                output_addresses=[str(a) for a in hop.get("output_addresses", [])],
            )
    parents = provider.parents_of(txid)
    return HopResolution(parents=list(parents))


def _apply_labels(
    addresses: list[str],
    label_lookup: LabelLookup | None,
) -> dict[str, str]:
    if label_lookup is None:
        return {}
    labels: dict[str, str] = {}
    for addr in addresses:
        try:
            lab = label_lookup(addr)
        except Exception:  # noqa: BLE001
            lab = None
        if lab:
            labels[addr] = str(lab)
    return labels


class DictParentProvider:
    """Parents (+ optional addresses) from an explicit graph (tests / offline).

    ``graph`` forms accepted:
      - ``{txid: [parent, …]}``
      - ``{txid: {"parents": […], "input_addresses": […], "output_addresses": […]}}``

    ``address_map`` (optional): ``{txid: [funding_addr, …]}`` overlay.
    """

    def __init__(
        self,
        graph: dict[str, Any] | None = None,
        *,
        address_map: dict[str, list[str]] | None = None,
        output_address_map: dict[str, list[str]] | None = None,
    ) -> None:
        self.graph = graph or {}
        self.address_map = address_map or {}
        self.output_address_map = output_address_map or {}

    def parents_of(self, txid: str) -> list[str]:
        return list(self.hop_of(txid).parents)

    def hop_of(self, txid: str) -> HopResolution:
        raw = self.graph.get(txid, [])
        parents: list[str] = []
        inputs: list[str] = []
        outputs: list[str] = []
        if isinstance(raw, dict):
            parents = [str(p) for p in raw.get("parents", [])]
            inputs = [str(a) for a in raw.get("input_addresses", [])]
            outputs = [str(a) for a in raw.get("output_addresses", [])]
        elif isinstance(raw, (list, tuple)):
            parents = [str(p) for p in raw]
        if txid in self.address_map:
            inputs = [str(a) for a in self.address_map[txid]]
        if txid in self.output_address_map:
            outputs = [str(a) for a in self.output_address_map[txid]]
        return HopResolution(
            parents=list(dict.fromkeys(parents)),
            input_addresses=list(dict.fromkeys(inputs)),
            output_addresses=list(dict.fromkeys(outputs)),
        )


class ElectrumParentProvider:
    """Resolve vin prev_txids + funding addresses via an injected Electrum client.

    Expected client methods (duck-typed):
      ``get_transaction(txid, verbose=False) → hex|dict``
      and/or ``get_transaction_hex(txid) → str``

    Tx hex is cached for the lifetime of this provider instance (one trace).
    """

    def __init__(self, client: Any, *, network: str = "main") -> None:
        self.client = client
        self.network = network
        self._hex_cache: dict[str, str | None] = {}

    def parents_of(self, txid: str) -> list[str]:
        return list(self.hop_of(txid).parents)

    def hop_of(self, txid: str) -> HopResolution:
        raw = self._fetch_hex(txid)
        if not raw:
            return HopResolution()
        return _hop_from_hex(
            raw,
            fetch_parent_hex=self._fetch_hex,
            network=self.network,
        )

    def _fetch_hex(self, txid: str) -> str | None:
        if txid in self._hex_cache:
            return self._hex_cache[txid]
        client = self.client
        hx: str | None = None
        try:
            if hasattr(client, "get_transaction_hex"):
                got = client.get_transaction_hex(txid)
                hx = str(got) if got else None
            else:
                result = client.get_transaction(txid, verbose=False)
                if isinstance(result, str):
                    hx = result
                elif isinstance(result, dict) and result.get("hex"):
                    hx = str(result["hex"])
        except Exception:  # noqa: BLE001 — surface as empty parents + ambiguity
            hx = None
        self._hex_cache[txid] = hx
        return hx


class TraceEngine:
    """Reverse-BFS from a tx/flow, depth-limited. Sets ambiguous on forks."""

    def __init__(
        self,
        max_depth: int = 5,
        *,
        client: Any | None = None,
        provider: TxParentProvider | None = None,
        label_lookup: LabelLookup | None = None,
    ) -> None:
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        self.max_depth = max_depth
        self.client = client
        self._provider = provider
        self._label_lookup = label_lookup
        self._resolver_mode: str | None = None

    def _resolve_provider(
        self, graph: dict[str, Any] | None
    ) -> TxParentProvider:
        """Resolve parents source; set ``_resolver_mode`` for note wording.

        Modes: ``injected`` | ``graph`` | ``electrum`` | ``none``.
        """
        if self._provider is not None:
            self._resolver_mode = "injected"
            return self._provider
        if graph is not None:
            # Accept either txid→[parents] or nested {"parents": [...], ...}
            normalized: dict[str, Any] = {}
            address_map: dict[str, list[str]] = {}
            output_map: dict[str, list[str]] = {}
            for k, v in graph.items():
                if isinstance(v, dict):
                    normalized[str(k)] = {
                        "parents": [str(p) for p in v.get("parents", [])],
                        "input_addresses": [
                            str(a) for a in v.get("input_addresses", [])
                        ],
                        "output_addresses": [
                            str(a) for a in v.get("output_addresses", [])
                        ],
                    }
                    if v.get("input_addresses"):
                        address_map[str(k)] = [
                            str(a) for a in v["input_addresses"]
                        ]
                    if v.get("output_addresses"):
                        output_map[str(k)] = [
                            str(a) for a in v["output_addresses"]
                        ]
                elif isinstance(v, (list, tuple)):
                    normalized[str(k)] = [str(p) for p in v]
                else:
                    normalized[str(k)] = []
            self._resolver_mode = "graph"
            return DictParentProvider(
                normalized,
                address_map=address_map or None,
                output_address_map=output_map or None,
            )
        if self.client is not None:
            self._resolver_mode = "electrum"
            return ElectrumParentProvider(self.client)
        self._resolver_mode = "none"
        return DictParentProvider({})

    def trace(
        self,
        root_txid: str,
        graph: dict[str, Any] | None = None,
        *,
        max_depth: int | None = None,
        label_lookup: LabelLookup | None = None,
    ) -> TraceResult:
        """Reverse BFS provenance from ``root_txid``.

        ``graph`` (optional): ``{txid: [parent_txid, …]}`` or richer dicts with
        ``input_addresses`` — preferred in tests.
        Without graph, uses injected Electrum client when available.
        """
        depth_limit = self.max_depth if max_depth is None else max_depth
        if depth_limit < 0:
            depth_limit = 0
        provider = self._resolve_provider(graph)
        labels = label_lookup if label_lookup is not None else self._label_lookup
        root = root_txid.strip()
        result = TraceResult(root_txid=root, max_depth=depth_limit)

        if not root:
            result.notes.append("Empty root_txid — nothing to trace.")
            result.ambiguous = True
            return result

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        depth_reached = 0

        while queue:
            txid, depth = queue.popleft()
            if txid in visited:
                continue
            visited.add(txid)
            depth_reached = max(depth_reached, depth)

            if depth >= depth_limit:
                step = TraceStep(
                    txid=txid,
                    depth=depth,
                    parents=[],
                    truncated=True,
                    note=(
                        f"Depth limit ({depth_limit}) reached — crawl truncated; "
                        "provenance beyond this hop is unresolved."
                    ),
                )
                result.steps.append(step)
                result.nodes.append(TraceNode(txid=txid, depth=depth, parents=[]))
                result.truncated = True
                result.notes.append(step.note or "")
                continue

            try:
                hop = _resolve_hop(provider, txid)
            except Exception as exc:  # noqa: BLE001
                hop = HopResolution()
                result.notes.append(f"{txid}: parent resolution failed ({exc})")

            unique_parents = list(dict.fromkeys(hop.parents))
            input_addrs = list(dict.fromkeys(hop.input_addresses))
            output_addrs = list(dict.fromkeys(hop.output_addresses))
            # Labels for funding inputs; also tag session outputs that match.
            label_targets = list(
                dict.fromkeys([*input_addrs, *output_addrs])
            )
            addr_labels = _apply_labels(label_targets, labels)

            # Ambiguity: multiple distinct parents (merge / coinjoin / multi-input)
            # OR zero parents before depth limit (unresolvable / coinbase / missing).
            step_ambiguous = False
            note: str | None = None
            if len(unique_parents) > 1:
                step_ambiguous = True
                note = (
                    f"Ambiguous provenance at depth {depth}: "
                    f"{len(unique_parents)} parent inputs — path not unique."
                )
            elif len(unique_parents) == 0 and depth > 0:
                note = f"No parents resolved for {txid} at depth {depth}."
                step_ambiguous = True
            elif len(unique_parents) == 0 and depth == 0:
                mode = getattr(self, "_resolver_mode", None)
                if mode == "none":
                    note = (
                        "No Electrum client available — cannot resolve vin "
                        "parents (kein Electrum-Client). Pass an offline graph "
                        "or connect Electrum; this is not a coinbase result."
                    )
                elif mode == "electrum":
                    note = (
                        "Root has no resolved parents via Electrum (missing tx "
                        "data, coinbase, or fetch failed) — provenance uncertain."
                    )
                else:
                    note = (
                        "Root has no resolved parents (missing tx data, coinbase, "
                        "or empty graph) — provenance uncertain."
                    )
                step_ambiguous = True

            step = TraceStep(
                txid=txid,
                depth=depth,
                parents=unique_parents,
                ambiguous=step_ambiguous,
                note=note,
                input_addresses=input_addrs,
                output_addresses=output_addrs,
                address_labels=addr_labels,
            )
            result.steps.append(step)
            result.nodes.append(
                TraceNode(txid=txid, depth=depth, parents=list(unique_parents))
            )
            if step_ambiguous:
                result.ambiguous = True
                if note:
                    result.notes.append(note)

            for parent in unique_parents:
                if parent not in visited:
                    queue.append((parent, depth + 1))

        result.depth_reached = depth_reached
        if not result.notes:
            result.notes.append(
                "Trace complete within depth limit. "
                "Ambiguity flags indicate non-unique or unresolved hops — "
                "never treat unmarked paths as certified provenance."
            )
        return result

    def reverse_bfs(
        self,
        root_txid: str,
        graph: dict[str, Any] | None = None,
        *,
        max_depth: int | None = None,
        label_lookup: LabelLookup | None = None,
    ) -> TraceResult:
        """Alias for :meth:`trace` (explicit reverse-BFS naming)."""
        return self.trace(
            root_txid, graph=graph, max_depth=max_depth, label_lookup=label_lookup
        )
