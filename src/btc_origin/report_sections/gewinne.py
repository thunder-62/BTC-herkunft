"""Abschnitt 3 — mögliche Gewinne und Verluste, Börsenverkäufe, Anlage SO."""

from __future__ import annotations

from typing import Any
from btc_origin.year_summary import freigrenze_eur
from types import SimpleNamespace

from btc_origin.origin_report import (  # noqa: F401
    ACCENT,
    MUTED,
    _and_list,
    _base_name,
    _day,
    _name,
    _thousands,
    anlage_so_rows,
    assumed_disposals,
    defunct_platforms_in,
    fmt_btc,
    fmt_date,
    fmt_eur,
)
from btc_origin.report_sections import _keep


def render_gewinne(c: SimpleNamespace) -> None:
    """3 Years"""
    P = c.P
    d = c.d
    para = c.para
    rep = c.rep
    section = c.section
    table = c.table
    _v = vars(c)
    if "details" in _v:
        details = _v["details"]
    if "g" in _v:
        g = _v["g"]
    if "r" in _v:
        r = _v["r"]
    if "rows" in _v:
        rows = _v["rows"]
    if "y" in _v:
        y = _v["y"]
    any_missing_price = any(y.short.missing_price or y.long.missing_price for y in rep.years)
    defunct_out = [label for label, _ in defunct_platforms_in(str(d.get("counterparty") or "") for d in details)]
    # Abflüsse an eine Börse mit Export, die dort aber nicht als Einzahlung stehen (z. B. Altkonto)
    known_ex = {_base_name(r.exchange) for r in rep.exchange_reports}
    other_ex = sorted({
        g["counterparty"].replace("³", "") for g in assumed_disposals(rep)
        if _base_name(g["counterparty"]) in known_ex
    })
    section(
        "3",
        "Mögliche Gewinne und Verluste",
        "So wird gerechnet:\n"
        "• Verkauf: Alles, was von den Wallets an eine fremde Adresse ging (z. B. an eine Börse), "
        "wird vorsichtshalber als Verkauf behandelt — am Tag des Abflusses zum Tageskurs."
        + (
            "\n• Ausnahme Einzahlung aufs eigene Börsenkonto: Enthält der Export einer Börse die "
            "Einzahlung, ist der Abfluss eine Umbuchung (Rz. 54), keine Veräußerung; die Teilbestände "
            "behalten ihr Anschaffungsdatum, ihre Netzwerkgebühr ist dann keine Werbungskosten (Anhang A)."
            if any(getattr(r, "deposits_matched", 0) for r in rep.exchange_reports)
            else ""
        )
        + (
            "\n• Verkauf auf einer Börse: Wurden Bitcoin auf einer Börse verkauft — dorthin "
            "überwiesene, dort gekaufte oder anders eingezahlte —, zählen Verkaufstag und Erlös aus "
            "dem Konto-Export der Börse (Rz. 20, 55), nicht die Überweisung; fehlt der Erlös im "
            "Export, Tageskurs des Verkaufstags. Exporte mit Verkäufen "
            f"lagen vor für: {', '.join(rep.exchange_exports)}."
            if rep.exchange_exports
            else ""
        )
        + (
            f"\n• Börsen ohne Konto-Export: Für Abflüsse an {_and_list(defunct_out)} gibt es keinen "
            "Export mehr (Plattform insolvent, siehe Abschnitt 4). Die Einzahlung auf ein eigenes "
            "Börsenkonto ist selbst keine Veräußerung; weil nicht belegt ist, was danach auf dem Konto "
            "geschah, gilt vorsichtshalber der Abflusstag zum Tageskurs als Verkauf."
            if defunct_out
            else ""
        )
        + (
            f"\n• Börsenkonten ohne passenden Export: Abflüsse an {_and_list(other_ex)} stehen in keinem "
            "vorliegenden Konto-Export (z. B. geschlossenes Altkonto, Abrechnung angefordert). Auch hier "
            "gilt vorsichtshalber der Abflusstag zum Tageskurs als Verkauf (Abschnitt 5); mit dem Export "
            "wird daraus eine Umbuchung auf das eigene Börsenkonto."
            if other_ex
            else ""
        )
        + "\n• Anschaffung: je Teilbestand (Rz. 61) Kaufdatum und Kaufpreis laut Börsen-Export, sonst "
        "Tag des Zuflusses in die Wallet zum Tageskurs.\n"
        "• Gebühren: Die Netzwerkgebühr des Abflusses mindert den Gewinn (Werbungskosten, "
        "§ 23 Abs. 3 Satz 1 EStG, Rz. 57, 59).\n"
        "Jeder einzelne Abfluss steht in Abschnitt 2 bei der jeweiligen Wallet"
        + (
            "; Verkäufe auf Börsen ohne Abfluss aus den Wallets einzeln in 3.1."
            if any(d.get("origin", "wallet") != "wallet" for y in rep.years for d in y.details)
            else "."
        ),
    )
    rows = []
    for y in (y for y in rep.years if y.disposals):
        d = y.as_dict()
        s_, l_ = d["short"], d["long"]
        flag = ""
        if d["over_freigrenze"] is True:
            flag = " (über Freigrenze)"
        elif d["over_freigrenze"] is False and s_["btc_sats"]:
            flag = " (unter Freigrenze)"
        has_s = bool(s_["btc_sats"])
        rows.append(
            [
                str(y.year) + ("" if d["complete"] else " *"),
                fmt_btc(s_["btc_sats"], P, unit=False) if has_s else "—",
                fmt_eur(s_["proceeds_eur"], P) if has_s else "—",
                fmt_eur(s_["cost_eur"], P) if has_s else "—",
                fmt_eur(s_["fees_eur"], P) if has_s else "—",
                (fmt_eur(s_["gain_eur"], P, signed=True) + flag) if has_s else "—",
                fmt_btc(l_["btc_sats"], P, unit=False) if l_["btc_sats"] else "—",
                fmt_eur(l_["gain_eur"], P, signed=True) if l_["btc_sats"] else "—",
            ]
        )
    if any(y.disposals for y in rep.years):
        any_s = any(y.short.btc_sats for y in rep.years)
        any_l = any(y.long.btc_sats for y in rep.years)
        rows.append(
            [
                "Summe",
                fmt_btc(sum(y.short.btc_sats for y in rep.years), P, unit=False) if any_s else "—",
                fmt_eur(sum(y.short.proceeds_eur for y in rep.years), P) if any_s else "—",
                fmt_eur(sum(y.short.cost_eur for y in rep.years), P) if any_s else "—",
                fmt_eur(sum(y.short.fees_eur for y in rep.years), P) if any_s else "—",
                fmt_eur(sum(y.short.gain_eur for y in rep.years), P, signed=True) if any_s else "—",
                fmt_btc(sum(y.long.btc_sats for y in rep.years), P, unit=False) if any_l else "—",
                fmt_eur(sum(y.long.gain_eur for y in rep.years), P, signed=True) if any_l else "—",
            ]
        )
        table(
            [
                "Jahr",
                "< 1 Jahr (BTC)",
                "Veräußerungs- preis",
                "Anschaffungs- kosten",
                "Werbungs- kosten",
                "Gewinn / Verlust < 1 Jahr",
                "≥ 1 Jahr (BTC)",
                "Gewinn / Verlust ≥ 1 Jahr",
            ],
            rows,
            [15, 19, 23, 24, 21, 24, 21, 27],
            ["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
            bold_last=True,
            size=7.3,
            head_size=6.8,
        )
        para(
            "< 1 Jahr gehalten: privates Veräußerungsgeschäft (§ 23 Abs. 1 Satz 1 Nr. 2 EStG), sofern "
            "tatsächlich veräußert. ≥ 1 Jahr: Haltefrist erfüllt, nicht steuerbar. Freigrenze laut "
            "Regelwerk des Jahres (§ 23 Abs. 3 Satz 5 EStG): "
            + ", ".join(f"{y.year}: {_thousands(freigrenze_eur(y.year))} €" for y in rep.years if y.disposals)
            + " — gilt für alle privaten Veräußerungsgeschäfte eines Jahres zusammen."
            + (" * = für einzelne Tage fehlt ein Kurs." if any_missing_price else ""),
            7.8,
            color=MUTED,
            h=4,
        )
    else:
        para("Keine Abflüsse an fremde Adressen.", 9, color=MUTED)
    _keep(c, locals(), ('any_missing_price', 'd', 'rows', 'y'))


def render_boersenverkaeufe(c: SimpleNamespace) -> None:
    """3.1 Veräußerungen auf Börsen ohne Abfluss aus den Wallets"""
    FONT = c.FONT
    P = c.P
    d = c.d
    para = c.para
    pdf = c.pdf
    rep = c.rep
    t = c.t
    table = c.table
    _v = vars(c)
    if "ds" in _v:
        ds = _v["ds"]
    if "head" in _v:
        head = _v["head"]
    if "proceeds" in _v:
        proceeds = _v["proceeds"]
    if "vals" in _v:
        vals = _v["vals"]
    if "y" in _v:
        y = _v["y"]
    other = [d for y in sorted(rep.years, key=lambda y: y.year) for d in y.details
             if d.get("origin", "wallet") != "wallet" and d.get("disposal", True)]
    so_no = "3.2" if other else "3.1"
    if other:
        if pdf.get_y() > pdf.page_break_trigger - 40:
            pdf.add_page()
        pdf.start_section(t("3.1  Veräußerungen auf Börsen ohne Abfluss aus den Wallets"), level=1)
        pdf.ln(1)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 10)
        pdf.set_text_color(*ACCENT)
        pdf.cell(pdf.epw, 6, t("3.1  Veräußerungen auf Börsen ohne Abfluss aus den Wallets"),
                 new_x="LMARGIN", new_y="NEXT")
        para(
            "Verkäufe laut Konto-Export einer Börse, deren Bitcoin nicht aus den betrachteten Wallets "
            "eingezahlt wurden (auf der Börse gekauft oder aus anderer Quelle eingezahlt). Sie sind in "
            "den Jahreswerten oben und in der Anlage-SO-Übersicht enthalten (Rz. 20, 55, 61). Umfasst "
            "ein Verkauf mehrere Teilbestände (FiFo auf der Börse), folgen sie mit ↳. Veräußerungspreis "
            "laut Export; „(Tageskurs)“ = kein Erlös im Export, Tageskurs des Verkaufstags.",
            8,
            color=MUTED,
            h=4,
        )

        def origin_text(d: dict[str, Any]) -> str:
            if d.get("origin") == "exchange":
                return "auf der Börse gekauft ¹"
            return "Herkunft unbekannt (keine Anschaffung belegt)"

        def eur(d: dict[str, Any], key: str, signed: bool = False) -> str:
            return fmt_eur(d.get(key), P, signed=signed) if d.get(key) is not None else "—"

        def total(ds: list[dict[str, Any]], key: str) -> float | None:
            vals = [float(d[key]) for d in ds if d.get(key) is not None]
            return sum(vals) if vals else None

        # ein Verkauf laut Export kann mehrere Teilbestände (FiFo) umfassen → Kopfzeile + ↳
        sales: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for d in other:
            sales.setdefault((d["exit_date"], d.get("counterparty"), d.get("status")), []).append(d)
        orow = []
        for (_day, cp, _st), ds in sales.items():
            est = any(d.get("proceeds_estimated") for d in ds)
            proceeds = total(ds, "proceeds_eur")
            head = [
                fmt_date(ds[0]["exit_date"]),
                "" if len(ds) > 1 else (fmt_date(ds[0]["acquisition_date"]) if ds[0].get("acquisition_date") else "unbekannt"),
                "" if len(ds) > 1 else (str(ds[0]["days_held"]) if ds[0].get("days_held") is not None else "—"),
                fmt_btc(sum(int(d["btc_sats"]) for d in ds), P, unit=False),
                (fmt_eur(proceeds, P) if proceeds is not None else "—") + (" (Tageskurs)" if est else ""),
                fmt_eur(total(ds, "cost_eur"), P) if total(ds, "cost_eur") is not None else "—",
                fmt_eur(total(ds, "fee_eur"), P) if total(ds, "fee_eur") is not None else "—",
                fmt_eur(total(ds, "gain_eur"), P, signed=True) if total(ds, "gain_eur") is not None else "—",
                _name(str(cp or ""), P)
                + (f" · 1 Verkauf, {len(ds)} Teilbestände" if len(ds) > 1 else " · " + origin_text(ds[0])),
            ]
            orow.append(head)
            if len(ds) > 1:
                for d in ds:
                    orow.append(
                        [
                            "↳",
                            fmt_date(d["acquisition_date"]) if d.get("acquisition_date") else "unbekannt",
                            str(d["days_held"]) if d.get("days_held") is not None else "—",
                            fmt_btc(int(d["btc_sats"]), P, unit=False),
                            eur(d, "proceeds_eur"),
                            eur(d, "cost_eur"),
                            eur(d, "fee_eur"),
                            eur(d, "gain_eur", True),
                            origin_text(d),
                        ]
                    )
        table(
            ["Verkauf", "Anschaffung", "Tage", "Menge (BTC)", "Veräußerungs- preis", "Anschaffungs- kosten",
             "Werbungs- kosten", "Gewinn / Verlust", "Börse · Herkunft"],
            orow,
            [18, 20, 11, 19, 22, 22, 19, 19, 24],
            ["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
            size=6.8,
            head_size=6.3,
        )
    _keep(c, locals(), ('d', 'so_no'))


def render_anlage_so(c: SimpleNamespace) -> None:
    """3.2 Jahresübersicht für die Anlage SO"""
    FONT = c.FONT
    P = c.P
    para = c.para
    pdf = c.pdf
    rep = c.rep
    t = c.t
    table = c.table
    _v = vars(c)
    if "any_missing_price" in _v:
        any_missing_price = _v["any_missing_price"]
    if "so_no" in _v:
        so_no = _v["so_no"]
    yrows = anlage_so_rows(rep)
    if yrows:
        if pdf.get_y() > pdf.page_break_trigger - 10 - 6 * len(yrows):
            pdf.add_page()
        pdf.start_section(t(f"{so_no}  Jahresübersicht für die Anlage SO"), level=1)
        pdf.ln(1)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 10)
        pdf.set_text_color(*ACCENT)
        pdf.cell(pdf.epw, 6, t(f"{so_no}  Jahresübersicht für die Anlage SO"), new_x="LMARGIN", new_y="NEXT")
        table(
            ["Jahr", "Veräußerungsgeschäfte < 1 Jahr", "Gewinn / Verlust < 1 Jahr", "Hinweis"],
            [
                [
                    str(r["year"]) + (" *" if r["incomplete"] else ""),
                    str(r["count"]) if r["count"] else "—",
                    fmt_eur(r["gain"], P, signed=True) if r["count"] else "—",
                    r["note"],
                ]
                for r in yrows
            ],
            [16, 46, 38, 74],
            ["LEFT", "RIGHT", "RIGHT", "LEFT"],
            size=7.6,
            head_size=7,
        )
        para(
            "Je Kalenderjahr mit Bewegungen in den Wallets. Ein Veräußerungsgeschäft = ein Abfluss "
            "(Transaktion) bzw. ein Verkauf laut Börsen-Export; es kann mehrere Teilbestände umfassen. "
            "Die Freigrenze (§ 23 Abs. 3 Satz 5 EStG) "
            "gilt für alle privaten Veräußerungsgeschäfte zusammen — andere Geschäfte sind hier nicht "
            "erfasst." + (" * = für einzelne Tage fehlt ein Kurs." if any_missing_price else ""),
            7.8,
            color=MUTED,
            h=4,
        )
