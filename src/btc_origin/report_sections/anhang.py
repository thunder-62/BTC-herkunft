"""Anhänge A–D."""

from __future__ import annotations

from typing import Any
from types import SimpleNamespace

from btc_origin.origin_report import (  # noqa: F401
    HEAD_FILL,
    INK,
    MUTED,
    REQ_MARK,
    RULE,
    TX_ANNEX_NO,
    TX_ANNEX_TITLE,
    ZEBRA,
    _mask_name,
    _md_escape,
    _name,
    file_origins,
    fmt_btc,
    fmt_date,
    fmt_eur,
)
from btc_origin.report_sections import _keep


def render_anhang_a(c: SimpleNamespace) -> None:
    """Annex A: Abgleich mit Börsen-Exporten (documentation)"""
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
    if "line" in _v:
        line = _v["line"]
    if "lines" in _v:
        lines = _v["lines"]
    if "n" in _v:
        n = _v["n"]
    if "r" in _v:
        r = _v["r"]
    if "rc" in _v:
        rc = _v["rc"]
    if "rows" in _v:
        rows = _v["rows"]
    if "x" in _v:
        x = _v["x"]
    if rep.exchange_files:
        pdf.add_page(orientation="P")
        section(
            "Anhang A",
            "Abgleich mit Börsen-Exporten",
            "Nach Rz. 20 und 55 ist bei Handel über eine zentrale Handelsplattform der dort "
            "aufgezeichnete Handelszeitpunkt maßgebend. Für die hier aufgeführten Börsen lagen "
            "Konto-Exporte vor; alle übrigen Zu- und Abflüsse sind mit dem Zeitpunkt des Zuflusses "
            "in bzw. des Abflusses aus den Wallets bewertet (Blockchain-Zeitstempel).",
        )
        reps = {r.exchange: r for r in rep.exchange_reports}
        origins = file_origins(rep)
        rows = []
        for f in rep.exchange_files:
            r = reps.get(f.get("name"))
            origin = origins.get(str(f.get("file") or ""), "")

            def n(key: str, fallback: int) -> str:
                # Zahlen je Datei — bei mehreren Dateien derselben Börse nicht die Börsensumme
                return str(f[key]) if key in f else str(fallback)

            rows.append(
                [
                    # Herkunft der Datei (Art „Datei“) als zweite Zeile unter dem Dateinamen
                    f"**{_md_escape(_name(str(f.get('file') or ''), P))}**"
                    + (f"\n{_md_escape(origin)}" if origin else ""),
                    _md_escape(_name(str(f.get("name") or ""), P)),
                    n("buys", r.buys if r else 0),
                    n("sells", r.sells if r else 0),
                    n("withdrawals", r.withdrawals if r else 0),
                    n("deposits", r.deposits_export if r else 0),
                    str(f.get("txids") or 0),
                ]
            )
        table(
            ["Datei", "Börse", "Käufe", "Verkäufe", "Auszahlungen", "Einzahlungen", "TxIDs"],
            rows,
            [60, 25, 13, 17, 24, 22, 13],
            ["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
            size=7.5,
            head_size=7,
            markdown=True,
        )
        para(
            "Zahlen je Datei (Bitcoin-Zeilen"
            + (f" bis {rep.until:%d.%m.%Y}" if rep.until is not None else "")
            + "). Mehrere Dateien derselben Börse (z. B. früheres und "
            "aktuelles Konto) werden gemeinsam ausgewertet; die Abgleiche unten gelten je Börse.",
            7.8,
            color=MUTED,
            h=4,
        )
        for r in rep.exchange_reports:
            if pdf.get_y() > pdf.page_break_trigger - 20:
                pdf.add_page()
            pdf.set_x(pdf.l_margin)
            pdf.set_font(FONT, "B", 9.5)
            pdf.set_text_color(*INK)
            pdf.cell(pdf.epw, 5.5, t(_name(r.exchange, P)), new_x="LMARGIN", new_y="NEXT")
            named = sum(1 for w in r.matched if str((w.matched_entry or {}).get("source_name") or "") == r.exchange)
            n_direct = sum(1 for w in r.matched if w.direct)
            n_wd = len(r.matched) - n_direct
            what = " und ".join(
                x
                for x in (
                    f"{n_wd} Auszahlung(en)" if n_wd else "",
                    f"{n_direct} Kauf/Käufe mit Direktversand" if n_direct else "",
                )
                if x
            ) or "0 Auszahlungen"
            lines = [
                f"Kauf-Seite: {what} laut Export einem Zufluss in die Wallets "
                "zugeordnet (Anschaffung = Kaufdatum und -preis laut Export)"
                + (
                    f", davon {len(r.matched) - named} an einen unbenannten Absender (ext-…)"
                    if len(r.matched) > named
                    else ""
                )
                + f". Zuflüsse mit Absender „{_name(r.exchange, P)}“: {r.entries_total}, davon ohne "
                f"Zuordnung {max(0, r.entries_total - named)} (Anschaffung = Tag des Zuflusses, Tageskurs).",
            ]
            direct_open = [w for w in r.unmatched if w.direct]
            if direct_open:
                lines.append(
                    f"{len(direct_open)} Kauf/Käufe mit Direktversand laut Export keinem Zufluss in die "
                    "Wallets zugeordnet (Empfangsadresse keiner betrachteten Wallet); einzeln mit "
                    "Erläuterung in Anhang B."
                )
            wd_open = [w for w in r.unmatched if not w.direct]
            if wd_open:
                linked = sum(1 for w in wd_open if w.matched_entry is not None)
                lines.append(
                    f"{len(wd_open)} Auszahlung(en) laut Export ohne Kaufdaten für einen Zufluss"
                    + (
                        f" — davon {linked} mit passendem Zufluss, aber ohne zuordenbaren Kauf im Export "
                        "davor (keiner vorhanden oder bereits mit anderen Auszahlungen abgeflossen; "
                        "Anschaffung = Zuflusstag)"
                        if linked
                        else ""
                    )
                    + "; einzeln mit Erläuterung in Anhang B."
                )
            if r.deposits_export or r.deposits_wallet:
                unknown = max(0, r.deposits_export - r.deposits_matched - len(r.returns))
                lines.append(
                    f"Einzahlungen laut Export: {r.deposits_export} — davon aus den betrachteten Wallets "
                    f"{r.deposits_matched}, aus unbekannter Quelle {unknown}"
                    + (f", Rücklauf einer eigenen Auszahlung {len(r.returns)}" if getattr(r, "returns", None) else "")
                    + ". Abflüsse aus den Wallets an "
                    f"diese Börse: {r.deposits_wallet}."
                    + (
                        f" Davon {r.satoshi_tests} Satoshi-Test(s) ⁶ zum Nachweis der Wallet-Inhaberschaft."
                        if getattr(r, "satoshi_tests", 0)
                        else ""
                    )
                    + (
                        " Im Export enthaltene Einzahlungen aus den Wallets sind Umbuchungen auf das eigene "
                        "Börsenkonto (Rz. 54), keine Veräußerung; die Teilbestände behalten ihr "
                        "Anschaffungsdatum (FiFo auf dem Börsenkonto, Rz. 61)."
                        if r.deposits_matched
                        else ""
                    )
                )
            if r.sells and not r.deposits_wallet:
                foreign = max(0, r.deposits_export - r.deposits_matched - len(r.returns))
                lines.append(
                    f"Verkauf-Seite: {r.sells} Verkauf/Verkäufe laut Export, keine Einzahlung aus den "
                    "Wallets — verkauft wurden "
                    + ("auf der Börse gekaufte oder aus anderer Quelle eingezahlte" if foreign
                       else "auf der Börse gekaufte")
                    + " Bitcoin (FiFo auf der Börse, Rz. 61; in Abschnitt 3 enthalten)."
                )
            elif r.sells:
                lines.append(
                    f"Verkauf-Seite: {r.deposits_wallet} Einzahlung(en) aus den Wallets; davon "
                    f"verkauft {fmt_btc(r.deposits_sold_sats, P)}, auf der Börse verblieben "
                    f"{fmt_btc(r.deposits_held_sats, P)}, zurück ausgezahlt "
                    f"{fmt_btc(r.deposits_back_sats, P)} (FiFo auf der Börse, Rz. 61)."
                    + (
                        f" Verkauft, aber nicht aus den betrachteten Wallets eingezahlt: "
                        f"{fmt_btc(r.unknown_sold_sats, P)} (in den Jahressummen von Abschnitt 3 enthalten)."
                        if r.unknown_sold_sats
                        else ""
                    )
                )
            elif r.deposits_wallet > r.deposits_matched:
                lines.append(
                    f"Verkauf-Seite: Export ohne Verkäufe — {r.deposits_wallet - r.deposits_matched} "
                    "Abfluss/Abflüsse an diese Börse ohne passende Einzahlung im Export gelten als "
                    "Veräußerung am Abflusstag (Annahme)."
                )
            for line in lines:
                para(line, 8, h=4.1)
            if r.matched:
                rows = []
                for w in r.matched:
                    e = w.matched_entry or {}
                    rc = w.recon()
                    buys = sorted({p.acquisition_date for p in w.pieces if p.source})
                    fee_txt = fmt_btc(rc["fee_sats"], P, unit=False) if rc["fee_sats"] else "—"
                    if rc["fee_on_top"] and rc["fee_sats"]:
                        fee_txt += " (zusätzl.)"
                    elif rc["fee_sats"]:
                        fee_txt = "−" + fee_txt
                    rows.append(
                        [
                            fmt_date(w.day.isoformat()),
                            ", ".join(fmt_date(b.isoformat()) for b in buys[:3])
                            + (f" (+{len(buys) - 3})" if len(buys) > 3 else "")
                            + (" + Rest ohne Kaufbeleg" if any(not p.source for p in w.pieces) else ""),
                            fmt_btc(rc["bought_sats"], P, unit=False),
                            fee_txt,
                            ("−" + fmt_btc(rc["rest_sats"], P, unit=False)) if rc["rest_sats"] else "—",
                            fmt_btc(rc["received_sats"], P, unit=False) + (" ✓" if rc["ok"] else " ✗"),
                            fmt_date(str(e.get("time") or "")[:10])
                            + " · "
                            + tx_cell(str(e.get("txid") or "")),
                        ]
                    )
                table(
                    ["Auszahlung lt. Export", "Käufe (Datum)", "Käufe (BTC)", "− Auszahlungs- gebühr", "− Transaktions- kosten", "= Eingang Wallet", "Zufluss · Tx"],
                    rows,
                    [20, 36, 21, 22, 21, 23, 31],
                    ["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
                    size=7.2,
                    head_size=6.8,
                )
                para(
                    "Je Auszahlung gilt: Käufe (Bitcoin vom Börsenkonto, FiFo) − Auszahlungsgebühr "
                    "laut Export − übrige Transaktionskosten = Eingang auf der Wallet laut Blockchain "
                    "(✓). „zusätzl.“ = Gebühr wurde zusätzlich zur ausgezahlten Menge "
                    "vom Börsenkonto abgebucht. Die Gebühren mindern die Menge, nicht den Kaufpreis je "
                    "BTC der ausgezahlten Bitcoin.",
                    7.8,
                    color=MUTED,
                    h=4,
                )
        para(
            "Regeln: Eine Auszahlung laut Export wird einem Zufluss zugeordnet, wenn Börse (Absender "
            "bzw. Zieladresse im Export) übereinstimmt, das Datum höchstens 7 Tage abweicht und die "
            "Menge laut Export dem Zufluss zuzüglich höchstens der Auszahlungsgebühr (max. 0,001 BTC "
            "bzw. 3 %) entspricht; jeder Zufluss und jede Auszahlung wird nur einmal verwendet. "
            "Auf dem Börsenkonto werden Bitcoin nach FiFo entnommen (Rz. 61). Käufe, die direkt an eine "
            "Adresse gesendet wurden (z. B. Relai), gelten als Kauf und Auszahlung am selben Tag. Die "
            "Auszahlungsgebühr der Börse ist nicht Teil der Anschaffungskosten.",
            7.8,
            color=MUTED,
            h=4,
        )
    _keep(c, locals(), ('r', 'rows'))


def render_anhang_b(c: SimpleNamespace) -> None:
    """Annex B: nicht zugeordnete Börsenvorgänge"""
    P = c.P
    para = c.para
    pdf = c.pdf
    rep = c.rep
    section = c.section
    table = c.table
    _v = vars(c)
    if "n_open" in _v:
        n_open = _v["n_open"]
    if rep.open_exchange:
        pdf.add_page(orientation="P")
        section(
            "Anhang B",
            "Nicht zugeordnete Börsenvorgänge",
            "Export-Zeilen ohne vollständige Zuordnung: Auszahlungen und Käufe mit Direktversand ohne "
            "passenden Zufluss oder ohne Kauf im Export davor, Einzahlungen ohne passenden Abfluss aus "
            "den Wallets. Sie verändern Bestand und Bewertung dieses Berichts nicht; die Erläuterung "
            "stammt vom Steuerpflichtigen, sonst „offen“.",
        )
        table(
            ["Börse", "Datum", "Art", "Menge (BTC)", "Status", "Erläuterung"],
            [
                [
                    _name(str(r["exchange"]), P),
                    fmt_date(r["day"].isoformat() if hasattr(r["day"], "isoformat") else str(r["day"])),
                    r["kind"],
                    fmt_btc(int(r["sats"]), P, unit=False),
                    r["status"],
                    r["note"],
                ]
                for r in rep.open_exchange
            ],
            [20, 19, 25, 23, 45, 42],
            ["LEFT", "LEFT", "LEFT", "RIGHT", "LEFT", "LEFT"],
            size=7.3,
            head_size=6.8,
        )
        n_open = sum(1 for r in rep.open_exchange if r["note"] == "offen")
        if n_open:
            para(
                f"{n_open} Vorgang/Vorgänge ohne Erläuterung („offen“).",
                7.8,
                color=MUTED,
                h=4,
            )


def render_anhang_c(c: SimpleNamespace) -> None:
    """Annex C: belegte Käufe/Verkäufe (alle Export-Zeilen, einheitlich)"""
    FONT = c.FONT
    P = c.P
    pdf = c.pdf
    rep = c.rep
    section = c.section
    t = c.t
    table = c.table
    _v = vars(c)
    if "fee" in _v:
        fee = _v["fee"]
    if "rows" in _v:
        rows = _v["rows"]
    if rep.exchange_trades:
        pdf.add_page(orientation="L")
        section(
            "Anhang C",
            "Belegte Käufe und Verkäufe (Börsen-Exporte)",
            "Alle Bitcoin-Zeilen der Konto-Exporte in einheitlicher Form. Diese Belege haben "
            "Vorrang; nur wo sie fehlen, stützt sich der Bericht auf die Blockchain (Abschnitt 5). "
            "„Zuordnung“ zeigt, in welchen Zufluss der Wallets gekaufte Bitcoin ausgezahlt wurden "
            "(Regeln in Anhang A).",
        )
        by_ex: dict[str, list[dict[str, Any]]] = {}
        for tr in rep.exchange_trades:
            by_ex.setdefault(str(tr["exchange"]), []).append(tr)
        for ex, trs in by_ex.items():
            if pdf.get_y() > pdf.page_break_trigger - 25:  # Börsenname nicht allein am Seitenende
                pdf.add_page(orientation="L")
            pdf.set_x(pdf.l_margin)
            pdf.set_font(FONT, "B", 9.5)
            pdf.set_text_color(*INK)
            pdf.cell(pdf.epw, 6, t(_name(ex, P)), new_x="LMARGIN", new_y="NEXT")

            def fee(tr: dict[str, Any]) -> str:
                parts = []
                if tr.get("fee_eur"):
                    parts.append(fmt_eur(tr["fee_eur"], P))
                if tr.get("fee_sats"):
                    parts.append(fmt_btc(int(tr["fee_sats"]), P))
                return " + ".join(parts) or "—"

            rows = [
                [
                    fmt_date(tr["day"]),
                    tr["kind"],
                    fmt_btc(int(tr["sats"]), P, unit=False),
                    fmt_eur(tr["eur"], P) if tr.get("eur") is not None else "—",
                    fee(tr),
                    fmt_eur(tr["price_eur"], P) if tr.get("price_eur") is not None else "—",
                    tr.get("note") or "",
                ]
                for tr in trs
            ]
            for kind in ("Kauf", "Verkauf"):
                sel = [tr for tr in trs if tr["kind"] == kind]
                if sel:
                    rows.append(
                        [
                            f"Summe {kind}",
                            f"{len(sel)}×",
                            fmt_btc(sum(int(tr["sats"]) for tr in sel), P, unit=False),
                            fmt_eur(sum(float(tr["eur"]) for tr in sel if tr.get("eur") is not None), P),
                            fmt_eur(sum(float(tr["fee_eur"]) for tr in sel if tr.get("fee_eur")), P),
                            "",
                            "",
                        ]
                    )
            table(
                ["Datum", "Art", "Menge (BTC)", "Betrag (EUR)", "Gebühr", "Kurs ohne Gebühr (€/BTC)", "Zuordnung"],
                rows,
                [20, 18, 24, 24, 26, 24, 105],
                ["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
                size=7.5,
                head_size=7,
            )


def render_anhang_d(c: SimpleNamespace) -> None:
    """Annex D: Transaktionsverzeichnis (Kurzreferenz → vollständiger Hash)"""
    FA = c.FA
    FONT = c.FONT
    P = c.P
    para = c.para
    pdf = c.pdf
    refs = c.refs
    released = c.released
    rep = c.rep
    section = c.section
    t = c.t
    _v = vars(c)
    if "r" in _v:
        r = _v["r"]
    if "x" in _v:
        x = _v["x"]
    listed = [x for x in refs if not FA or x.txid in released]
    pdf.add_page(orientation="P")
    section(
        TX_ANNEX_NO,
        TX_ANNEX_TITLE,
        (
            "Transaktionen der Veräußerungen (Abflüsse, die als Veräußerung gelten) und der Zuflüsse, aus "
            "denen veräußerte Teilbestände stammen. Die übrigen Transaktionen sind in den Tabellen mit "
            f"„{REQ_MARK}“ gekennzeichnet (T-042{REQ_MARK}) und werden auf Anforderung vollständig vorgelegt "
            "(Rz. 87, 101–104). Verkäufe auf einer Börse ohne Abfluss aus den Wallets haben keine "
            "Transaktion; Beleg ist der Börsen-Export."
            if FA
            else "Alle Transaktionen der betrachteten Wallets"
            + (f" bis {fmt_date(rep.until.isoformat())}" if rep.until is not None else "")
            + ", nummeriert nach Blockzeit. Die Kurzreferenz (T-Nummer) steht in den Tabellen des "
            "Berichts; hier der vollständige Transaktions-Hash zum Nachschlagen in der Blockchain."
        ),
    )
    if listed:
        from fpdf.enums import TableCellFillMode
        from fpdf.fonts import FontFace

        mono = FontFace(family="Courier", size_pt=6.8)
        pdf.set_font(FONT, "", 7.3)
        pdf.set_text_color(*INK)
        pdf.set_draw_color(*RULE)
        with pdf.table(
            col_widths=(18, 18, 24, 18, 96),
            width=pdf.epw,
            text_align=("LEFT", "LEFT", "LEFT", "LEFT", "LEFT"),
            line_height=4.4,
            padding=(1.2, 1.6),
            headings_style=FontFace(emphasis="B", size_pt=6.8, fill_color=HEAD_FILL, color=INK),
            cell_fill_color=ZEBRA,
            cell_fill_mode=TableCellFillMode.ROWS,
            borders_layout="HORIZONTAL_LINES",
            repeat_headings=1,
        ) as tbl:
            r = tbl.row()
            for h in ("Referenz", "Datum", "Wallet", "Art", "Transaktions-ID"):
                r.cell(t(h))
            for x in listed:
                rr = tbl.row()
                rr.cell(x.ref)
                rr.cell(fmt_date(x.day) if x.day else "unbestätigt")
                rr.cell(t(_name(x.wallet, P) or "—"))
                rr.cell(t(x.art))
                # Monospace, eine Zeile, kein Trennzeichen — direkt aus dem PDF kopierbar
                shown = _mask_name(x.txid, P)
                rr.cell(t(shown), style=mono if shown.isascii() else None)
    else:
        para("Keine — es gibt keine Veräußerung aus den Wallets.", 8.5, color=MUTED, h=4.2)
