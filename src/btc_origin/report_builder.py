"""CSV + PDF report builders — in-memory bytes only; no path writes by default.

Output is produced **only on explicit user request** (Save / Print).
The API returns content for download; nothing is written to disk unless a
caller deliberately opts in (out of scope for defaults).

Price fields
------------
* Spot at generation time → **REFERENZWERT** (never Kauf-/Anschaffungspreis).
* Per-inflow historical EUR/USD → **Anschaffungs-Referenz** (never Kostenbasis).
Both fail-open; export is never blocked by oracle failure.

CSV columns target DE Anlage-SO Zuarbeit (documentation aid — no tax advice).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from btc_origin.holding_clock import HoldingClock
from btc_origin.price_oracle import (
    HISTORICAL_UNAVAILABLE,
    SPOT_UNAVAILABLE,
    PriceOracle,
    days_from,
)

# Stable column order for Anlage-SO Zuarbeit CSV.
CSV_COLUMNS: list[str] = [
    "date",
    "amount_sats",
    "amount_btc",
    "external_amount_sats",
    "fee_sats",
    "txid",
    "address",
    "direction",
    "internal_external",
    "lot_date",
    "haltefrist_hint",
    "haltefrist_days",
    "evidence_gap_status",
    "entity_label",
    "anschaffungs_referenz_label",
    "anschaffungs_referenz_date",
    "anschaffungs_referenz_usd",
    "anschaffungs_referenz_eur",
    "anschaffungs_referenz_source",
    "anschaffungs_referenz_note",
    "referenzwert_label",
    "referenzwert_usd",
    "referenzwert_eur",
    "referenzwert_timestamp_utc",
    "referenzwert_source",
    "referenzwert_note",
    "wallet_id",
]


@dataclass
class ReportArtifact:
    """In-memory report payload. ``path`` is always None in the default API."""

    kind: str  # csv | pdf
    content: bytes | str | None = None
    path: str | None = None  # unused — durable writes forbidden by default
    rows_or_pages: int = 0
    message: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _sats_to_btc(sats: Any) -> str:
    try:
        return f"{int(sats) / 100_000_000:.8f}"
    except (TypeError, ValueError):
        return ""


def _normalize_row_base(row: dict[str, Any], clock: HoldingClock) -> dict[str, Any]:
    """Map a raw session/flow row into stable Anlage-SO-oriented fields."""
    is_internal = row.get("is_internal")
    if isinstance(is_internal, str):
        is_internal = is_internal.strip() in ("1", "true", "True", "yes")
    internal_external = (
        row.get("internal_external")
        or ("internal" if is_internal else "external")
    )

    block_time = row.get("date") or row.get("block_time") or row.get("inflow_date")
    date_str = ""
    if block_time:
        date_str = str(block_time)[:10]

    lot_date = row.get("lot_date") or row.get("anschaffung_date") or ""
    if lot_date:
        lot_date = str(lot_date)[:10]

    haltefrist_hint = row.get("haltefrist_hint")
    haltefrist_days = row.get("haltefrist_days")
    if haltefrist_days is None and row.get("holding_days") is not None:
        haltefrist_days = row.get("holding_days")

    # Prefer lot acquisition date for holding checks (FIFO); fall back to block date.
    # For external inflows only when no explicit hint — remaining lots / disposals
    # should pass haltefrist_* from enrichment.
    hold_basis = lot_date or date_str
    is_external_in = (
        row.get("direction", "in") == "in" and not is_internal
    )
    if haltefrist_hint is None and hold_basis and is_external_in:
        try:
            flag = clock.flag_inflow(
                str(row.get("txid") or row.get("id") or hold_basis),
                hold_basis,
            )
            haltefrist_hint = flag.qualifies_haltefrist_hint
            haltefrist_days = flag.days_held
        except (ValueError, TypeError):
            if haltefrist_days is not None and haltefrist_days != "":
                try:
                    days = int(haltefrist_days)
                    haltefrist_hint = days >= clock.threshold_days
                except (TypeError, ValueError):
                    haltefrist_hint = False
                    haltefrist_days = ""
            else:
                haltefrist_hint = False
                haltefrist_days = ""
    # If days already known but hint still unset, derive from ≥ 365 threshold.
    if haltefrist_hint is None and haltefrist_days not in (None, ""):
        try:
            haltefrist_hint = int(haltefrist_days) >= clock.threshold_days
        except (TypeError, ValueError):
            pass
    # Disposals: if lot_date + direction=out external, compute held duration to date.
    if (
        haltefrist_hint is None
        and lot_date
        and row.get("direction") == "out"
        and not is_internal
        and date_str
    ):
        try:
            flag = clock.flag_inflow(
                str(row.get("txid") or row.get("id") or lot_date),
                lot_date,
                as_of=date_str,
                kind="disposal",
            )
            haltefrist_hint = flag.qualifies_haltefrist_hint
            haltefrist_days = flag.days_held
        except (ValueError, TypeError):
            pass

    amount = row.get("amount_sats", row.get("amount"))
    external_amount = row.get("external_amount_sats", "")
    fee = row.get("fee_sats", "")

    # Anschaffungs-Referenz only for external inflows (lots), not change.
    inflow_for_acq = None
    if is_external_in:
        inflow_for_acq = (
            row.get("inflow_date")
            or lot_date
            or date_str
            or None
        )

    return {
        "date": date_str,
        "amount_sats": amount if amount is not None else "",
        "amount_btc": row.get("amount_btc") or _sats_to_btc(amount),
        "external_amount_sats": (
            external_amount if external_amount is not None else ""
        ),
        "fee_sats": fee if fee is not None else "",
        "txid": row.get("txid", ""),
        "address": row.get("address", ""),
        "direction": row.get("direction", ""),
        "internal_external": internal_external,
        "lot_date": lot_date,
        "haltefrist_hint": (
            "yes"
            if haltefrist_hint in (True, 1, "1", "yes", "true", "True")
            else "no"
        ),
        "haltefrist_days": (
            haltefrist_days if haltefrist_days is not None else ""
        ),
        "evidence_gap_status": row.get("evidence_gap_status")
        or row.get("status")
        or "",
        "entity_label": row.get("entity_label") or row.get("label") or "",
        "wallet_id": row.get("wallet_id", ""),
        # Preserve inflow_date for oracle lookup — external inflows / lots only.
        "inflow_date": inflow_for_acq,
        "anschaffung_date": lot_date or row.get("anschaffung_date"),
    }


class ReportBuilder:
    """Build CSV/PDF **in memory** for explicit Save/Print requests."""

    def __init__(
        self,
        oracle: PriceOracle | None = None,
        *,
        holding_clock: HoldingClock | None = None,
    ) -> None:
        self._oracle = oracle or PriceOracle()
        self._clock = holding_clock or HoldingClock()

    @property
    def oracle(self) -> PriceOracle:
        return self._oracle

    def build_csv(
        self,
        rows: list[dict[str, Any]],
        *,
        fetch_spot: bool = True,
        dest: Any = None,
    ) -> ReportArtifact:
        """Return CSV text/bytes in memory with Anlage-SO Zuarbeit columns."""
        _ = dest  # deliberately unused — no disk writes
        spot = self._oracle.get_spot() if fetch_spot else None
        spot_fields = spot.as_report_fields() if spot else {
            "referenzwert_label": "REFERENZWERT",
            "referenzwert_usd": SPOT_UNAVAILABLE,
            "referenzwert_eur": SPOT_UNAVAILABLE,
            "referenzwert_timestamp_utc": "",
            "referenzwert_source": "",
            "referenzwert_note": (
                "REFERENZWERT (aktueller Markt) — kein Kaufpreis, "
                "keine Anschaffungs-Referenz, keine Kostenbasis."
            ),
        }

        bases = [_normalize_row_base(row, self._clock) for row in rows]
        self._oracle.prefetch_historical(
            days_from(b.get("inflow_date") or b.get("anschaffung_date") for b in bases)
        )
        enriched: list[dict[str, Any]] = []
        for row, base in zip(rows, bases):
            out: dict[str, Any] = {col: base.get(col, "") for col in CSV_COLUMNS}

            # Anschaffungs-Referenz for inflows (fail-open).
            inflow_day = base.get("inflow_date") or base.get("anschaffung_date")
            acq_fields: dict[str, str]
            if isinstance(inflow_day, date):
                acq_fields = self._oracle.get_acquisition_reference(
                    inflow_day
                ).as_report_fields()
            elif isinstance(inflow_day, str) and inflow_day:
                try:
                    acq_fields = self._oracle.get_acquisition_reference(
                        date.fromisoformat(str(inflow_day)[:10])
                    ).as_report_fields()
                except ValueError:
                    acq_fields = {
                        "anschaffungs_referenz_label": "Anschaffungs-Referenz",
                        "anschaffungs_referenz_date": str(inflow_day)[:10],
                        "anschaffungs_referenz_usd": HISTORICAL_UNAVAILABLE,
                        "anschaffungs_referenz_eur": HISTORICAL_UNAVAILABLE,
                        "anschaffungs_referenz_source": "",
                        "anschaffungs_referenz_note": (
                            "Anschaffungs-Referenz — keine Kostenbasis."
                        ),
                    }
            else:
                acq_fields = {
                    "anschaffungs_referenz_label": "Anschaffungs-Referenz",
                    "anschaffungs_referenz_date": "",
                    "anschaffungs_referenz_usd": "",
                    "anschaffungs_referenz_eur": "",
                    "anschaffungs_referenz_source": "",
                    "anschaffungs_referenz_note": (
                        "Anschaffungs-Referenz nur für externe Zuflüsse (Lots); "
                        "nicht für Change/interne Umbuchungen."
                    ),
                }
            out.update(acq_fields)
            out.update(spot_fields)
            enriched.append(out)

        buf = io.StringIO()
        writer = csv.DictWriter(
            buf, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        if enriched:
            writer.writerows(enriched)
        else:
            # Header-only + one spot placeholder so UI can still Save.
            empty = {c: "" for c in CSV_COLUMNS}
            empty.update(spot_fields)
            empty["anschaffungs_referenz_label"] = "Anschaffungs-Referenz"
            empty["anschaffungs_referenz_usd"] = HISTORICAL_UNAVAILABLE
            empty["anschaffungs_referenz_eur"] = HISTORICAL_UNAVAILABLE
            writer.writerow(empty)

        text = buf.getvalue()
        return ReportArtifact(
            kind="csv",
            content=text,
            path=None,
            rows_or_pages=len(enriched),
            message=(
                "CSV generated in memory — Save explicitly in UI. "
                "No file written to disk. REFERENZWERT ≠ Kaufpreis; "
                "Anschaffungs-Referenz ≠ Kostenbasis. No tax advice."
            ),
            meta={"spot": spot_fields, "disk_written": False, "columns": CSV_COLUMNS},
        )

    def build_pdf(
        self,
        payload: dict[str, Any],
        *,
        fetch_spot: bool = True,
        dest: Any = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> ReportArtifact:
        """Return a simple real PDF as bytes in memory (fpdf2)."""
        _ = dest
        spot = self._oracle.get_spot() if fetch_spot else None
        spot_fields = spot.as_report_fields() if spot else {
            "referenzwert_usd": SPOT_UNAVAILABLE,
            "referenzwert_eur": SPOT_UNAVAILABLE,
            "referenzwert_timestamp_utc": "",
            "referenzwert_source": "",
        }
        report_rows = rows if rows is not None else list(payload.get("rows") or [])
        try:
            pdf_bytes = self._render_pdf_fpdf(payload, spot_fields, report_rows)
            kind_note = "PDF (fpdf2) bytes in memory"
        except Exception as exc:  # noqa: BLE001 — fail-open to minimal PDF
            pdf_bytes = self._render_pdf_minimal(payload, spot_fields, report_rows)
            kind_note = f"PDF minimal fallback ({exc})"

        return ReportArtifact(
            kind="pdf",
            content=pdf_bytes,
            path=None,
            rows_or_pages=max(1, len(report_rows)),
            message=(
                f"{kind_note} — Print/Save explicitly. "
                "No file written to disk. No tax advice."
            ),
            meta={"spot": spot_fields, "disk_written": False},
        )

    def _render_pdf_fpdf(
        self,
        payload: dict[str, Any],
        spot_fields: dict[str, str],
        rows: list[dict[str, Any]],
    ) -> bytes:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "BTC-Herkunft Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=9)
        pdf.multi_cell(
            0,
            5,
            "Keine Steuerberatung. Keine Investmentberatung. "
            "Nur Dokumentationshilfe (Anlage-SO Zuarbeit).",
        )
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "REFERENZWERT (aktueller Markt) — KEIN Kaufpreis", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=9)
        pdf.cell(
            0,
            5,
            f"USD: {spot_fields.get('referenzwert_usd', SPOT_UNAVAILABLE)}  |  "
            f"EUR: {spot_fields.get('referenzwert_eur', SPOT_UNAVAILABLE)}",
            ln=True,
        )
        pdf.cell(
            0,
            5,
            f"UTC: {spot_fields.get('referenzwert_timestamp_utc', '')}  |  "
            f"Quelle: {spot_fields.get('referenzwert_source', '')}",
            ln=True,
        )
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(
            0,
            7,
            "Anschaffungs-Referenz = historischer Kurs je Zufluss (fail-open)",
            ln=True,
        )
        pdf.set_font("Helvetica", size=8)
        pdf.cell(
            0,
            5,
            f"Flows in payload: {payload.get('flow_count', len(rows))}  |  "
            f"Rows listed: {len(rows)}",
            ln=True,
        )
        pdf.ln(2)

        # Compact table header
        if rows:
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(22, 5, "Date", border=1)
            pdf.cell(28, 5, "Amount sats", border=1)
            pdf.cell(14, 5, "Dir", border=1)
            pdf.cell(18, 5, "Int/Ext", border=1)
            pdf.cell(18, 5, "Haltefrist", border=1)
            pdf.cell(40, 5, "Evidence", border=1)
            pdf.cell(0, 5, "Anschaffungs-Ref EUR", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", size=7)
            for raw in rows[:40]:
                base = _normalize_row_base(raw, self._clock)
                inflow = base.get("inflow_date")
                eur = ""
                if inflow and base.get("direction") == "in":
                    try:
                        d = (
                            inflow
                            if isinstance(inflow, date)
                            else date.fromisoformat(str(inflow)[:10])
                        )
                        eur = self._oracle.get_acquisition_reference(d).as_report_fields()[
                            "anschaffungs_referenz_eur"
                        ]
                    except ValueError:
                        eur = HISTORICAL_UNAVAILABLE
                pdf.cell(22, 5, str(base.get("date") or "")[:10], border=1)
                pdf.cell(28, 5, str(base.get("amount_sats") or "")[:16], border=1)
                pdf.cell(14, 5, str(base.get("direction") or "")[:6], border=1)
                pdf.cell(18, 5, str(base.get("internal_external") or "")[:8], border=1)
                pdf.cell(18, 5, str(base.get("haltefrist_hint") or "")[:8], border=1)
                evid = str(base.get("evidence_gap_status") or "")[:22]
                pdf.cell(40, 5, evid, border=1)
                pdf.cell(0, 5, str(eur)[:18], border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if len(rows) > 40:
                pdf.cell(0, 5, f"... {len(rows) - 40} further rows omitted in PDF view", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.ln(4)
        pdf.set_font("Helvetica", "I", 8)
        pdf.multi_cell(
            0,
            4,
            "Disk-Written: false. Session memory only. "
            "REFERENZWERT ≠ Kaufpreis. Anschaffungs-Referenz ≠ Kostenbasis. "
            "Kaufnachweis fehlt → never silent cost basis 0.",
        )
        out = pdf.output()
        if isinstance(out, (bytes, bytearray)):
            return bytes(out)
        return str(out).encode("latin-1")

    def _render_pdf_minimal(
        self,
        payload: dict[str, Any],
        spot_fields: dict[str, str],
        rows: list[dict[str, Any]],
    ) -> bytes:
        """Tiny valid PDF without external deps (fallback)."""
        lines = [
            "BTC-Herkunft Report",
            "Keine Steuerberatung. Keine Investmentberatung.",
            "",
            "REFERENZWERT (aktueller Markt) — KEIN Kauf-/Anschaffungspreis:",
            f"  USD: {spot_fields.get('referenzwert_usd', SPOT_UNAVAILABLE)}",
            f"  EUR: {spot_fields.get('referenzwert_eur', SPOT_UNAVAILABLE)}",
            f"  UTC: {spot_fields.get('referenzwert_timestamp_utc', '')}",
            f"  Quelle: {spot_fields.get('referenzwert_source', '')}",
            "",
            f"Flow count: {payload.get('flow_count', len(rows))}",
            "Anschaffungs-Referenz = historischer Kurs je Zufluss (fail-open).",
            "Disk-Written: false",
        ]
        text = "\n".join(lines)
        # Escape for PDF literal string
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        # Simple one-page PDF
        stream = f"BT /F1 10 Tf 50 750 Td 12 TL ({escaped}) Tj ET"
        # Multi-line: use T* roughly via separate texts — keep single block escaped newlines as spaces
        stream = "BT /F1 9 Tf 50 780 Td 11 TL\n"
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream += f"({esc}) '\n"
        stream += "ET"
        stream_bytes = stream.encode("latin-1", errors="replace")
        objects: list[bytes] = []
        objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
        objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
        objects.append(
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        )
        objects.append(
            f"4 0 obj<< /Length {len(stream_bytes)} >>stream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream endobj\n"
        )
        objects.append(
            b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
        )
        out = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for obj in objects:
            offsets.append(len(out))
            out.extend(obj)
        xref_pos = len(out)
        out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        out.extend(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
        out.extend(
            f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
        )
        return bytes(out)
