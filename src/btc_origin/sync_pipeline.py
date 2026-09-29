"""End-to-end in-memory sync: xpub → derive → Electrum → ingest → ledger → DB.

Hard Boundary: never writes to disk. SQLite connection must be ``:memory:``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from btc_origin.chain_cache import ChainCache
from btc_origin.config import get_settings
from btc_origin.db import persist_ledger, persist_wallet_addresses
from btc_origin.electrum_client import (
    ElectrumClient,
    ElectrumConnectionError,
    ElectrumError,
    electrum_server_candidates,
    try_connect_first_available,
)
from btc_origin.hd_deriver import SCRIPT_TYPE_LABELS, DerivedAddress, HdDeriver
from btc_origin.merger import Ledger, Merger
from btc_origin.enrichment import SessionEnricher
from btc_origin.tx_ingestor import (
    Flow,
    ProgressFn,
    TxIngestor,
    link_destination_sweeps,
    resolve_source_addresses,
)
from btc_origin.wallet_registry import WalletEntry, WalletRegistry


@dataclass
class SyncSummary:
    status: str
    wallets: int = 0
    addresses_derived: int = 0
    addresses_with_history: int = 0
    txids: int = 0
    flows: int = 0
    inflows: int = 0
    outflows: int = 0
    internal_count: int = 0
    labels_applied: int = 0
    haltefrist_qualified: int = 0
    enrichment: dict[str, Any] | None = None
    ownership: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    electrum: dict[str, Any] | None = None
    ephemeral: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "wallets": self.wallets,
            "addresses_derived": self.addresses_derived,
            "addresses_with_history": self.addresses_with_history,
            "txids": self.txids,
            "flows": self.flows,
            "inflows": self.inflows,
            "outflows": self.outflows,
            "internal_count": self.internal_count,
            "labels_applied": self.labels_applied,
            "haltefrist_qualified": self.haltefrist_qualified,
            "enrichment": self.enrichment,
            "ownership": self.ownership,
            "errors": self.errors,
            "notes": self.notes,
            "electrum": self.electrum,
            "ephemeral": self.ephemeral,
            "persistence": "memory_only",
        }


# Max. address index scanned per chain (safety net against endless scans).
SCAN_HARD_CAP = 100_000
# Eine einzelne eigene Adresse hat selten mehr als ein paar Dutzend Vorgänge; darüber ist es
# fast immer eine Adresse einer Börse oder eines Dienstes (fremde Transaktionen, langsamer Sync).
ADDRESS_WALLET_MAX_TXS = 100


def _has_history(h: object) -> bool:
    """True for a non-empty history list (errors / None count as unused)."""
    return isinstance(h, list) and len(h) > 0


class SyncPipeline:
    """Orchestrate HD derive + Electrum + ingest + merge into session RAM/DB."""

    def __init__(
        self,
        registry: WalletRegistry,
        db: sqlite3.Connection,
        *,
        deriver: HdDeriver | None = None,
        client: ElectrumClient | None = None,
        ingestor: TxIngestor | None = None,
        merger: Merger | None = None,
        enricher: SessionEnricher | None = None,
        cache: ChainCache | None = None,
    ) -> None:
        self.registry = registry
        # Session-RAM chain cache (tx hex / headers for the whole session,
        # address histories per run). Always on; the app passes its session
        # cache so trace and repeated syncs share it.
        self.cache = cache if cache is not None else ChainCache()
        self.db = db
        self.deriver = deriver or HdDeriver()
        self.client = client
        self.ingestor = ingestor or TxIngestor()
        self.merger = merger or Merger()
        self.enricher = enricher or SessionEnricher()
        self.last_ledger: Ledger | None = None
        self.last_enrichment = None
        self.last_ownership: dict[str, Any] | None = None
        self.last_tx_io: dict[str, dict[str, Any]] = {}
        # Address groups of the same foreign receiver (deposit sweeps).
        self.last_ext_links: list[list[str]] = []

    def run(
        self,
        *,
        connect: bool = True,
        on_progress: ProgressFn | None = None,
    ) -> SyncSummary:
        settings = get_settings()
        wallets = self.registry.list_wallets()

        def prog(**kwargs: Any) -> None:
            if on_progress is not None:
                on_progress(**kwargs)

        if not wallets:
            prog(phase="done", running=False, message="Keine Wallets")
            return SyncSummary(
                status="empty",
                notes=["No wallets registered — paste an xpub first."],
            )

        client = self.client
        owns_client = False
        electrum_info: dict[str, Any] | None = None
        errors: list[str] = []
        notes: list[str] = []

        prog(phase="connecting", message="Electrum …", running=True)

        self._scan_capped = False
        if self.cache is not None:
            # Histories may have changed since the last run; txs/headers stay.
            self.cache.begin_run()
            if client is not None and client.cache is None:
                client.cache = self.cache

        if client is None and connect:
            owns_client = True
            try:
                # Prefer configured host, then failover across DEFAULT_ELECTRUM_SERVERS.
                client = try_connect_first_available(
                    electrum_server_candidates(),
                    timeout=8.0,
                    cache=self.cache,
                )
                electrum_info = {
                    "host": client.host,
                    "port": client.port,
                    "ssl": client.use_ssl,
                    "version": client._server_version,
                    "failover": True,
                }
            except ElectrumConnectionError as exc:
                if client is not None:
                    client.close()
                    client = None
                prog(phase="error", running=False, message="Electrum unerreichbar")
                return SyncSummary(
                    status="electrum_unreachable",
                    wallets=len(wallets),
                    errors=[str(exc)],
                    notes=[
                        "Electrum server unreachable (configured host + public "
                        "failover list). Set ELECTRUM_HOST/PORT or retry later. "
                        "Public servers may block some networks. "
                        "Session data stays in memory only."
                    ],
                    electrum={
                        "host": settings.electrum_host,
                        "port": settings.electrum_port,
                        "ssl": settings.electrum_ssl,
                        "failover": True,
                    },
                )

        if client is None:
            prog(phase="error", running=False, message="Kein Electrum-Client")
            return SyncSummary(
                status="error",
                wallets=len(wallets),
                errors=["No Electrum client available"],
            )

        # Surface connected host even when a mock/injected client is used.
        if electrum_info is None:
            electrum_info = {
                "host": client.host,
                "port": client.port,
                "ssl": client.use_ssl,
                "version": getattr(client, "_server_version", None),
            }

        flows_by_wallet: dict[int, list[Flow]] = {}
        all_derived: list[DerivedAddress] = []
        addrs_with_hist: set[str] = set()
        txids: set[str] = set()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Shared across wallets so unique tx counts accumulate.
        shared_txids_done: set[str] = set()
        shared_discovered: set[str] = set()
        shared_tx_io: dict[str, dict[str, Any]] = {}
        wallet_jobs: list[tuple[int, list[str]]] = []

        try:
            # --- Derive all wallets first → addresses_total ---
            prog(phase="deriving", message="Adressen ableiten …")
            for wallet in wallets:
                wid = wallet.id if wallet.id is not None else 0
                derived, used, active_types = self._derive_for_wallet(
                    wallet, client
                )
                if active_types and wallet.xpub and wallet.xpub.strip().lower()[:4] in (
                    "xpub",
                    "tpub",
                ):
                    notes.append(
                        f"Wallet „{wallet.name}“: Adresstyp erkannt — "
                        + ", ".join(SCRIPT_TYPE_LABELS.get(t, t) for t in active_types)
                        + "."
                    )
                if wallet.kind != "address" and not used:
                    notes.append(
                        f"Achtung: Wallet „{wallet.name}“ hat keine Transaktionen "
                        "(alle Adresstypen geprüft). Schlüssel prüfen — fehlt eine "
                        "Wallet, erscheinen Umbuchungen aus ihr als Cloud-Eintritt."
                    )
                all_derived.extend(derived)
                persist_wallet_addresses(self.db, wallet, derived, created_at=now)

                if wallet.kind == "address" and wallet.address:
                    addr_list = [wallet.address]
                    try:
                        hist = client.get_histories([wallet.address]).get(wallet.address)
                    except ElectrumConnectionError:
                        raise
                    except Exception:  # noqa: BLE001 — Prüfung ist nur ein Schutz
                        hist = None
                    n_tx = len(hist) if isinstance(hist, list) else 0
                    if n_tx > ADDRESS_WALLET_MAX_TXS:
                        notes.append(
                            f"Wallet „{wallet.name}“ nicht synchronisiert: die Adresse hat {n_tx} "
                            "Transaktionen — das ist fast sicher eine Adresse einer Börse oder eines "
                            "Dienstes, keine eigene Wallet. Wallet entfernen und die Adresse stattdessen "
                            "in local/externe-adressen.csv benennen."
                        )
                        continue
                else:
                    addr_list = [d.address for d in derived]
                wallet_jobs.append((wid, addr_list))
                prog(
                    phase="deriving",
                    addresses_total=sum(len(a) for _, a in wallet_jobs),
                    addresses_done=0,
                    message=(
                        f"Adressen 0/{sum(len(a) for _, a in wallet_jobs)} …"
                    ),
                )
                _ = used

            addresses_total = sum(len(a) for _, a in wallet_jobs)
            prog(
                phase="history",
                addresses_total=addresses_total,
                addresses_done=0,
                message=(
                    f"Adressen 0/{addresses_total} …"
                    if addresses_total
                    else "Electrum …"
                ),
            )

            # --- Prefetch: all txs + block times in pipelined batches ---
            # The ingest pass below then reads from the session cache instead
            # of one sequential round-trip per tx / header. Fail-open.
            try:
                all_addrs = [a for _, addrs in wallet_jobs for a in addrs]
                prog(phase="history", message="Transaktionen laden …")
                hists = client.get_histories(all_addrs)
                items = [
                    it
                    for h in hists.values()
                    if isinstance(h, list)
                    for it in h
                ]
                client.prefetch_transactions([it.txid for it in items])
                client.prefetch_block_times(
                    [it.height for it in items if it.height and it.height > 0]
                )
            except ElectrumConnectionError:
                raise
            except Exception as exc:  # noqa: BLE001 — ingest falls back per tx
                notes.append(f"Vorladen übersprungen: {exc}")

            # --- History + ingest per wallet (two-pass inside ingestor) ---
            addresses_done_global = 0
            for wid, addr_list in wallet_jobs:
                def _wallet_progress(
                    *,
                    _base_done: int = addresses_done_global,
                    **kwargs: Any,
                ) -> None:
                    local_done = kwargs.get("addresses_done")
                    if local_done is not None:
                        kwargs["addresses_done"] = _base_done + int(local_done)
                    kwargs["addresses_total"] = addresses_total
                    tx_d = int(kwargs.get("tx_done") or 0)
                    tx_t = int(kwargs.get("tx_total") or 0)
                    a_d = int(kwargs.get("addresses_done") or 0)
                    if tx_t > 0 or tx_d > 0:
                        kwargs["message"] = f"{tx_d} / {tx_t} Tx verarbeitet"
                    else:
                        kwargs["message"] = f"Adressen {a_d}/{addresses_total} …"
                    prog(**kwargs)

                result = self.ingestor.ingest_addresses(
                    addr_list,
                    client,
                    wallet_id=wid,
                    on_progress=_wallet_progress,
                    addresses_total_hint=len(addr_list),
                    txids_done=shared_txids_done,
                    discovered_txids=shared_discovered,
                    tx_io_index=shared_tx_io,
                )
                addresses_done_global += len(addr_list)
                flows_by_wallet[wid] = result.flows
                addrs_with_hist |= result.addresses_with_history
                txids |= result.txids_seen
                errors.extend(result.errors)
        except ElectrumError as exc:
            errors.append(str(exc))
            prog(phase="error", message="Electrum-Fehler")
        finally:
            if owns_client and client is not None:
                client.close()

        prog(phase="enrich", message="Anreichern …")
        ledger = self.merger.merge(
            flows_by_wallet, warn_threshold=settings.large_wallet_warn_threshold
        )
        notes.extend(ledger.notes)
        if getattr(self, "_scan_capped", False):
            notes.append(
                f"Achtung: Adresssuche an der Sicherheitsgrenze ({SCAN_HARD_CAP} je "
                "Kette) abgebrochen — es können Adressen fehlen."
            )
        if self.cache is not None:
            st = self.cache.as_dict()
            notes.append(
                f"Chain-Cache (RAM): {st['hits']} Abfragen aus Cache, "
                f"{st['transactions']} Tx / {st['headers']} Header gemerkt."
            )
        persist_ledger(self.db, ledger)
        self.last_ledger = ledger

        # Sender addresses of incoming txs (for the „Ursprung“ column):
        # parents batched through the session cache. Fail-open.
        if client is not None:
            try:
                prog(phase="enrich", message="Absender ermitteln …")
                spend_txids = {f.txid for f in ledger.flows if f.direction == "out"}
                resolve_source_addresses(
                    client,
                    [
                        f.txid
                        for f in ledger.flows
                        if f.direction == "in" and f.txid not in spend_txids
                    ],
                    shared_tx_io,
                )
            except Exception as exc:  # noqa: BLE001 — optional detail
                notes.append(f"Absender nicht ermittelt: {exc}")
            # Follow payments to foreign addresses: exchange deposit addresses
            # swept together belong to one receiver (bundled as one ext-NNN).
            try:
                prog(phase="enrich", message="Empfänger bündeln …")
                own = {d.address for d in all_derived} | {
                    a for _, addrs in wallet_jobs for a in addrs
                }
                dests = [
                    a
                    for txid in spend_txids
                    for a in (shared_tx_io.get(txid) or {}).get("output_addresses") or []
                    if a not in own
                ]
                self.last_ext_links = link_destination_sweeps(
                    client, dests, shared_tx_io, own
                )
            except Exception as exc:  # noqa: BLE001 — optional detail
                notes.append(f"Empfänger nicht gebündelt: {exc}")

        # M2 post-sync enrichment: ownership inference → tags → labels → FIFO
        self.last_tx_io = shared_tx_io
        enrichment_payload = None
        ownership_payload = None
        internal_count = 0
        labels_applied = 0
        haltefrist_qualified = 0
        try:
            enr = self.enricher.enrich(self.db, tx_graph=shared_tx_io or None)
            self.last_enrichment = enr
            enrichment_payload = enr.as_dict()
            if enr.ownership is not None:
                ownership_payload = enr.ownership.as_dict(include_full=True)
                self.last_ownership = ownership_payload
            internal_count = enr.internal_count
            labels_applied = enr.labels_applied
            haltefrist_qualified = enr.haltefrist_qualified
            notes.extend(enr.notes)
        except Exception as exc:  # noqa: BLE001 — sync result still useful
            errors.append(f"enrichment: {exc}")
            notes.append("Enrichment failed; ledger remains in session memory.")

        status = "ok" if not errors else "ok_with_errors"
        if not ledger.flows and errors:
            status = "error"

        final_tx = len(txids)
        n_addrs = sum(len(a) for _, a in wallet_jobs)
        prog(
            phase="done" if status != "error" else "error",
            running=False,
            tx_done=len(shared_txids_done) or final_tx,
            tx_total=final_tx,
            addresses_done=n_addrs,
            addresses_total=n_addrs,
            message=(
                f"{len(shared_txids_done) or final_tx} / {final_tx} Tx verarbeitet"
                if final_tx
                else ("Sync fertig" if status != "error" else "Sync-Fehler")
            ),
        )

        return SyncSummary(
            status=status,
            wallets=len(wallets),
            addresses_derived=len(all_derived),
            addresses_with_history=len(addrs_with_hist),
            txids=len(txids),
            flows=len(ledger.flows),
            inflows=ledger.inflow_count,
            outflows=ledger.outflow_count,
            internal_count=internal_count,
            labels_applied=labels_applied,
            haltefrist_qualified=haltefrist_qualified,
            enrichment=enrichment_payload,
            ownership=ownership_payload,
            errors=errors,
            notes=notes,
            electrum=electrum_info,
        )

    def _derive_for_wallet(
        self, wallet: WalletEntry, client: ElectrumClient
    ) -> tuple[list[DerivedAddress], set[str], list[str]]:
        """Derive addresses until a real gap of unused addresses per chain.

        Histories are fetched in pipelined batches (one round-trip per window
        of ``gap_limit`` addresses) and cached per run, so the ingest pass does
        not query them again. For a plain ``xpub`` the script type is unknown:
        the first window of every candidate type is probed in one batch, and
        only types with history are scanned further.

        Returns ``(derived, used, script_types_with_history)``.
        """
        if wallet.kind == "address" and wallet.address:
            return [], set(), []
        if not wallet.xpub:
            return [], set(), []

        gap = max(1, int(self.deriver.gap_limit))
        candidates = self.deriver.candidate_script_types(wallet.xpub)
        if len(candidates) > 1:
            windows = {
                t: self.deriver.derive_receive(wallet.xpub, count=gap, script_type=t)
                + self.deriver.derive_change(wallet.xpub, count=gap, script_type=t)
                for t in candidates
            }
            hist = client.get_histories(
                [d.address for t in candidates for d in windows[t]]
            )
            active = [
                t
                for t in candidates
                if any(_has_history(hist.get(d.address)) for d in windows[t])
            ]
            if not active:
                # Empty wallet: keep the most likely type so its addresses are known.
                return windows[candidates[0]], set(), []
        else:
            active = candidates

        out_derived: list[DerivedAddress] = []
        out_used: set[str] = set()
        for t in active:
            for is_change in (False, True):
                d, u = self._scan_chain(
                    wallet.xpub, client, is_change=is_change, script_type=t
                )
                out_derived.extend(d)
                out_used |= u
        return out_derived, out_used, active

    def _scan_chain(
        self,
        xpub: str,
        client: ElectrumClient,
        *,
        is_change: bool,
        script_type: str,
    ) -> tuple[list[DerivedAddress], set[str]]:
        """Scan one chain window-by-window until ``gap_limit`` unused in a row."""
        gap = max(1, int(self.deriver.gap_limit))
        # Safety net only (the gap rule ends every real scan); if ever hit it
        # is reported, never silently truncated.
        hard_cap = SCAN_HARD_CAP
        derived: list[DerivedAddress] = []
        used: set[str] = set()
        start = 0
        last_used = -1
        while start <= hard_cap:
            window = self.deriver._derive_range(
                xpub, is_change=is_change, start=start, count=gap, script_type=script_type
            )
            hist = client.get_histories([d.address for d in window])
            for d in window:
                derived.append(d)
                if _has_history(hist.get(d.address)):
                    used.add(d.address)
                    last_used = d.index
            start += gap
            if start - (last_used + 1) >= gap:
                break
        else:
            self._scan_capped = True
        return derived, used
