"""Berichte: CSV/PDF, Herkunftsanalyse (intern/Finanzamt), Kontrolldatei, Diagnosen."""

from __future__ import annotations


from contextlib import contextmanager
import dataclasses
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response

from btc_origin.kategorisierung import apply_acks
from btc_origin.regelwerk import (
    RegelwerkFehler,
    check_manual,
    check_outputs,
    inflow_outputs,
    load_categories,
    rules_for,
    rules_used,
)
from btc_origin.tax_report import (
    Anschluss,
    check_tax_reports,
    default_year,
    protocol_result,
    render_protocol_pdf,
    render_tax_pdf,
    tax_report_name,
)
from btc_origin.tax_year import TaxConfig, TaxYear, build_tax_year
from btc_origin.year_summary import yearly_summary
from btc_origin.exchange_sales import (
    destination_check,
    matching_diagnosis,
    open_exchange_items,
    replay_exchanges,
    trade_ledger,
)
from btc_origin.local_files import (
    CHECK_XPUBS_FILE,
    file_counts,
)
from btc_origin.db import (
    list_flows,
)
from btc_origin.electrum_client import (
    ElectrumConnectionError,
    electrum_server_candidates,
    try_connect_first_available,
)
from btc_origin.enrichment import (
    flows_from_db_rows,
    label_map_for_addresses,
    own_addresses_from_db,
)
from btc_origin.fa_inbound_report import (
    build_fa_inbound_pdf,
    default_stichtag,
    parse_stichtag,
)
from btc_origin.holding_clock import (
    lot_to_genealogy_dict,
)
from btc_origin.price_oracle import (
    days_from,
    price_sources_for,
)
from btc_origin.cloud_summary import summarize_cloud
from btc_origin.origin_report import (
    HerkunftReport,
    build_id,
    fmt_date,
    WalletInfo,
    address_type_label,
    build_inflow_lines,
    nacherklaerung_years,
    render_nacherklaerung_pdf,
    render_altbestand_pdf,
    render_matching_diagnosis,
    wallet_reconciliation,
    build_lot_lines,
    build_sources,
    build_tx_refs,
    check_reports,
    render_pdf,
    source_label,
)
from fastapi import APIRouter

from btc_origin.api.core import (
    AppState,
    _ElectrumChain,
    _address_wallet_name_maps,
    _ensure_exchange_acquisitions,
    _external_entries,
    _session,
    _shaped_cloud_flows,
)

router = APIRouter()


@router.get("/api/report")
def report_info() -> dict[str, Any]:
    """Describe report capability — does not generate or write files."""
    return {
        "status": "ready_on_demand",
        "formats": ["csv", "pdf", "pdf/fa-inbound"],
        "persistence": "none",
        "message": (
            "Reports are generated in memory only when you POST "
            "/api/report/csv, /api/report/pdf, or /api/report/pdf/fa-inbound "
            "(explicit Save/Print). "
            "No automatic exports. No disk writes. "
            "Spot = REFERENZWERT (not Kaufpreis). "
            "Historical per-inflow = Anschaffungs-Referenz (not Kostenbasis). "
            "FA-Inbound PDF = remaining session lots (Eingangsdatum, BTC-Preis EUR, "
            "Zieladresse heute) + Sitzungs-Cloud header. "
            "Hot-Wallet-Cloud is session-only (ephemeral paste), not fixed repo addresses. "
            "Haltefrist / evidence-gap columns included for Anlage-SO Zuarbeit. "
            "No tax advice."
        ),
    }


def _flows_as_report_rows(flows: list[dict[str, Any]], state: AppState) -> list[dict[str, Any]]:
    """Map session flows → report rows with labels + FIFO holding + net fields."""
    amap = label_map_for_addresses(state.db)
    clock = state.clock
    rows: list[dict[str, Any]] = []
    for f in flows:
        is_internal = bool(f.get("is_internal"))
        row: dict[str, Any] = {
            "txid": f.get("txid"),
            "address": f.get("address"),
            "direction": f.get("direction"),
            "amount_sats": f.get("amount_sats"),
            "wallet_id": f.get("wallet_id"),
            "is_internal": f.get("is_internal"),
            "block_time": f.get("block_time"),
            "external_amount_sats": f.get("external_amount_sats"),
            "fee_sats": f.get("fee_sats"),
            "lot_date": f.get("lot_date"),
            "holding_days": f.get("holding_days"),
        }
        lab = amap.get(str(f.get("address") or ""))
        if lab:
            row["entity_label"] = lab["label"]
            row["evidence_gap_status"] = lab["status"]
        bt = f.get("block_time")
        if bt:
            row["date"] = str(bt)[:10]
        # Anschaffungs-Referenz / inflow_date only for external inflows (lots).
        if f.get("direction") == "in" and not is_internal and bt:
            try:
                row["inflow_date"] = date.fromisoformat(str(bt)[:10]).isoformat()
            except ValueError:
                row["inflow_date"] = str(bt)[:10]
        if f.get("lot_date"):
            row["anschaffung_date"] = str(f.get("lot_date"))[:10]
        # Prefer enrichment-computed holding_days; else derive for remaining external lots.
        if f.get("holding_days") is not None:
            row["haltefrist_days"] = f.get("holding_days")
            try:
                basis = str(f.get("lot_date") or bt or "")[:10]
                if basis:
                    flag = clock.flag_inflow(
                        f"{f.get('txid')}:{f.get('address')}",
                        basis,
                        as_of=(
                            str(bt)[:10]
                            if f.get("direction") == "out" and bt
                            else None
                        ),
                    )
                    row["haltefrist_hint"] = flag.qualifies_haltefrist_hint
                    if row.get("haltefrist_days") is None:
                        row["haltefrist_days"] = flag.days_held
            except (ValueError, TypeError):
                pass
        elif f.get("direction") == "in" and not is_internal and bt:
            try:
                flag = clock.flag_inflow(
                    f"{f.get('txid')}:{f.get('address')}", str(bt)
                )
                row["haltefrist_hint"] = flag.qualifies_haltefrist_hint
                row["haltefrist_days"] = flag.days_held
            except (ValueError, TypeError):
                pass
        rows.append(row)
    return rows


