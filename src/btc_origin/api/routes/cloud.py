"""Wallet-Cloud: Flüsse, Lots, Jahre, Labels, Anreicherung, Eigentum."""

from __future__ import annotations


import functools
from datetime import date
from typing import Any, Callable


from btc_origin.config import get_settings
from btc_origin.year_summary import yearly_summary
from btc_origin.exchange_sales import (
    replay_exchanges,
)
from btc_origin.db import (
    list_flows,
)
from btc_origin.enrichment import (
    flows_from_db_rows,
    label_map_for_addresses,
    list_labels,
    own_addresses_from_db,
)
from btc_origin.ownership_inference import OwnershipInferencer
from btc_origin.holding_clock import (
    lot_to_genealogy_dict,
)
from btc_origin.price_oracle import (
    HISTORICAL_UNAVAILABLE,
    days_from,
)
from btc_origin.cloud_summary import summarize_cloud
from fastapi import APIRouter

from btc_origin.api.core import (
    _address_wallet_name_maps,
    _ensure_exchange_acquisitions,
    _flow_days,
    _session,
    _shaped_cloud_flows,
)

router = APIRouter()


# Max. time a UI request waits for background price loading before it
# answers with „Kurs wird geladen …“ (prices_pending) and the UI re-polls.
UI_PRICE_WAIT_SECONDS = 1.0


