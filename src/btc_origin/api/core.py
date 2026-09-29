"""Gemeinsamer Sitzungszustand und Hilfsfunktionen der API-Router."""

from __future__ import annotations


from datetime import date
from typing import Any, Callable


from btc_origin.regelwerk import RegelwerkFehler
from btc_origin.chain_cache import ChainCache
from btc_origin.external_book import ExternalBook
from btc_origin.exchange_sales import (
    ExchangeReport,
    match_withdrawals,
    replay_exchanges,
)
from btc_origin.local_files import (
    LocalData,
    load_local_data,
    match_amount_rows,
)
from btc_origin.db import (
    connect,
    init_schema,
    list_flows,
)
from btc_origin.electrum_client import (
    ElectrumClient,
)
from btc_origin.enrichment import (
    SessionEnricher,
    flows_from_db_rows,
    own_addresses_from_db,
)
from btc_origin.holding_clock import (
    HoldingClock,
    cloud_flows_from_lot_result,
)
from btc_origin.label_service import LabelService
from btc_origin.price_oracle import (
    PriceOracle,
    days_from,
    usd_per_eur_on,
)
from btc_origin.report_builder import ReportBuilder
from btc_origin.sync_progress import SyncProgressTracker
from btc_origin.wallet_registry import WalletRegistry


class AppState:
    """Process-lifetime RAM state — discarded on shutdown."""

    def __init__(self) -> None:
        self.registry = WalletRegistry()
        self.oracle = PriceOracle()
        self.labels = LabelService()
        self.clock = HoldingClock()
        self.enricher = SessionEnricher(labels=self.labels, clock=self.clock)
        self.reports = ReportBuilder(oracle=self.oracle, holding_clock=self.clock)
        self.db = connect()  # :memory: only
        init_schema(self.db)
        self.last_sync: dict[str, Any] | None = None
        self.last_enrichment: dict[str, Any] | None = None
        self.last_ownership: dict[str, Any] | None = None
        self.last_tx_io: dict[str, dict[str, Any]] = {}
        self.sync_progress = SyncProgressTracker()
        # Optional injected Electrum client (tests); None → real client on sync.
        self.electrum_client: ElectrumClient | None = None
        self.trace_client: Any | None = None  # optional injected for /api/trace
        # Session-RAM Electrum cache shared by sync + trace (never on disk).
        self.chain_cache = ChainCache()
        # User names for external destination addresses (CSV import; RAM only).
        self.external_book = ExternalBook()
        self.last_ext_links: list[list[str]] = []
        # Kennzahlen der zuletzt erzeugten PP-Delta-Datei (für die Diagnose; RAM only).
        self.pp_last_delta: dict[str, int] | None = None
        # Read-only user files in local/ (git-ignored): names + exchange exports.
        self.local = LocalData()
        # Purchases from exchange exports (Rz. 20): recomputed when flows or
        # local files change; documented per exchange in the report.
        self.exchange_key: tuple | None = None
        self.exchange_reports: dict[str, ExchangeReport] = {}
        # Withdrawals whose fee was charged on top of the exported amount.
        self.exchange_net_fee: set[tuple[str, date, int]] = set()
        self.exchange_withdrawals: list[Any] = []  # Diagnose (Matching)
        # Jahressteuerreport: Herkunftsnachweise dieser Sitzung mit „bis“ (bis-Tag → Stand,
        # Stichtag, Build) für den Anschlussvermerk (RAM only)
        self.herkunft_runs: dict[str, Any] = {}
        reload_local_files(self)


def reload_local_files(state: AppState) -> LocalData:
    """(Re-)read local/externe-adressen.csv + local/boersen/*. Never writes."""
    try:
        state.local = load_local_data()
    except RegelwerkFehler:
        raise  # kategorien.yaml fehlerhaft → klare Meldung statt still ohne Export-Daten
    except Exception as exc:  # noqa: BLE001 — optional convenience
        state.local = LocalData(errors=[str(exc)])
    _convert_usd_trades(state.local)
    state.external_book.set_local_names(state.local.names)
    state.exchange_key = None  # re-match exchange purchases
    return state.local