@router.post("/api/report/csv")
def report_csv(body: dict[str, Any] | None = None) -> Response:
    """Explicit Save: build CSV bytes in memory and return for download."""
    state = _session()
    payload = body or {}
    rows = payload.get("rows")
    if rows is None:
        rows = _flows_as_report_rows(list_flows(state.db), state)
    artifact = state.reports.build_csv(rows, fetch_spot=True)
    content = artifact.content if isinstance(artifact.content, str) else (
        artifact.content or b""
    )
    data = content.encode("utf-8") if isinstance(content, str) else content
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="btc-herkunft-report.csv"',
            "X-BTC-Herkunft-Disk-Written": "false",
            "X-BTC-Herkunft-Referenzwert": "spot-not-kaufpreis",
            "X-BTC-Herkunft-Anschaffungs-Referenz": "historical-not-kostenbasis",
        },
    )


@router.post("/api/report/pdf")
def report_pdf(body: dict[str, Any] | None = None) -> Response:
    """Explicit Print/Save: build PDF bytes in memory for download."""
    state = _session()
    payload = body or {}
    rows = payload.get("rows")
    if rows is None:
        rows = _flows_as_report_rows(list_flows(state.db), state)
        payload = {
            **payload,
            "flow_count": len(rows),
            "rows": rows,
        }
    artifact = state.reports.build_pdf(payload, fetch_spot=True, rows=rows)
    data = artifact.content if isinstance(artifact.content, (bytes, bytearray)) else (
        str(artifact.content or "").encode("utf-8")
    )
    return Response(
        content=bytes(data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="btc-herkunft-report.pdf"',
            "X-BTC-Herkunft-Disk-Written": "false",
            "X-BTC-Herkunft-Referenzwert": "spot-not-kaufpreis",
            "X-BTC-Herkunft-Anschaffungs-Referenz": "historical-not-kostenbasis",
        },
    )


