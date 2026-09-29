"""Struktur-Prüfung eines Börsen-Exports — ohne Beträge, Adressen oder IDs.

Aufruf (im Projektordner, Windows):

    .venv\\Scripts\\python.exe -m btc_origin.export_check local\\boersen\\strike.csv
    .venv\\Scripts\\python.exe -m btc_origin.export_check local\\boersen\\strike.csv --datum 2024-03-15

Die Ausgabe ist zum Weitergeben gedacht: Spaltennamen, Zeilenarten, je Zeile nur
Datum, Art, Vorzeichen/Nachkommastellen der Zahlen und Verhältnisse in Prozent
(z. B. Gebühr zu Menge). Zahlen, Adressen, Transaktions-IDs und E-Mail-Adressen
werden maskiert. Liest nur, schreibt nichts.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from btc_origin.local_files import (
    _parse_decimal,
    _read_text,
    _row_dates,
    _skip_preamble,
    exchange_name_from_file,
    parse_trades,
)

_TYPE_COLS = ("transactiontype", "transaction type", "type", "operation", "side", "art", "kind")
_HEX = re.compile(r"[0-9a-fA-F]{16,}")
_ADDR = re.compile(r"\b(bc1[0-9a-z]{8,}|[13][1-9A-HJ-NP-Za-km-z]{25,34}|lnbc[0-9a-z]+)\b", re.IGNORECASE)
_MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_NUM = re.compile(r"^[\s€$£]*[-+−]?[\s€$£]*[\d.,']+\s*[A-Za-z]{0,5}$")


def _shape(cell: str) -> str:
    """Zahl → Form ohne Wert (Vorzeichen, Nachkommastellen, Einheit)."""
    c = cell.strip()
    if not c:
        return ""
    if _NUM.match(c):
        neg = "−" if re.search(r"[-−]", c) else ""
        unit = re.sub(r"[\d.,'\s€$£+\-−]", "", c)
        dec = re.search(r"[.,](\d+)\D*$", c)
        places = len(dec.group(1)) if dec else 0
        return f"{neg}Zahl({places} NK){' ' + unit if unit else ''}"
    if _row_dates(c):
        return c  # Datum/Zeit bleibt sichtbar
    c = _MAIL.sub("<E-Mail>", c)
    c = _ADDR.sub("<Adresse>", c)
    c = _HEX.sub("<ID>", c)
    c = re.sub(r"\d+([.,]\d+)?", "#", c)  # übrige Zahlen im Text
    return c if len(c) <= 40 else c[:37] + "…"


def _num(cell: str) -> float | None:
    c = re.sub(r"[^\d.,\-−]", "", cell or "").replace("−", "-")
    return _parse_decimal(c) if c else None


def check(path: Path, day: date | None = None, per_type: int = 2) -> str:
    text = _read_text(path)
    lines = _skip_preamble([ln for ln in text.splitlines() if ln.strip()])
    out: list[str] = [f"Datei: {path.name} · Börse laut Dateiname: {exchange_name_from_file(path)}"]
    if len(lines) < 2:
        return "\n".join(out + ["Keine Datenzeilen erkannt."])
    delim = max(("\t", ";", ","), key=lines[0].count)
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    head = [h.strip() for h in rows[0]]
    out.append(f"Trennzeichen: {delim!r} · Spalten ({len(head)}): " + " | ".join(head))
    low = [h.lower() for h in head]
    tcol = next((i for i, h in enumerate(low) if h in _TYPE_COLS), None)
    btc_cols = [i for i, h in enumerate(low) if ("bitcoin" in h or "btc" in h or h in ("amount", "quantity"))
                and "price" not in h and "kurs" not in h]
    fee_cols = [i for i in btc_cols if "fee" in low[i] or "gebühr" in low[i]]
    qty_cols = [i for i in btc_cols if i not in fee_cols]
    types = Counter((r[tcol].strip() if tcol is not None and tcol < len(r) else "?") for r in rows[1:])
    out.append("Zeilenarten: " + ", ".join(f"{k or '(leer)'} ×{v}" for k, v in types.most_common()))

    id_cols = {i for i, h in enumerate(low)
               if re.search(r"(^|[^a-z])id$|id$|hash|reference|txid|address|adresse|destination|wallet|iban|user", h)}

    def describe(r: list[str]) -> str:
        cells = [
            f"{head[i]}={'<ID/Adresse>' if i in id_cols else _shape(r[i])}"
            for i in range(min(len(head), len(r)))
            if r[i].strip()
        ]
        ratio = ""
        if qty_cols and fee_cols:
            q = _num(r[qty_cols[0]]) if qty_cols[0] < len(r) else None
            f = _num(r[fee_cols[0]]) if fee_cols[0] < len(r) else None
            if q and f:
                ratio = f" · {head[fee_cols[0]]}/{head[qty_cols[0]]} = {abs(f) / abs(q) * 100:.2f} %"
        return "; ".join(cells) + ratio

    out.append("")
    out.append("Beispielzeilen je Art (maskiert):")
    shown: Counter[str] = Counter()
    for r in rows[1:]:
        k = r[tcol].strip() if tcol is not None and tcol < len(r) else "?"
        if shown[k] < per_type:
            shown[k] += 1
            out.append(f"  [{k}] " + describe(r))
    if day is not None:
        out.append("")
        out.append(f"Alle Zeilen vom {day:%d.%m.%Y} (maskiert):")
        for r in rows[1:]:
            if any(day in _row_dates(c) for c in r):
                out.append("  " + describe(r))
    trades = parse_trades(text, exchange_name_from_file(path))
    out.append("")
    out.append("So liest das Tool die Datei: " + ", ".join(
        f"{k} ×{v}" for k, v in Counter(t.kind for t in trades).most_common()) if trades else "So liest das Tool die Datei: nichts erkannt")
    for t in trades:
        if day is not None and t.day != day:
            continue
        if day is None and t.kind not in ("withdraw", "deposit"):
            continue
        fee = f" · Gebühr/Menge = {t.fee_sats / t.sats * 100:.2f} %" if t.fee_sats and t.sats else ""
        extra = " · Direktversand" if t.destination and t.kind == "buy" else ""
        out.append(f"  {t.day:%d.%m.%Y} {t.kind}{fee}{extra}"
                   + (f" · Gegenwert {t.quote}" if getattr(t, "quote", "") else ""))
    out.append("")
    out.append("Maskiert: Zahlen (nur Form), Adressen, IDs, E-Mail-Adressen. Sichtbar: Spaltennamen, "
               "Datum, Art, Prozentverhältnisse.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Struktur eines Börsen-Exports prüfen (ohne Beträge/Adressen).")
    ap.add_argument("datei", type=Path)
    ap.add_argument("--datum", help="alle Zeilen dieses Tages zeigen (JJJJ-MM-TT)")
    ap.add_argument("--beispiele", type=int, default=2, help="Beispielzeilen je Art (Standard 2)")
    a = ap.parse_args(argv)
    day = date.fromisoformat(a.datum) if a.datum else None
    print(check(a.datei, day, a.beispiele))
    return 0


if __name__ == "__main__":
    sys.exit(main())
