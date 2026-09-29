"""Lokale Dateien (local/) und Abgleich mit Portfolio Performance."""

from __future__ import annotations


from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response

from btc_origin.local_files import (
    _read_text,
    resolve_local_dir,
)
from btc_origin.pp_diff import ist_csv
from btc_origin.pp_check import (
    PP_FILE,
    build_import_csv,
    chain_movements,
    compare,
    internal_fees,
    parse_pp_file,
    pp_diagnose,
)
from btc_origin.db import (
    list_flows,
)
from btc_origin.cloud_summary import summarize_cloud
from fastapi import APIRouter

from btc_origin.api.core import (
    AppState,
    _price_eur,
    _session,
    _shaped_cloud_flows,
    reload_local_files,
)

router = APIRouter()


@router.post("/api/local/reload")
def reload_local() -> dict[str, Any]:
    """Re-read local/ (names CSV + exchange exports) without a new sync."""
    data = reload_local_files(_session())
    return {"ok": True, "local": data.as_dict(), "ephemeral": True}


def _pp_check(state: AppState) -> tuple[Any, dict[str, Any]] | None:
    """(PPFile, Abgleich) oder None, wenn local/check-pp.csv fehlt."""
    path = resolve_local_dir() / PP_FILE
    if not path.is_file():
        return None
    ppf = parse_pp_file(_read_text(path))
    rows = list_flows(state.db)
    chain = chain_movements(_shaped_cloud_flows(state, rows, include_fees=True)) if rows else []
    bestand = summarize_cloud(rows).bestand_sats if rows else 0
    return ppf, compare(ppf.rows, chain, bestand_sats=bestand, fees=internal_fees(rows))


@router.get("/api/check/pp")
def check_portfolio_performance() -> dict[str, Any]:
    """Abgleich local/check-pp.csv (Portfolio Performance) ↔ Blockchain.

    Nur für die Anzeige im Browser (Bereinigung des PP-Depots) — nie im Bericht.
    Die Datei wird bei jedem Aufruf neu gelesen, nie geschrieben.
    """
    state = _session()
    try:
        res = _pp_check(state)
    except (OSError, ValueError) as exc:
        return {"exists": True, "file": PP_FILE, "errors": [str(exc)], "ephemeral": True}
    if res is None:
        return {"exists": False, "file": PP_FILE, "ephemeral": True}
    ppf, result = res
    return {
        "exists": True,
        "file": PP_FILE,
        "synced": bool(list_flows(state.db)),
        "errors": ppf.errors,
        "skipped_transfers": ppf.skipped_transfers,
        "securities": ppf.counted,
        "ignored_securities": ppf.ignored,
        **result,
        "ephemeral": True,
    }


@router.post("/api/check/pp/import.csv")
def export_pp_import_csv(body: dict[str, Any] | None = None) -> Response:
    """Delta-CSV für den PP-Import: nur fehlende Bewegungen, Gebühren und
    Mengenkorrekturen — bestehende PP-Buchungen bleiben unangetastet.
    Nur auf Klick, Bytes im Speicher — local/ wird nie beschrieben.
    Body: ``{"keep_lines": [Zeilennummern]}`` = PP-Zeilen ohne Gegenstück, die
    bleiben sollen (die übrigen zählen als „in PP löschen“)."""
    state = _session()
    res = _pp_check(state)
    if res is None:
        raise HTTPException(status_code=404, detail=f"local/{PP_FILE} fehlt")
    ppf, result = res
    keep = [int(x) for x in (body or {}).get("keep_lines") or [] if str(x).isdigit()]
    try:
        text, stats = build_import_csv(ppf, result, _price_eur(state), keep)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.pp_last_delta = dict(stats)
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="pp-import-bitcoin.csv"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
            "X-PP-Import-Stats": ",".join(f"{k}={v}" for k, v in stats.items()),
        },
    )


@router.get("/api/check/pp/ist.csv")
def pp_ist_csv() -> Response:
    """Ist-Stand laut Blockchain für ``btc-origin-pp-diff`` (Vergleich PP vorher/nachher/Ist):
    Bewegungen je Transaktion, Netzwerkgebühren reiner Umbuchungen, Bestand. Nur auf Klick,
    Bytes im Speicher — die App schreibt nichts nach local/."""
    state = _session()
    rows = list_flows(state.db)
    chain = chain_movements(_shaped_cloud_flows(state, rows, include_fees=True)) if rows else []
    bestand = summarize_cloud(rows).bestand_sats if rows else 0
    text = ist_csv(chain, internal_fees(rows), bestand)
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ist-stand-blockchain.csv"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )


@router.get("/api/check/pp/diagnose")
def pp_diagnose_text() -> dict[str, Any]:
    """Diagnose zum PP-Abgleich zum Weitergeben: nur Zähler, Jahre, Vorzeichen und
    Anteile in % — keine Bestände, Beträge, Daten, Adressen oder TxIDs."""
    state = _session()
    res = _pp_check(state)
    if res is None:
        raise HTTPException(status_code=404, detail=f"local/{PP_FILE} fehlt")
    ppf, result = res
    return {"text": pp_diagnose(ppf, result, state.pp_last_delta), "ephemeral": True}