def _convert_usd_trades(local: LocalData) -> None:
    """USD-Beträge (BitGo) → EUR zum EZB-Referenzkurs des Tages."""
    usd = [t for t in local.trades if t.usd is not None and t.eur is None]
    if not usd:
        return
    try:
        rates = usd_per_eur_on(sorted({t.day for t in usd}))
    except Exception:  # noqa: BLE001 — fail-open
        rates = {}
    missing = 0
    for t in usd:
        rate = rates.get(t.day)
        if rate:
            t.eur = round(t.usd / rate, 2)
        else:
            missing += 1
    if missing:
        local.errors.append(f"{missing} USD-Beträge ohne EZB-Kurs — Kaufpreis fehlt")


def _flow_days(rows: list[dict[str, Any]]) -> list[date]:
    return days_from(r.get("block_time") or r.get("lot_date") for r in rows)


def _address_wallet_name_maps(state: AppState) -> tuple[dict[int, str], dict[str, str]]:
    """Map session wallet id / address → display name (registry + DB)."""
    by_id: dict[int, str] = {}
    by_addr: dict[str, str] = {}
    for w in state.registry.list_wallets():
        if w.id is not None:
            by_id[int(w.id)] = w.name
        if w.address:
            by_addr[str(w.address)] = w.name
    try:
        rows = state.db.execute(
            "SELECT a.address, w.id, w.name "
            "FROM addresses a JOIN wallets w ON w.id = a.wallet_id"
        ).fetchall()
    except Exception:  # noqa: BLE001 — empty/mid-init schema
        rows = []
    for r in rows:
        addr = str(r[0] or "")
        wid = r[1]
        name = str(r[2] or "")
        if not name:
            continue
        if wid is not None and int(wid) not in by_id:
            by_id[int(wid)] = name
        if addr and addr not in by_addr:
            by_addr[addr] = name
    return by_id, by_addr


def _export_txid_names(state: AppState, shaped: list[dict[str, Any]]) -> dict[str, str]:
    """Exchange-export names per txid: exact TxID hits win over date+amount."""
    by_amount = match_amount_rows(state.local.amount_rows, shaped) if state.local.amount_rows else {}
    return {**by_amount, **state.local.txid_names}


def _price_eur(state: AppState) -> Callable[[date], float | None]:
    def price(day: date) -> float | None:
        ref = state.oracle.get_acquisition_reference(day)
        return float(ref.eur) if ref.available and ref.eur is not None else None

    return price


def _ensure_exchange_acquisitions(state: AppState, rows: list[dict[str, Any]]) -> None:
    """Kauf-Seite aus Börsen-Exporten (BMF 06.03.2025 Rz. 20): withdrawals of
    the exports matched to Cloud-Eintritte give those entries the purchase
    dates and prices. Two passes: lots without overrides → replay exchanges →
    match → overrides on the clock. Cached until flows or local files change."""
    trades = state.local.trades
    key = (
        len(rows),
        sum(int(r.get("amount_sats") or 0) for r in rows),
        id(state.local),
        len(trades),
    )
    if state.exchange_key == key:
        return
    state.exchange_key = key  # set first: the pass below re-enters shaping
    state.clock.acquisition_overrides = {}
    state.exchange_reports = {}
    state.exchange_net_fee = set()
    state.exchange_withdrawals = []
    if not trades or not rows:
        return
    shaped = _shaped_cloud_flows(state, rows, include_fees=True)
    outs = [r for r in shaped if r["direction"] == "out"]
    _rows, withdrawals, reports = replay_exchanges(outs, trades, _price_eur(state))
    state.clock.acquisition_overrides = match_withdrawals(
        [r for r in shaped if r["direction"] == "in"], withdrawals, reports
    )
    state.exchange_reports = reports
    state.exchange_withdrawals = withdrawals
    state.exchange_net_fee = {
        (w.exchange, w.day, w.sats) for r in reports.values() for w in r.matched if w.net_fee
    }