def _session_lots_for_fa(
    state: AppState,
    *,
    as_of: date | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Remaining FIFO lots (same basis as GET /api/cloud/lots) + wallet name map.

    Haltefrist flags are evaluated against ``as_of`` (Stichtag) when given; ``rows``
    (default: all flows) e.g. only the flows up to the Stichtag.
    """
    rows = list_flows(state.db) if rows is None else rows
    by_id, _by_addr = _address_wallet_name_maps(state)
    if not rows:
        return [], by_id
    _ensure_exchange_acquisitions(state, rows)
    flows = flows_from_db_rows(rows)
    lot_result = state.clock.apply_fifo_lots(flows, as_of=as_of)
    lots_out = [
        lot_to_genealogy_dict(
            lot,
            as_of=as_of,
            wallet_names=by_id,
            threshold_days=state.clock.threshold_days,
        )
        for lot in lot_result.lots
        if lot.remaining_sats > 0
    ]
    return lots_out, by_id


def _build_herkunft_report(
    state: AppState,
    *,
    stichtag: date,
    privacy: bool,
    person_name: str = "",
    tax_id: str = "",
    until: date | None = None,
) -> HerkunftReport:
    """Collect everything for the „Herkunftsanalyse Bitcoin“ report (RAM only).

    ``until``: only flows up to this day (UTC); exchange exports are cut via _cut_at."""
    rows = list_flows(state.db)
    if until is not None:
        cut = until.isoformat()
        rows = [r for r in rows if r.get("block_time") and str(r["block_time"])[:10] <= cut]
    shaped = [dict(r) for r in _shaped_cloud_flows(state, rows, include_fees=True)]
    # Names from local/zuordnung.csv are the taxpayer's statement → marked ³.
    for r in shaped:
        if r.get("source_declared") and r.get("source_name"):
            r["source_name"] = f"{r['source_name']}³"
        if r.get("external_declared") and r.get("external_name"):
            r["external_name"] = f"{r['external_name']}³"
    # Explicit request → fetch all needed prices now (not cache-only).
    state.oracle.prefetch_historical(
        days_from(r.get("time") for r in shaped) + days_from(r.get("lot_date") for r in shaped)
    )

    def price(day: date) -> float | None:
        ref = state.oracle.get_acquisition_reference(day)
        return float(ref.eur) if ref.available and ref.eur is not None else None

    lots, _names = _session_lots_for_fa(state, as_of=stichtag, rows=rows)
    lot_source = {
        str(r["lot_id"]): source_label(r)
        for r in shaped
        if r["direction"] == "in" and r.get("lot_id")
    }
    outs = [r for r in shaped if r["direction"] == "out"]
    if state.local.trades:
        # Real sale date/proceeds from exchange exports (Rz. 20, 55).
        state.oracle.prefetch_historical([t.day for t in state.local.trades])
        outs, _w, ex_reports = replay_exchanges(
            outs, state.local.trades, price, state.exchange_net_fee
        )
        # Kauf-Seite (matches) from the acquisition pass, Verkauf-Seite from here.
        for name, rep in ex_reports.items():
            first = state.exchange_reports.get(name)
            if first is not None:
                rep.matched, rep.unmatched = first.matched, first.unmatched
                rep.entries_total = first.entries_total
    else:
        ex_reports = {}
    summary = summarize_cloud(rows)

    # Bestand je Wallet zum Stichtag (Rz. 104) from the raw ledger (end of day UTC).
    names = {w.id: w.name for w in state.registry.list_wallets()}
    at_stichtag: dict[str, int] = {name: 0 for name in names.values()}
    cut = stichtag.isoformat()
    for r in rows:
        if not r.get("block_time") or str(r["block_time"])[:10] > cut:
            continue
        name = names.get(r.get("wallet_id"))
        if name is None:
            continue
        sats = int(r.get("amount_sats") or 0)
        at_stichtag[name] += sats if r.get("direction") == "in" else -sats
    # Stichtag in the future (e.g. 31.12. of this year) → today's price.
    price_day = min(stichtag, datetime.now(timezone.utc).date())
    state.oracle.prefetch_historical([price_day])
    recon = wallet_reconciliation(rows, names, stichtag)
    used_days = (
        days_from(r.get("time") for r in outs)
        + days_from(r.get("lot_date") for r in outs)
        + days_from(r.get("time") for r in shaped)
        + days_from(r.get("lot_date") for r in shaped)
        + [stichtag]
    )
    piece_days = [
        p.acquisition_date for r in state.exchange_reports.values() for w in r.matched for p in w.pieces
        if p.price_eur is None
    ]
    if piece_days:
        state.oracle.prefetch_historical(piece_days)
    sources = price_sources_for(used_days)

    balance_by_wallet: dict[int, int] = {}
    for lot in lots:
        wid = lot.get("current_wallet_id")
        if wid is not None:
            balance_by_wallet[int(wid)] = balance_by_wallet.get(int(wid), 0) + int(lot["remaining_sats"])
    wallets: list[WalletInfo] = []
    for w in state.registry.list_wallets():
        wid = w.id
        w_rows = [r for r in rows if r.get("wallet_id") == wid]
        if until is not None and not w_rows:
            continue  # keine Vorgänge im Berichtszeitraum → nicht aufführen
        times = sorted(str(r["block_time"])[:10] for r in w_rows if r.get("block_time"))
        used = sorted({str(r["address"]) for r in w_rows if r.get("address")})
        key = w.xpub or w.address or ""
        wallets.append(
            WalletInfo(
                name=w.name,
                kind=w.kind,
                address_type=address_type_label(used or ([w.address] if w.address else [])),
                first=times[0] if times else None,
                last=times[-1] if times else None,
                tx_count=len({r["txid"] for r in w_rows}),
                balance_sats=balance_by_wallet.get(int(wid), 0) if wid is not None else 0,
                key=key,
            )
        )
    # Transaktionsverzeichnis: Kurzreferenzen T-001 … nach Blockzeit
    transactions = build_tx_refs(
        rows,
        names,
        {str(r.get("txid") or "") for r in shaped if r["direction"] == "in"},
        {str(r.get("txid") or "") for r in shaped if r["direction"] == "out" and r.get("kind") != "fee"},
    )
    # Regelwerk (REGELWERK.md Abschnitt 5): Datei je Jahr mit Veräußerung und für das
    # Berichtsjahr (Rechtsgrundlagen); manuelle Kategorien: T-Nummer vorhanden, Datum passt.
    years = yearly_summary(outs, price, threshold_days=state.clock.threshold_days)
    kat = load_categories()
    outputs = inflow_outputs(rows, {str(r.get("txid") or "") for r in shaped if r["direction"] == "in"})
    check_manual(kat, transactions, until, outputs)
    for q in kat.quittungen:
        if q.tnr:
            check_outputs(q.tnr, "geprueft_nicht_unterstuetzt", outputs)
    last_day = (until or stichtag).isoformat()
    by_ref = {r.ref: r for r in transactions}
    manual = {}
    for m in kat.manuell.values():
        r = m.resolve(transactions)  # per Transaktions-Hash oder T-Nummer
        if r is None or m.datum.isoformat() > last_day:
            continue
        if r.txid in manual:
            raise RegelwerkFehler(f"zuordnung_manuell: {r.ref} ist mehrfach eingeordnet")
        manual[r.txid] = {
            "tnr": r.ref, "kategorie": m.kategorie, "behandlung": kat.kategorien[m.kategorie].behandlung,
            "anzeigename": kat.kategorien[m.kategorie].anzeigename,
            "erlaeuterung": m.erlaeuterung,
            # txid:vout → nur dieser Zufluss (Menge des Outputs)
            **({"vout": m.vout, "sats": dict(outputs.get(r.txid, [])).get(m.vout, 0)} if m.vout is not None else {}),
        }
    # Regelwerk je benötigtem Jahr (REGELWERK.md 5): Jahre mit Veräußerung, mit Einkunft nach
    # § 22 Nr. 3 EStG (Export-Zeilen und Einordnung per T-Nummer) und das Berichtsjahr
    income_years = [t.day.year for t in state.local.trades if t.einkunft and t.day.isoformat() <= last_day]
    income_years += [by_ref[m["tnr"]].day[:4] for m in manual.values()
                     if m["behandlung"] == "einkunft_22_3" and by_ref[m["tnr"]].day]
    rules_used([y.year for y in years if y.disposals] + [int(y) for y in income_years] + [stichtag.year])
    unsupported = [u.as_dict() for u in state.local.unsupported if u.day.isoformat() <= last_day]
    unsupported += _manual_unsupported(manual, shaped)
    unmatched_acks = [q.label for q in apply_acks(unsupported, kat) if q.datum.isoformat() <= last_day]
    return HerkunftReport(
        generated_at=datetime.now(),
        stichtag=stichtag,
        until=until,
        transactions=transactions,
        wallets=wallets,
        sources=build_sources(shaped),
        inflows=build_inflow_lines(shaped, price),
        years=years,
        stichtag_balances=at_stichtag,
        declared_rules=[(n, d.isoformat()) for n, d in state.local.date_rules],
        recon=recon,
        stichtag_price=price(price_day),
        stichtag_price_day=price_day,
        exchange_exports=sorted({t.exchange for t in state.local.trades if t.kind == "sell"}),
        exchange_reports=[ex_reports[k] for k in sorted(ex_reports)],
        exchange_files=file_counts(state.local.exchange_files, state.local.trades),
        exchange_trades=trade_ledger(state.local.trades, state.exchange_reports),
        open_exchange=open_exchange_items(state.exchange_reports, state.local.explanations),
        explanations=list(state.local.explanations),
        withdrawal_recon={
            str((w.matched_entry or {}).get("txid") or ""): {
                "exchange": w.exchange,
                "day": w.day.isoformat(),
                "export_sats": w.sats,
                "pieces": [
                    {
                        "day": p.acquisition_date.isoformat(),
                        "sats": p.sats,
                        # None = Tageskurs (AcqPiece); beim ersten Durchlauf evtl. noch nicht
                        # geladen → hier nachtragen (Tausch bleibt unbewertet)
                        "price_eur": p.price_eur
                        if p.price_eur is not None or "Tausch gegen" in p.source
                        else price(p.acquisition_date),
                        "source": p.source,
                    }
                    for p in w.pieces
                ],
                **w.recon(),
            }
            for r in state.exchange_reports.values()
            for w in r.matched
        },
        price_source_counts=sources,
        lots=build_lot_lines(lots, lot_source, stichtag, price),
        inflow_sats=summary.inflow_sats,
        outflow_sats=summary.outflow_sats,
        balance_sats=summary.bestand_sats,
        consistent=summary.consistent,
        disposal_fees_sats=summary.external_fees_sats,
        transfer_fees_sats=summary.internal_fees_sats,
        chain_balance_sats=summary.chain_balance_sats,
        unsupported=unsupported,
        unmatched_acks=unmatched_acks,
        geltungsbereich=kat.geltungsbereich(),
        manual_categories=manual,
        person_name=person_name.strip()[:120],
        tax_id=tax_id.strip()[:40],
        privacy=privacy,
    )


def _manual_unsupported(manual: dict[str, dict[str, str]], shaped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Zuflüsse, die der Steuerpflichtige einer nicht unterstützten Kategorie zuordnet
    (zuordnung_manuell, z. B. mining) — wie nicht unterstützte Export-Zeilen ausweisen."""
    out = []
    for r in shaped:
        m = manual.get(str(r.get("txid") or ""))
        if r.get("direction") != "in" or m is None or m["behandlung"] != "nicht_unterstuetzt":
            continue
        out.append({"exchange": str(r.get("wallet_name") or "Wallet"), "day": str(r.get("time") or "")[:10],
                    "art": f"Zufluss {m['tnr']}", "richtung": "Zufluss", "sats": int(r.get("amount_sats") or 0),
                    "grund": f"Kategorie „{m['anzeigename']}“ (Einordnung des Steuerpflichtigen)", "file": "",
                    "status": "nicht unterstützt – manuell prüfen", "ref": "", "tnr": m["tnr"],
                    "txid": str(r.get("txid") or ""), "vout": m.get("vout"),
                    "quittiert": False, "erlaeuterung": ""})
    return out


@contextmanager
def _cut_at(state: AppState, day: date | None) -> Any:
    """Finanzamt-Fassung mit Stichtag: Börsen-Exporte nur bis ``day`` (Kauf- und
    Verkauf-Seite werden damit neu zugeordnet); danach wieder alle Zeilen."""
    if day is None:
        yield
        return
    orig = state.local
    state.local = dataclasses.replace(orig, trades=[t for t in orig.trades if t.day <= day])
    state.exchange_key = None
    try:
        yield
    finally:
        state.local = orig
        state.exchange_key = None


def _herkunft(
    state: AppState, stichtag: str | None, bis: str | None, privacy: bool, name: str, steuer_id: str
) -> HerkunftReport:
    """Bericht für beide Fassungen. ``bis``: nur Vorgänge bis zu diesem Tag (Tagesende UTC,
    Blockchain und Börsen-Exporte); liegt er vor dem Stichtag, erfolgt auch die Abstimmung
    zu diesem Tag."""
    day = parse_stichtag(stichtag, fallback=default_stichtag())
    until = date.fromisoformat(bis.strip()[:10]) if bis and bis.strip() else None
    if until is not None and until < day:
        day = until
    with _cut_at(state, until):
        return _build_herkunft_report(
            state, stichtag=day, privacy=privacy, person_name=name, tax_id=steuer_id, until=until
        )


def _tax_year(state: AppState, year: int) -> TaxYear:
    """Jahressteuerreport-Daten: Herkunftsnachweis bis 31.12. des Jahres (vollständige
    Historie, Börsen-Exporte bis Jahresende), gefiltert auf das Jahr."""
    return _tax_data(state, year)[1]


def _tax_data(
    state: AppState, year: int, privacy: bool = False, name: str = "", steuer_id: str = ""
) -> tuple[HerkunftReport, TaxYear]:
    end = f"{year}-12-31"
    rep = _herkunft(state, end, end, privacy, name, steuer_id)

    def price(day: date) -> float | None:
        ref = state.oracle.get_acquisition_reference(day)
        return float(ref.eur) if ref.available and ref.eur is not None else None

    return rep, build_tax_year(
        rep,
        year,
        config=state.local.tax_config or TaxConfig(),
        rows=list_flows(state.db),
        wallet_names={w.id: w.name for w in state.registry.list_wallets()},
        price_eur=price,
        today=datetime.now(timezone.utc).date(),
    )


def _report_name(report: HerkunftReport, suffix: str) -> str:
    """herkunftsanalyse-bitcoin-<Stand>[-bis-<Datum>]-<suffix>"""
    until = f"-bis-{report.until.isoformat()}" if report.until is not None else ""
    return f"herkunftsanalyse-bitcoin-{report.generated_at:%Y-%m-%d}{until}-{suffix}"


@router.get("/api/report/herkunft.pdf")
def report_herkunft_pdf(
    stichtag: str | None = None,
    privacy: bool = False,
    name: str = "",
    steuer_id: str = "",
    fassung: str = "intern",
    bis: str | None = None,
) -> Response:
    """„Herkunftsanalyse Bitcoin“ as PDF shown inline in the browser.

    ``fassung=finanzamt``: Finanzamt-Fassung (ohne Bestände, xpubs, Adressen und nicht
    freigegebene Transaktions-IDs), sonst interne Fassung (Full-Detail). ``bis``: nur
    Vorgänge bis zu diesem Tag. Explicit request only; bytes in memory (never written to
    disk). Name / Steuer-ID come from the request and are not stored anywhere.
    """
    try:
        report = _herkunft(_session(), stichtag, bis, privacy, name, steuer_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {exc}") from exc
    fa = fassung == "finanzamt"
    report.fassung = "finanzamt" if fa else "full"
    data = render_pdf(report)
    if report.until is not None:
        # Anschluss für den Jahressteuerreport des Folgejahres (nur in dieser Sitzung)
        _session().herkunft_runs[report.until.isoformat()] = Anschluss(
            f"{report.generated_at:%Y-%m-%d %H:%M:%S}", fmt_date(report.stichtag.isoformat()), build_id(),
        )
    fname = _report_name(report, "finanzamt" if fa else "intern") + ".pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{fname}"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )


@router.get("/api/report/herkunft-kontrolle.txt")
def report_herkunft_control(
    stichtag: str | None = None,
    privacy: bool = False,
    name: str = "",
    steuer_id: str = "",
    bis: str | None = None,
) -> Response:
    """Kontrolldatei: beide Fassungen aus demselben Lauf, Prüfprotokolle und Freigabeliste
    der Transaktions-IDs. Nur zum Herunterladen; nichts wird auf dem Server gespeichert."""
    try:
        report = _herkunft(_session(), stichtag, bis, privacy, name, steuer_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {exc}") from exc
    _full, _fa, control = check_reports(report)
    fname = _report_name(report, "kontrolle") + ".txt"
    return Response(
        content=control.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
            # Ergebnis für die Oberfläche (rot bei Fehler) — entspricht der letzten Zeile
            "X-BTC-Herkunft-Pruefergebnis": "fehler" if "\nERGEBNIS: FEHLER" in control
            else "warnung" if "\nERGEBNIS: BESTANDEN mit" in control else "bestanden",
        },
    )


def _trace_foreign_destinations(state: AppState, dest: list[dict[str, Any]], by_addr: dict[str, str]) -> None:
    """Zieladressen ohne eigene Wallet: tiefe Ableitung der xpubs, dann Blockchain-Spur.
    Ergänzt ``result``; die Adresse selbst wird danach aus den Zeilen entfernt."""
    from btc_origin.destination_trace import chain_fate, deep_owner

    foreign = [d for d in dest if not d.get("own") and d.get("address")]
    if foreign:
        xpubs = [(w.name, w.xpub) for w in state.registry.list_wallets() if w.xpub]
        checks = [(f"{n} (aus {CHECK_XPUBS_FILE}, nicht im Bericht)", x) for n, x in state.local.check_xpubs]
        own = dict(by_addr)
        for a in own_addresses_from_db(state.db):
            own.setdefault(a, "eigene Wallet")
        client = state.trace_client or state.electrum_client
        owned = None
        for d in foreign:
            hit = deep_owner(str(d["address"]), xpubs)
            if hit is not None:
                d["result"] = (f"Adresse gehört zu „{hit[0]}“ ({hit[1]}), liegt aber hinter den beim Sync "
                               "abgeleiteten Adressen — Gap-Limit erhöhen (GAP_LIMIT) und neu laden")
                continue
            hit = deep_owner(str(d["address"]), checks)
            if hit is not None:
                d["result"] = (f"Adresse gehört zu „{hit[0]}“ ({hit[1]}) — diese xpub als Wallet "
                               "aufnehmen, damit der Kauf zugeordnet wird")
                continue
            if client is None:
                try:
                    owned = client = try_connect_first_available(
                        electrum_server_candidates(), timeout=8.0, cache=state.chain_cache
                    )
                except ElectrumConnectionError as exc:
                    d["result"] += f"; Blockchain-Abfrage nicht möglich ({exc})"
                    continue
            try:
                d["result"] += "; " + chain_fate(
                    _ElectrumChain(client), str(d["address"]), d["day"], int(d["sats"]), own,
                    lambda a: (state.labels.lookup_address(a) or (None, None))[1],
                )
            except Exception as exc:  # noqa: BLE001 — Netz/Server: Diagnose trotzdem liefern
                d["result"] += f"; Blockchain-Abfrage fehlgeschlagen ({type(exc).__name__})"
        if owned is not None:
            try:
                owned.close()
            except Exception:  # noqa: BLE001
                pass
    for d in dest:
        d.pop("address", None)


def _check_own_names(state: AppState, by_addr: dict[str, str]) -> dict[str, Any]:
    """Greifen die Namen aus local/externe-adressen.csv? Je Name: Ergebnis ohne Adresse."""
    entries = _external_entries(state)
    seen = {a: e for e in entries if (e.out_count or e.in_count) for a in (e.addresses or [e.address])}
    own = set(by_addr) | own_addresses_from_db(state.db)
    rows = []
    for name, addr in state.local.names:
        if addr in own:
            res = "Adresse gehört zu einer eigenen Wallet — wird nicht als Gegenstelle benannt"
        elif addr in seen:
            e = seen[addr]
            counts = f"{e.out_count} Abfluss/Abflüsse, {e.in_count} Zufluss/Zuflüsse"
            res = (f"greift ({counts})" if e.name == name
                   else f"greift nicht: dieselbe Gegenstelle trägt schon den Namen „{e.name}“ ({counts})")
        else:
            res = "Adresse kommt in keinem Zu- oder Abfluss der betrachteten Wallets vor"
        rows.append({"name": name, "result": res})
    errors = [e.split(": ", 1)[-1] for e in state.local.errors if e.startswith("externe-adressen.csv")]
    return {"rows": rows, "errors": errors}


def _trace_assumed_disposals(state: AppState, report: Any, by_addr: dict[str, str]) -> list[dict[str, Any]]:
    """Abflüsse ohne Verkaufsbeleg: Blockchain-Spur ab der Empfängeradresse (ohne Adresse im Ergebnis)."""
    from btc_origin.destination_trace import chain_fate
    from btc_origin.origin_report import assumed_disposals

    rows = assumed_disposals(report)
    todo = [g for g in rows if g.get("address")]
    if not todo:
        return rows
    own = dict(by_addr)
    for a in own_addresses_from_db(state.db):
        own.setdefault(a, "eigene Wallet")
    client = state.trace_client or state.electrum_client
    owned = None
    try:
        for g in todo:
            if client is None:
                try:
                    owned = client = try_connect_first_available(
                        electrum_server_candidates(), timeout=8.0, cache=state.chain_cache
                    )
                except ElectrumConnectionError as exc:
                    g["trace"] = f"Blockchain-Abfrage nicht möglich ({exc})"
                    continue
            try:
                g["trace"] = chain_fate(
                    _ElectrumChain(client), g["address"], date.fromisoformat(g["day"][:10]), int(g["sats"]), own,
                    lambda a: (state.labels.lookup_address(a) or (None, None))[1],
                    ref="zum Abfluss",
                )
            except Exception as exc:  # noqa: BLE001 — Netz/Server: Diagnose trotzdem liefern
                g["trace"] = f"Blockchain-Abfrage fehlgeschlagen ({type(exc).__name__})"
    finally:
        if owned is not None:
            try:
                owned.close()
            except Exception:  # noqa: BLE001
                pass
    for g in rows:
        g.pop("address", None)
    return rows


@router.get("/api/report/matching-diagnose.md")
def report_matching_diagnose(stichtag: str | None = None, privacy: bool = False) -> Response:
    """Prüfstatus + Beinahe-Treffer der Börsen-Zuordnung als Markdown — nur für den
    Steuerpflichtigen (enthält echte Mengen und Daten, nicht Teil des PDFs).
    Bytes nur im Speicher; die Datei speichert erst der Browser."""
    state = _session()
    day = parse_stichtag(stichtag, fallback=default_stichtag())
    report = _build_herkunft_report(state, stichtag=day, privacy=False)
    rows = list_flows(state.db)
    shaped = _shaped_cloud_flows(state, rows) if rows else []
    diag = matching_diagnosis(
        [r for r in shaped if r["direction"] == "in"],
        state.exchange_reports,
        state.exchange_withdrawals,
        set(state.clock.acquisition_overrides),
    )
    _by_id, by_addr = _address_wallet_name_maps(state)
    inflows_by_address: dict[str, list[tuple[date, int]]] = {}
    for r in rows:
        if r.get("direction") == "in" and r.get("address") and r.get("block_time"):
            d = date.fromisoformat(str(r["block_time"])[:10])
            inflows_by_address.setdefault(str(r["address"]), []).append((d, int(r.get("amount_sats") or 0)))
    dest = destination_check(state.exchange_reports, by_addr, inflows_by_address)
    _trace_foreign_destinations(state, dest, by_addr)
    disposals = _trace_assumed_disposals(state, report, by_addr)
    text = render_matching_diagnosis(report, diag, state.exchange_reports, privacy=privacy, destinations=dest,
                                     disposals=disposals, own_names=_check_own_names(state, by_addr))
    fname = f"matching-diagnose-{'maskiert-' if privacy else ''}{report.generated_at:%Y-%m-%d}.md"
    return Response(
        content=text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )


@router.get("/api/report/nacherklaerung.pdf")
def report_nacherklaerung_pdf(
    jahre: str = "",
    stichtag: str | None = None,
    privacy: bool = False,
    name: str = "",
    steuer_id: str = "",
) -> Response:
    """Entwurf einer Nacherklärung (§ 153 / § 371 AO) für Jahre mit möglichem
    steuerpflichtigem Gewinn — inline im Browser, nur auf Klick, nie gespeichert.
    ``jahre`` = Kommaliste; leer = Jahre, deren Gewinn < 1 Jahr die Freigrenze erreicht."""
    state = _session()
    day = parse_stichtag(stichtag, fallback=default_stichtag())
    report = _build_herkunft_report(
        state, stichtag=day, privacy=privacy, person_name=name, tax_id=steuer_id
    )
    # Only relevant years (gain < 1 Jahr reaches the Freigrenze) — a request
    # for other years is ignored.
    relevant = nacherklaerung_years(report.years)
    asked = [int(x) for x in jahre.split(",") if x.strip().isdigit()]
    selected = [y for y in asked if y in relevant] or relevant
    data = render_nacherklaerung_pdf(report, selected)
    fname = f"nacherklaerung-entwurf-{report.generated_at:%Y-%m-%d}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{fname}"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )


