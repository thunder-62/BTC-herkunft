"""Externe Gegenstellen: Namen, Import/Export, Spurensuche."""

from __future__ import annotations


from datetime import date
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from btc_origin.external_book import parse_csv, to_csv
from btc_origin.db import (
    list_flows,
)
from btc_origin.electrum_client import (
    ElectrumConnectionError,
    electrum_server_candidates,
    try_connect_first_available,
)
from btc_origin.enrichment import (
    own_addresses_from_db,
)
from btc_origin.trail import search as trail_search
from fastapi import APIRouter

from btc_origin.api.core import (
    _ElectrumChain,
    _address_wallet_name_maps,
    _external_entries,
    _price_eur,
    _session,
    _shaped_cloud_flows,
)

router = APIRouter()


@router.get("/api/external")
def get_external() -> dict[str, Any]:
    """External destination addresses (ext-NNN or imported names). RAM only."""
    entries = _external_entries(_session())
    return {
        "external": [e.as_dict() for e in entries],
        "count": len(entries),
        "local": _session().local.as_dict(),
        "ephemeral": True,
        "note": (
            "Fremde Zieladressen von Cloud-Austritten. Kurzname ext-NNN, "
            "sofern nicht per CSV (name,adresse) benannt."
        ),
    }


class ExternalImport(BaseModel):
    csv: str


@router.post("/api/external/import")
def import_external(body: ExternalImport) -> dict[str, Any]:
    """Import names for external addresses from pasted/uploaded CSV text."""
    state = _session()
    pairs, errors = parse_csv(body.csv)
    _, by_addr = _address_wallet_name_maps(state)
    own = set(by_addr.keys()) | own_addresses_from_db(state.db)
    res = state.external_book.set_names(pairs, own_addresses=own)
    out = res.as_dict()
    out["errors"] = errors + out.pop("skipped")
    out["ephemeral"] = True
    return out


class TrailRequest(BaseModel):
    name: str
    max_hops: int | None = None


@router.post("/api/external/trail")
def external_trail(body: TrailRequest) -> dict[str, Any]:
    """Spurensuche für eine Gegenstelle: bis zu 3 Schritte vorwärts (Abflüsse)
    bzw. rückwärts (Zuflüsse) — öffentliche Labels, Börsen-Muster, eigene
    Wallets. Heuristisch, nur Hinweis; nichts wird gespeichert."""
    state = _session()
    entry = next((e for e in _external_entries(state) if e.name == body.name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Gegenstelle {body.name} nicht gefunden")
    addrs = list(entry.addresses or [entry.address])
    # Eigene Zufluss-Transaktionen von dieser Gegenstelle: Startpunkt der Rückwärtssuche
    in_txids = [
        str(r.get("txid"))
        for r in _shaped_cloud_flows(state, list_flows(state.db))
        if r["direction"] == "in" and r.get("source_name") == entry.name and r.get("txid")
    ]
    _by_id, by_addr = _address_wallet_name_maps(state)
    own = set(by_addr.keys()) | own_addresses_from_db(state.db)
    client = state.trace_client or state.electrum_client
    owned = None
    if client is None:
        try:
            owned = client = try_connect_first_available(
                electrum_server_candidates(), timeout=8.0, cache=state.chain_cache
            )
        except ElectrumConnectionError as exc:
            raise HTTPException(status_code=503, detail=f"Electrum nicht erreichbar: {exc}") from exc
    try:
        res = trail_search(
            _ElectrumChain(client),
            name=entry.name,
            out_addresses=addrs if entry.out_count else [],
            in_addresses=addrs if entry.in_count else [],
            label_of=lambda a: (state.labels.lookup_address(a) or (None, None))[1],
            own=own,
            max_hops=max(1, min(int(body.max_hops or 3), 4)),
            in_txids=list(dict.fromkeys(in_txids))[-5:],
        )
    finally:
        if owned is not None:
            try:
                owned.close()
            except Exception:  # noqa: BLE001
                pass
    return {**res.as_dict(), "addresses": addrs, "ephemeral": True}


@router.get("/api/external/suchliste.csv")
def external_search_list() -> Response:
    """Suchliste ungeklärter Gegenstellen (ext-NNN bzw. Datumsregel) für die
    eigene Belegsuche: Datum, Richtung, BTC, EUR zum Tageskurs, Wallet, TxID.
    Nur auf Klick, Bytes im Speicher."""
    import csv as _csv
    import io as _io

    state = _session()
    shaped = _shaped_cloud_flows(state, list_flows(state.db))
    unresolved = {e.name for e in _external_entries(state) if e.auto_named or e.declared}
    price = _price_eur(state)
    buf = _io.StringIO()
    w = _csv.writer(buf, delimiter=";")
    w.writerow(["Gegenstelle", "Richtung", "Datum", "BTC", "EUR (Tageskurs)", "Wallet", "TxID", "Adresse"])
    rows = []
    for r in shaped:
        who = r.get("external_name") if r["direction"] == "out" else r.get("source_name")
        if who not in unresolved:
            continue
        day = str(r.get("time") or "")[:10]
        sats = int(r.get("amount_sats") or 0)
        try:
            px = price(date.fromisoformat(day)) if day else None
        except ValueError:
            px = None
        eur = f"{px * sats / 1e8:.2f}".replace(".", ",") if px else ""
        addr = r.get("address") if r["direction"] == "out" else ", ".join(r.get("source_addresses") or [])
        rows.append([
            who, "Abfluss" if r["direction"] == "out" else "Zufluss", day,
            f"{sats / 1e8:.8f}".replace(".", ","), eur, r.get("wallet_name") or "",
            r.get("txid") or "", addr or "",
        ])
    rows.sort(key=lambda x: (x[0], x[2]))
    w.writerows(rows)
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="suchliste-gegenstellen.csv"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )


@router.post("/api/external/csv")
def export_external_csv() -> Response:
    """Explicit Save: external addresses as CSV (name,adresse) — bytes only."""
    data = to_csv(_external_entries(_session())).encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="externe-adressen.csv"',
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )
