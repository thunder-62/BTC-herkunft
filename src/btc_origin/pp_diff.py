"""PP-Abgleich auf der Kommandozeile: PP-Export vorher / nachher ↔ Ist-Stand der Blockchain.

Läuft nur lokal, liest drei Dateien und schreibt nichts:

    btc-origin-pp-diff --vorher alt.csv --nachher neu.csv --ist ist-stand.csv [--delta pp-import-bitcoin.csv]

* ``vorher`` / ``nachher``: PP-Export („Alle Buchungen“) vor bzw. nach Import und Löschen.
* ``ist``: Ist-Stand aus der App (PP-Karte → „Ist-Stand (Blockchain) als CSV“).
* ``delta`` (optional): die importierte Delta-Datei — prüft, ob sie genau einmal in PP steht.

Ausgabe: Bestände, was sich in PP geändert hat (neu/entfernt), was nach dem Import noch
von der Blockchain abweicht. ``--teilen`` gibt stattdessen eine Zusammenfassung ohne
Beträge, Daten und Adressen aus (zum Weitergeben bei der Fehlersuche).
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from btc_origin.pp_check import PPFile, PPRow, _signed, compare, parse_pp_file

IST_HEADER = ["Datum", "Richtung", "Menge BTC", "Gebühr BTC", "Wallet", "Gegenstelle", "TxID"]
_FEE_ROW = "Gebühr intern"


# ---------------------------------------------------------------------------
# Ist-Stand (Blockchain) — Export aus der App und Einlesen
# ---------------------------------------------------------------------------


def _btc(sats: int) -> str:
    return f"{sats / 1e8:.8f}".replace(".", ",")


def _sats(cell: str) -> int:
    t = (cell or "").strip().replace(".", "").replace(",", ".") if "," in (cell or "") else (cell or "").strip()
    return round(float(t or 0) * 1e8)


def ist_csv(chain: Iterable[dict[str, Any]], fees: Iterable[dict[str, Any]], bestand_sats: int) -> str:
    """Bewegungen laut Blockchain (je Transaktion und Richtung) + Netzwerkgebühren reiner
    Umbuchungen + Bestand — so, wie der PP-Abgleich der App sie verwendet."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(IST_HEADER)
    rows = [
        (c["day"], "Eingang" if c["direction"] == "in" else "Ausgang", _btc(int(c["sats"])),
         _btc(int(c["fee_sats"])), c.get("wallet") or "", c.get("counterparty") or "", c["txid"])
        for c in chain if c["sats"]
    ] + [(f["day"], _FEE_ROW, _btc(0), _btc(int(f["sats"])), "", "", f.get("txid") or "") for f in fees]
    for r in sorted(rows):
        w.writerow(r)
    w.writerow(["Bestand", "", _btc(bestand_sats), "", "", "", ""])
    return buf.getvalue()


