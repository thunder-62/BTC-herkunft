"""Zwei Berichts-PDFs vergleichen — nur lokal, schreibt nichts, gibt keine Werte aus.

    python -m btc_origin.pdf_vergleich alt.pdf neu.pdf [--zeigen]

Vergleicht den Text beider PDFs Zeile für Zeile. Unberücksichtigt bleiben Zeitstempel,
Kopf- und Fußzeilen (Stand, Build, Seitenzahl) und reine Seitenverweise im Inhaltsverzeichnis.
Ausgabe je Abschnitt (Überschrift wie „3  Mögliche Gewinne …“): Anzahl geänderter Zeilen —
ohne Beträge, Daten, Adressen oder TxIDs. ``--zeigen`` gibt die geänderten Zeilen aus
(nur für den eigenen Bildschirm; enthält Werte). Exit-Code 0 = gleich, 1 = Abweichungen.
"""

from __future__ import annotations

import argparse
import difflib
import io
import re
import sys
from collections import Counter
from pathlib import Path

_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?\s{1,2}\S.*|Anhang [A-Z]\b.*|Zusammenfassung.*)$")


def pdf_lines(data: bytes) -> list[str]:
    import pymupdf

    doc = pymupdf.open(stream=io.BytesIO(data), filetype="pdf")
    out: list[str] = []
    for page in doc:
        for line in page.get_text().splitlines():
            line = line.strip()
            if not line or re.fullmatch(r"\d{1,3}", line) or re.fullmatch(r"Seite \d+ von \d+", line):
                continue
            if line.startswith("Erstellt mit BTC-Herkunft"):
                continue
            line = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?", "<Zeit>", line)
            if "· Stand <Zeit>" in line:
                continue  # Kopfzeile jeder Seite (verschiebt sich mit dem Seitenumbruch)
            out.append(line)
    return out


def compare(old: list[str], new: list[str]) -> Counter[str]:
    """Geänderte Zeilen je Abschnitt (Überschrift der Stelle im neuen bzw. alten PDF)."""
    def section_at(lines: list[str]) -> list[str]:
        cur, out = "Titelseite", []
        for line in lines:
            if _HEADING.match(line) and len(line) < 90:
                cur = line
            out.append(cur)
        return out

    sec_old, sec_new = section_at(old), section_at(new)
    changed: Counter[str] = Counter()
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            changed[sec_old[i]] += 1
        for j in range(j1, j2):
            changed[sec_new[j]] += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pdf_vergleich", description=__doc__.split("\n\n")[0])
    ap.add_argument("alt")
    ap.add_argument("neu")
    ap.add_argument("--zeigen", action="store_true", help="geänderte Zeilen ausgeben (enthält Werte)")
    a = ap.parse_args(argv)
    old, new = pdf_lines(Path(a.alt).read_bytes()), pdf_lines(Path(a.neu).read_bytes())
    changed = compare(old, new)
    if not changed:
        print(f"gleich ({len(new)} Zeilen verglichen)")
        return 0
    print(f"Abweichungen in {len(changed)} Abschnitt(en) — Anzahl geänderter Zeilen (alt + neu):")
    for sec, n in changed.items():
        print(f"  {n:>4}  {sec}")
    if a.zeigen:
        print()
        for line in difflib.unified_diff(old, new, "alt", "neu", lineterm="", n=0):
            print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