@router.get("/api/report/altbestand.pdf")
def report_altbestand_pdf(privacy: bool = False, name: str = "", steuer_id: str = "") -> Response:
    """Nachweis Altbestand: heute gehaltene Coins, angeschafft bis 31.12.2026
    (Bestandsschutz laut Referentenentwurf zur Kryptosteuer-Reform) — inline,
    nur auf Klick, nie gespeichert."""
    state = _session()
    report = _build_herkunft_report(
        state, stichtag=date.today(), privacy=privacy, person_name=name, tax_id=steuer_id
    )
    data = render_altbestand_pdf(report)
    fname = f"nachweis-altbestand-bitcoin-{report.generated_at:%Y-%m-%d}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{fname}"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )


@router.post("/api/report/pdf/fa-inbound")
def report_pdf_fa_inbound(body: dict[str, Any] | None = None) -> Response:
    """Explicit Save: FA-Inbound PDF from current session remaining lots.

    Body may include ``stichtag`` (ISO YYYY-MM-DD). Default = 31.12. of the
    current calendar year. Haltefrist split is evaluated against Stichtag.
    Hot-Wallet-Cloud = wallets in this ephemeral paste session (not Git).
    Disk-Written: false. Keine Steuerberatung.
    """
    payload = body or {}
    raw = payload.get("stichtag")
    try:
        stichtag = parse_stichtag(raw, fallback=default_stichtag())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "stichtag muss ISO-Datum YYYY-MM-DD sein "
                f"(erhalten: {raw!r}). Keine Steuerberatung."
            ),
        ) from exc

    privacy = bool(payload.get("privacy") or payload.get("privacy_mode"))

    state = _session()
    lots, by_id = _session_lots_for_fa(state, as_of=stichtag)
    wallets = [
        {
            "id": w.id,
            "name": w.name,
            "kind": w.kind,
            "xpub": w.xpub,
            "address": w.address,
        }
        for w in state.registry.list_wallets()
    ]
    artifact = build_fa_inbound_pdf(
        lots=lots,
        wallets=wallets,
        oracle=state.oracle,
        stichtag=stichtag,
        holding_clock=state.clock,
        wallet_names=by_id,
        privacy=privacy,
    )
    data = artifact.content if isinstance(artifact.content, (bytes, bytearray)) else (
        str(artifact.content or "").encode("utf-8")
    )
    return Response(
        content=bytes(data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                'attachment; filename="btc-herkunft-fa-inbound.pdf"'
            ),
            "X-BTC-Herkunft-Disk-Written": "false",
            "X-BTC-Herkunft-Referenzwert": "spot-not-kaufpreis",
            "X-BTC-Herkunft-Anschaffungs-Referenz": "historical-not-kostenbasis",
            "X-BTC-Herkunft-Report-Kind": "fa-inbound",
            "X-BTC-Herkunft-Stichtag": stichtag.isoformat(),
            "X-BTC-Herkunft-Privacy": "true" if privacy else "false",
        },
    )