def _ui_cached_prices(fn: Callable[..., Any]) -> Callable[..., Any]:
    """UI read endpoints: never block on price HTTP.

    Missing historical/spot prices are fetched by a background thread; until
    then rows show „Kurs wird geladen …“ and the response carries
    ``prices_pending: true`` so the UI re-polls. Reports (CSV/PDF/FA) keep
    fetching synchronously — they need complete data.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        state = _session()
        if state.oracle.start_background_prefetch(_flow_days(list_flows(state.db))):
            # Fast networks / warm caches: show prices in the first response.
            state.oracle.wait_for_prefetch(UI_PRICE_WAIT_SECONDS)
        with state.oracle.cached_only():
            out = fn(*args, **kwargs)
        if isinstance(out, dict):
            out["prices_pending"] = state.oracle.prefetch_running
            out["price_status"] = state.oracle.price_status()
        return out

    return wrapper


def _resolve_wallet_name(
    *,
    wallet_id: object,
    address: object,
    by_id: dict[int, str],
    by_addr: dict[str, str],
) -> str | None:
    if wallet_id is not None and wallet_id != "":
        try:
            name = by_id.get(int(wallet_id))
            if name:
                return name
        except (TypeError, ValueError):
            pass
    addr = str(address or "")
    if addr:
        return by_addr.get(addr)
    return None


@router.get("/api/flows")
@_ui_cached_prices
def get_flows() -> dict[str, Any]:
    """Return session flows from the in-memory ledger (with label/price hints)."""
    state = _session()
    flows = list_flows(state.db)
    amap = label_map_for_addresses(state.db)
    by_id, by_addr = _address_wallet_name_maps(state)
    clock = state.clock
    oracle = state.oracle
    oracle.prefetch_historical(
        days_from(f.get("block_time") or f.get("lot_date") for f in flows)
    )
    enriched = []
    for f in flows:
        row = dict(f)
        lab = amap.get(str(f.get("address") or ""))
        if lab:
            row["entity_label"] = lab["label"]
            row["evidence_gap_status"] = lab["status"]

        # Cloud / session wallet display name (Name\tXPub paste names).
        wname = _resolve_wallet_name(
            wallet_id=f.get("wallet_id"),
            address=f.get("address"),
            by_id=by_id,
            by_addr=by_addr,
        )
        row["wallet_name"] = wname
        addr = str(f.get("address") or "")
        if wname and addr:
            row["address_label"] = f"{wname} ({addr})"
        else:
            row["address_label"] = addr or None

        # Prefer FIFO lot_date (acquisition) over block_time for holding checks.
        # Also honour enrichment holding_days ≥ 365 when present.
        hold_basis = f.get("lot_date") or f.get("block_time")
        if f.get("direction") == "in" and hold_basis:
            try:
                flag = clock.flag_inflow(
                    f"{f.get('txid')}:{f.get('address')}", str(hold_basis)
                )
                row["haltefrist_hint"] = flag.qualifies_haltefrist_hint
                row["haltefrist_days"] = flag.days_held
            except (ValueError, TypeError):
                hd = f.get("holding_days")
                if hd is not None:
                    try:
                        days = int(hd)
                        row["haltefrist_days"] = days
                        row["haltefrist_hint"] = days >= clock.threshold_days
                    except (TypeError, ValueError):
                        pass
        elif f.get("holding_days") is not None:
            try:
                days = int(f["holding_days"])
                row["haltefrist_days"] = days
                row["haltefrist_hint"] = days >= clock.threshold_days
            except (TypeError, ValueError):
                pass

        # Historical BTC price at tx time (Anschaffungs-Referenz style; fail-open).
        bt = f.get("block_time") or f.get("lot_date")
        row["btc_price_eur"] = None
        row["btc_price_usd"] = None
        row["btc_price_note"] = None
        if bt:
            try:
                on = date.fromisoformat(str(bt)[:10])
                ref = oracle.get_acquisition_reference(on)
                if ref.available and (ref.eur is not None or ref.usd is not None):
                    row["btc_price_eur"] = ref.eur
                    row["btc_price_usd"] = ref.usd
                    row["btc_price_note"] = (
                        "Anschaffungs-Referenz (historisch) — keine Kostenbasis"
                    )
                else:
                    row["btc_price_note"] = ref.message or HISTORICAL_UNAVAILABLE
            except (ValueError, TypeError):
                row["btc_price_note"] = HISTORICAL_UNAVAILABLE
        # No block_time / lot_date → leave note None (UI shows em-dash).

        enriched.append(row)

    cloud = summarize_cloud(flows, oracle=state.oracle).as_dict()
    return {
        "flows": enriched,
        "count": len(enriched),
        "cloud": cloud,
        "ephemeral": True,
        "last_sync": state.last_sync,
        "last_enrichment": state.last_enrichment,
    }


@router.get("/api/cloud")
@_ui_cached_prices
def get_cloud_summary() -> dict[str, Any]:
    """Prominent cloud-of-wallets totals (Inflow / Bestand / Outflow / interne Fees)."""
    state = _session()
    flows = list_flows(state.db)
    return summarize_cloud(flows, oracle=state.oracle).as_dict()


@router.get("/api/cloud/lots")
@_ui_cached_prices
def get_cloud_lots() -> dict[str, Any]:
    """Open FIFO lots with genealogy (Inflow → internal hops → current wallet).

    Recomputes FIFO in session RAM from the tagged ledger — no disk, no sample data.
    Path hops are recorded only when lots actually peel/push across wallets.
    """
    state = _session()
    rows = list_flows(state.db)
    cloud = summarize_cloud(rows, oracle=state.oracle).as_dict()
    by_id, _by_addr = _address_wallet_name_maps(state)
    if not rows:
        return {
            "lots": [],
            "count": 0,
            "cloud": cloud,
            "ephemeral": True,
            "note": (
                "Keine Lots — nach Sync erscheinen offene FIFO-Lots mit Pfad "
                "durch die Wallet-Cloud."
            ),
        }

    _ensure_exchange_acquisitions(state, rows)
    flows = flows_from_db_rows(rows)
    # Prefer today; HoldingClock uses UTC date when as_of is None.
    lot_result = state.clock.apply_fifo_lots(flows)
    lots_out = [
        lot_to_genealogy_dict(
            lot,
            wallet_names=by_id,
            threshold_days=state.clock.threshold_days,
        )
        for lot in lot_result.lots
        if lot.remaining_sats > 0
    ]
    return {
        "lots": lots_out,
        "count": len(lots_out),
        "cloud": cloud,
        "ephemeral": True,
        "note": (
            "FIFO-Lots aus der Sitzung. Interne Umbuchungen setzen lot_date nicht "
            "zurück. Genealogie = echte Peel/Push-Transfers — keine Beispieldaten. "
            "Keine Steuerberatung."
        ),
    }


@router.get("/api/cloud/years")
@_ui_cached_prices
def get_cloud_years() -> dict[str, Any]:
    """Jahres-Resümee (hypothetisch): Cloud-Austritte je Jahr als Verkauf am
    Austrittstag bewertet; getrennt < 1 Jahr / ≥ 1 Jahr. Keine Steuerberatung."""
    state = _session()
    shaped = _shaped_cloud_flows(state, list_flows(state.db), include_fees=True)
    outs = [r for r in shaped if r["direction"] == "out"]

    def price(day: date) -> float | None:
        ref = state.oracle.get_acquisition_reference(day)
        return float(ref.eur) if ref.available and ref.eur is not None else None

    if state.local.trades:
        outs = replay_exchanges(outs, state.local.trades, price, state.exchange_net_fee)[0]
    years = yearly_summary(outs, price, threshold_days=state.clock.threshold_days)
    return {
        "years": [y.as_dict() for y in years if y.disposals],
        "ephemeral": True,
        "assumption": (
            "Hypothetisch: Jeder Cloud-Austritt an eine fremde Adresse gilt als "
            "Verkauf am Austrittstag zum Tageskurs (EUR, Referenzkurs). "
            "Anschaffung = Einzelbetrachtung je Coin (BMF 06.03.2025, Rz. 61), "
            "Kurs am Cloud-Eintritt. Netzwerkgebühren der Veräußerung anteilig als "
            "Werbungskosten (Rz. 57, 59). Einzahlungen auf eine Börse, deren Export "
            "(local/boersen/) Verkäufe enthält: Verkaufstag und Erlös laut Export, "
            "auf der Börse FiFo (Rz. 20, 55, 61). "
            "Überweisungen an eigene, nicht erfasste Wallets zählen hier fälschlich mit."
        ),
        "note": (
            "< 1 Jahr gehalten: privates Veräußerungsgeschäft (§ 23 EStG) — nur "
            "falls tatsächlich verkauft. Freigrenze gilt für alle privaten "
            "Veräußerungsgeschäfte des Jahres zusammen. Keine Steuerberatung."
        ),
    }


@router.get("/api/cloud/flows")
@_ui_cached_prices
def get_cloud_flows() -> dict[str, Any]:
    """Wallet-Cloud Flows: Cloud-Eintritt (IN lots) / Cloud-Austritt (OUT disposals).

    Not the raw address-level session ledger (see ``GET /api/flows``). Rows are
    shaped from FIFO lots + consumptions:

    * **in** — one row per remaining lot; Betrag = remaining_sats; Zeit =
      Anschaffung / Cloud-Entry; Adresse = where sats sit now; path = genealogy.
    * **out** — external disposals; Betrag = consumed_sats; Zeit = Verkauf;
      Adresse = external destination if known, else „außerhalb Cloud“.

    BTC-Kurs (historisch) + Haltefrist flags included. Session RAM only.
    """
    state = _session()
    rows = list_flows(state.db)
    cloud = summarize_cloud(rows, oracle=state.oracle).as_dict()
    if not rows:
        return {
            "flows": [],
            "count": 0,
            "cloud": cloud,
            "ephemeral": True,
            "semantics": "wallet_cloud",
            "amount_basis_in": "entry_sats",
            "note": (
                "Keine Cloud-Flows — nach Sync erscheinen Cloud-Eintritte "
                "(offene Lots) und Cloud-Austritte (externe Veräußerungen)."
            ),
        }

    shaped = _shaped_cloud_flows(state, rows)

    # Attach historical BTC price at Cloud-Entry / disposal date (fail-open).
    oracle = state.oracle
    oracle.prefetch_historical(days_from(r.get("time") for r in shaped))
    for row in shaped:
        row["btc_price_eur"] = None
        row["btc_price_usd"] = None
        row["btc_price_note"] = None
        bt = row.get("time")
        if not bt:
            continue
        try:
            on = date.fromisoformat(str(bt)[:10])
            ref = oracle.get_acquisition_reference(on)
            if ref.available and (ref.eur is not None or ref.usd is not None):
                row["btc_price_eur"] = ref.eur
                row["btc_price_usd"] = ref.usd
                row["btc_price_note"] = (
                    "Anschaffungs-Referenz (historisch) — keine Kostenbasis"
                )
            else:
                row["btc_price_note"] = ref.message or HISTORICAL_UNAVAILABLE
        except (ValueError, TypeError):
            row["btc_price_note"] = HISTORICAL_UNAVAILABLE

    return {
        "flows": shaped,
        "count": len(shaped),
        "cloud": cloud,
        "ephemeral": True,
        "semantics": "wallet_cloud",
        "amount_basis_in": "entry_sats",
        "note": (
            "Wallet-Cloud-Flows: IN = jeder Cloud-Eintritt (Betrag beim Eintritt, "
            "remaining_sats = davon noch da), OUT = Cloud-Austritt an fremde "
            "Adresse (ext-NNN oder eigener Name). Interne Umbuchungen sind keine "
            "eigenen Zeilen — sie erscheinen in der Pfad-Kette. Keine Steuerberatung."
        ),
    }


@router.get("/api/labels")
def get_labels() -> dict[str, Any]:
    state = _session()
    labels = list_labels(state.db)
    return {
        "labels": labels,
        "count": len(labels),
        "pack_entities": sorted(set(state.labels.entity_pack.values())),
        "note": (
            "Evidence-gap labels only. NEVER silent cost basis 0. "
            "Status e.g. 'Kaufnachweis fehlt, Quelle nicht erreichbar'."
        ),
        "ephemeral": True,
    }


@router.post("/api/enrich")
def enrich_session() -> dict[str, Any]:
    """Re-run M2 enrichment on current session flows (no Electrum)."""
    state = _session()
    result = state.enricher.enrich(
        state.db, tx_graph=state.last_tx_io or None
    )
    payload = result.as_dict()
    state.last_enrichment = payload
    if result.ownership is not None:
        state.last_ownership = result.ownership.as_dict(include_full=True)
    return {
        "ok": True,
        "enrichment": payload,
        "ownership": state.last_ownership,
        "ephemeral": True,
    }


@router.get("/api/cloud/ownership")
def cloud_ownership(privacy: bool = False) -> dict[str, Any]:
    """Session ownership report: seed + heuristically inferred addresses.

    ``privacy=true`` masks address strings (Demo / Privacy-Ansicht).
    Inferred set lives in RAM only — abgeleitet / heuristisch, keine Steuerberatung.
    """
    state = _session()
    seed = own_addresses_from_db(state.db)
    if get_settings().ownership_inference:
        flows = flows_from_db_rows(list_flows(state.db))
        graph = state.last_tx_io or None
        result = OwnershipInferencer().infer(seed, graph, flows=flows)
    else:
        # Only pasted xpubs/addresses are own — no heuristic expansion.
        result = OwnershipInferencer().infer(seed, None, flows=None)
    payload = result.as_dict(include_full=True, privacy_mask=privacy)
    if not privacy:
        state.last_ownership = dict(payload)
        payload["source"] = "computed"
    else:
        payload["source"] = "computed_privacy_masked"
    return payload
