"""Wallets, Sync, Electrum-Server und Sitzung."""

from __future__ import annotations


from typing import Any

from pydantic import BaseModel, Field

from btc_origin import __version__
from btc_origin.config import get_settings
from btc_origin.db import (
    clear_session_data,
    is_memory_only,
    list_flows,
)
from btc_origin.electrum_client import (
    ElectrumClient,
    probe_electrum_status,
)
from btc_origin.sync_pipeline import SyncPipeline
from fastapi import APIRouter

from btc_origin.api.core import (
    _flow_days,
    _session,
    reload_local_files,
)

router = APIRouter()


class WalletCreate(BaseModel):
    name: str = Field(..., min_length=1)
    xpub: str | None = None
    address: str | None = None


class WalletBatchCreate(BaseModel):
    """Register any number of xpubs — no hard cap. Paste-only inputs.

    Prefer structured ``items``, or pass multi-line ``paste`` with optional
    ``Name\\tXPub`` (literal TAB) lines. ``default_name`` applies when a line
    has no TAB name (or an empty name after the TAB).
    """

    items: list[WalletCreate] = Field(default_factory=list)
    paste: str | None = None
    default_name: str | None = None


@router.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "product": "BTC-Herkunft",
        "package": "btc_origin",
        "persistence": "memory_only",
        "sqlite_memory": is_memory_only(),
        "input": "paste_only",
        "reports": "explicit_request_only",
        "milestone": "M5",
        "features": [
            "internal_transfer_tagger",
            "ownership_inference",
            "trace_engine",
            "label_service",
            "holding_clock",
            "report_builder",
            "localhost_ui",
        ],
    }


@router.get("/api/wallets")
def list_wallets() -> dict[str, Any]:
    reg = _session().registry
    wallets = [
        {
            "id": w.id,
            "name": w.name,
            "kind": w.kind,
            "xpub": w.xpub,
            "address": w.address,
        }
        for w in reg.list_wallets()
    ]
    warning = None
    threshold = get_settings().large_wallet_warn_threshold
    n = reg.count()
    if n >= threshold:
        warning = (
            f"{n} wallets registered — sync may take longer; "
            "no hard limit, processing continues."
        )
    return {
        "wallets": wallets,
        "count": n,
        "unlimited_xpubs": True,
        "hardware_wallet_agnostic": True,
        "ephemeral": True,
        "paste_only": True,
        "warning": warning,
    }


@router.post("/api/wallets")
def add_wallet(body: WalletCreate) -> dict[str, Any]:
    reg = _session().registry
    try:
        if body.xpub:
            result = reg.register_xpub(body.name, body.xpub)
        elif body.address:
            result = reg.register_address(body.name, body.address)
        else:
            return {
                "ok": False,
                "error": "Provide a public xpub or address (never a seed/key).",
            }
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "wallet": {
            "id": result.entry.id,
            "name": result.entry.name,
            "kind": result.entry.kind,
            "xpub": result.entry.xpub,
            "address": result.entry.address,
        },
        "warning": result.warning,
        "ephemeral": True,
        "note": (
            "Paste-only, RAM session. Closing the app discards this xpub. "
            "Hardware-wallet-agnostic: any BIP32/BIP84 xpub "
            "(Trezor, BitBox, Ledger, Coldcard, Foundation, Sparrow, …)."
        ),
    }


@router.post("/api/wallets/batch")
def add_wallets_batch(body: WalletBatchCreate) -> dict[str, Any]:
    """Import unlimited pasted xpubs — soft warning only if many.

    Accepts structured ``items`` and/or multi-line ``paste`` text. Paste lines
    may be ``Name\\tXPub`` (TAB); lines without TAB keep auto/default naming.
    """
    reg = _session().registry
    results: list[dict[str, Any]] = []
    for item in body.items:
        try:
            if item.xpub:
                r = reg.register_xpub(item.name, item.xpub)
            elif item.address:
                r = reg.register_address(item.name, item.address)
            else:
                continue
        except ValueError as exc:
            results.append({"name": item.name, "error": str(exc)})
            continue
        results.append(
            {
                "id": r.entry.id,
                "name": r.entry.name,
                "kind": r.entry.kind,
                "warning": r.warning,
            }
        )
    if body.paste and body.paste.strip():
        # name_offset unused — register_paste uses live registry count
        results.extend(
            reg.register_paste(
                body.paste,
                default_name=body.default_name,
            )
        )
    return {
        "ok": True,
        "imported": sum(1 for r in results if "error" not in r),
        "total": reg.count(),
        "unlimited_xpubs": True,
        "ephemeral": True,
        "results": results,
    }


