"""Regelwerk und Kategorien in der Oberfläche: Übersicht der Export-Arten, Einordnung einzelner
Zuflüsse, Vorschau/Download/Speichern von ``local/kategorien.yaml``, PDF-Vergleich.

Änderungen gelten sofort für die Sitzung (nur im Arbeitsspeicher). Die App schreibt nur
``local/kategorien.yaml`` — und nur nach ausdrücklicher Bestätigung des Nutzers
(``bestaetigt: true``); die vorherige Fassung bleibt als ``kategorien.yaml.bak``."""

from __future__ import annotations

import base64
import csv
import io
from datetime import date
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from btc_origin.api.core import _session, _shaped_cloud_flows, reload_local_files
from btc_origin.db import list_flows
from btc_origin.kategorisierung import art_overview, treatment_changes
from btc_origin.local_files import resolve_local_dir
from btc_origin.origin_report import build_tx_refs, source_label
from btc_origin.regelwerk import (
    CATEGORIES_FILE,
    TECHNISCHE_ARTEN,
    RegelwerkFehler,
    load_categories,
    parse_categories,
    inflow_outputs,
    rules_root,
    session_categories,
    set_session_categories,
)

router = APIRouter()

_HEADER = (
    "# Persönliche Zuordnungen zum Regelwerk (btc-regeln/REGELWERK.md, Abschnitt 4).\n"
    "# Nicht im Git. Erzeugt in der Oberfläche von BTC-Herkunft; von Hand änderbar.\n"
    "# zuordnung_manuell: Schlüssel = Transaktions-Hash des Zuflusses (stabil), datum zur Kontrolle.\n"
)


def _local_path() -> Any:
    return resolve_local_dir() / CATEGORIES_FILE


def _file_text() -> str | None:
    path = _local_path()
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _saved_categories() -> Any:
    """Zuordnungen laut Datei (ohne Änderungen der Sitzung) — für „noch nicht gespeichert“."""
    return parse_categories((rules_root() / CATEGORIES_FILE).read_text(encoding="utf-8"), local_text=_file_text())


def _inflows(state: Any) -> list[dict[str, Any]]:
    """Zuflüsse in die Wallets mit T-Nummer (wie im Herkunftsnachweis ohne Stichtag)."""
    rows = list_flows(state.db)
    if not rows:
        return []
    shaped = _shaped_cloud_flows(state, rows, include_fees=True)
    names = {w.id: w.name for w in state.registry.list_wallets()}
    refs = build_tx_refs(
        rows, names,
        {str(r.get("txid") or "") for r in shaped if r["direction"] == "in"},
        {str(r.get("txid") or "") for r in shaped if r["direction"] == "out" and r.get("kind") != "fee"},
    )
    ref_of = {r.txid: r.ref for r in refs}
    outputs = inflow_outputs(rows, {str(r.get("txid") or "") for r in shaped if r["direction"] == "in"})
    out: dict[str, dict[str, Any]] = {}
    for r in shaped:
        if r["direction"] != "in":
            continue
        txid = str(r.get("txid") or "")
        e = out.setdefault(txid, {
            "tnr": ref_of.get(txid, ""), "key": txid, "day": str(r.get("time") or "")[:10],
            "wallet": str(r.get("wallet_name") or ""), "source": source_label(r),
            "sats": 0, "belegt": bool(r.get("acq_source")),
        })
        e["sats"] += int(r.get("amount_sats") or 0)
    # mehrere Zuflüsse in einer Transaktion → je Output eine Zeile (Schlüssel txid:vout)
    rows_out: list[dict[str, Any]] = []
    for txid, e in out.items():
        outs = outputs.get(txid, [])
        if len(outs) > 1:
            rows_out += [{**e, "tnr": f"{e['tnr']}:{vout}", "key": f"{txid}:{vout}", "sats": sats}
                         for vout, sats in outs]
        else:
            rows_out.append(e)
    return sorted(rows_out, key=lambda e: e["tnr"])