# ---------------------------------------------------------------------------
# Jahressteuerreport
# ---------------------------------------------------------------------------


@router.get("/api/report/steuer/jahre")
def tax_years() -> dict[str, Any]:
    """Jahre, die sich mit Transaktionsdaten belegen lassen (Wallets und Börsen-Exporte), je mit
    Angabe, ob eine Regeldatei vorliegt; Vorgabe = das vergangene Jahr."""
    state = _session()
    years = {int(str(r["block_time"])[:4]) for r in list_flows(state.db) if r.get("block_time")}
    years |= {t.day.year for t in state.local.all_trades}
    out = []
    for y in sorted(years):
        try:
            rw = rules_for(y)
            out.append({"jahr": y, "regelwerk": True, "vermerk": rw.vermerk})
        except RegelwerkFehler as exc:
            out.append({"jahr": y, "regelwerk": False, "vermerk": str(exc)})
    usable = [e["jahr"] for e in out if e["regelwerk"]]
    return {"jahre": out, "vorgabe": default_year(datetime.now(timezone.utc).date(), usable),
            # in dieser Sitzung erzeugte Herkunftsnachweise mit Stichtag 31.12. (Anschluss)
            "herkunft": {k[:4]: {"stand": v.stand, "stichtag": v.stichtag, "build": v.build}
                         for k, v in state.herkunft_runs.items() if k.endswith("-12-31")}}