@router.post("/api/sync")
def sync_wallets() -> dict[str, Any]:
    """Derive addresses, pull Electrum history, ingest + M2 enrich in session RAM."""
    state = _session()
    state.sync_progress.reset_for_run()

    def on_progress(**kwargs: Any) -> None:
        state.sync_progress.update(**kwargs)

    pipeline = SyncPipeline(
        state.registry,
        state.db,
        client=state.electrum_client,
        enricher=state.enricher,
        cache=state.chain_cache,
    )
    try:
        summary = pipeline.run(
            connect=state.electrum_client is None,
            on_progress=on_progress,
        )
    except Exception:
        state.sync_progress.finish(phase="error", message="Sync-Fehler")
        raise
    payload = summary.as_dict()
    state.last_sync = payload
    state.last_enrichment = payload.get("enrichment")
    state.last_ownership = payload.get("ownership")
    state.last_tx_io = dict(getattr(pipeline, "last_tx_io", {}) or {})
    state.last_ext_links = list(getattr(pipeline, "last_ext_links", []) or [])
    reload_local_files(state)  # pick up edits in local/ on every sync
    # Pipeline already sets phase done/error; ensure running=false.
    snap = state.sync_progress.snapshot()
    if snap.running:
        state.sync_progress.finish(
            phase="done" if summary.status != "error" else "error",
        )
    else:
        # Re-assert running cleared (pipeline may have set it).
        state.sync_progress.update(running=False, auto_message=False)
    # Start loading historical/spot prices right away (background, fail-open).
    state.oracle.start_background_prefetch(_flow_days(list_flows(state.db)))
    return payload


@router.get("/api/sync/progress")
def sync_progress() -> dict[str, Any]:
    """Live Electrum sync progress (in-memory, thread-safe). Idle → running:false."""
    return _session().sync_progress.as_dict()


@router.delete("/api/session")
def clear_session() -> dict[str, Any]:
    """Explicitly wipe session RAM (registry + :memory: ledger + caches)."""
    state = _session()
    state.registry.clear()
    clear_session_data(state.db)
    state.oracle.clear_cache()
    state.chain_cache.clear()
    state.external_book.clear()
    state.last_ext_links = []
    from btc_origin.regelwerk import set_session_categories

    set_session_categories(None)  # Zuordnungen der Sitzung (nicht gespeichert)
    reload_local_files(state)
    state.last_sync = None
    state.last_enrichment = None
    state.last_ownership = None
    state.last_tx_io = {}
    state.sync_progress.clear()
    return {
        "ok": True,
        "ephemeral": True,
        "note": (
            "Session cleared in RAM. Closing the tab/process would do the same. "
            "Nothing was written to disk or LocalStorage."
        ),
    }


@router.get("/api/electrum/servers")
def electrum_servers() -> dict[str, Any]:
    settings = get_settings()
    return {
        "configured": {
            "host": settings.electrum_host,
            "port": settings.electrum_port,
            "ssl": settings.electrum_ssl,
        },
        "defaults": ElectrumClient.default_servers(),
        "note": (
            "Read-only public servers. User-configurable via ELECTRUM_* env. "
            "Sync tries the configured host first, then fails over across defaults. "
            "Public servers may block some networks. "
            "Use GET /api/electrum/status?probe=1 for a short live probe."
        ),
    }


@router.get("/api/electrum/status")
def electrum_status(probe: int = 0) -> dict[str, Any]:
    """Electrum reachability — lazy. Without probe=1 no network I/O.

    With ``?probe=1``: short (~4s) connect to the configured host only.
    Fail-open; never hangs forever. Full failover is used by POST /api/sync.
    """
    settings = get_settings()
    configured = {
        "host": settings.electrum_host,
        "port": settings.electrum_port,
        "ssl": settings.electrum_ssl,
    }
    if not probe:
        return {
            "configured": configured,
            "connected": None,
            "server": None,
            "error": None,
            "probed": False,
            "note": "Pass ?probe=1 for a short live check (does not hang forever).",
        }
    result = probe_electrum_status(timeout=4.0)
    result["probed"] = True
    return result
