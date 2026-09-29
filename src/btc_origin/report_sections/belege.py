"""Abschnitte 4 und 5 — Belege und Zuflüsse ohne Beleg."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from btc_origin.origin_report import (  # noqa: F401
    ACCENT,
    EXPLAIN_EVIDENCE,
    INK,
    MUTED,
    SATS_PER_BTC,
    _name,
    assumed_disposals,
    defunct_platforms_in,
    fmt_btc,
    fmt_date,
    fmt_eur,
    inflow_explanation,
)
from btc_origin.report_sections import _keep


def render_belege_hintergrund(c: SimpleNamespace) -> None:
    """4 Hintergrund Belege (allgemeinverständlich)"""
    FONT = c.FONT
    d = c.d
    explain = c.explain
    para = c.para
    pdf = c.pdf
    rep = c.rep
    section = c.section
    t = c.t
    _v = vars(c)
    if "src" in _v:
        src = _v["src"]
    if "text" in _v:
        text = _v["text"]
    if "x" in _v:
        x = _v["x"]
    if "y" in _v:
        y = _v["y"]
    section(
        "4",
        "Belege und Ersatzwerte",
        "Rechtsrahmen für fehlende Belege und die im Bericht verwendeten Ersatzwerte.",
    )
    explain(EXPLAIN_EVIDENCE)
    counterparties = (
        [src.name for src in rep.sources]
        + [x.source for x in rep.inflows]
        + [str(d.get("counterparty") or "") for y in rep.years for d in y.details]
    )
    defunct = defunct_platforms_in(counterparties)
    if defunct:
        explain(
            (
                (
                    "In diesem Bericht betroffene Handelsplattformen",
                    "Folgende Gegenstellen sind insolvente Handelsplattformen; ihre Kontoauszüge "
                    "sind nicht mehr abrufbar. Die Vorgänge sind mit den Daten der Blockchain "
                    "bewertet (Abschnitt 5).",
                ),
            )
        )
        for label, text in defunct:
            if pdf.get_y() > pdf.page_break_trigger - 10:
                pdf.add_page()
            pdf.set_x(pdf.l_margin)
            pdf.set_font(FONT, "B", 9)
            pdf.set_text_color(*INK)
            pdf.multi_cell(pdf.epw, 4.8, t(f"• {label}"), align="L")
            para(text, 8.6, h=4.4)
            pdf.ln(1.2)
    _keep(c, locals(), ('text',))


def render_ohne_beleg(c: SimpleNamespace) -> None:
    """5 Käufe ohne Beleg"""
    FA = c.FA
    FONT = c.FONT
    P = c.P
    para = c.para
    pdf = c.pdf
    rep = c.rep
    section = c.section
    t = c.t
    table = c.table
    tx_cell = c.tx_cell
    _v = vars(c)
    if "g" in _v:
        g = _v["g"]
    if "heads" in _v:
        heads = _v["heads"]
    if "r" in _v:
        r = _v["r"]
    if "rows" in _v:
        rows = _v["rows"]
    if "x" in _v:
        x = _v["x"]
    section(
        "5",
        "Nicht durch Belege nachgewiesene Transaktionen",
        "Für diese Zuflüsse liegt kein Kaufbeleg einer Handelsplattform vor (kein Börsen-Export "
        "in der Auswertung). Als Anschaffung gilt der Tag des Zuflusses in die Wallets mit dem "
        "Tageskurs (Rz. 55, 91). Belege (Kaufabrechnungen, Konto-Exporte) können die Werte "
        "ersetzen; die Finanzbehörde kann sie anfordern (Rz. 101–103). Das gilt auch für eigene "
        "Bitcoin aus einer nicht erfassten Quelle (z. B. Übertrag über eine Adresse eines Dienstes): "
        "ohne Nachweis des ursprünglichen Kaufs gilt vorsichtshalber der Zuflusstag als Anschaffung "
        "— ein späteres Anschaffungsdatum verkürzt die Haltedauer eher. Abflüsse ohne "
        "Verkaufsbeleg folgen am Ende dieses Abschnitts.",
    )
    no_buy = [x for x in rep.inflows if not x.acq_source]
    if no_buy:
        # Erläuterung aus erlaeuterungen.csv (Art „Zufluss“), sonst die der zugehörigen
        # Börsen-Auszahlung aus Anhang B (z. B. „Lightning-Test“)
        linked_notes = {
            str(r.get("entry_txid")): str(r["note"])
            for r in rep.open_exchange
            if r.get("entry_txid") and r.get("note") and r["note"] != "offen"
        }

        def manual_note(x: Any) -> str:
            # Einordnung durch den Steuerpflichtigen (local/kategorien.yaml, zuordnung_manuell)
            m = rep.manual_categories.get(x.txid)
            if m is None:
                return ""
            kind = {"einkunft_22_3": "Einkunft § 22 Nr. 3 EStG, Tageskurs",
                    "nicht_unterstuetzt": "nicht unterstützt, Abschnitt 8.3"}.get(m["behandlung"], m["behandlung"])
            return f"{m['anzeigename']} ({kind}; Angabe des Steuerpflichtigen)" + (
                f": {m['erlaeuterung']}" if m["erlaeuterung"] else "")

        notes = {id(x): manual_note(x) or inflow_explanation(rep.explanations, x) or linked_notes.get(x.txid, "")
                 for x in no_buy}
        with_notes = any(notes.values())
        rows = [
            [
                fmt_date(x.date),
                _name(x.wallet, P),
                _name(x.source, P),
                tx_cell(x.txid),
                fmt_btc(x.sats, P, unit=False),
                fmt_eur(x.price_eur * x.sats / SATS_PER_BTC, P) if x.price_eur is not None else "Kurs fehlt",
            ]
            + ([] if FA else [fmt_btc(x.remaining_sats, P, unit=False) if x.remaining_sats else "—"])
            + ([notes[id(x)] or ""] if with_notes else [])
            for x in no_buy
        ]
        rows.append(
            [
                ("Summe", 2),
                f"{len(no_buy)}×",
                "",
                fmt_btc(sum(x.sats for x in no_buy), P, unit=False),
                fmt_eur(sum(x.price_eur * x.sats / SATS_PER_BTC for x in no_buy if x.price_eur is not None), P),
            ]
            + ([] if FA else [fmt_btc(sum(x.remaining_sats for x in no_buy), P, unit=False)])
            + ([""] if with_notes else [])
        )
        heads = ["Zufluss", "Wallet", "Herkunft", "Tx", "Menge (BTC)", "Wert (Tageskurs)", "Heute vorhanden (BTC)"]
        if FA:  # ohne „Heute vorhanden“ (Bestand)
            heads = heads[:-1]
            table(
                heads + (["Erläuterung"] if with_notes else []),
                rows,
                [17, 21, 20, 21, 21, 23, 51] if with_notes else [22, 30, 32, 24, 30, 36],
                ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT"][: len(heads) + (1 if with_notes else 0)],
                bold_last=True,
                size=7 if with_notes else 7.3,
                head_size=6.6 if with_notes else 6.8,
            )
        elif with_notes:
            table(
                heads + ["Erläuterung"],
                rows,
                [17, 21, 20, 16, 21, 23, 20, 36],
                ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
                bold_last=True,
                size=7,
                head_size=6.6,
            )
        else:
            table(
                heads,
                rows,
                [20, 26, 28, 20, 24, 28, 28],
                ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"],
                bold_last=True,
                size=7.3,
                head_size=6.8,
            )
    else:
        para("Keine — alle Zuflüsse sind durch Börsen-Exporte belegt.", 8.5, color=MUTED, h=4.2)

    # Abflüsse ohne Verkaufsbeleg: als Veräußerung angenommen (Abschnitte 2 und 3)
    assumed = assumed_disposals(rep)
    if assumed:
        if pdf.get_y() > pdf.page_break_trigger - 30:
            pdf.add_page()
        pdf.ln(2)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 10)
        pdf.set_text_color(*ACCENT)
        pdf.cell(pdf.epw, 6, t("Abflüsse ohne Verkaufsbeleg (Veräußerung angenommen)"),
                 new_x="LMARGIN", new_y="NEXT")
        para(
            "Abflüsse an Adressen, die keiner betrachteten Wallet und keinem Börsen-Export zugeordnet sind. "
            "Sie gelten als Veräußerung am Abflusstag zum Tageskurs und sind in Abschnitt 3 und der "
            "Anlage-SO-Übersicht enthalten. Handelt es sich um eine eigene, hier nicht erfasste Wallet, "
            "ist es keine Veräußerung (Rz. 54) — dann die Wallet in die Auswertung aufnehmen. Auch eine "
            "Einzahlung auf ein eigenes Börsenkonto ist selbst keine Veräußerung; fehlt dazu der "
            "Konto-Export, ist nicht belegt, was danach auf dem Konto geschah — deshalb wird hier "
            "vorsichtshalber ein Verkauf am Einzahlungstag angesetzt.",
            8,
            color=MUTED,
            h=4.1,
        )
        with_notes = any(g["note"] for g in assumed)
        rows = [
            [
                fmt_date(g["day"]),
                _name(g["wallet"], P) or "—",
                _name(g["counterparty"], P),
                tx_cell(g["txid"]),
                fmt_btc(g["sats"], P, unit=False),
                fmt_eur(g["proceeds"], P) if g["priced"] else "Kurs fehlt",
                fmt_eur(g["gain"], P) if g["priced"] else "—",
            ]
            + ([g["note"]] if with_notes else [])
            for g in assumed
        ]
        heads = ["Abfluss", "Wallet", "Empfänger", "Tx", "Menge (BTC)", "Veräußerungspreis", "Gewinn/Verlust"]
        if with_notes:
            table(heads + ["Erläuterung"], rows, [17, 21, 20, 16, 20, 22, 21, 37],
                  ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT"], size=7, head_size=6.6)
        else:
            table(heads, rows, [20, 26, 26, 20, 24, 29, 29],
                  ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"], size=7.3, head_size=6.8)
    _keep(c, locals(), ('rows',))
