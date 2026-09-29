"""„Jahressteuerreport Bitcoin <Jahr>“ — PDF (Seite 1 und Abschnitte 1–6).

Datenbasis: ``tax_year.TaxYear`` (Auszug aus dem Herkunftsnachweis bis 31.12. des Jahres).
Aufbau:

  Seite 1  § 23 EStG (innerhalb/nach Ablauf der Haltefrist, weitere Geschäfte, Summe,
           Freigrenze), § 22 Nr. 3 EStG (Einkünfte laut Kategorien, Freigrenze), Hinweis auf
           nicht unterstützte Vorgänge. Keine ELSTER-Zeilennummern.
  1        Anschluss an den Herkunftsnachweis, Mengenabstimmung, Wallets mit Aktivität
  2        Veräußerungen des Jahres je Vorgang mit den veräußerten Teilbeständen
  3        Anschaffungen des Jahres
  4        Sonstige Bewegungen (keine Veräußerung, Rz. 54)
  5        Offene Punkte
  6        Methodik (kurz), Regelwerk, Geltungsbereich

Zwei Fassungen wie beim Herkunftsnachweis: intern und Finanzamt (Mengenabstimmung ohne
Bestände — „auf Anforderung“, keine Spalte „Haltefrist endet am“). Anhänge, Hash-Verzeichnis
und Prüfprotokoll folgen gesondert. Nur im Arbeitsspeicher. Keine Steuerberatung.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from btc_origin.origin_report import (
    ACCENT,
    FA_LABEL,
    FULL_LABEL,
    HEAD_FILL,
    INK,
    MASK,
    MUTED,
    ON_REQUEST,
    REQ_MARK,
    HerkunftReport,
    _make_doc,
    file_origins,
    tx_refs,
    build_id,
    fmt_btc,
    fmt_date,
    fmt_eur,
)
from btc_origin.regelwerk import KURSREGELN, load_categories, methodik_vermerk
from btc_origin.tax_year import TaxDisposal, TaxLot, TaxYear

TITLE = "Jahressteuerreport Bitcoin"
HINT_22 = "Die Einordnung ist vom Steuerpflichtigen zu prüfen; der Report ermittelt nur die Werte."
NO_TAX_ADVICE = "Diese Aufstellung ist eine Arbeitsgrundlage und keine Steuerberatung."


@dataclass(frozen=True)
class Anschluss:
    """Anschlussvermerk (Abschnitt 1.1): ein in dieser Sitzung erzeugter Herkunftsnachweis
    (Stand, Stichtag, Build des Dokuments) — oder ``art="anforderung"``: Herkunftsnachweis auf
    Anforderung (Standard, Vorgabe der steuerlichen Prüfung)."""

    stand: str = ""  # „2026-01-10 10:05:00“
    stichtag: str = ""  # „31.12.2024“
    build: str = ""
    art: str = "stichtag"  # stichtag | anforderung

    @property
    def vermerk(self) -> str:
        if self.art == "anforderung":
            return AUF_ANFORDERUNG
        return (f"Anschluss an Herkunftsnachweis Stand {self.stand}, Stichtag {self.stichtag}, "
                f"Build {self.build or 'unbekannt'}. T-Nummern identisch.")


AUF_ANFORDERUNG = ("Die Herkunft aller Bestände ist in einem Herkunftsnachweis dokumentiert, der aus derselben "
                   "Datenbasis erzeugt wird; er wird auf Anforderung vorgelegt.")


def tax_released(tax: TaxYear) -> set[str]:
    """Freigabeliste der Finanzamt-Fassung des Jahresreports: Transaktionen der Veräußerungen
    des Jahres und die Zuflüsse, aus denen veräußerte Teilbestände stammen (auch aus
    Vorjahren). Alle übrigen T-Nummern tragen „°“, der Hash wird auf Anforderung vorgelegt."""
    out: set[str] = set()
    for d in tax.disposals:
        if d.txid:
            out.add(d.txid)
        out.update(x.origin_txid for x in d.lots if x.origin_txid)
    return out


def _relevant_inflows(tax: TaxYear) -> set[str]:
    """Zuflüsse, aus denen veräußerte Teilbestände stammen, und Zuflüsse mit Einkunft des Jahres."""
    out = {x.origin_txid for d in tax.disposals for x in d.lots if x.origin_txid}
    out.update(i.txid for i in tax.income if i.txid)
    out.update(a.txid for a in tax.acquisitions if a.category and a.txid)
    return out


_BUY_AT = re.compile(r"^Kauf auf (.+?) lt\. Export")
NO_EVIDENCE = "keine Vorgänge mit Veräußerung oder Einkunft"


def _fa_evidence(tax: TaxYear, rep: HerkunftReport) -> tuple[set[str], set[tuple[str, str]]]:
    """Finanzamt-Fassung, Anhänge A/B (Vorgabe der steuerlichen Prüfung): was eine Veräußerung
    oder Einkunft des Jahres belegt. Rückgabe: (Zuflüsse veräußerter Teilbestände,
    (Börse, Kauftag) der Käufe hinter veräußerten Teilbeständen — auch aus Vorjahren)."""
    origins = {x.origin_txid for d in tax.disposals for x in d.lots if x.origin_txid}
    buys: set[tuple[str, str]] = set()
    for d in tax.disposals:
        for x in d.lots:
            m = _BUY_AT.match(x.acq_source or "")
            if x.acquisition and (m or x.origin == "exchange"):
                buys.add((m.group(1) if m else d.counterparty.replace("³", ""), x.acquisition[:10]))
    for tx in origins:
        rc = rep.withdrawal_recon.get(tx) or {}
        for p in rc.get("pieces") or []:
            if str(p.get("source") or "").startswith("Kauf auf "):
                buys.add((str(rc.get("exchange") or ""), str(p.get("day") or "")[:10]))
    return origins, buys


def _fa_annex_b_rows(tax: TaxYear, rep: HerkunftReport) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Zeilen für Anhang B der Finanzamt-Fassung und je Börse die Zahl der übrigen Zeilen des Jahres.
    Aufgeführt: Verkäufe und Einzahlungen des Jahres, Käufe hinter veräußerten Teilbeständen,
    Zeilen mit Einkunft nach § 22 Nr. 3 EStG."""
    y = str(tax.year)
    _origins, buys = _fa_evidence(tax, rep)
    shown: list[dict[str, Any]] = []
    rest: dict[str, int] = {}
    for r in rep.exchange_trades:
        day = str(r.get("day") or "")[:10]
        in_year = day.startswith(y)
        kind = str(r.get("kind") or "")
        if (in_year and (kind in ("Verkauf", "Einzahlung") or r.get("einkunft"))) or (
            kind == "Kauf" and not r.get("einkunft") and (str(r.get("exchange") or ""), day) in buys
        ):
            shown.append(r)
        elif in_year:
            rest[str(r["exchange"])] = rest.get(str(r["exchange"]), 0) + 1
    return shown, rest


def _eur(v: float | None, P: bool, *, signed: bool = False) -> str:
    return fmt_eur(v, P, signed=signed) if v is not None else "Kurs fehlt"


def _cat_name(key: str) -> str:
    kat = load_categories()
    k = kat.kategorien.get(key)
    return k.anzeigename if k is not None else key