@router.get("/api/regelwerk/kategorien")
def get_categories() -> dict[str, Any]:
    """Kategorien, Übersicht der Export-Arten (ohne Beträge/Daten), Zuflüsse mit T-Nummer und
    die aktuelle lokale Datei."""
    state = _session()
    kat = load_categories()
    saved = _saved_categories()
    inflows = _inflows(state)
    path = _local_path()
    arten = art_overview(state.local.all_trades, kat)
    for a in arten:
        a["ungespeichert"] = saved.export_kategorie(a["exchange"], a["art"]) != kat.export_kategorie(a["exchange"], a["art"])
    ungespeichert = session_categories() is not None and session_categories() != (_file_text() or "")
    return {
        "kategorien": [
            {"name": k.name, "anzeigename": k.anzeigename, "behandlung": k.behandlung,
             "beschreibung": k.beschreibung}
            for k in kat.kategorien.values()
        ],
        "technisch": list(TECHNISCHE_ARTEN),
        "arten": arten,
        "sitzung": {"aktiv": session_categories() is not None, "ungespeichert": ungespeichert},
        "aenderungen": {
            "zeilen": len(changes := treatment_changes(state.local.all_trades, kat)),
            "wirkt_auf_werte": sum(1 for r in changes if r["wirkt_auf_werte"]),
        },
        # Anzeige per aktueller T-Nummer; gespeichert wird per Transaktions-Hash
        "manuell": {tnr: {"kategorie": m.kategorie, "datum": m.datum.isoformat(), "erlaeuterung": m.erlaeuterung}
                    for m in kat.manuell.values() if (tnr := _tnr_of(m.tnr, inflows))},
        "zufluesse": [{k: v for k, v in e.items() if k != "key"} for e in inflows],
        "local_file": str(path),
        "local_exists": path.is_file(),
    }


def _tnr_of(key: str, inflows: list[dict[str, Any]]) -> str:
    """Schlüssel aus zuordnung_manuell (Hash oder T-Nummer) → aktuelle T-Nummer ("" = unbekannt)."""
    return next((e["tnr"] for e in inflows if key in (e["key"], e["tnr"])), "")


def _merged_yaml(body: dict[str, Any]) -> str:
    """Bestehende local/kategorien.yaml + Änderungen aus der Oberfläche → neuer Dateitext.
    ``export``: {Börse: {Art: Kategorie oder "" (entfernen)}}; ``manuell``: {T-Nr: {kategorie,
    datum, erlaeuterung} oder null (entfernen)}."""
    base = session_categories() if session_categories() is not None else _file_text()
    current = yaml.safe_load(base) if base else None
    data: dict[str, Any] = current if isinstance(current, dict) else {}
    repo = parse_categories((rules_root() / CATEGORIES_FILE).read_text(encoding="utf-8"))
    export = data.setdefault("zuordnung_export", {}) or {}
    data["zuordnung_export"] = export
    for boerse, arten in (body.get("export") or {}).items():
        key = str(boerse).strip().lower()
        entry = export.setdefault(key, {"spalte_art": repo.export[key].spalte_art if key in repo.export
                                        else "Art laut Export", "arten": {}})
        entry.setdefault("arten", {})
        for art, kat in (arten or {}).items():
            if kat:
                if repo.export_kategorie(key, str(art)) == kat:
                    entry["arten"].pop(art, None)  # steht schon im Repo — nicht doppelt
                else:
                    entry["arten"][art] = kat
            else:
                entry["arten"].pop(art, None)
        if not entry["arten"]:
            export.pop(key, None)
    # zuordnung_manuell per Transaktions-Hash (stabil); T-Nummern aus älteren Einträgen werden
    # dabei in den Hash umgeschrieben, wenn Zufluss und Datum passen
    inflows = _inflows(_session())
    by_tnr = {e["tnr"]: e for e in inflows if e["tnr"]}
    manual: dict[Any, Any] = {}
    for key, v in (data.get("zuordnung_manuell") or {}).items():
        e = by_tnr.get(str(key))
        same_day = isinstance(v, dict) and e is not None and str(v.get("datum"))[:10] == e["day"]
        manual[e["key"] if same_day else key] = v
    data["zuordnung_manuell"] = manual
    for tnr, m in (body.get("manuell") or {}).items():
        e = by_tnr.get(tnr)
        key = e["key"] if e else tnr
        manual.pop(tnr, None)
        if not m or not m.get("kategorie"):
            manual.pop(key, None)
            continue
        manual[key] = {"kategorie": m["kategorie"], "datum": date.fromisoformat(str(m["datum"])[:10]),
                       **({"erlaeuterung": m["erlaeuterung"]} if m.get("erlaeuterung") else {})}
    for key in ("zuordnung_export", "zuordnung_manuell"):
        if not data[key]:
            data.pop(key)
    text = _HEADER + (yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100) if data else "")
    # dieselbe Validierung wie beim Start (REGELWERK.md Abschnitt 5)
    parse_categories((rules_root() / CATEGORIES_FILE).read_text(encoding="utf-8"), local_text=text)
    return text