def _link(state: AppState, jahr: int, anschluss_jahr: int | None) -> Anschluss | None:
    """Anschlussvermerk (Abschnitt 1.1, Vorgabe der steuerlichen Prüfung): Standard (``None``
    oder ``-1``) „Herkunftsnachweis auf Anforderung“; ``0`` = ausdrücklich keiner (Warnung im
    Prüfprotokoll); ein Jahr = der in dieser Sitzung erzeugte Herkunftsnachweis mit Stichtag
    31.12. dieses Jahres (Stand, Stichtag, Build des Dokuments)."""
    if anschluss_jahr is None or anschluss_jahr < 0:
        return Anschluss(art="anforderung")
    if anschluss_jahr == 0:
        return None
    run = state.herkunft_runs.get(f"{anschluss_jahr}-12-31")
    if run is None:
        raise HTTPException(status_code=422, detail=(
            f"Herkunftsnachweis mit Stichtag 31.12.{anschluss_jahr} wurde in dieser Sitzung nicht erzeugt"))
    return run


@router.get("/api/report/steuer.pdf")
def tax_pdf(
    jahr: int, fassung: str = "intern", privacy: bool = False, name: str = "", steuer_id: str = "",
    anschluss_jahr: int | None = None,
) -> Response:
    """„Jahressteuerreport Bitcoin <Jahr>“ als PDF (inline). ``fassung``: intern | finanzamt.
    Nur auf ausdrückliche Anforderung; Bytes nur im Speicher."""
    state = _session()
    rep, tax = _tax_data(state, jahr, privacy, name, steuer_id)
    fa = fassung == "finanzamt"
    rep.fassung = "finanzamt" if fa else "full"
    link = _link(state, jahr, anschluss_jahr)
    data = render_tax_pdf(tax, rep, anschluss=link)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{tax_report_name(jahr, fassung)}"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
        },
    )