def lot_proof(tax: TaxYear, lot: TaxLot, ref: Any = None) -> str:
    """Nachweis eines veräußerten Teilbestands: Kauf im Jahr → Beleg + T-Nr des Zuflusses;
    aus Vorjahren → Verweis auf den Herkunftsnachweis mit T-Nr und Belegart."""
    if lot.origin == "unknown" or not lot.acquisition:
        return "Herkunft unbekannt (nicht aus den Wallets) — Anschaffung nicht nachgewiesen"
    if lot.origin == "exchange":
        return lot.acq_source or "Kauf auf der Börse lt. Export"
    kind = "Kauf lt. Export" if lot.beleg == "Export" else "Zuflusstag, Tageskurs"
    r = (ref or (lambda x: tax.ref(x) or "—"))(lot.origin_txid)
    if tax.lot_in_year(lot):
        return f"{kind}; Zufluss {r}"
    return f"siehe Herkunftsnachweis, {r} ({kind})"


def _disposal_proof(tax: TaxYear, d: TaxDisposal, ref: Any) -> str:
    if d.assumed:
        return ref(d.txid)
    return f"Export {d.counterparty.replace('³', '')}" + (f", {d.status}" if d.status else "")


def render_tax_pdf(tax: TaxYear, rep: HerkunftReport, *, anschluss: Anschluss | None = None) -> bytes:
    """Jahressteuerreport als PDF. ``rep.fassung`` entscheidet über die Fassung."""
    fa = rep.fassung == "finanzamt"
    running = f"{TITLE} {tax.year}"
    released = tax_released(tax)
    d = _make_doc(rep, running_title=running, doc_title=running, released=released,
                  legend_text={REQ_MARK: "Transaktions-Hash auf Anforderung (nicht im Transaktionsverzeichnis, Anhang C)"})

    def R(txid: str) -> str:
        """T-Nummer; in der Finanzamt-Fassung mit „°“, wenn der Hash nicht abgedruckt wird."""
        r = tax.ref(txid)
        if not r:
            return "—"
        used_refs.add(txid)
        return r + (REQ_MARK if fa and txid not in released else "")

    used_refs: set[str] = set()
    pdf, t, para, section, table, FONT, W, P = d.pdf, d.t, d.para, d.section, d.table, d.FONT, d.W, d.P
    y = tax.year
    span = f"01.01.{y}–31.12.{y}"

    def sub(title: str) -> None:
        pdf.ln(1)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.multi_cell(pdf.epw, 5.2, t(title), align="L", new_x="LMARGIN", new_y="NEXT")

    def none(text: str = "keine") -> None:
        para(text, 8.5, color=MUTED, h=4.2)

    # ---------------------------------------------------------------- Seite 1
    pdf.set_font(FONT, "B", 20)
    pdf.set_text_color(*INK)
    pdf.multi_cell(W, 10, t(running), align="L", new_x="LMARGIN", new_y="NEXT")
    # Untertitel: wozu das Dokument gehört (Anregung der steuerlichen Prüfung)
    pdf.set_font(FONT, "", 11.5)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(W, 6, t(f"Anlage zur Einkommensteuererklärung {y}"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.l_margin + 30, pdf.get_y() + 1)
    pdf.set_line_width(0.2)
    pdf.ln(4)
    meta = [
        ("Fassung", FA_LABEL if fa else FULL_LABEL),
        ("Veranlagungsjahr", f"{y} (Vorgänge {span}, UTC)"),
        ("Stand", d.stand),
        ("Regelwerk", tax.regelwerk.vermerk if tax.regelwerk else f"VZ {y}"),
    ]
    if rep.person_name:
        meta.append(("Name", rep.person_name))
    if rep.tax_id:
        meta.append(("Steuer-ID", MASK if P else rep.tax_id))
    for k, v in meta:
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(38, 5, t(k))
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(W - 38, 5, t(v), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    open_unsupported = [u for u in tax.unsupported if not u.get("quittiert")]
    if tax.unsupported:
        pdf.set_fill_color(255, 238, 232) if open_unsupported else pdf.set_fill_color(*HEAD_FILL)
        pdf.set_draw_color(190, 60, 40) if open_unsupported else pdf.set_draw_color(*ACCENT)
        pdf.set_text_color(130, 30, 20) if open_unsupported else pdf.set_text_color(*INK)
        pdf.set_font(FONT, "B", 9)
        acked = len(tax.unsupported) - len(open_unsupported)
        msg = (
            f"Achtung: {len(open_unsupported)} nicht unterstützte(r) Vorgang/Vorgänge im Jahr {y} — "
            "nicht bewertet und in keiner Summe enthalten; manuell prüfen (Abschnitt 5.4)."
            if open_unsupported else
            f"{acked} nicht unterstützte(r) Vorgang/Vorgänge im Jahr {y}, geprüft und erläutert — "
            "nicht bewertet und in keiner Summe enthalten (Abschnitt 5.4)."
        )
        if open_unsupported and acked:
            msg += f" Weitere {acked} sind geprüft."
        pdf.multi_cell(W, 4.8, t(msg), border=1, fill=True, align="L", padding=(1.5, 2, 1.5, 2))
        pdf.set_fill_color(255, 255, 255)
        pdf.ln(3)

    # § 23 EStG
    sub("Private Veräußerungsgeschäfte (§ 23 Abs. 1 Satz 1 Nr. 2 EStG)")
    n_t, n_f = tax.count(True), tax.count(False)
    rows = [
        [
            "innerhalb der Haltefrist (steuerbar)",
            str(n_t),
            *([fmt_btc(tax.sats(True), P, unit=False), _eur(tax.total(True, "proceeds_eur"), P),
               _eur(tax.total(True, "cost_eur"), P), _eur(tax.total(True, "fee_eur"), P),
               _eur(tax.gain_taxable, P, signed=True)] if n_t else ["—"] * 5),
        ],
        [
            "nach Ablauf der Haltefrist (nicht steuerbar, zur Information)",
            str(n_f),
            *([fmt_btc(tax.sats(False), P, unit=False), _eur(tax.total(False, "proceeds_eur"), P)]
              if n_f else ["—", "—"]),
            "—", "—", "—",
        ],
        [
            ("Weitere private Veräußerungsgeschäfte (nicht in diesem Report)", 6),
            _eur(tax.other_sales_eur, P, signed=True) if tax.other_sales_given else "keine angegeben",
        ],
        [("Summe steuerbarer Gewinn/Verlust", 6),
         _eur(tax.gain_total, P, signed=True) if n_t or tax.other_sales_given else "—"],
    ]
    table(
        ["", "Anzahl", "Menge (BTC)", "Erlös", "Anschaffungs- kosten", "Werbungs- kosten", "Gewinn/Verlust"],
        rows,
        [44, 12, 21, 21, 27, 23, 25],
        ["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
        bold_last=True,
        size=7.8,
        head_size=7,
    )
    fg = tax.freigrenze_eur
    fg_txt = f"{fg:,}".replace(",", ".")
    reached = tax.freigrenze_reached
    note = (
        f"Freigrenze {fg_txt} € (§ 23 Abs. 3 Satz 5 EStG, Regelwerk VZ {y}): "
        + ("Werte unvollständig (Kurs fehlt) — nicht beurteilbar" if reached is None
           else "erreicht — der gesamte Gewinn ist steuerpflichtig" if reached
           else "nicht erreicht")
        + ". Sie gilt für alle privaten Veräußerungsgeschäfte des Jahres zusammen."
    )
    para(note, 8.3, h=4.3)
    if tax.loss:
        para("Verlust – zur Feststellung erklären (§ 23 Abs. 3 Satz 7 und 8 EStG).", 8.6, bold=True, h=4.5)
    pdf.ln(2)

    # § 22 Nr. 3 EStG
    sub("Sonstige Einkünfte aus Leistungen (§ 22 Nr. 3 EStG)")
    if tax.income:
        irows: list[list[Any]] = [
            [fmt_date(i.day), _cat_name(i.category), i.source.replace("³", ""), fmt_btc(i.sats, P, unit=False),
             _eur(i.value_eur, P)]
            for i in tax.income
        ]
        irows.append([("Summe", 3), fmt_btc(sum(i.sats for i in tax.income), P, unit=False),
                      _eur(tax.income_total, P)])
        table(["Datum", "Art", "Quelle", "Menge (BTC)", "Wert (Tageskurs)"], irows, [22, 40, 48, 30, 34],
              ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT"], bold_last=True, size=7.8, head_size=7)
        f22 = tax.freigrenze_22_3_eur
        r22 = tax.income_freigrenze_reached
        para(
            f"Freigrenze {f22} € (§ 22 Nr. 3 Satz 2 EStG, Regelwerk VZ {y}): "
            + ("Werte unvollständig (Kurs fehlt)" if r22 is None else "erreicht" if r22 else "nicht erreicht")
            + f". {HINT_22}",
            8.3,
            h=4.3,
        )
    else:
        none(f"Keine Zuflüsse als Einkunft nach § 22 Nr. 3 EStG eingeordnet. {HINT_22}")

    # ---------------------------------------------------------------- 1
    pdf.add_page()
    section("1", "Grundlagen und Mengenabstimmung")
    sub("1.1  Anschluss an den Herkunftsnachweis")
    if anschluss is not None:
        para(anschluss.vermerk, 8.6, h=4.4)
    else:
        para("Kein Herkunftsnachweis angegeben.", 8.6, h=4.4)
    sub(f"1.2  Mengenabstimmung {y}")
    r = tax.recon
    if r is None:
        none("nicht verfügbar (keine Wallet-Daten)")
    else:
        ok = r.ok
        mark = "✓" if ok else "✗"

        def amt(sats: int, sign: str = "") -> str:
            return ON_REQUEST if fa else sign + fmt_btc(sats, P, unit=False)

        rrows = [
            [f"Anfangsbestand 01.01.{y}", amt(r.opening)],
            ["+ Zuflüsse von fremden Adressen", amt(r.inflows, "+")],
            ["− Abflüsse an fremde Adressen", amt(r.outflows, "−")],
            ["− Transaktionsgebühren", amt(r.fees, "−")],
            [f"= Endbestand 31.12.{y} (berechnet)", amt(r.computed)],
        ]
        if r.check_sats is not None:
            rrows.append([f"Endbestand laut Blockchain ({r.check_label})", amt(r.check_sats)])
        rrows.append(["Abstimmung", f"{mark} {'stimmt mit der Blockchain überein' if ok else 'Abweichung — bitte prüfen'}"])
        table(["Position", "Menge (BTC)"], rrows, [110, 64], ["LEFT", "RIGHT"], bold_last=True, size=8)
        if fa:
            para("Bestände werden in dieser Fassung nicht abgedruckt; sie werden auf Anforderung vorgelegt. "
                 "Die Abstimmung wurde mit den vollständigen Werten durchgeführt.", 8.2, color=MUTED, h=4.2)
    sub(f"1.3  Wallets mit Aktivität im Jahr {y}")
    if tax.wallets:
        table(["Wallet", "Aktivität von", "bis", "Transaktionen"],
              [[w.name, fmt_date(w.first), fmt_date(w.last), str(w.tx_count)] for w in tax.wallets],
              [70, 36, 36, 32], ["LEFT", "LEFT", "LEFT", "RIGHT"], size=8)
    else:
        none()

    # ---------------------------------------------------------------- 2
    section("2", f"Veräußerungen {y}",
            "Je Vorgang die veräußerten Teilbestände (Einzelbetrachtung, Rz. 61; auf einem Börsenkonto FiFo). "
            "Gebühren sind Werbungskosten (Rz. 59) und nach Menge auf die Teilbestände verteilt (Rz. 57). "
            "Umbuchungen und Satoshi-Tests stehen in Abschnitt 4.")
    if not tax.disposals:
        none(f"Keine Veräußerungen im Jahr {y}.")
    reform = tax.show_reform
    for n, dsp in enumerate(tax.disposals, 1):
        if pdf.get_y() > pdf.page_break_trigger - 40:
            pdf.add_page()
        erloes = _eur(dsp.proceeds_eur, P) + (" (Tageskurs)" if dsp.proceeds_estimated else "")
        head = (f"2.{n}  {fmt_date(dsp.day)} · {dsp.art} · {dsp.counterparty.replace('³', '') or '—'} · "
                f"Nachweis {_disposal_proof(tax, dsp, R)}")
        sub(head)
        para(f"Menge {fmt_btc(dsp.sats, P)} · Erlös {erloes} · Gebühr (Werbungskosten) {_eur(dsp.fee_eur, P)}"
             + (f" ({fmt_btc(dsp.fee_sats, P)} Netzwerkgebühr)" if dsp.fee_sats else ""), 8.3, h=4.3)
        if dsp.assumed:
            para("angenommen — Abfluss an eine fremde Adresse ohne Verkaufsbeleg; als Veräußerung am "
                 "Abflusstag zum Tageskurs angesetzt" + (f". Erläuterung: {dsp.note}" if dsp.note else ""),
                 8.2, color=MUTED, h=4.2)
        lrows = []
        for lot in dsp.lots:
            row = [
                "– " + (fmt_date(lot.acquisition) if lot.acquisition else "unbekannt"),
                fmt_btc(lot.sats, P, unit=False),
                _eur(lot.cost_eur, P),
                f"{lot.days_held} Tage" if lot.days_held is not None else "—",
                "ja" if lot.taxable else "nein",
                lot_proof(tax, lot, R),
            ]
            if reform:
                row.append("—" if lot.altbestand is None else "Alt" if lot.altbestand else "Neu")
            lrows.append(row)
        heads = ["Anschaffung", "Menge (BTC)", "Anschaffungs- kosten", "Haltedauer", "steuerbar", "Nachweis"]
        widths = [22, 20, 28, 19, 17, 68]
        aligns = ["LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT", "LEFT"]
        if reform:
            heads.append("Alt/Neu")
            widths = [21, 19, 27, 18, 16, 60, 13]
            aligns.append("LEFT")
        table(heads, lrows, widths, aligns, size=7.5, head_size=6.8)
        g_t, g_f = dsp.gain(True), dsp.gain(False)
        parts = []
        if dsp.has_taxable:
            parts.append(f"steuerbar {_eur(g_t, P, signed=True)}")
        if dsp.has_free:
            parts.append(f"nicht steuerbar {_eur(g_f, P, signed=True)}")
        para("Ergebnis: " + " · ".join(parts), 8.4, bold=True, h=4.4)
        pdf.ln(1.5)

    # ---------------------------------------------------------------- 3
    # Finanzamt-Fassung: Käufe nur als Anzahl (Aufstellung auf Anforderung), Einkünfte nach
    # § 22 Nr. 3 EStG weiterhin einzeln (Vorgabe der steuerlichen Prüfung, 29.09.2026)
    purchases = [a for a in tax.acquisitions if not a.category]
    listed_acq = [a for a in tax.acquisitions if a.category] if fa else tax.acquisitions
    section("3", f"Anschaffungen {y}",
            ("Zuflüsse ohne Kaufbeleg gelten am Zuflusstag zum Tageskurs als angeschafft; belegte Käufe mit "
             "Kaufdatum und Kaufpreis laut Export (Rz. 20, 43, 55). Einkünfte nach § 22 Nr. 3 EStG gelten zum "
             "Tageskurs als angeschafft und sind einzeln aufgeführt.")
            if fa else
            ("Zuflüsse ohne Kaufbeleg gelten am Zuflusstag zum Tageskurs als angeschafft; belegte Käufe mit "
             "Kaufdatum und Kaufpreis laut Export (Rz. 20, 43, 55). Mehrere Käufe eines Zuflusses stehen in "
             "einer Zeile (Einzelheiten: Export-Zeilen, Anhang B). Einkünfte nach § 22 Nr. 3 EStG gelten zum "
             "Tageskurs als angeschafft."))
    if fa:
        n_p = f"{len(purchases)} Zufluss" if len(purchases) == 1 else f"{len(purchases)} Zuflüsse"
        para(f"Im Jahr {y} gab es {n_p} von fremden Adressen (Anschaffungen); die "
             "vollständige Aufstellung mit Belegen wird auf Anforderung vorgelegt." if purchases else
             f"Im Jahr {y} gab es keine Zuflüsse von fremden Adressen (Anschaffungen).", 8.6, h=4.4)
    if listed_acq:
        if fa:
            sub("Zuflüsse mit Einkunft nach § 22 Nr. 3 EStG")
        arows = []
        for a in listed_acq:
            beleg = a.beleg + (f" · {a.count} Käufe" if a.count > 1 else "")
            if a.category:
                beleg = f"{_cat_name(a.category)} (§ 22 Nr. 3 EStG), Tageskurs"
            row = [fmt_date(a.day), a.wallet, a.source.replace("³", ""), R(a.txid),
                   fmt_btc(a.sats, P, unit=False), _eur(a.cost_eur, P), beleg]
            if not fa:
                row.append(fmt_date(a.holding_end))
            arows.append(row)
        heads = ["Datum", "Wallet", "Herkunft", "T-Nr.", "Menge (BTC)", "Anschaffungs- kosten", "Beleg"]
        widths = [20, 24, 22, 14, 21, 27, 48]
        aligns = ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT"]
        if not fa:
            heads.append("Haltefrist endet am")
            widths = [20, 21, 20, 13, 21, 27, 36, 22]
            aligns.append("LEFT")
        table(heads, arows, widths, aligns, size=7.3, head_size=6.6)
    elif not fa:
        none(f"Keine Anschaffungen im Jahr {y}.")

    # ---------------------------------------------------------------- 4
    section("4", "Sonstige Bewegungen",
            "Umbuchungen zwischen eigenen Wallets, Satoshi-Tests und Einzahlungen auf eigene Börsenkonten: "
            "keine Veräußerung, da nicht auf Dritte übertragen wird (Rz. 54).")
    if tax.movements:
        table(["Art", "Datum", "T-Nr.", "Menge (BTC)", "Gebühr (BTC)", "Einordnung"],
              [[m.kind + (f" ({m.detail})" if m.detail and not fa else ""), fmt_date(m.day), R(m.txid),
                fmt_btc(m.sats, P, unit=False), fmt_btc(m.fee_sats, P, unit=False), "keine Veräußerung (Rz. 54)"]
               for m in sorted(tax.movements, key=lambda m: (not tax.ref(m.txid), tax.ref(m.txid), m.day))],
              [54, 20, 14, 22, 20, 44], ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT"], size=7.5, head_size=6.8)
    else:
        none()

    # ---------------------------------------------------------------- 5
    section("5", "Offene Punkte des Jahres")
    sub("5.1  Zuflüsse ohne Kaufbeleg")
    # Finanzamt-Fassung: nur, wenn sie zu einer Veräußerung oder Einkunft dieses Jahres gehören
    unproven = [x for x in tax.unproven_inflows if not fa or x.txid in _relevant_inflows(tax)]
    if fa and tax.unproven_inflows:
        para("Aufgeführt sind nur Zuflüsse, die zu einer Veräußerung dieses Jahres gehören.",
             8.2, color=MUTED, h=4.2)
    if unproven:
        table(["Datum", "Wallet", "Absender", "T-Nr.", "Menge (BTC)", "Erläuterung"],
              [[fmt_date(x.date), x.wallet, x.source.replace("³", ""), R(x.txid),
                fmt_btc(x.sats, P, unit=False), tax.unproven_notes.get(x.txid) or "—"] for x in unproven],
              [20, 26, 26, 14, 22, 66], ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "LEFT"], size=7.5, head_size=6.8)
    else:
        none()
    sub("5.2  Angenommene Veräußerungen (ohne Verkaufsbeleg)")
    if tax.assumed_disposals:
        table(["Datum", "Gegenstelle", "T-Nr.", "Menge (BTC)", "Erläuterung"],
              [[fmt_date(x.day), x.counterparty.replace("³", ""), R(x.txid),
                fmt_btc(x.sats, P, unit=False), x.note or "—"] for x in tax.assumed_disposals],
              [20, 40, 14, 24, 76], ["LEFT", "LEFT", "LEFT", "RIGHT", "LEFT"], size=7.5, head_size=6.8)
    else:
        none()
    sub("5.3  Nicht zugeordnete Börsenvorgänge")
    if tax.open_exchange:
        table(["Datum", "Börse", "Art", "Menge (BTC)", "Status", "Erläuterung"],
              [[fmt_date(str(o["day"])), o["exchange"], o["kind"], fmt_btc(int(o["sats"]), P, unit=False),
                o.get("status", ""), o.get("note", "")] for o in tax.open_exchange],
              [20, 20, 26, 22, 48, 38], ["LEFT", "LEFT", "LEFT", "RIGHT", "LEFT", "LEFT"], size=7.3, head_size=6.8)
    else:
        none()
    sub("5.4  Nicht unterstützte Vorgänge")
    if tax.unsupported:
        table(["Datum", "Börse bzw. Wallet", "Art", "Menge (BTC)", "Grund", "Status"],
              [[fmt_date(u["day"]), u["exchange"], u["art"] or "—", fmt_btc(int(u["sats"]), P, unit=False),
                u["grund"], f"geprüft: {u['erlaeuterung']}" if u.get("quittiert") else "nicht unterstützt – manuell prüfen"]
               for u in tax.unsupported],
              [20, 24, 26, 22, 46, 36], ["LEFT", "LEFT", "LEFT", "RIGHT", "LEFT", "LEFT"], size=7.3, head_size=6.8)
    else:
        none()

    # ---------------------------------------------------------------- 6
    section("6", "Methodik (kurz)")
    rw = tax.regelwerk
    kurs = KURSREGELN.get(rw.kursregel_id, rw.kursregel_primaer) if rw else rep.price_source
    years = {y}
    for line in (
        f"Kurse nach fester Regel (Rz. 43, 91): {kurs}; dieselbe Regel für Anschaffungskosten und "
        "Veräußerungspreise. Erlöse und Kaufpreise laut Börsen-Export gehen vor (Rz. 20, 55).",
        "Verwendungsreihenfolge: Einzelbetrachtung je Einzelbetrag (UTXO, Rz. 61); auf einem Börsenkonto "
        "FiFo, weil Bitcoin dort nicht einzeln unterscheidbar sind. Haltedauer ab dem Anschaffungstag; "
        "steuerbar bei höchstens einem Jahr (§ 23 Abs. 1 Satz 1 Nr. 2 EStG).",
        "Gebühren: Transaktions- und Börsengebühren einer Veräußerung sind Werbungskosten (Rz. 59), nach Menge "
        "auf steuerbare und nicht steuerbare Teilbestände verteilt (Rz. 57); Gebühren reiner Umbuchungen "
        "bleiben unberücksichtigt.",
        "Satoshi-Test: kleine Einzahlung aus der eigenen Wallet an die Börse als Nachweis der "
        "Wallet-Inhaberschaft (VO (EU) 2023/1113) — Umbuchung, keine Veräußerung (Rz. 54).",
        "Börsenverkäufe: Verkaufstag, Erlös und Börsengebühr laut Export; Abflüsse an fremde Adressen ohne "
        "Verkaufsbeleg gelten als Veräußerung am Abflusstag zum Tageskurs (Annahme, Abschnitt 5.2).",
        f"Freigrenzen laut Regelwerk VZ {y}: § 23 Abs. 3 Satz 5 EStG {fg_txt} €, § 22 Nr. 3 Satz 2 EStG "
        f"{tax.freigrenze_22_3_eur} € — nur Hinweise; die Freigrenze nach § 23 gilt für alle privaten "
        "Veräußerungsgeschäfte des Jahres zusammen.",
        methodik_vermerk(years),
        load_categories().geltungsbereich("Abschnitt 5.4"),
        "Keine Zeilennummern der Steuerformulare; die Übertragung in die Erklärung obliegt dem "
        "Steuerpflichtigen.",
        f"Erstellt mit BTC-Herkunft {build_id() or 'unbekannt'}. {NO_TAX_ADVICE}",
    ):
        para(f"• {line}", 8.3, h=4.3)

    # ---------------------------------------------------------------- Anhänge
    _annexes(tax, rep, d, fa=fa, released=released, used=used_refs)
    return bytes(pdf.output())


def _annexes(tax: TaxYear, rep: HerkunftReport, d: Any, *, fa: bool, released: set[str], used: set[str]) -> None:
    """Anhang A (Abgleich mit Börsen-Exporten im Jahr), B (Export-Zeilen des Jahres),
    C (Transaktionsverzeichnis der verwendeten T-Nummern)."""
    pdf, t, para, section, table, P = d.pdf, d.t, d.para, d.section, d.table, d.P
    y = str(tax.year)

    def none(text: str = "keine") -> None:
        para(text, 8.5, color=MUTED, h=4.2)

    # A — Abgleich mit Börsen-Exporten (nur Zeilen und Auszahlungen des Jahres)
    pdf.add_page()
    # Finanzamt-Fassung: nur, was eine Veräußerung oder Einkunft des Jahres belegt (Maßstab wie
    # Abschnitt 3; Vorgabe der steuerlichen Prüfung) — Dateiliste bleibt vollständig
    evidence = bool(tax.disposals or tax.income)
    origins, _buys = _fa_evidence(tax, rep)
    section("Anhang A", f"Abgleich mit Börsen-Exporten {y}",
            ("Zeilen der Exportdateien im Jahr je Datei und Art; darunter die Herkunft der Datei, soweit angegeben. "
             "Abgleich nur für Auszahlungen, aus denen ein im Jahr veräußerter Teilbestand stammt: Käufe − "
             "Auszahlungsgebühr − Transaktionskosten = Eingang laut Blockchain.")
            if fa else
            "Zeilen der Exportdateien im Jahr je Datei und Art; darunter die Herkunft der Datei, soweit angegeben. "
            "Je Auszahlung in eine Wallet: Käufe − Auszahlungsgebühr − Transaktionskosten = Eingang laut Blockchain.")
    rows_in_year = [r for r in rep.exchange_trades if str(r.get("day") or "").startswith(y)]
    files = [f for f in rep.exchange_files if f.get("file")]
    origins = file_origins(rep)
    if rows_in_year and files:
        by_file: dict[str, dict[str, int]] = {}
        for r in rows_in_year:
            f = str(r.get("file") or "")
            by_file.setdefault(f, {})
            by_file[f][str(r["kind"])] = by_file[f].get(str(r["kind"]), 0) + 1
        frows = []
        for f in files:
            counts = by_file.get(str(f["file"]), {})
            if not counts:
                continue
            name = str(f["file"]) + (f"\n{origins[str(f['file'])]}" if origins.get(str(f["file"])) else "")
            frows.append([name, str(f.get("name") or ""), *(str(counts.get(k, 0)) for k in
                          ("Kauf", "Verkauf", "Auszahlung", "Einzahlung"))])
        if frows:
            table(["Datei (Herkunft)", "Börse", "Käufe", "Verkäufe", "Auszahlungen", "Einzahlungen"], frows,
                  [66, 24, 18, 20, 24, 22], ["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"], size=7.5, head_size=6.8)
        else:
            none("Keine Exportzeilen je Datei zuordenbar.")
    else:
        none(f"Keine Zeilen aus Börsen-Exporten im Jahr {y}.")
    if fa and not evidence:
        none(f"Im Jahr {y} {NO_EVIDENCE}.")
    recon = [(tx, rc) for tx, rc in rep.withdrawal_recon.items()
             if (tx in origins if fa else str(rc.get("day") or "").startswith(y))]
    if recon:
        rrows = []
        for tx, rc in sorted(recon, key=lambda x: (str(x[1].get("day")), x[0])):
            ref = tax.ref(tx)
            ref = (ref + (REQ_MARK if fa and tx not in released else "")) if ref else "—"
            if ref != "—":
                used.add(tx)
            rrows.append([fmt_date(str(rc["day"])), str(rc.get("exchange") or ""), ref,
                          fmt_btc(int(rc.get("bought_sats") or 0), P, unit=False),
                          fmt_btc(int(rc.get("fee_sats") or 0), P, unit=False),
                          fmt_btc(int(rc.get("received_sats") or 0), P, unit=False),
                          "✓" if rc.get("ok", True) else "✗"])
        table(["Auszahlung", "Börse", "Zufluss", "Käufe (BTC)", "Gebühr (BTC)", "Eingang (BTC)", ""], rrows,
              [22, 24, 18, 30, 26, 30, 8], ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
              size=7.5, head_size=6.8)

    # B — Export-Zeilen des Jahres (Finanzamt-Fassung: nur Belege zu Veräußerung/Einkunft);
    # dazu Einkünfte aus zuordnung_manuell (Zufluss ohne Export-Zeile) einzeln
    income_rows = []
    for i in tax.income:
        if not i.txid:
            continue  # Einkunft laut Export-Zeile — steht dort schon
        ref = tax.ref(i.txid)
        if ref:
            used.add(i.txid)
            ref += REQ_MARK if fa and i.txid not in released else ""
        income_rows.append({
            "day": i.day, "exchange": i.source.replace("³", ""), "kind": "Zufluss", "sats": i.sats,
            "eur": i.value_eur,
            "note": f"{ref or '—'} · {_cat_name(i.category)} (§ 22 Nr. 3 EStG), Tageskurs — Zufluss laut "
                    "Blockchain, keine Export-Zeile; Einordnung laut zuordnung_manuell",
        })
    if fa:
        _fa_annex_b(tax, rep, d, evidence, income_rows)
    else:
        section("Anhang B", f"Export-Zeilen {y}",
                "Alle Zeilen der Börsen-Exporte im Jahr, einheitlich dargestellt, mit ihrer Zuordnung (Anschaffungen "
                "mit mehreren Käufen je Zufluss, Verkäufe, Ein- und Auszahlungen).")
        if rows_in_year or income_rows:
            table(["Datum", "Börse", "Art", "Menge (BTC)", "Betrag", "Gebühr", "Zuordnung"],
                  [_b_row(r, P) for r in sorted(rows_in_year + income_rows, key=lambda r: str(r["day"]))],
                  [18, 18, 18, 20, 20, 18, 62], ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
                  size=7, head_size=6.6)
        else:
            none()

    # C — Transaktionsverzeichnis (nur die im Report verwendeten T-Nummern)
    section("Anhang C", "Transaktionsverzeichnis",
            ("Vollständige Transaktions-Hashes der Veräußerungen des Jahres und der Zuflüsse, aus denen die "
             f"veräußerten Teilbestände stammen (auch aus Vorjahren). T-Nummern mit „{REQ_MARK}“ sind hier nicht "
             "aufgeführt; ihre Hashes werden auf Anforderung vorgelegt (Rz. 87, 101–104).")
            if fa else
            "Die im Report verwendeten T-Nummern mit vollständigem Transaktions-Hash; Nummerierung wie im "
            "Herkunftsnachweis (nach Blockzeit).")
    # Finanzamt-Fassung: nur freigegebene Hashes; Art wie in Abschnitt 4 (z. B. Satoshi-Test)
    listed = [x for x in tx_refs(rep) if x.txid in used and (not fa or x.txid in released)]
    kind_of = {m.txid: m.kind for m in tax.movements if m.txid}
    if listed:
        from fpdf.enums import TableCellFillMode
        from fpdf.fonts import FontFace

        from btc_origin.origin_report import HEAD_FILL as _HF, INK as _INK, RULE, ZEBRA

        mono = FontFace(family="Courier", size_pt=6.8)
        pdf.set_font(d.FONT, "", 7.3)
        pdf.set_text_color(*_INK)
        pdf.set_draw_color(*RULE)
        with pdf.table(
            col_widths=(16, 18, 20, 24, 96), width=pdf.epw, text_align=("LEFT",) * 5, line_height=4.4,
            padding=(1.2, 1.6), headings_style=FontFace(emphasis="B", size_pt=6.8, fill_color=_HF, color=_INK),
            cell_fill_color=ZEBRA, cell_fill_mode=TableCellFillMode.ROWS, borders_layout="HORIZONTAL_LINES",
            repeat_headings=1,
        ) as tbl:
            r = tbl.row()
            for h in ("Referenz", "Datum", "Wallet", "Art", "Transaktions-Hash"):
                r.cell(t(h))
            for x in listed:
                shown = x.txid if (not fa or x.txid in released) else ON_REQUEST
                rr = tbl.row()
                rr.cell(x.ref + (REQ_MARK if fa and x.txid not in released else ""))
                rr.cell(fmt_date(x.day) if x.day else "unbestätigt")
                rr.cell(t(x.wallet or "—"))
                rr.cell(t(kind_of.get(x.txid, x.art)))
                rr.cell(t(MASK if P and shown != ON_REQUEST else shown),
                        style=mono if shown != ON_REQUEST and not P else None)
    else:
        none("keine")


def _b_row(r: dict[str, Any], P: bool) -> list[Any]:
    return [fmt_date(str(r["day"])), str(r["exchange"]), str(r["kind"]), fmt_btc(int(r["sats"]), P, unit=False),
            fmt_eur(r.get("eur"), P) if r.get("eur") is not None else "—",
            fmt_eur(r.get("fee_eur"), P) if r.get("fee_eur") else (fmt_btc(int(r["fee_sats"]), P) if r.get("fee_sats") else "—"),
            str(r.get("note") or "")]


def _fa_annex_b(tax: TaxYear, rep: HerkunftReport, d: Any, evidence: bool,
                income_rows: list[dict[str, Any]]) -> None:
    """Anhang B der Finanzamt-Fassung: nur Zeilen, die eine Veräußerung oder Einkunft des Jahres
    belegen, dazu nicht unterstützte Zeilen; je Börse eine Abschlusszeile mit der Zahl der übrigen."""
    para, section, table, P = d.para, d.section, d.table, d.P
    y = tax.year
    section("Anhang B", f"Export-Zeilen {y}",
            "Zeilen der Börsen-Exporte, die eine Veräußerung oder Einkunft des Jahres belegen: Verkäufe und "
            "Einzahlungen des Jahres, Käufe hinter veräußerten Teilbeständen (auch aus Vorjahren), Zeilen mit "
            "Einkunft nach § 22 Nr. 3 EStG, nicht unterstützte Zeilen. Übrige Zeilen auf Anforderung.")
    shown, rest = _fa_annex_b_rows(tax, rep) if evidence else ([], {})
    shown = shown + income_rows
    unsupported = [{"day": u["day"], "exchange": u["exchange"], "kind": u.get("richtung") or u["art"] or "—",
                    "sats": u["sats"], "note": f"nicht unterstützt (Abschnitt 5.4): {u['grund']}"}
                   for u in tax.unsupported]
    if not evidence:
        para(f"Im Jahr {y} {NO_EVIDENCE}.", 8.5, color=MUTED, h=4.2)
    rows: list[list[Any]] = []
    for ex in sorted({str(r["exchange"]) for r in shown + unsupported} | set(rest), key=str.lower):
        rows += [_b_row(r, P) for r in sorted(shown + unsupported, key=lambda r: str(r["day"]))
                 if str(r["exchange"]) == ex]
        if rest.get(ex):
            rows.append([(f"{ex}: {rest[ex]} weitere Zeile(n) des Jahres ohne Bezug zu einer Veräußerung oder "
                          "Einkunft; auf Anforderung", 7)])
    if rows:
        table(["Datum", "Börse", "Art", "Menge (BTC)", "Betrag", "Gebühr", "Zuordnung"], rows,
              [18, 18, 18, 20, 20, 18, 62], ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
              size=7, head_size=6.6)
    elif evidence:
        para("keine", 8.5, color=MUTED, h=4.2)


def tax_report_name(year: int, fassung: str) -> str:
    return f"steuerreport-bitcoin-{year}-{'finanzamt' if fassung == 'finanzamt' else 'intern'}.pdf"


def default_year(today: date, years: list[int]) -> int | None:
    """Vorgabe in der Oberfläche: das vergangene Jahr, sonst das jüngste verfügbare."""
    if today.year - 1 in years:
        return today.year - 1
    return max(years) if years else None


# ---------------------------------------------------------------------------
# Prüfprotokoll (Abnahmeprüfungen 1–7)
# ---------------------------------------------------------------------------


def result_hash(tax: TaxYear) -> str:
    """SHA-256 über alle Rechenergebnisse des Jahres (kanonisches JSON): gleiche Eingaben
    (Daten, Regeldateien, Kategorien, Build) ergeben denselben Hash."""
    import hashlib
    import json

    def r2(v: float | None) -> float | None:
        return None if v is None else round(v, 2)

    data = {
        "jahr": tax.year,
        "veraeusserungen": [
            {"tag": d.day, "art": d.art, "tx": tax.ref(d.txid), "gegenstelle": d.counterparty,
             "teile": [[x.acquisition, x.sats, r2(x.proceeds_eur), r2(x.cost_eur), r2(x.fee_eur), r2(x.gain_eur),
                        x.taxable, tax.ref(x.origin_txid)] for x in d.lots]}
            for d in tax.disposals
        ],
        "anschaffungen": [[a.day, a.wallet, tax.ref(a.txid), a.sats, r2(a.cost_eur), a.count, a.category]
                          for a in tax.acquisitions],
        "einkuenfte": [[i.day, i.category, i.sats, r2(i.value_eur), tax.ref(i.txid)] for i in tax.income],
        "bewegungen": [[m.kind, m.day, tax.ref(m.txid), m.sats, m.fee_sats] for m in tax.movements],
        "abstimmung": None if tax.recon is None else [tax.recon.opening, tax.recon.inflows, tax.recon.outflows,
                                                       tax.recon.fees, tax.recon.closing, tax.recon.check_sats],
        "summe": [r2(tax.gain_taxable), r2(tax.other_sales_eur), r2(tax.income_total)],
        "nicht_unterstuetzt": [[u["day"], u["exchange"], u["art"], u["sats"], bool(u.get("quittiert"))]
                               for u in tax.unsupported],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def check_tax_reports(
    tax: TaxYear, rep: HerkunftReport, *, anschluss: Anschluss | None = None
) -> tuple[bytes, bytes, str]:
    """Beide Fassungen erzeugen und die Abnahmeprüfungen 1–7 ausführen. Rückgabe: (PDF intern,
    PDF Finanzamt-Fassung, Prüfprotokoll). Das Protokoll endet mit ERGEBNIS: BESTANDEN,
    BESTANDEN mit Warnung(en) oder FEHLER."""
    import dataclasses

    from btc_origin.origin_report import (
        _TEXT_LOG,
        FaCheck,
        _check_fa_only,
        _pdf_text,
        _rules_block,
    )

    full_rep = dataclasses.replace(rep, fassung="full")
    fa_rep = dataclasses.replace(rep, fassung="finanzamt")
    full_pdf = render_tax_pdf(tax, full_rep, anschluss=anschluss)
    full_log = list(_TEXT_LOG.get() or [])
    fa_pdf = render_tax_pdf(tax, fa_rep, anschluss=anschluss)
    fa_log = list(_TEXT_LOG.get() or [])
    fa_text, fa_meta = _pdf_text(fa_pdf)
    released = tax_released(tax)
    checks: list[FaCheck] = []

    def near(a: float | None, b: float | None) -> bool:
        return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 0.015)

    # 1 Summen auf Seite 1 = Summe der Einzelgeschäfte (Abschnitt 2)
    parts = [(d.gain(True), d.gain(False)) for d in tax.disposals]
    sum_t = None if any(p[0] is None for p in parts if p) else sum(p[0] or 0.0 for p in parts)
    per_lot = {
        attr: sum(float(getattr(x, attr) or 0) for d in tax.disposals for x in d.lots if x.taxable)
        for attr in ("proceeds_eur", "cost_eur", "fee_eur")
    }
    ok1 = near(tax.gain_taxable, sum_t) and all(near(tax.total(True, a), v) for a, v in per_lot.items()) \
        and tax.count(True) == sum(1 for d in tax.disposals if d.has_taxable)
    checks.append(FaCheck("1 Summen auf Seite 1 = Summe der Einzelgeschäfte", ok1,
                          f"{len(tax.disposals)} Veräußerung(en), {sum(len(d.lots) for d in tax.disposals)} Teilbestände"))

    # 2 Mengenabstimmung
    r = tax.recon
    checks.append(FaCheck("2 Mengenabstimmung Anfangsbestand + Zuflüsse − Abflüsse − Gebühren = Endbestand",
                          r is not None and r.ok,
                          "stimmt" + (f" ({r.check_label})" if r and r.check_label else "") if r and r.ok
                          else "nicht verfügbar" if r is None else "Abweichung"))

    # 3 Abgleich mit dem Herkunftsnachweis (Jahreswerte Abschnitt 3)
    s = tax.summary
    if s is None:
        ok3, det3 = not tax.disposals, "keine Veräußerungen" if not tax.disposals else "Jahreswerte fehlen"
    else:
        diffs = [name for name, a, b in (
            ("Gewinn/Verlust", tax.gain_taxable, s.short.gain_eur if not s.short.missing_price else None),
            ("Erlös", tax.total(True, "proceeds_eur"), s.short.proceeds_eur if not s.short.missing_price else None),
            ("Anschaffungskosten", tax.total(True, "cost_eur"), s.short.cost_eur if not s.short.missing_price else None),
        ) if not near(a, b)]
        if tax.sats(False) != s.long.btc_sats or tax.sats(True) != s.short.btc_sats:
            diffs.append("Mengen")
        ok3, det3 = not diffs, "identisch" if not diffs else "Abweichung: " + ", ".join(diffs)
    checks.append(FaCheck("3 Werte = Herkunftsnachweis (Abschnitt 3, gleiches Jahr)", ok3, det3))

    # 4 T-Nummern aufgelöst (Anhang C) oder mit ° gekennzeichnet — je Fassung
    def refs_ok(log: list[tuple[int, bool, str]], fa: bool) -> tuple[bool, str]:
        body = [txt for _p, footer, txt in log if not footer]
        head = next((i for i, x in enumerate(body) if x == "Anhang C  Transaktionsverzeichnis"), len(body))
        listed = {x.ref for x in tx_refs(rep) if not fa or x.txid in released}
        open_refs = sorted({m.group() for txt in body[:head] for m in re.finditer(r"T-\d{3,}", txt)
                            if m.group() not in listed and not txt[m.end():].startswith(REQ_MARK)})
        return not open_refs, "alle aufgelöst" if not open_refs else "nicht aufgelöst: " + ", ".join(open_refs[:10])

    ok_i, det_i = refs_ok(full_log, False)
    ok_f, det_f = refs_ok(fa_log, True)
    checks.append(FaCheck("4 T-Nummern in Anhang C aufgelöst oder mit „°“ gekennzeichnet", ok_i and ok_f,
                          f"intern: {det_i}; Finanzamt-Fassung: {det_f}"))

    # 5 nur Vorgänge des Jahres (Ausnahme: Anschaffungsdaten veräußerter Teilbestände)
    y = str(tax.year)
    outside = [d.day for d in tax.disposals if not d.day.startswith(y)]
    outside += [a.day for a in tax.acquisitions if not a.day.startswith(y)]
    outside += [i.day for i in tax.income if not i.day.startswith(y)]
    outside += [m.day for m in tax.movements if not m.day.startswith(y)]
    outside += [x.date for x in tax.unproven_inflows if not x.date.startswith(y)]
    outside += [str(u["day"]) for u in tax.unsupported if not str(u["day"]).startswith(y)]
    checks.append(FaCheck(f"5 Nur Vorgänge {y} (außer Anschaffungsdaten veräußerter Teilbestände)", not outside,
                          "ja" if not outside else f"{len(outside)} Vorgang/Vorgänge außerhalb des Jahres"))

    # 6 Finanzamt-Fassung: keine xpubs/Adressen, nur freigegebene Hashes, keine Bestände, Metadaten
    fa_checks = _check_fa_only(fa_rep, fa_pdf, fa_log, fa_text, fa_meta, released=released)
    checks.append(FaCheck("6 Finanzamt-Fassung (wie Herkunftsnachweis 3a–3e)", all(c.ok for c in fa_checks),
                          "; ".join(f"{c.name.split(' ', 1)[0]} {'ok' if c.ok else c.detail}" for c in fa_checks)))

    # 7 Fußnoten auf derselben Seite erklärt — je Fassung
    def notes_ok(log: list[tuple[int, bool, str]]) -> list[int]:
        marks = "¹²³⁴⁵⁶" + REQ_MARK + "✓✗"
        pages: dict[int, tuple[set[str], set[str]]] = {}
        for page, footer, txt in log:
            body_m, foot_m = pages.setdefault(page, (set(), set()))
            if footer:
                if txt[:1] in marks:
                    foot_m.add(txt[:1])
            else:
                body_m.update(c for c in txt if c in marks)
        return sorted(p for p, (b_, f_) in pages.items() if not b_ <= f_)

    bad7 = notes_ok(full_log) + notes_ok(fa_log)
    checks.append(FaCheck("7 Fußnoten auf derselben Seite erklärt", not bad7,
                          "ja" if not bad7 else f"nicht erklärt auf Seite(n) {', '.join(map(str, bad7))}"))

    # Geltungsbereich: nicht unterstützte Vorgänge (quittiert → Warnung)
    open_u = [u for u in tax.unsupported if not u.get("quittiert")]
    checks.append(FaCheck("8 Keine nicht unterstützten Vorgänge (Geltungsbereich)", not open_u,
                          "keine" if not open_u else f"{len(open_u)} nicht unterstützt und nicht quittiert (Abschnitt 5.4)"))
    warnings = [f"Quittiert (geprüft, nicht bewertet): {u['day']} {u['exchange']} „{u['art']}“ — {u['erlaeuterung']}"
                for u in tax.unsupported if u.get("quittiert")]
    if anschluss is None:
        warnings.append("Kein Herkunftsnachweis angegeben — ausdrücklich gewählt (Abschnitt 1.1)")

    ok = all(c.ok for c in checks)
    lines = [
        f"{TITLE} {tax.year}: Prüfprotokoll",
        f"Stand {rep.generated_at:%Y-%m-%d %H:%M:%S} · Build {build_id() or 'unbekannt'} · Vorgänge 01.01.–31.12.{y} (UTC)",
        "Nur für die eigenen Unterlagen — enthält die freigegebenen Transaktions-IDs.",
        "",
        *_rules_block(rep),
        "Export-Dateien:",
        *([f"  {f['file']} · SHA-256 {f.get('sha256', 'unbekannt')}"
           + (f" · Herkunft: {file_origins(rep)[f['file']]}" if file_origins(rep).get(f["file"]) else "")
           for f in rep.exchange_files if f.get("file")]
          or ["  keine"]),
        f"Rechenergebnisse: SHA-256 {result_hash(tax)} (gleiche Eingaben → gleicher Wert)",
        (anschluss.vermerk if anschluss else "Anschluss: kein Herkunftsnachweis angegeben"),
        "",
        "Abnahmeprüfungen:",
        *[f"  [{'OK' if c.ok else 'FEHLER'}] {c.name}: {c.detail}" for c in checks],
        *[f"  [WARNUNG] {w}" for w in warnings],
        "",
        f"Freigabeliste Finanzamt-Fassung ({len(released)} Transaktions-IDs):",
        *[f"  {tax.ref(x) or '—':<7} {x}" for x in sorted(released, key=lambda x: (tax.ref(x), x))],
        "",
        ("ERGEBNIS: BESTANDEN" + (f" mit {len(warnings)} Warnung(en)" if warnings else "")) if ok
        else "ERGEBNIS: FEHLER — " + "; ".join(c.name for c in checks if not c.ok),
        "",
        NO_TAX_ADVICE,
    ]
    return full_pdf, fa_pdf, "\n".join(lines) + "\n"


def protocol_result(text: str) -> dict[str, Any]:
    """Ergebnis aus dem Prüfprotokoll: bestanden | warnung | fehler, dazu die nicht bestandenen
    Prüfungen und die Anzahl der Warnungen (Anzeige am Button der Oberfläche)."""
    lines = text.splitlines()
    fehler = [ln.split("] ", 1)[1].split(":", 1)[0] for ln in lines if ln.strip().startswith("[FEHLER]")]
    warnungen = sum(1 for ln in lines if ln.strip().startswith("[WARNUNG]"))
    ergebnis = "fehler" if fehler else "warnung" if warnungen else "bestanden"
    return {"ergebnis": ergebnis, "fehler": fehler, "warnungen": warnungen}


def render_protocol_pdf(text: str, rep: HerkunftReport, year: int) -> bytes:
    """Prüfprotokoll als PDF (gleicher Inhalt wie die Textfassung; nur für die eigenen
    Unterlagen — enthält die freigegebenen Transaktions-IDs)."""
    import dataclasses

    title = f"Prüfprotokoll {TITLE} {year}"
    d = _make_doc(dataclasses.replace(rep, fassung="full"), running_title=title, doc_title=title)
    pdf, t, FONT, W = d.pdf, d.t, d.FONT, d.W
    colors = {"[OK]": (30, 120, 60), "[FEHLER]": (170, 30, 20), "[WARNUNG]": (150, 100, 0)}
    lines = text.splitlines()
    pdf.set_font(FONT, "B", 17)
    pdf.set_text_color(*INK)
    pdf.multi_cell(W, 9, t(title), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    for line in lines[1:]:
        s = line.strip()
        if not s:
            pdf.ln(2)
            continue
        pdf.set_x(pdf.l_margin)
        if s.startswith("ERGEBNIS:"):
            bad = "FEHLER" in s
            warn = "Warnung" in s
            pdf.set_fill_color(*((255, 232, 228) if bad else (255, 246, 225) if warn else (228, 244, 232)))
            pdf.set_draw_color(*(colors["[FEHLER]"] if bad else colors["[WARNUNG]"] if warn else colors["[OK]"]))
            pdf.set_text_color(*(colors["[FEHLER]"] if bad else colors["[WARNUNG]"] if warn else colors["[OK]"]))
            pdf.set_font(FONT, "B", 11)
            pdf.multi_cell(W, 6.5, t(s), border=1, fill=True, align="L", padding=(1.5, 2, 1.5, 2))
            pdf.set_fill_color(255, 255, 255)
            continue
        tag = next((k for k in colors if s.startswith(k)), None)
        heading = not line.startswith(" ") and s.endswith(":")
        small = "SHA-256" in s or (line.startswith("  T-") and len(s) > 64)
        pdf.set_font(FONT, "B" if heading else "", 7.2 if small else 8.6)
        pdf.set_text_color(*(colors[tag] if tag else MUTED if small else INK))
        indent = 4 if line.startswith("  ") else 0
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(W - indent, 3.8 if small else 4.4, t(s), align="L", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