@router.post("/api/regelwerk/kategorien/vorschau")
def preview_categories(body: dict[str, Any]) -> dict[str, Any]:
    """Neuer Inhalt von local/kategorien.yaml (nichts wird geschrieben)."""
    try:
        return {"yaml": _merged_yaml(body), "file": str(_local_path())}
    except (RegelwerkFehler, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/regelwerk/kategorien/anwenden")
def apply_categories(body: dict[str, Any]) -> dict[str, Any]:
    """Änderungen für die laufende Sitzung übernehmen — nur im Arbeitsspeicher, nichts wird
    geschrieben. Berichte und Prüfprotokoll rechnen sofort damit."""
    try:
        text = _merged_yaml(body)
        set_session_categories(text)
    except (RegelwerkFehler, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    reload_local_files(_session())
    return {"ok": True, "disk_written": False}


@router.post("/api/regelwerk/kategorien/verwerfen")
def discard_categories() -> dict[str, Any]:
    """Änderungen der Sitzung verwerfen — es gilt wieder local/kategorien.yaml (bzw. nur das Repo)."""
    set_session_categories(None)
    reload_local_files(_session())
    return {"ok": True, "disk_written": False}


@router.post("/api/regelwerk/kategorien/speichern")
def save_categories(body: dict[str, Any]) -> dict[str, Any]:
    """Schreibt local/kategorien.yaml — nur mit ``bestaetigt: true`` (Bestätigung in der
    Oberfläche). Die vorherige Fassung bleibt als kategorien.yaml.bak."""
    if body.get("bestaetigt") is not True:
        raise HTTPException(status_code=400, detail="Speichern nur nach Bestätigung")
    text = str(body.get("yaml") or "")
    try:
        parse_categories((rules_root() / CATEGORIES_FILE).read_text(encoding="utf-8"), local_text=text)
    except RegelwerkFehler as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    path = _local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(".yaml.bak")
    if path.is_file():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    set_session_categories(None)  # Datei = Stand der Sitzung
    reload_local_files(_session())
    return {"ok": True, "file": str(path), "backup": str(backup) if backup.is_file() else None}


@router.get("/api/regelwerk/aenderungen.csv")
def changes_csv() -> Response:
    """Export-Zeilen, deren Behandlung sich durch kategorien.yaml ändert (Börse, Art, Datum,
    Menge, bisher, neu) — nur als Download auf Klick, für die eigene Prüfung."""
    rows = treatment_changes(_session().local.all_trades, load_categories())
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Börse", "Art laut Export", "Datum", "Menge BTC", "Datei", "bisher", "neu", "wirkt auf Werte"])
    for r in rows:
        w.writerow([r["exchange"], r["art"], r["day"], f"{r['sats'] / 1e8:.8f}".replace(".", ","), r["file"],
                    r["bisher"], r["neu"], "ja" if r["wirkt_auf_werte"] else "nein"])
    return Response(
        content=("\ufeff" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kategorien-aenderungen.csv"',
                 "Cache-Control": "no-store", "X-BTC-Herkunft-Disk-Written": "false"},
    )


@router.post("/api/pdf-vergleich")
def compare_pdfs(body: dict[str, Any]) -> dict[str, Any]:
    """Zwei Berichts-PDFs (Base64) vergleichen — nur Abschnitte und Anzahl geänderter Zeilen,
    keine Werte. Nichts wird gespeichert."""
    from btc_origin.pdf_vergleich import compare, pdf_lines

    try:
        old = pdf_lines(base64.b64decode(str(body.get("alt") or "")))
        new = pdf_lines(base64.b64decode(str(body.get("neu") or "")))
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="PDF-Vergleich braucht pymupdf (pip install pymupdf)") from exc
    except Exception as exc:  # noqa: BLE001 — ungültige Datei
        raise HTTPException(status_code=422, detail=f"PDF nicht lesbar: {exc}") from exc
    changed = compare(old, new)
    return {"gleich": not changed, "zeilen": len(new),
            "abschnitte": [{"abschnitt": k, "zeilen": v} for k, v in changed.items()]}