@router.get("/api/report/steuer-pruefprotokoll.txt")
def tax_protocol(
    jahr: int, privacy: bool = False, name: str = "", steuer_id: str = "", anschluss_jahr: int | None = None
) -> Response:
    """Prüfprotokoll des Jahressteuerreports (Abnahmeprüfungen 1–7, Regelwerk, Hashes) — beide
    Fassungen werden dafür erzeugt und geprüft. Nur zum Herunterladen, nichts wird gespeichert."""
    state = _session()
    rep, tax = _tax_data(state, jahr, privacy, name, steuer_id)
    link = _link(state, jahr, anschluss_jahr)
    _intern, _fa, text = check_tax_reports(tax, rep, anschluss=link)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="steuerreport-bitcoin-{jahr}-pruefprotokoll.txt"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
            "X-BTC-Herkunft-Pruefergebnis": "fehler" if "\nERGEBNIS: FEHLER" in text
            else "warnung" if "\nERGEBNIS: BESTANDEN mit" in text else "bestanden",
        },
    )


def _tax_protocol(state: AppState, jahr: int, privacy: bool, name: str, steuer_id: str,
                  anschluss_jahr: int | None) -> tuple[HerkunftReport, str]:
    rep, tax = _tax_data(state, jahr, privacy, name, steuer_id)
    _intern, _fa, text = check_tax_reports(tax, rep, anschluss=_link(state, jahr, anschluss_jahr))
    return rep, text


