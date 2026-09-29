"""Abschnitte 8 und 9 — unklare Transaktionen und Erläuterungen."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from btc_origin.origin_report import (  # noqa: F401
    EXPLAIN_BASICS,
    INK,
    MUTED,
    SATS_PER_BTC,
    _name,
    fa_basics,
    fmt_btc,
    fmt_date,
    fmt_eur,
    is_altbestand,
    reform_cutoff,
    tax_free_from,
)
from btc_origin.report_sections import _keep


def render_unklare(c: SimpleNamespace) -> None:
    """8 Unklare Transaktionen: unbelegte Käufe innerhalb der Haltefrist"""
    FA = c.FA
    FONT = c.FONT
    P = c.P
    para = c.para
    pdf = c.pdf
    rep = c.rep
    section = c.section
    t = c.t
    table = c.table
    _v = vars(c)
    if "rows" in _v:
        rows = _v["rows"]
    if "x" in _v:
        x = _v["x"]
    open_lots = [x for x in rep.lots if not x.acq_source and not x.qualifies]
    section(
        "8",
        "Unklare Transaktionen: unbelegte Käufe innerhalb der Haltefrist",
        f"Bitcoin, die am Stichtag {fmt_date(rep.stichtag.isoformat())} noch in den Wallets liegen, "
        "deren Kauf nicht durch einen Beleg einer Handelsplattform nachgewiesen ist und deren "
        "Haltefrist noch nicht abgelaufen ist. Als Anschaffung gilt der Tag des Zuflusses in die "
        "Wallet (in der Regel der späteste mögliche Zeitpunkt, Rz. 20, 55). Eine Veräußerung vor "
        "dem genannten Tag wäre ein privates Veräußerungsgeschäft (§ 23 Abs. 1 Satz 1 Nr. 2 "
        "EStG); ein Beleg (Börsen-Export) kann ein früheres Kaufdatum nachweisen.",
    )
    pdf.set_x(pdf.l_margin)
    pdf.set_font(FONT, "B", 9.5)
    pdf.set_text_color(*INK)
    pdf.cell(
        pdf.epw, 5.5, t("8.1  Unbelegte Teilbestände innerhalb der Haltefrist"),
        new_x="LMARGIN", new_y="NEXT",
    )
    if open_lots and FA:
        # ohne Mengen und Werte gehaltener Bestände
        table(
            ["Zufluss = Anschaffung", "Herkunft", "Wallet", "Tage bis Stichtag", "steuerfrei ab"],
            [
                [
                    fmt_date(x.acquisition),
                    _name(x.source, P),
                    _name(x.wallet, P),
                    str(x.days_held),
                    fmt_date(tax_free_from(date.fromisoformat(x.acquisition)).isoformat()),
                ]
                for x in open_lots
            ]
            + [["Summe", f"{len(open_lots)}×", "", "", ""]],
            [30, 40, 40, 30, 34],
            ["LEFT", "LEFT", "LEFT", "RIGHT", "LEFT"],
            bold_last=True,
            size=7.5,
            head_size=7,
        )
    elif open_lots:
        rows = [
            [
                fmt_date(x.acquisition),
                _name(x.source, P),
                _name(x.wallet, P),
                fmt_btc(x.sats, P, unit=False),
                str(x.days_held),
                fmt_date(tax_free_from(date.fromisoformat(x.acquisition)).isoformat()),
                fmt_eur(x.price_eur * x.sats / SATS_PER_BTC, P) if x.price_eur is not None else "Kurs fehlt",
            ]
            for x in open_lots
        ]
        rows.append(
            [
                "Summe",
                f"{len(open_lots)}×",
                "",
                fmt_btc(sum(x.sats for x in open_lots), P, unit=False),
                "",
                "",
                fmt_eur(
                    sum(x.price_eur * x.sats / SATS_PER_BTC for x in open_lots if x.price_eur is not None), P
                ),
            ]
        )
        table(
            ["Zufluss = Anschaffung", "Herkunft", "Wallet", "Menge (BTC)", "Tage bis Stichtag", "steuerfrei ab", "Wert (Tageskurs Zufluss)"],
            rows,
            [24, 28, 26, 24, 18, 24, 30],
            ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT", "RIGHT"],
            bold_last=True,
            size=7.5,
            head_size=7,
        )
    else:
        para(
            "Keine — alle noch gehaltenen Bitcoin sind belegt gekauft oder haben die Haltefrist "
            "zum Stichtag erfüllt.",
            8.5,
            color=MUTED,
            h=4.2,
        )

    unproven_alt = [x for x in rep.lots if not x.acq_source and is_altbestand(x.acquisition, reform_cutoff(rep))]
    if unproven_alt and not FA:  # 8.2 (Altbestand/Reform) nicht in der Finanzamt-Fassung
        pdf.ln(1)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(
            pdf.epw,
            5.5,
            t("8.2  Hinweis: unbelegte Teilbestände im Altbestand (Kryptosteuer-Reform, Entwurf)"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        para(
            f"Noch gehaltene Teilbestände im Altbestand ohne Kaufbeleg: {len(unproven_alt)} (zusammen "
            f"{fmt_btc(sum(x.sats for x in unproven_alt), P)}). Sie wurden bis "
            f"{fmt_date(reform_cutoff(rep).isoformat())} "
            "angeschafft (Altbestand), ihr Kauf ist aber nicht durch einen Beleg einer "
            "Handelsplattform nachgewiesen. Die Blockchain belegt den Besitz spätestens ab dem Tag des Zuflusses; "
            "die Anschaffungskosten sind nur mit dem Tageskurs geschätzt. Nach dem "
            "Referentenentwurf zur Kryptosteuer-Reform (September 2026, nicht beschlossen) soll "
            "ohne Nachweis von Anschaffungszeitpunkt und -kosten pauschal 50 % des "
            "Veräußerungserlöses angesetzt werden. Kaufbelege für diese Teilbestände sollten daher "
            "beschafft und aufbewahrt werden. Einzelaufstellung: separates Dokument „Nachweis Altbestand "
            f"Bitcoin“ (eigene PDF aus demselben Programm, Stand {rep.generated_at:%d.%m.%Y}); nicht Teil "
            "dieses Berichts.",
            8.3,
            h=4.3,
        )
    render_nicht_unterstuetzt(c)
    _keep(c, locals(), ('rows',))


def render_nicht_unterstuetzt(c: SimpleNamespace) -> None:
    """8.3 Nicht unterstützte Vorgänge: Export-Zeilen außerhalb des Geltungsbereichs —
    ohne steuerlichen Wert, in keiner Summe (REGELWERK.md 4.1)."""
    rep, pdf, para, table, t, FONT, P = c.rep, c.pdf, c.para, c.table, c.t, c.FONT, c.P
    pdf.ln(1)
    pdf.set_x(pdf.l_margin)
    pdf.set_font(FONT, "B", 9.5)
    pdf.set_text_color(*INK)
    pdf.cell(pdf.epw, 5.5, t("8.3  Nicht unterstützte Vorgänge"), new_x="LMARGIN", new_y="NEXT")
    if not rep.unsupported:
        para("Keine — alle Export-Zeilen sind einer unterstützten Kategorie zugeordnet.", 8.5, color=MUTED, h=4.2)
        return
    para(
        "Diese Export-Zeilen liegen außerhalb des Geltungsbereichs (Methodik, Abschnitt 7) oder ihre "
        "Art ist keiner Kategorie zugeordnet. Für sie ist kein steuerlicher Wert berechnet; sie "
        "fließen in keine Summe ein. Die Mengenabstimmung mit der Blockchain bleibt unberührt. "
        "Status: nicht unterstützt – manuell prüfen.",
        8.3,
        h=4.3,
    )
    table(
        ["Datum", "Börse bzw. Wallet", "Art", "Richtung", "Menge (BTC)", "Grund", "Status"],
        [
            [fmt_date(u["day"]), u["exchange"], u["art"] or "—", u["richtung"],
             fmt_btc(int(u["sats"]), P, unit=False), u["grund"],
             f"geprüft: {u['erlaeuterung']}" if u.get("quittiert") else "manuell prüfen"]
            for u in rep.unsupported
        ],
        [16, 20, 24, 16, 20, 42, 32],
        ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "LEFT", "LEFT"],
        size=7.3,
        head_size=6.8,
    )


def render_erlaeuterungen(c: SimpleNamespace) -> None:
    """9 Erläuterungen (allgemeinverständlich) — am Ende, vor den Anhängen"""
    FA = c.FA
    explain = c.explain
    pdf = c.pdf
    section = c.section
    pdf.add_page()
    section(
        "9",
        "Erläuterungen für Leser ohne Bitcoin-Vorkenntnisse",
        "Dieser Abschnitt erklärt die Begriffe und Zusammenhänge, die zum Verständnis des "
        "Berichts nötig sind. Die Randnummern (Rz.) beziehen sich auf das BMF-Schreiben vom "
        "06.03.2025 (siehe Abschnitt 6).",
    )
    explain(fa_basics() if FA else EXPLAIN_BASICS)
