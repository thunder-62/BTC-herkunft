"""Herkunfts-Trace einer Transaktion."""

from __future__ import annotations


from typing import Any

from pydantic import BaseModel, Field

from btc_origin.config import get_settings
from btc_origin.electrum_client import (
    ElectrumClient,
    ElectrumConnectionError,
    electrum_server_candidates,
    try_connect_first_available,
)
from btc_origin.trace_engine import TraceEngine
from fastapi import APIRouter

from btc_origin.api.core import (
    AppState,
    _address_wallet_name_maps,
    _session,
)

router = APIRouter()


class TraceRequest(BaseModel):
    txid: str = Field(..., min_length=1)
    max_depth: int | None = None
    # Optional explicit parent graph for offline / test traces:
    # { "child_txid": ["parent1", "parent2"], ... }
    # or richer: { "child": {"parents": [...], "input_addresses": [...]} }
    graph: dict[str, Any] | None = None


def _trace_label_lookup(state: AppState):
    """In-memory address → label: session wallet name first, then pack entity.

    Never persists; uses registry + :memory: addresses + LabelService packs only.
    """
    _by_id, by_addr = _address_wallet_name_maps(state)
    labels = state.labels

    def lookup(address: str) -> str | None:
        addr = (address or "").strip()
        if not addr:
            return None
        # Session-owned (registry / derived) — display wallet name.
        if addr in by_addr:
            return by_addr[addr]
        # case-insensitive bech32 mirror
        low = addr.lower()
        if low in by_addr:
            return by_addr[low]
        hit = labels.lookup_address(addr)
        if hit:
            _key, display = hit
            return display
        return None

    return lookup


@router.post("/api/trace")
def trace_provenance(body: TraceRequest) -> dict[str, Any]:
    """Reverse-BFS provenance (depth-limited). Ambiguity surfaced explicitly.

    Offline: pass ``graph`` (tests) — no Electrum required.
    Live TxID: uses injected ``trace_client`` / ``electrum_client``, else
    connects on demand via the same failover list as sync.
    """
    state = _session()
    settings = get_settings()
    depth = body.max_depth if body.max_depth is not None else settings.trace_max_depth
    root = body.txid.strip()

    owned_client: ElectrumClient | None = None
    electrum_info: dict[str, Any] | None = None

    try:
        # Explicit parent graph → offline / test path; never require Electrum.
        label_lookup = _trace_label_lookup(state)

        if body.graph is not None:
            engine = TraceEngine(max_depth=depth, label_lookup=label_lookup)
            result = engine.trace(
                root, graph=body.graph, max_depth=depth, label_lookup=label_lookup
            )
        else:
            client = state.trace_client or state.electrum_client
            if client is None:
                try:
                    owned_client = try_connect_first_available(
                        electrum_server_candidates(),
                        timeout=8.0,
                        cache=state.chain_cache,
                    )
                    client = owned_client
                except ElectrumConnectionError as exc:
                    return {
                        "root_txid": root,
                        "max_depth": depth,
                        "depth_reached": 0,
                        "ambiguous": True,
                        "truncated": False,
                        "notes": [
                            "Electrum unreachable / Electrum unerreichbar — "
                            "cannot resolve vin parents (no chain data). "
                            f"{exc}"
                        ],
                        "steps": [],
                        "nodes": [],
                        "ephemeral": True,
                        "status": "electrum_unreachable",
                        "note": (
                            "Electrum server unreachable (configured host + public "
                            "failover). Set ELECTRUM_HOST/PORT or retry later. "
                            "Cannot resolve parents without chain data — "
                            "this is not a coinbase / empty-graph result. "
                            "Keine Steuerberatung / no tax advice."
                        ),
                    }

            if hasattr(client, "host"):
                electrum_info = {
                    "host": getattr(client, "host", None),
                    "port": getattr(client, "port", None),
                    "ssl": getattr(client, "use_ssl", None),
                }

            engine = TraceEngine(
                max_depth=depth, client=client, label_lookup=label_lookup
            )
            result = engine.trace(
                root, graph=None, max_depth=depth, label_lookup=label_lookup
            )

        payload = result.as_dict()
        payload["ephemeral"] = True
        payload["note"] = (
            "Reverse provenance is heuristic and depth-limited. "
            "ambiguous=true means the path is not unique or unresolved — "
            "never treat this as certified certainty. No tax advice."
        )
        if electrum_info is not None:
            payload["electrum"] = electrum_info
        return payload
    finally:
        if owned_client is not None:
            try:
                owned_client.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