def read_ist(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    """Ist-Stand-CSV → (Bewegungen, interne Gebühren, Bestand)."""
    chain: list[dict[str, Any]] = []
    fees: list[dict[str, Any]] = []
    bestand = None
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    next(reader, None)
    for row in reader:
        if not row or not row[0].strip():
            continue
        row = row + [""] * (len(IST_HEADER) - len(row))
        if row[0] == "Bestand":
            bestand = _sats(row[2])
        elif row[1] == _FEE_ROW:
            fees.append({"day": row[0], "sats": _sats(row[3]), "txid": row[6]})
        else:
            chain.append({"day": row[0], "direction": "in" if row[1] == "Eingang" else "out",
                          "sats": _sats(row[2]), "fee_sats": _sats(row[3]), "wallet": row[4],
                          "counterparty": row[5], "txid": row[6]})
    return chain, fees, bestand


# ---------------------------------------------------------------------------
# Vergleich
# ---------------------------------------------------------------------------


def _key(r: PPRow) -> tuple[str, str, int]:
    return (r.day.isoformat(), r.direction, r.sats)


def row_diff(vorher: list[PPRow], nachher: list[PPRow]) -> tuple[list[PPRow], list[PPRow]]:
    """(neu in nachher, entfernt aus vorher) — Zeilen gleich, wenn Datum, Richtung und
    Menge gleich sind (Zeilennummern ändern sich beim Neuexport)."""
    before, after = Counter(_key(r) for r in vorher), Counter(_key(r) for r in nachher)
    added, removed = [], []
    left = after - before
    for r in nachher:
        if left[_key(r)] > 0:
            left[_key(r)] -= 1
            added.append(r)
    gone = before - after
    for r in vorher:
        if gone[_key(r)] > 0:
            gone[_key(r)] -= 1
            removed.append(r)
    return added, removed


def _bestand(rows: Iterable[PPRow]) -> int:
    return sum(_signed(r.direction, r.sats) for r in rows)


def _line(r: PPRow) -> str:
    return f"  {r.day.isoformat()}  {r.type:<24.24} {'+' if r.direction == 'in' else '−'}{_btc(r.sats):>14}  {r.note}"


def report(vorher: PPFile, nachher: PPFile, ist: tuple[list[dict[str, Any]], list[dict[str, Any]], int | None],
           delta: PPFile | None = None) -> str:
    chain, fees, bestand = ist
    res = compare(nachher.rows, chain, bestand_sats=bestand, fees=fees)
    chain_sats = int(res["bestand"]["chain_sats"])
    b0, b1 = _bestand(vorher.rows), _bestand(nachher.rows)
    added, removed = row_diff(vorher.rows, nachher.rows)
    out = ["PP-Abgleich vorher / nachher / Ist (Blockchain)", ""]
    out.append(f"Bestand vorher : {_btc(b0):>14} BTC  ({len(vorher.rows)} Bitcoin-Zeilen)")
    out.append(f"Bestand nachher: {_btc(b1):>14} BTC  ({len(nachher.rows)} Bitcoin-Zeilen)")
    out.append(f"Ist Blockchain : {_btc(chain_sats):>14} BTC")
    out.append(f"Δ nachher − Ist: {_btc(b1 - chain_sats):>14} BTC"
               + ("  ✓" if b1 == chain_sats else "  ✗"))
    out.append("")
    out.append(f"Neu in PP ({len(added)}, Wirkung {_btc(_bestand(added))} BTC):")
    out += [_line(r) for r in added] or ["  —"]
    out.append(f"Aus PP entfernt ({len(removed)}, Wirkung {_btc(-_bestand(removed))} BTC):")
    out += [_line(r) for r in removed] or ["  —"]
    if delta is not None:
        want = Counter(_key(r) for r in delta.rows)
        got = Counter(_key(r) for r in added)
        missing, extra = want - got, got - want
        out.append("")
        out.append(f"Delta-Datei ({len(delta.rows)} Zeilen): "
                   + ("vollständig und genau einmal importiert ✓" if not missing and not extra else "weicht ab ✗"))
        for (day, d, s), n in sorted(missing.items()):
            out.append(f"  fehlt in PP: {day} {'+' if d == 'in' else '−'}{_btc(s)} BTC" + (f" ×{n}" if n > 1 else ""))
        for (day, d, s), n in sorted(extra.items()):
            out.append(f"  zusätzlich neu (nicht aus der Delta-Datei): {day} {'+' if d == 'in' else '−'}{_btc(s)} BTC"
                       + (f" ×{n}" if n > 1 else ""))
    out.append("")
    open_ = res["actions"]
    out.append(f"Noch offen nachher ↔ Ist: {len(open_)}")
    for a in open_:
        out.append(f"  {a['day']}  {a['title']}  (Wirkung {_btc(int(a['effect_sats']))} BTC)")
        for p in a["pp"]:
            out.append(f"      PP   {p['day']} {p['type']:<20.20} {_btc(int(p['sats']))}  {p.get('note') or ''}")
        for c in a["chain"]:
            out.append(f"      Chain {c['day']} {'Eingang' if c.get('direction') == 'in' else 'Ausgang':<20}"
                       f"{_btc(int(c['sats']))}  {c.get('counterparty') or ''}")
    return "\n".join(out) + "\n"


def share_report(vorher: PPFile, nachher: PPFile, ist: tuple[list[dict[str, Any]], list[dict[str, Any]], int | None],
                 delta: PPFile | None = None) -> str:
    """Wie ``report``, aber nur Zähler, Jahre und Anteile in % des Ist-Bestands."""
    chain, fees, bestand = ist
    res = compare(nachher.rows, chain, bestand_sats=bestand, fees=fees)
    chain_sats = int(res["bestand"]["chain_sats"]) or 1

    def pct(s: int) -> str:
        return "0" if not s else f"{100 * s / chain_sats:+.1f} %"

    added, removed = row_diff(vorher.rows, nachher.rows)
    years = lambda rows: ", ".join(f"{y}: {n}" for y, n in sorted(Counter(r.day.year for r in rows).items())) or "—"  # noqa: E731
    out = ["PP-Abgleich (ohne Beträge, Daten, Adressen) — Anteile in % des Ist-Bestands"]
    out.append(f"vorher {len(vorher.rows)} Zeilen, Δ zu Ist {pct(_bestand(vorher.rows) - chain_sats)}; "
               f"nachher {len(nachher.rows)} Zeilen, Δ zu Ist {pct(_bestand(nachher.rows) - chain_sats)}")
    out.append(f"neu in PP: {len(added)} ({years(added)}), Wirkung {pct(_bestand(added))}")
    out.append(f"entfernt: {len(removed)} ({years(removed)}), Wirkung {pct(-_bestand(removed))}")
    if delta is not None:
        want, got = Counter(_key(r) for r in delta.rows), Counter(_key(r) for r in added)
        out.append(f"Delta-Datei {len(delta.rows)} Zeilen: fehlt in PP {sum((want - got).values())}, "
                   f"zusätzlich neu {sum((got - want).values())}")
    kinds = Counter(a["kind"] for a in res["actions"])
    out.append("noch offen: " + (", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) or "nichts")
               + f"; Wirkung {pct(sum(int(a['effect_sats']) for a in res['actions']))}")
    return "\n".join(out) + "\n"


def _read(path: str) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="btc-origin-pp-diff", description=__doc__.split("\n\n")[0])
    ap.add_argument("--vorher", required=True, help="PP-Export vor dem Import")
    ap.add_argument("--nachher", required=True, help="PP-Export nach Import und Löschen")
    ap.add_argument("--ist", required=True, help="Ist-Stand (Blockchain) aus der App")
    ap.add_argument("--delta", help="importierte Delta-Datei (optional)")
    ap.add_argument("--teilen", action="store_true", help="nur Zähler und %% — ohne Beträge/Daten")
    a = ap.parse_args(argv)
    vorher, nachher = parse_pp_file(_read(a.vorher)), parse_pp_file(_read(a.nachher))
    for name, f in (("vorher", vorher), ("nachher", nachher)):
        for e in f.errors[:5]:
            print(f"{name}: {e}", file=sys.stderr)
    delta = parse_pp_file(_read(a.delta)) if a.delta else None
    ist = read_ist(_read(a.ist))
    sys.stdout.write((share_report if a.teilen else report)(vorher, nachher, ist, delta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
