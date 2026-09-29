"""Ingest Electrum history / raw txs into Flow records (in-memory only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction

from btc_origin.electrum_client import ElectrumClient, ElectrumError, TxHistoryItem

# Optional progress hook: kwargs match SyncProgressTracker.update fields.
ProgressFn = Callable[..., None]


@dataclass
class Flow:
    txid: str
    address: str
    direction: str  # in | out
    amount_sats: int
    wallet_id: int | None = None
    is_internal: bool = False
    block_height: int | None = None
    block_time: str | None = None  # ISO-8601 UTC when known
    vout: int | None = None
    vin_index: int | None = None
    # Tx-level helpers (optional; set by raw-tx parse / tagger):
    # sum of all output values in the tx (for fee / external netting).
    tx_total_output_sats: int | None = None
    # Net amount paid to non-session scripts (set by tagger on out flows).
    external_amount_sats: int | None = None
    # Tx fee when computable (all spent inputs owned); set by tagger.
    fee_sats: int | None = None
    # Acquisition date of the lot this flow relates to (FIFO; ISO date).
    lot_date: str | None = None
    # Days held for remaining lot balance or for a disposal consumption.
    holding_days: int | None = None
    # OUT flows: the spent coin (outpoint) — enables Einzelbetrachtung per UTXO.
    prev_txid: str | None = None
    prev_vout: int | None = None


@dataclass
class IngestResult:
    flows: list[Flow] = field(default_factory=list)
    txids_seen: set[str] = field(default_factory=set)
    addresses_with_history: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    # txid → {input_addresses, output_addresses, vin_count, vout_count} (RAM)
    tx_io: dict[str, dict[str, Any]] = field(default_factory=dict)



def _vout_address(script_pubkey, *, network: str = "main") -> str | None:
    """Best-effort scriptPubKey → address (embit)."""
    try:
        from embit import script as embit_script
        from embit.networks import NETWORKS

        if hasattr(script_pubkey, "address"):
            net = NETWORKS.get(network) or NETWORKS["main"]
            addr = script_pubkey.address(net)
            return str(addr) if addr else None
        # Fallback: wrap raw script
        spk = embit_script.Script(script_pubkey.data)
        net = NETWORKS.get(network) or NETWORKS["main"]
        addr = spk.address(net)
        return str(addr) if addr else None
    except Exception:  # noqa: BLE001
        return None


def _record_tx_io(
    tx_io_index: dict[str, dict[str, Any]] | None,
    *,
    txid: str,
    tx: Any,
    known_input_address: str | None = None,
) -> None:
    """Accumulate session Tx-IO metadata for ownership inference (RAM only)."""
    if tx_io_index is None:
        return
    entry = tx_io_index.setdefault(
        txid,
        {
            "input_addresses": [],
            "output_addresses": [],
            "vin_count": len(tx.vin),
            "vout_count": len(tx.vout),
        },
    )
    entry["vin_count"] = max(int(entry.get("vin_count") or 0), len(tx.vin))
    entry["vout_count"] = max(int(entry.get("vout_count") or 0), len(tx.vout))
    # All output addresses (available without parent fetch).
    outs: list[str] = list(entry.get("output_addresses") or [])
    seen_out = set(outs)
    for vout in tx.vout:
        addr = _vout_address(vout.script_pubkey)
        if addr and addr not in seen_out:
            seen_out.add(addr)
            outs.append(addr)
    entry["output_addresses"] = outs
    if known_input_address:
        ins: list[str] = list(entry.get("input_addresses") or [])
        if known_input_address not in ins:
            ins.append(known_input_address)
        entry["input_addresses"] = ins


COINBASE = "coinbase"
_NULL_TXID = "00" * 32


def resolve_source_addresses(
    client: Any,
    txids: Iterable[str],
    tx_io_index: dict[str, dict[str, Any]],
    *,
    max_parents: int | None = None,
) -> int:
    """Store the sender addresses of each tx as ``source_addresses`` (RAM).

    A tx only references its inputs by (parent txid, vout); the address sits in
    the parent's output. Parents are prefetched in batches via the session
    cache, so this costs ~1 round-trip per 100 parents. Newly mined coins get
    ``["coinbase"]``. Fail-open: unknown parents set ``source_complete=False``.
    ``max_parents`` caps the parent fetches (txs with fewer inputs first).
    Returns the number of txs resolved.
    """
    candidates: list[tuple[str, Any]] = []
    for txid in dict.fromkeys(txids):
        if not txid or "source_addresses" in tx_io_index.get(txid, {}):
            continue
        try:
            candidates.append((txid, Transaction.from_string(client.get_transaction_hex(txid))))
        except Exception:  # noqa: BLE001 — fail-open
            continue
    if max_parents is not None:
        candidates.sort(key=lambda c: len(c[1].vin))
    parsed: dict[str, Any] = {}
    parents: set[str] = set()
    for txid, tx in candidates:
        mine = {bytes(v.txid).hex() for v in tx.vin} - {_NULL_TXID}
        if max_parents is not None and len(parents | mine) > max_parents:
            continue
        parsed[txid] = tx
        parents |= mine
    if hasattr(client, "prefetch_transactions"):
        try:
            client.prefetch_transactions(sorted(parents))
        except Exception:  # noqa: BLE001 — per-tx fallback below
            pass
    parent_tx: dict[str, Any] = {}
    for txid, tx in parsed.items():
        addrs: list[str] = []
        complete = True
        for vin in tx.vin:
            prev = bytes(vin.txid).hex()
            if prev == _NULL_TXID:
                if COINBASE not in addrs:
                    addrs.append(COINBASE)
                continue
            if prev not in parent_tx:
                try:
                    parent_tx[prev] = Transaction.from_string(client.get_transaction_hex(prev))
                except Exception:  # noqa: BLE001
                    parent_tx[prev] = None
            ptx = parent_tx[prev]
            idx = int(vin.vout)
            if ptx is None or idx >= len(ptx.vout):
                complete = False
                continue
            addr = _vout_address(ptx.vout[idx].script_pubkey)
            if addr and addr not in addrs:
                addrs.append(addr)
        entry = tx_io_index.setdefault(
            txid,
            {"input_addresses": [], "output_addresses": [], "vin_count": len(tx.vin), "vout_count": len(tx.vout)},
        )
        entry["source_addresses"] = addrs
        entry["source_complete"] = complete
        if not entry.get("output_addresses"):
            entry["output_addresses"] = [
                a for a in (_vout_address(o.script_pubkey) for o in tx.vout) if a
            ]
    return len(parsed)


# Parent-fetch budget for following payments to foreign addresses (sweeps).
SWEEP_PARENT_BUDGET = 3000


def link_destination_sweeps(
    client: Any,
    destinations: Iterable[str],
    tx_io_index: dict[str, dict[str, Any]],
    own_addresses: set[str],
    *,
    max_parents: int = SWEEP_PARENT_BUDGET,
) -> list[list[str]]:
    """Link foreign destination addresses that belong to the same receiver.

    Exchanges hand out a fresh (HD-derived) deposit address per deposit and
    later sweep many of them in ONE tx into their main wallet. For every
    foreign address the cloud paid, look at the next txs of that address; a tx
    that SPENDS from it is a sweep. All its input addresses belong to the same
    owner (common-input heuristic); a sweep with a single output
    (consolidation) links that output too — it often is the exchange wallet
    that later pays out withdrawals.

    Returns groups of addresses to bundle (heuristic, RAM only, fail-open).
    """
    dests = [a for a in dict.fromkeys(destinations) if a and a not in own_addresses]
    if not dests or not hasattr(client, "get_histories"):
        return []
    hists = client.get_histories(dests)
    by_tx: dict[str, set[str]] = {}
    for addr, hist in hists.items():
        if not isinstance(hist, list):
            continue
        for item in hist:
            txid = getattr(item, "txid", "")
            if txid and txid not in tx_io_index:  # skip the cloud's own txs
                by_tx.setdefault(txid, set()).add(addr)
    if not by_tx:
        return []
    sweep_io: dict[str, dict[str, Any]] = {}
    resolve_source_addresses(client, sorted(by_tx), sweep_io, max_parents=max_parents)
    groups: list[list[str]] = []
    for txid, touched in by_tx.items():
        io = sweep_io.get(txid) or {}
        srcs = io.get("source_addresses") or []
        if not touched & set(srcs):
            continue  # only received there — not a spend from our destination
        group = [a for a in srcs if a != COINBASE and a not in own_addresses]
        outs = [a for a in io.get("output_addresses") or [] if a]
        if len(outs) == 1 and outs[0] not in own_addresses:
            group.append(outs[0])
        if len(group) > 1:
            groups.append(group)
    return groups


def _spk_bytes(address: str) -> bytes:
    return address_to_scriptpubkey(address).data


def _tx_hex_to_flows(
    tx_hex: str,
    *,
    address: str,
    wallet_id: int | None,
    height: int | None,
    block_time: str | None,
    owned_outpoints: dict[str, tuple[str, int]],
    tx_io_index: dict[str, dict[str, Any]] | None = None,
) -> list[Flow]:
    """Parse one raw tx into in/out flows for ``address``.

    ``owned_outpoints`` maps ``\"txid:vout\"`` → (address, amount_sats) for
    outputs we previously attributed to any of our addresses. Spent ones
    become outflows.

    Sets ``tx_total_output_sats`` on every produced flow so the tagger can
    net external payments vs change without treating change-only txs as
    fully internal incorrectly.
    """
    tx = Transaction.from_string(tx_hex)
    txid = tx.txid().hex()
    target_spk = _spk_bytes(address)
    flows: list[Flow] = []
    tx_total_output_sats = sum(int(vout.value) for vout in tx.vout)
    # Record outputs early (even if this address only receives later).
    _record_tx_io(tx_io_index, txid=txid, tx=tx)

    # Inflows: outputs paying our address
    for idx, vout in enumerate(tx.vout):
        if vout.script_pubkey.data == target_spk:
            amount = int(vout.value)
            flows.append(
                Flow(
                    txid=txid,
                    address=address,
                    direction="in",
                    amount_sats=amount,
                    wallet_id=wallet_id,
                    block_height=height,
                    block_time=block_time,
                    vout=idx,
                    tx_total_output_sats=tx_total_output_sats,
                )
            )
            owned_outpoints[f"{txid}:{idx}"] = (address, amount)

    # Outflows: inputs spending our previously seen outpoints.
    # Only pop when the outpoint belongs to *this* address — otherwise leave
    # it for the owner address's ingest pass (finding 3).
    # embit keeps vin.txid in display (big-endian) order.
    for vin_i, vin in enumerate(tx.vin):
        prev_txid = bytes(vin.txid).hex()
        key = f"{prev_txid}:{int(vin.vout)}"
        if key not in owned_outpoints:
            continue
        owned_addr, amount = owned_outpoints[key]
        if owned_addr != address:
            continue
        owned_outpoints.pop(key)
        flows.append(
            Flow(
                txid=txid,
                address=address,
                direction="out",
                amount_sats=amount,
                wallet_id=wallet_id,
                block_height=height,
                block_time=block_time,
                vin_index=vin_i,
                tx_total_output_sats=tx_total_output_sats,
                prev_txid=prev_txid,
                prev_vout=int(vin.vout),
            )
        )
        _record_tx_io(
            tx_io_index, txid=txid, tx=tx, known_input_address=address
        )

    return flows


def _block_time_from_verbose(verbose: dict[str, Any] | None) -> str | None:
    if not verbose or not isinstance(verbose, dict):
        return None
    ts = verbose.get("blocktime") or verbose.get("time")
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OSError):
        return None




def _normalize_history(history: list[Any]) -> list[TxHistoryItem]:
    items: list[TxHistoryItem] = []
    for h in history:
        if isinstance(h, TxHistoryItem):
            items.append(h)
        elif isinstance(h, dict):
            items.append(
                TxHistoryItem(
                    txid=str(h.get("tx_hash") or h.get("txid") or ""),
                    height=h.get("height"),
                )
            )
    return items

class TxIngestor:
    """Convert Electrum history into Flow objects. All state is caller-owned."""

    def ingest_tx(
        self,
        tx: dict[str, Any],
        *,
        wallet_id: int | None = None,
        address: str | None = None,
    ) -> list[Flow]:
        """Ingest a pre-parsed / verbose tx dict (tests / stubs).

        Expected keys: ``txid``, optional ``hex``, ``address``, ``direction``,
        ``amount_sats``, ``block_height``, ``block_time``.
        """
        if "hex" in tx and address:
            owned: dict[str, tuple[str, int]] = {}
            return _tx_hex_to_flows(
                str(tx["hex"]),
                address=address,
                wallet_id=wallet_id,
                height=tx.get("block_height") or tx.get("height"),
                block_time=tx.get("block_time"),
                owned_outpoints=owned,
            )
        # Simple dict → single flow (used by unit tests / stubs)
        if not tx.get("txid"):
            return []
        direction = str(tx.get("direction") or "in")
        amount = int(tx.get("amount_sats") or tx.get("value") or 0)
        addr = address or str(tx.get("address") or "")
        return [
            Flow(
                txid=str(tx["txid"]),
                address=addr,
                direction=direction,
                amount_sats=amount,
                wallet_id=wallet_id,
                block_height=tx.get("block_height") or tx.get("height"),
                block_time=tx.get("block_time"),
                vout=tx.get("vout"),
            )
        ]

    def ingest_history(
        self,
        address: str,
        history: list[Any],
        *,
        wallet_id: int | None = None,
        client: ElectrumClient | None = None,
        owned_outpoints: dict[str, tuple[str, int]] | None = None,
        fetch_txs: bool = True,
        on_progress: ProgressFn | None = None,
        txids_done: set[str] | None = None,
        tx_total: int | None = None,
        addresses_done: int | None = None,
        addresses_total: int | None = None,
        tx_io_index: dict[str, dict[str, Any]] | None = None,
    ) -> IngestResult:
        """Turn Electrum history items into flows.

        When ``client`` is provided and ``fetch_txs`` is True, each tx hex is
        fetched and parsed. Otherwise history-only stubs create zero-amount
        placeholder inflows (useful for dry runs).

        ``txids_done`` (optional shared set) tracks unique txids already counted
        toward ``tx_done`` progress across addresses.
        """
        result = IngestResult()
        owned = owned_outpoints if owned_outpoints is not None else {}
        done = txids_done if txids_done is not None else set()
        io_index = tx_io_index if tx_io_index is not None else result.tx_io
        items = _normalize_history(history)
        if not items:
            return result

        result.addresses_with_history.add(address)
        for item in items:
            if not item.txid:
                continue
            result.txids_seen.add(item.txid)
            if not fetch_txs or client is None:
                result.flows.append(
                    Flow(
                        txid=item.txid,
                        address=address,
                        direction="in",
                        amount_sats=0,
                        wallet_id=wallet_id,
                        block_height=item.height,
                    )
                )
                if item.txid not in done:
                    done.add(item.txid)
                    if on_progress is not None:
                        total = tx_total if tx_total is not None else len(done)
                        on_progress(
                            phase="ingest",
                            tx_done=len(done),
                            tx_total=total,
                            addresses_done=addresses_done,
                            addresses_total=addresses_total,
                            message=f"{len(done)} / {total} Tx verarbeitet",
                        )
                continue
            try:
                block_time = None
                verbose: dict[str, Any] | None = None
                try:
                    raw = client.get_transaction(item.txid, verbose=True)
                    if isinstance(raw, dict):
                        verbose = raw
                        block_time = _block_time_from_verbose(verbose)
                        tx_hex = str(raw.get("hex") or "")
                        if not tx_hex:
                            tx_hex = client.get_transaction_hex(item.txid)
                    else:
                        tx_hex = str(raw)
                except ElectrumError:
                    tx_hex = client.get_transaction_hex(item.txid)
                # electrs and others often omit verbose time — use block header.
                if block_time is None and item.height and item.height > 0:
                    try:
                        block_time = client.get_block_timestamp(item.height)
                    except ElectrumError:
                        block_time = None
                flows = _tx_hex_to_flows(
                    tx_hex,
                    address=address,
                    wallet_id=wallet_id,
                    height=item.height,
                    block_time=block_time,
                    owned_outpoints=owned,
                    tx_io_index=io_index,
                )
                result.flows.extend(flows)
                if item.txid not in done:
                    done.add(item.txid)
                    if on_progress is not None:
                        total = tx_total if tx_total is not None else len(done)
                        on_progress(
                            phase="ingest",
                            tx_done=len(done),
                            tx_total=total,
                            addresses_done=addresses_done,
                            addresses_total=addresses_total,
                            message=f"{len(done)} / {total} Tx verarbeitet",
                        )
            except ElectrumError as exc:
                result.errors.append(f"{item.txid}: {exc}")
            except Exception as exc:  # noqa: BLE001 — keep sync going
                result.errors.append(f"{item.txid}: parse error: {exc}")
        if tx_io_index is not None:
            result.tx_io = tx_io_index
        else:
            result.tx_io = io_index
        return result

    def ingest_addresses(
        self,
        addresses: Iterable[str],
        client: ElectrumClient,
        *,
        wallet_id: int | None = None,
        on_progress: ProgressFn | None = None,
        addresses_total_hint: int | None = None,
        txids_done: set[str] | None = None,
        discovered_txids: set[str] | None = None,
        known_tx_total: int | None = None,
        tx_io_index: dict[str, dict[str, Any]] | None = None,
    ) -> IngestResult:
        """Query Electrum for each address and ingest histories (two-pass).

        Pass A: gather histories → know unique ``tx_total``.
        Pass B: fetch/parse each history item → bump unique ``tx_done``.

        ``discovered_txids`` / ``txids_done`` may be shared across wallets so
        unique totals accumulate for the whole sync.
        """
        addr_list = list(addresses)
        total_addrs = (
            addresses_total_hint
            if addresses_total_hint is not None
            else len(addr_list)
        )
        merged = IngestResult()
        owned: dict[str, tuple[str, int]] = {}
        done = txids_done if txids_done is not None else set()
        discovered = discovered_txids if discovered_txids is not None else set()
        io_index = tx_io_index if tx_io_index is not None else merged.tx_io

        # --- Pass A: histories ---
        histories: list[tuple[str, list[TxHistoryItem]]] = []

        for i, address in enumerate(addr_list):
            try:
                history = client.get_history(address)
            except ElectrumError as exc:
                merged.errors.append(f"{address}: {exc}")
                discovered_total = (
                    known_tx_total
                    if known_tx_total is not None
                    else len(discovered)
                )
                if on_progress is not None:
                    on_progress(
                        phase="history",
                        addresses_done=i + 1,
                        addresses_total=total_addrs,
                        tx_done=len(done),
                        tx_total=discovered_total,
                        message=(
                            f"Adressen {i + 1}/{total_addrs} …"
                            if discovered_total == 0
                            else f"{len(done)} / {discovered_total} Tx verarbeitet"
                        ),
                    )
                continue

            items = _normalize_history(history or [])
            if known_tx_total is None:
                for it in items:
                    if it.txid:
                        discovered.add(it.txid)
            discovered_total = (
                known_tx_total
                if known_tx_total is not None
                else len(discovered)
            )
            if on_progress is not None:
                on_progress(
                    phase="history",
                    addresses_done=i + 1,
                    addresses_total=total_addrs,
                    tx_done=len(done),
                    tx_total=discovered_total,
                    message=(
                        f"Adressen {i + 1}/{total_addrs} …"
                        if discovered_total == 0
                        else f"{len(done)} / {discovered_total} Tx verarbeitet"
                    ),
                )

            if not items:
                continue
            histories.append((address, items))

        tx_total = (
            known_tx_total if known_tx_total is not None else len(discovered)
        )
        if on_progress is not None and tx_total > 0:
            on_progress(
                phase="ingest",
                addresses_done=total_addrs,
                addresses_total=total_addrs,
                tx_done=len(done),
                tx_total=tx_total,
                message=f"{len(done)} / {tx_total} Tx verarbeitet",
            )

        # --- Pass B: fetch + parse ---
        for address, items in histories:
            part = self.ingest_history(
                address,
                items,
                wallet_id=wallet_id,
                client=client,
                owned_outpoints=owned,
                fetch_txs=True,
                on_progress=on_progress,
                txids_done=done,
                tx_total=tx_total,
                addresses_done=total_addrs,
                addresses_total=total_addrs,
                tx_io_index=io_index,
            )
            merged.flows.extend(part.flows)
            merged.txids_seen |= part.txids_seen
            merged.addresses_with_history |= part.addresses_with_history
            merged.errors.extend(part.errors)
        merged.tx_io = io_index
        return merged