def _shaped_cloud_flows(
    state: AppState, rows: list[dict[str, Any]], *, include_fees: bool = False
) -> list[dict[str, Any]]:
    """Cloud-Flows rows (no prices) with ext-NNN / user names on OUT rows.

    ``include_fees`` adds OUT rows ``kind == "fee"`` (network fees per coin) —
    needed for Werbungskosten (Rz. 59); they never count as counterparties.
    """
    if not rows:
        return []
    _ensure_exchange_acquisitions(state, rows)
    by_id, by_addr = _address_wallet_name_maps(state)
    own_addrs = set(by_addr.keys()) | own_addresses_from_db(state.db)
    lot_result = state.clock.apply_fifo_lots(flows_from_db_rows(rows))
    shaped = cloud_flows_from_lot_result(
        lot_result,
        wallet_names=by_id,
        threshold_days=state.clock.threshold_days,
        own_addresses=own_addrs,
        tx_io=state.last_tx_io or None,
        include_fees=include_fees,
    )
    # Ursprung of each Cloud-Eintritt: sender addresses of the entry tx.
    tx_io = state.last_tx_io or {}
    for r in shaped:
        if r["direction"] != "in":
            continue
        io = tx_io.get(str(r.get("txid") or "")) or {}
        r["source_addresses"] = [
            a for a in (io.get("source_addresses") or []) if a not in own_addrs
        ]
        r["source_complete"] = bool(io.get("source_complete", False))
        r["source_coinbase"] = "coinbase" in (io.get("source_addresses") or [])
        r["entry_vout_count"] = int(io.get("vout_count") or 0)
    entries = state.external_book.build(
        (r for r in shaped if r["direction"] == "out" and r.get("kind") != "fee"),
        (r for r in shaped if r["direction"] == "in"),
        links=state.last_ext_links,
        label_lookup=lambda a: (state.labels.lookup_address(a) or (None, None))[1],
        txid_names=_export_txid_names(state, shaped),
        date_rules=state.local.date_rules,
    )
    names = {a: e.name for e in entries for a in (e.addresses or [e.address])}
    declared = {a for e in entries if e.declared for a in (e.addresses or [e.address])}
    for r in shaped:
        if r["direction"] == "out" and r.get("address") in names:
            r["external_name"] = names[r["address"]]
            r["address_display"] = f"{names[r['address']]} ({r['address']})"
            r["external_declared"] = r["address"] in declared
        elif r["direction"] == "in":
            srcs = [a for a in r.get("source_addresses") or [] if a != "coinbase"]
            if srcs and srcs[0] in names:
                r["source_name"] = names[srcs[0]]
                r["source_declared"] = srcs[0] in declared
            src = str(r.get("acq_source") or "")
            if src.startswith("Kauf auf ") and src.endswith(" lt. Export"):
                # Purchase receipt of an exchange beats any other name.
                r["source_name"] = src[len("Kauf auf ") : -len(" lt. Export")]
                r["source_declared"] = False
    return shaped


def _external_entries(state: AppState) -> list[Any]:
    shaped = _shaped_cloud_flows(state, list_flows(state.db))
    return state.external_book.build(
        (r for r in shaped if r["direction"] == "out" and r.get("kind") != "fee"),
        (r for r in shaped if r["direction"] == "in"),
        links=state.last_ext_links,
        label_lookup=lambda a: (state.labels.lookup_address(a) or (None, None))[1],
        txid_names=_export_txid_names(state, shaped),
        date_rules=state.local.date_rules,
    )


class _ElectrumChain:
    """Adapter Electrum-Client → Spurensuche (nur lesend, Cache der Sitzung)."""

    def __init__(self, client: Any) -> None:
        self.c = client

    def history(self, address: str) -> list[tuple[str, int | None]]:
        return [(h.txid, h.height) for h in self.c.get_history(address)]

    def tx_hex(self, txid: str) -> str:
        return self.c.get_transaction_hex(txid)

    def block_time(self, height: int | None) -> str | None:
        return self.c.get_block_timestamp(height) if height and height > 0 else None


def _session() -> AppState:
    """Sitzungszustand der laufenden App (``app.state.session``)."""
    from btc_origin.api.app import app  # spät importiert: app bindet die Router

    return app.state.session
