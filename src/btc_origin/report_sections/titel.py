"""Titelblock, Zusammenfassung und Inhaltsverzeichnis."""

from __future__ import annotations

from typing import Any
from types import SimpleNamespace

from btc_origin.origin_report import (  # noqa: F401
    ACCENT,
    FA_LABEL,
    FA_NOTICE,
    FULL_LABEL,
    HEAD_FILL,
    INK,
    MASK,
    MUTED,
    RULE,
    TITLE,
    aufbau_text,
    fmt_date,
    summary_rows,
)
from btc_origin.report_sections import _keep


def render_titel(c: SimpleNamespace) -> None:
    """Title block"""
    FA = c.FA
    FONT = c.FONT
    P = c.P
    W = c.W
    heading = c.heading
    pdf = c.pdf
    rep = c.rep
    stand = c.stand
    t = c.t
    pdf.set_font(FONT, "B", 22 if heading == TITLE else 18)
    pdf.set_text_color(*INK)
    pdf.multi_cell(W, 11, t(heading), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.l_margin + 30, pdf.get_y() + 1)
    pdf.set_line_width(0.2)
    pdf.ln(5)
    if FA:
        # Pflichthinweis direkt unter der Titelzeile
        pdf.set_fill_color(*HEAD_FILL)
        pdf.set_draw_color(*ACCENT)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 10)
        pdf.set_text_color(*ACCENT)
        pdf.cell(W, 6, t(FA_LABEL), border="LTR", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(FONT, "", 9)
        pdf.set_text_color(*INK)
        pdf.multi_cell(W, 4.6, t(FA_NOTICE), border="LBR", fill=True, align="L", padding=(0, 1.5, 1.5, 1.5))
        pdf.ln(4)
    meta = [
        ("Fassung", FA_LABEL if FA else FULL_LABEL),
        ("Stand", stand),
        ("Stichtag Haltefrist", fmt_date(rep.stichtag.isoformat())),
    ]
    if rep.until is not None:
        meta.append(("Berücksichtigt", f"alle Vorgänge bis {fmt_date(rep.until.isoformat())}"))
    if rep.person_name:
        meta.append(("Name", rep.person_name))
    if rep.tax_id:
        meta.append(("Steuer-ID", rep.tax_id if not P else MASK))
    meta.append(("Betrachtete Wallets", str(len(rep.wallets))))
    for k, v in meta:
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(42, 5.2, t(k))
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(W - 42, 5.2, t(v), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    _keep(c, locals(), ('v',))


def render_zusammenfassung(c: SimpleNamespace) -> None:
    """Zusammenfassung (Seite 1) und Inhaltsverzeichnis (Seite 2)"""
    FA = c.FA
    FONT = c.FONT
    pdf = c.pdf
    rep = c.rep
    section = c.section
    t = c.t
    table = c.table
    section(
        "",
        "Zusammenfassung",
        "Die wichtigsten Ergebnisse auf einen Blick — Einzelheiten, Annahmen und Belege in den "
        "genannten Abschnitten; Begriffe für Leser ohne Bitcoin-Vorkenntnisse erklärt Abschnitt 9. "
        "Keine Steuerberatung.",
    )
    table(
        ["Ergebnis", "Wert", "Abschnitt"],
        summary_rows(rep),
        [56, 100, 18],
        ["LEFT", "LEFT", "LEFT"],
        size=8,
        head_size=7.5,
    )

    def render_toc(doc: Any, outline: list[Any]) -> None:
        doc.set_x(doc.l_margin)
        doc.set_font(FONT, "B", 12.5)
        doc.set_text_color(*ACCENT)
        doc.cell(doc.epw, 7, t("Inhaltsverzeichnis"), new_x="LMARGIN", new_y="NEXT")
        doc.set_draw_color(*ACCENT)
        doc.set_line_width(0.4)
        doc.line(doc.l_margin, doc.get_y(), doc.l_margin + doc.epw, doc.get_y())
        doc.set_line_width(0.2)
        doc.ln(4)
        for sec in outline:
            if sec.level > 0 or sec.name == t("Zusammenfassung"):
                continue
            link = doc.add_link(page=sec.page_number)
            is_annex = sec.name.startswith("Anhang")
            doc.set_font(FONT, "", 9.5 if not is_annex else 9)
            doc.set_text_color(*INK)
            name_w = doc.get_string_width(sec.name) + 2
            page_w = 12
            y = doc.get_y()
            doc.set_x(doc.l_margin)
            doc.cell(name_w, 6.4, sec.name, link=link)
            # gepunktete Führungslinie bis zur Seitenzahl
            doc.set_draw_color(*RULE)
            doc.set_dash_pattern(dash=0.4, gap=1.2)
            x0 = doc.l_margin + name_w + 1
            x1 = doc.l_margin + doc.epw - page_w - 1
            if x1 > x0:
                doc.line(x0, y + 4.6, x1, y + 4.6)
            doc.set_dash_pattern()
            doc.set_xy(doc.l_margin + doc.epw - page_w, y)
            doc.cell(page_w, 6.4, str(sec.page_number), align="R", link=link, new_x="LMARGIN", new_y="NEXT")
        # Orientierung für den Leser direkt unter dem Inhaltsverzeichnis
        doc.ln(6)
        doc.set_x(doc.l_margin)
        doc.set_font(FONT, "B", 9.5)
        doc.set_text_color(*ACCENT)
        doc.multi_cell(doc.epw, 5, t("So ist der Bericht aufgebaut"), align="L")
        doc.set_x(doc.l_margin)
        doc.set_font(FONT, "", 8.8)
        doc.set_text_color(*INK)
        doc.multi_cell(doc.epw, 4.5, t(aufbau_text(FA)), align="L")

    pdf.add_page()
    pdf.insert_toc_placeholder(render_toc, pages=1)