@router.get("/api/report/steuer-pruefung")
def tax_check(
    jahr: int, privacy: bool = False, name: str = "", steuer_id: str = "", anschluss_jahr: int | None = None
) -> dict[str, Any]:
    """Nur das Ergebnis des Prüfprotokolls (bestanden | warnung | fehler) für die Anzeige am
    Button; nichts wird gespeichert."""
    _rep, text = _tax_protocol(_session(), jahr, privacy, name, steuer_id, anschluss_jahr)
    return protocol_result(text)


@router.get("/api/report/steuer-pruefprotokoll.pdf")
def tax_protocol_pdf(
    jahr: int, privacy: bool = False, name: str = "", steuer_id: str = "", anschluss_jahr: int | None = None
) -> Response:
    """Prüfprotokoll als PDF (inline im Browser) — Inhalt wie die Textfassung."""
    rep, text = _tax_protocol(_session(), jahr, privacy, name, steuer_id, anschluss_jahr)
    return Response(
        content=render_protocol_pdf(text, rep, jahr),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="steuerreport-bitcoin-{jahr}-pruefprotokoll.pdf"',
            "Cache-Control": "no-store",
            "X-BTC-Herkunft-Disk-Written": "false",
            "X-BTC-Herkunft-Pruefergebnis": protocol_result(text)["ergebnis"],
        },
    )
