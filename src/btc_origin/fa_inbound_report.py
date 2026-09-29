"""Finanzamt-oriented inbound PDF — remaining session lots only.

Builds an in-memory PDF from **current session** remaining FIFO lots
(same basis as GET /api/cloud/lots). Hot-Wallet-Cloud = wallets registered
in the *ephemeral session* (paste), not a fixed address list in Git.

Two tables as of **Stichtag**:
  1. „Coins aus der Haltefrist“ — holding period elapsed (≥365 days)
  2. „Coins die einen Nachweis benötigen“ — still within Haltefrist

Columns (both tables):
  Eintrittszeitpunkt in die Wallet Cloud | Anzahl | Wert zu der Zeit |
  Wallet auf der das jetzt liegt

Hard rules: Keine Steuerberatung; Anschaffungs-Referenz ≠ Kostenbasis;
Disk-Written false; only on explicit request. Never invents prices or txs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from btc_origin.holding_clock import HOLDING_DAYS, HoldingClock, Lot, lot_to_genealogy_dict
from btc_origin.price_oracle import HISTORICAL_UNAVAILABLE, PriceOracle
from btc_origin.report_builder import ReportArtifact
from btc_origin.wallet_registry import WalletEntry

# Page / footer disclaimers (every page).
DISCLAIMER_NO_TAX = "Keine Steuerberatung."
DISCLAIMER_REF = "Kurse = Anschaffungs-Referenz ≠ Kostenbasis."
DISCLAIMER_DISK = "Disk-Written: false — nur auf ausdrückliche Anfrage erzeugt."
DISCLAIMER_CLOUD = (
    "Hot-Wallet-Cloud = Sitzungs-Cloud (ephemeral), keine Steuerberatung."
)

TABLE_HALTEFRIST = "Coins aus der Haltefrist"
TABLE_NACHWEIS = "Coins die einen Nachweis benötigen"
EMPTY_TABLE_NOTE = "keine Positionen"

COL_EINTRITT = "Eintrittszeitpunkt in die Wallet Cloud"
COL_ANZAHL = "Anzahl"
COL_WERT = "Wert zu der Zeit"
COL_WALLET = "Wallet auf der das jetzt liegt"

# Privacy / Demo-Maskierung (matches frontend/src/privacy.tsx)
MASK_NAME_PDF = "********"
MASK_BTC_PDF = "*.********"
MASK_ADDR_PDF = "************"
MASK_XPUB_PDF = "************************"

PRICE_MISSING_NOTE_DE = (
    "Kurs nicht ermittelbar — Anschaffungs-Referenz fehlt (fail-open); "
    "kein erfundener Wert."
)
PRICE_MISSING_SHORT = "Kurs nicht ermittelbar"

_DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# Kept for backward-compat imports in older tests; no longer rendered as column.
HALTEFRIST_CHECK = "✓"
LEGEND_HALTEFRIST = (
    f"Tabelle 1 = {TABLE_HALTEFRIST}; Tabelle 2 = {TABLE_NACHWEIS} "
    f"(Bewertung gegen Stichtag, ≥{HOLDING_DAYS} Tage)."
)


def mask_xpub(xpub: str | None, *, head: int = 8, tail: int = 6) -> str:
    """Short public fingerprint: first/last chars of xpub (never a secret)."""
    if not xpub:
        return ""
    s = str(xpub).strip()
    if len(s) <= head + tail + 1:
        return s
    return f"{s[:head]}…{s[-tail:]}"


def session_cloud_lines(wallets: Iterable[WalletEntry | dict[str, Any]]) -> list[str]:
    """Human lines for the Hot-Wallet-Cloud header (session registry only)."""
    lines: list[str] = []
    for w in wallets:
        if isinstance(w, WalletEntry):
            name = w.name
            kind = w.kind
            xpub = w.xpub
            address = w.address
            wid = w.id
        else:
            name = str(w.get("name") or "")
            kind = str(w.get("kind") or "")
            xpub = w.get("xpub")
            address = w.get("address")
            wid = w.get("id")
        parts = [f"#{wid}" if wid is not None else "#?", name or "(ohne Name)", f"[{kind}]"]
        if xpub:
            parts.append(f"xpub {mask_xpub(str(xpub))}")
        if address:
            addr = str(address)
            if len(addr) > 20:
                addr = f"{addr[:10]}…{addr[-6:]}"
            parts.append(f"addr {addr}")
        lines.append(" · ".join(parts))
    return lines


def resolve_current_address(lot_payload: dict[str, Any]) -> str:
    """Prefer explicit current_address, else last path hop, else origin address."""
    cur = lot_payload.get("current_address")
    if cur:
        return str(cur)
    path = lot_payload.get("path") or []
    if path:
        last = path[-1] or {}
        if isinstance(last, dict) and last.get("address"):
            return str(last["address"])
    origin = lot_payload.get("origin") or {}
    if isinstance(origin, dict) and origin.get("address"):
        return str(origin["address"])
    return str(lot_payload.get("address") or "")


def resolve_current_wallet_name(
    lot_payload: dict[str, Any],
    wallet_names: dict[int, str] | None = None,
) -> str:
    """Session registry wallet name where remaining sats currently sit."""
    name = lot_payload.get("current_wallet_name")
    if name:
        return str(name)
    names = wallet_names or {}
    wid = lot_payload.get("current_wallet_id")
    if wid is not None:
        mapped = names.get(int(wid))
        if mapped:
            return str(mapped)
    origin = lot_payload.get("origin") or {}
    if isinstance(origin, dict) and origin.get("wallet_name"):
        return str(origin["wallet_name"])
    return ""


def sats_to_btc_str(sats: int) -> str:
    return f"{int(sats) / 100_000_000:.8f}"


def format_eur_value(eur_per_btc: float, remaining_sats: int) -> str:
    """remaining_sats × historical EUR/BTC → EUR string (2 decimals)."""
    value = (remaining_sats / 100_000_000.0) * float(eur_per_btc)
    return f"{value:.2f}"


def default_stichtag(today: date | None = None) -> date:
    """31.12. of the current calendar year (Europe/Berlin / local date fine)."""
    end = today or datetime.now(timezone.utc).date()
    return date(end.year, 12, 31)


def parse_stichtag(value: Any, *, fallback: date | None = None) -> date:
    """Parse ISO YYYY-MM-DD; raise ValueError on invalid non-empty input."""
    if value is None or value == "":
        return fallback if fallback is not None else default_stichtag()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    return date.fromisoformat(text[:10])


@dataclass
class FaInboundRow:
    """One remaining external inbound lot for the FA inbound PDF."""

    eintrittszeitpunkt: str
    anzahl_btc: str
    remaining_sats: int
    wert_zu_der_zeit: str
    wallet_aktuell: str
    wallet_address: str = ""
    qualifies_haltefrist: bool = False
    days_held: int = 0
    lot_id: str = ""
    price_missing: bool = False
    note: str = ""
    # Legacy aliases kept in as_dict for older callers / tests.
    eingangsdatum: str = ""
    btc_preis_eur: str = ""
    zieladresse_heute: str = ""
    haltefrist_mark: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "eintrittszeitpunkt": self.eintrittszeitpunkt,
            "anzahl_btc": self.anzahl_btc,
            "remaining_sats": self.remaining_sats,
            "wert_zu_der_zeit": self.wert_zu_der_zeit,
            "wallet_aktuell": self.wallet_aktuell,
            "wallet_address": self.wallet_address,
            "qualifies_haltefrist": self.qualifies_haltefrist,
            "days_held": self.days_held,
            "lot_id": self.lot_id,
            "price_missing": self.price_missing,
            "note": self.note,
            # Legacy keys
            "eingangsdatum": self.eingangsdatum or self.eintrittszeitpunkt,
            "btc_preis_eur": self.btc_preis_eur or self.wert_zu_der_zeit,
            "zieladresse_heute": self.zieladresse_heute
            or self.wallet_address
            or self.wallet_aktuell,
            "haltefrist_mark": self.haltefrist_mark
            or (HALTEFRIST_CHECK if self.qualifies_haltefrist else ""),
        }


@dataclass
class FaInboundPayload:
    rows_haltefrist: list[FaInboundRow] = field(default_factory=list)
    rows_nachweis: list[FaInboundRow] = field(default_factory=list)
    cloud_lines: list[str] = field(default_factory=list)
    generated_at: str = ""
    stichtag: str = ""
    as_of: str = ""  # alias of stichtag
    ephemeral: bool = True
    disk_written: bool = False
    note: str = (
        "FA-Inbound aus Sitzungs-Lots. Hot-Wallet-Cloud = ephemeral paste session. "
        "Keine Steuerberatung. Anschaffungs-Referenz ≠ Kostenbasis."
    )

    @property
    def rows(self) -> list[FaInboundRow]:
        """All rows (haltefrist first, then nachweis) for legacy callers."""
        return list(self.rows_haltefrist) + list(self.rows_nachweis)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_haltefrist": [r.as_dict() for r in self.rows_haltefrist],
            "rows_nachweis": [r.as_dict() for r in self.rows_nachweis],
            "rows": [r.as_dict() for r in self.rows],
            "cloud_lines": list(self.cloud_lines),
            "generated_at": self.generated_at,
            "stichtag": self.stichtag or self.as_of,
            "as_of": self.as_of or self.stichtag,
            "ephemeral": self.ephemeral,
            "disk_written": self.disk_written,
            "note": self.note,
            "count": len(self.rows),
            "count_haltefrist": len(self.rows_haltefrist),
            "count_nachweis": len(self.rows_nachweis),
            "table_haltefrist": TABLE_HALTEFRIST,
            "table_nachweis": TABLE_NACHWEIS,
            "columns": [COL_EINTRITT, COL_ANZAHL, COL_WERT, COL_WALLET],
        }



def cloud_entry_date(payload: dict[str, Any]) -> date | None:
    """FA Eintrittszeitpunkt = first external cloud inflow date.

    Prefer ``path[0].date`` when ``path[0].kind == "inflow"``; else ``lot_date``
    / ``acquisition_date``. They must agree when both are present.
    """
    path = payload.get("path") or []
    path0_day = None
    if path:
        step0 = path[0]
        if isinstance(step0, dict) and step0.get("kind") == "inflow":
            path0_day = _parse_day(step0.get("date"))
        elif hasattr(step0, "kind") and getattr(step0, "kind", None) == "inflow":
            path0_day = _parse_day(getattr(step0, "date", None))
    lot_day = _parse_day(payload.get("lot_date") or payload.get("acquisition_date"))
    if path0_day is not None and lot_day is not None and path0_day != lot_day:
        # Prefer explicit cloud-entry hop; lot_date should match — keep path[0].
        return path0_day
    return path0_day or lot_day


def apply_privacy_to_row(row: FaInboundRow) -> FaInboundRow:
    """Mask identifying fields for Privacy/Demo export (amounts + names + addrs).

    Bitcoin addresses (bc1/1/3/tb1…) must not appear — full mask, no prefix.
    """
    row.wallet_aktuell = MASK_NAME_PDF
    # Always mask address fields (even if empty → stay empty; never leave bc1…)
    if row.wallet_address:
        row.wallet_address = MASK_ADDR_PDF
    if row.zieladresse_heute:
        row.zieladresse_heute = MASK_ADDR_PDF
    row.anzahl_btc = MASK_BTC_PDF
    # Dates + Wert stay (Wert is EUR reference, not an address); amounts masked.
    return row


def apply_privacy_to_cloud_lines(lines: list[str]) -> list[str]:
    """Mask wallet cloud header lines for Privacy export."""
    out: list[str] = []
    for i, _ln in enumerate(lines):
        out.append(f"#{i + 1} · {MASK_NAME_PDF} · [****] · {MASK_XPUB_PDF}")
    return out


def _parse_day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def build_fa_inbound_rows(
    lots: list[dict[str, Any]] | list[Lot],
    oracle: PriceOracle,
    *,
    as_of: date | None = None,
    stichtag: date | None = None,
    holding_clock: HoldingClock | None = None,
    wallet_names: dict[int, str] | None = None,
) -> list[FaInboundRow]:
    """Map remaining lots → FA inbound rows (Haltefrist evaluated vs Stichtag)."""
    clock = holding_clock or HoldingClock()
    end = stichtag or as_of or default_stichtag()
    threshold = clock.threshold_days or HOLDING_DAYS
    rows: list[FaInboundRow] = []
    oracle.prefetch_historical(
        item.acquisition_date
        if isinstance(item, Lot)
        else (cloud_entry_date(item) or _parse_day(item.get("lot_date")))
        for item in lots
    )

    for item in lots:
        if isinstance(item, Lot):
            if item.remaining_sats <= 0:
                continue
            payload = lot_to_genealogy_dict(
                item,
                as_of=end,
                wallet_names=wallet_names,
                threshold_days=threshold,
            )
        else:
            payload = dict(item)
            if int(payload.get("remaining_sats") or 0) <= 0:
                continue
            # Re-evaluate Haltefrist against Stichtag when lot_date present.
            day_for_hold = _parse_day(payload.get("lot_date"))
            if day_for_hold is not None:
                days = (end - day_for_hold).days
                payload["days_held"] = days
                payload["qualifies_haltefrist"] = days >= threshold

        day = cloud_entry_date(payload)
        if day is None:
            day = _parse_day(payload.get("lot_date"))
        eintritt = day.isoformat() if day else str(payload.get("lot_date") or "")[:10]

        remaining = int(payload.get("remaining_sats") or 0)
        anzahl = sats_to_btc_str(remaining)

        wert = PRICE_MISSING_SHORT
        price_missing = True
        note = ""
        unit_eur: str = HISTORICAL_UNAVAILABLE
        if day is not None:
            acq = oracle.get_acquisition_reference(day)
            fields = acq.as_report_fields()
            eur_val = fields.get("anschaffungs_referenz_eur") or HISTORICAL_UNAVAILABLE
            unit_eur = str(eur_val)
            available = bool(getattr(acq, "available", False))
            if available and unit_eur != HISTORICAL_UNAVAILABLE and unit_eur.strip():
                try:
                    wert = format_eur_value(float(unit_eur), remaining)
                    price_missing = False
                except (TypeError, ValueError):
                    wert = PRICE_MISSING_SHORT
                    price_missing = True
                    note = PRICE_MISSING_NOTE_DE
            else:
                wert = PRICE_MISSING_SHORT
                price_missing = True
                note = PRICE_MISSING_NOTE_DE
        else:
            note = PRICE_MISSING_NOTE_DE

        if "days_held" in payload and payload.get("days_held") is not None:
            days_held = int(payload["days_held"])
        elif day is not None:
            days_held = (end - day).days
        else:
            days_held = 0

        if "qualifies_haltefrist" in payload and payload.get("qualifies_haltefrist") is not None:
            qualifies = bool(payload.get("qualifies_haltefrist"))
        else:
            qualifies = days_held >= threshold

        wallet_name = resolve_current_wallet_name(payload, wallet_names)
        addr = resolve_current_address(payload)

        rows.append(
            FaInboundRow(
                eintrittszeitpunkt=eintritt,
                anzahl_btc=anzahl,
                remaining_sats=remaining,
                wert_zu_der_zeit=wert,
                wallet_aktuell=wallet_name or "(ohne Name)",
                wallet_address=addr,
                qualifies_haltefrist=qualifies,
                days_held=days_held,
                lot_id=str(payload.get("lot_id") or ""),
                price_missing=price_missing,
                note=note,
                eingangsdatum=eintritt,
                btc_preis_eur=unit_eur if not price_missing else HISTORICAL_UNAVAILABLE,
                zieladresse_heute=addr,
                haltefrist_mark=HALTEFRIST_CHECK if qualifies else "",
            )
        )
    return rows


def split_fa_inbound_rows(
    rows: list[FaInboundRow],
) -> tuple[list[FaInboundRow], list[FaInboundRow]]:
    """Split into (aus Haltefrist, Nachweis nötig)."""
    haltefrist = [r for r in rows if r.qualifies_haltefrist]
    nachweis = [r for r in rows if not r.qualifies_haltefrist]
    return haltefrist, nachweis


def build_fa_inbound_payload(
    *,
    lots: list[dict[str, Any]] | list[Lot],
    wallets: Iterable[WalletEntry | dict[str, Any]],
    oracle: PriceOracle,
    as_of: date | None = None,
    stichtag: date | None = None,
    holding_clock: HoldingClock | None = None,
    wallet_names: dict[int, str] | None = None,
    privacy: bool = False,
) -> FaInboundPayload:
    end = stichtag or as_of or default_stichtag()
    all_rows = build_fa_inbound_rows(
        lots,
        oracle,
        as_of=end,
        stichtag=end,
        holding_clock=holding_clock,
        wallet_names=wallet_names,
    )
    if privacy:
        all_rows = [apply_privacy_to_row(r) for r in all_rows]
    haltefrist, nachweis = split_fa_inbound_rows(all_rows)
    iso = end.isoformat()
    cloud = session_cloud_lines(wallets)
    if privacy:
        cloud = apply_privacy_to_cloud_lines(cloud)
    return FaInboundPayload(
        rows_haltefrist=haltefrist,
        rows_nachweis=nachweis,
        cloud_lines=cloud,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        stichtag=iso,
        as_of=iso,
    )


def _table_header_row(pdf: Any, font: str, col_w: list[int]) -> None:
    pdf.set_font(font, "B", 7)
    headers = [COL_EINTRITT, COL_ANZAHL, COL_WERT, COL_WALLET]
    for w, h in zip(col_w, headers):
        label = h if len(h) <= 28 else h[:26] + "…"
        pdf.cell(w, 7, label, border=1)
    pdf.ln()
    pdf.set_font(font, "", 7)


def _ensure_table_space(
    pdf: Any,
    font: str,
    col_w: list[int],
    *,
    need_mm: float,
    title: str | None = None,
    text_block: Any,
    left: Any,
) -> None:
    """Page-break before drawing if the next block would collide with the footer."""
    # Footer reserved ~22mm; leave a little headroom.
    bottom_limit = pdf.h - pdf.b_margin - 4
    if pdf.get_y() + need_mm <= bottom_limit:
        return
    pdf.add_page()
    left()
    if title:
        text_block(10, title + " (Fortsetzung)", bold=True, h=6)
        left()
    _table_header_row(pdf, font, col_w)


def _render_table_fpdf(
    pdf: Any,
    font: str,
    title: str,
    rows: list[FaInboundRow],
    *,
    text_block: Any,
    left: Any,
) -> None:
    text_block(10, title, bold=True, h=6)
    left()
    # A4 usable ~190mm; proportions for long German headers.
    col_w = [48, 28, 42, 72]
    _table_header_row(pdf, font, col_w)

    if not rows:
        _ensure_table_space(
            pdf, font, col_w, need_mm=8, title=None, text_block=text_block, left=left
        )
        text_block(8, EMPTY_TABLE_NOTE, h=5)
        pdf.ln(1)
        return

    for row in rows:
        # Row + optional price-missing note
        need = 5.5 + (4.0 if row.price_missing and row.note else 0.0)
        _ensure_table_space(
            pdf,
            font,
            col_w,
            need_mm=need,
            title=title,
            text_block=text_block,
            left=left,
        )
        left()
        wallet = row.wallet_aktuell or "(ohne Name)"
        addr = row.wallet_address or ""
        # Never show real address suffixes when Privacy-masked (no bc1/1/3/tb1 leak).
        if addr and addr != MASK_ADDR_PDF and not addr.startswith("*") and len(wallet) < 30:
            if len(addr) > 18:
                addr = f"{addr[:8]}…{addr[-6:]}"
            wallet_cell = f"{wallet} ({addr})"
        else:
            wallet_cell = wallet
        if len(wallet_cell) > 40:
            wallet_cell = wallet_cell[:38] + "…"

        wert = row.wert_zu_der_zeit
        if len(wert) > 24:
            wert = wert[:24]

        cells = [
            (col_w[0], (row.eintrittszeitpunkt or "")[:19]),
            (col_w[1], row.anzahl_btc),
            (col_w[2], wert),
            (col_w[3], wallet_cell),
        ]
        for w, cell_text in cells:
            pdf.cell(w, 5, cell_text, border=1)
        pdf.ln()
        if row.price_missing and row.note:
            text_block(6, f"  → {row.note}", h=3.2)
            pdf.set_font(font, "", 7)
    pdf.ln(2)


def _render_fa_inbound_fpdf(payload: FaInboundPayload) -> bytes:
    """Render FA-Inbound PDF with DejaVu (umlauts) when available."""
    from fpdf import FPDF

    use_dejavu = _DEJAVU.is_file()
    font = "DejaVu" if use_dejavu else "Helvetica"

    class Doc(FPDF):
        def footer(self) -> None:
            self.set_y(-18)
            self.set_x(self.l_margin)
            self.set_font(font, size=7)
            self.multi_cell(
                0,
                3.2,
                f"{DISCLAIMER_NO_TAX} {DISCLAIMER_REF} {DISCLAIMER_DISK} "
                f"Seite {self.page_no()}/{{nb}}",
            )

    pdf = Doc(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=22)
    if use_dejavu:
        pdf.add_font("DejaVu", "", str(_DEJAVU))
        bold_path = _DEJAVU_BOLD if _DEJAVU_BOLD.is_file() else _DEJAVU
        pdf.add_font("DejaVu", "B", str(bold_path))
    pdf.alias_nb_pages()
    pdf.add_page()

    def left() -> None:
        pdf.set_x(pdf.l_margin)

    def text_block(size: int, body: str, *, bold: bool = False, h: float = 4.0) -> None:
        left()
        pdf.set_font(font, "B" if bold else "", size)
        pdf.multi_cell(0, h, body)

    stichtag = payload.stichtag or payload.as_of
    text_block(13, "BTC-Herkunft — FA-Inbound (Finanzamt-Zuarbeit)", bold=True, h=8)
    text_block(11, f"Stichtag: {stichtag}", bold=True, h=7)
    text_block(
        8,
        f"{DISCLAIMER_NO_TAX} {DISCLAIMER_CLOUD} "
        f"erzeugt: {payload.generated_at}",
        h=4,
    )
    pdf.ln(1)

    text_block(10, "Hot-Wallet-Cloud (Sitzungs-Cloud, ephemeral)", bold=True, h=6)
    if payload.cloud_lines:
        for line in payload.cloud_lines:
            text_block(8, f"• {line}", h=4)
    else:
        text_block(8, "(leer — keine Wallets in dieser Sitzung registriert)", h=4)
    text_block(
        7,
        "Hinweis: Keine festen Adressen aus dem Git-Repo — nur die aktuelle "
        "Paste-Sitzung. " + DISCLAIMER_NO_TAX,
        h=3.5,
    )
    pdf.ln(2)

    text_block(8, LEGEND_HALTEFRIST + " " + DISCLAIMER_REF, h=4)
    pdf.ln(1)

    _render_table_fpdf(
        pdf,
        font,
        TABLE_HALTEFRIST,
        payload.rows_haltefrist,
        text_block=text_block,
        left=left,
    )
    _render_table_fpdf(
        pdf,
        font,
        TABLE_NACHWEIS,
        payload.rows_nachweis,
        text_block=text_block,
        left=left,
    )

    pdf.ln(2)
    text_block(
        7,
        f"{DISCLAIMER_NO_TAX} {DISCLAIMER_REF} {DISCLAIMER_DISK} "
        "Nur Dokumentationshilfe (Anlage-SO Zuarbeit).",
        h=3.5,
    )

    out = pdf.output()
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    return str(out).encode("latin-1", errors="replace")


def build_fa_inbound_pdf(
    *,
    lots: list[dict[str, Any]] | list[Lot],
    wallets: Iterable[WalletEntry | dict[str, Any]],
    oracle: PriceOracle | None = None,
    as_of: date | None = None,
    stichtag: date | None = None,
    holding_clock: HoldingClock | None = None,
    wallet_names: dict[int, str] | None = None,
    privacy: bool = False,
) -> ReportArtifact:
    """Return FA-inbound PDF bytes in memory (never writes disk)."""
    oracle = oracle or PriceOracle()
    end = stichtag or as_of or default_stichtag()
    payload = build_fa_inbound_payload(
        lots=lots,
        wallets=wallets,
        oracle=oracle,
        as_of=end,
        stichtag=end,
        holding_clock=holding_clock,
        wallet_names=wallet_names,
        privacy=privacy,
    )
    try:
        pdf_bytes = _render_fa_inbound_fpdf(payload)
        kind_note = "FA-Inbound PDF (fpdf2) bytes in memory"
    except Exception as exc:  # noqa: BLE001 — fail-open minimal PDF
        pdf_bytes = _render_fa_inbound_minimal(payload)
        kind_note = f"FA-Inbound PDF minimal fallback ({exc})"

    return ReportArtifact(
        kind="pdf",
        content=pdf_bytes,
        path=None,
        rows_or_pages=max(1, len(payload.rows)),
        message=(
            f"{kind_note} — explicit Save only. Disk-Written: false. "
            "Keine Steuerberatung. Anschaffungs-Referenz ≠ Kostenbasis. "
            "Hot-Wallet-Cloud = Sitzungs-Cloud (ephemeral)."
        ),
        meta={
            "disk_written": False,
            "kind": "fa-inbound",
            "ephemeral": True,
            "stichtag": payload.stichtag,
            "privacy": bool(privacy),
            "cloud_count": len(payload.cloud_lines),
            "row_count": len(payload.rows),
            "count_haltefrist": len(payload.rows_haltefrist),
            "count_nachweis": len(payload.rows_nachweis),
            "legend": LEGEND_HALTEFRIST,
            "columns": [COL_EINTRITT, COL_ANZAHL, COL_WERT, COL_WALLET],
            "payload": payload.as_dict(),
        },
    )


def _render_fa_inbound_minimal(payload: FaInboundPayload) -> bytes:
    """Tiny valid PDF without Unicode font deps (fallback)."""
    stichtag = payload.stichtag or payload.as_of
    lines = [
        "BTC-Herkunft FA-Inbound",
        DISCLAIMER_NO_TAX,
        DISCLAIMER_REF,
        DISCLAIMER_DISK,
        DISCLAIMER_CLOUD,
        f"Stichtag: {stichtag}",
        f"Legende: {LEGEND_HALTEFRIST}",
        "",
        "Hot-Wallet-Cloud:",
    ]
    if payload.cloud_lines:
        lines.extend(f"  - {ln}" for ln in payload.cloud_lines)
    else:
        lines.append("  (leer)")

    def _table(title: str, rows: list[FaInboundRow]) -> None:
        lines.append("")
        lines.append(title)
        lines.append(
            f"{COL_EINTRITT} | {COL_ANZAHL} | {COL_WERT} | {COL_WALLET}"
        )
        if not rows:
            lines.append(EMPTY_TABLE_NOTE)
            return
        for row in rows:
            lines.append(
                f"{row.eintrittszeitpunkt} | {row.anzahl_btc} | "
                f"{row.wert_zu_der_zeit} | {row.wallet_aktuell}"
            )
            if row.price_missing:
                lines.append(f"  note: {PRICE_MISSING_NOTE_DE}")

    _table(TABLE_HALTEFRIST, payload.rows_haltefrist)
    _table(TABLE_NACHWEIS, payload.rows_nachweis)

    stream = "BT /F1 8 Tf 40 800 Td 10 TL\n"
    for line in lines[:80]:
        esc = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .encode("latin-1", errors="replace")
            .decode("latin-1")
        )
        stream += f"({esc}) '\n"
    stream += "ET"
    stream_bytes = stream.encode("latin-1", errors="replace")
    objects: list[bytes] = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        ),
        (
            f"4 0 obj<< /Length {len(stream_bytes)} >>stream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream endobj\n"
        ),
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
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
