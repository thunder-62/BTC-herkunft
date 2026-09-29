"""Abschnitt 2 — Zu- und Abflüsse je Wallet."""

from __future__ import annotations

from types import SimpleNamespace

from btc_origin.origin_report import (  # noqa: F401
    ACCENT,
    FLOW_EXIT,
    FLOW_PROVEN,
    FLOW_TRANSFER,
    INK,
    InflowLine,
    MUTED,
    REQ_MARK,
    RULE,
    SATS_PER_BTC,
    UNPROVEN,
    _Blue,
    _draw_wallet_flow,
    _is_transit,
    _mask_name,
    _name,
    _transfer_rows,
    _wallets_by_age,
    bestand_label,
    fmt_btc,
    fmt_date,
    fmt_eur,
    stichtag_in_future,
)
from btc_origin.report_sections import _keep


def render_zufluesse(c: SimpleNamespace) -> None:
    """2 Inflows per wallet"""
    FA = c.FA
    FONT = c.FONT
    P = c.P
    d = c.d
    para = c.para
    pdf = c.pdf
    rep = c.rep
    section = c.section
    t = c.t
    table = c.table
    tx_cell = c.tx_cell
    _v = vars(c)
    if "all_details" in _v:
        all_details = _v["all_details"]
    if "line" in _v:
        line = _v["line"]
    if "rest" in _v:
        rest = _v["rest"]
    if "rows" in _v:
        rows = _v["rows"]
    if "v" in _v:
        v = _v["v"]
    pdf.add_page()
    section(
        "2",
        "Zu- und Abflüsse je Wallet",
        "Jeder Zufluss von einer fremden Adresse in die jeweilige Wallet: Tag des Zuflusses, "
        "Anschaffung, Herkunft (Absender), Transaktion, Menge und Anschaffungswert. Ohne "
        "Börsen-Export gilt der Zufluss als Anschaffung zum Tageskurs (Ersatz aus der "
        "Blockchain). Belegte Käufe haben Vorrang: Liegt der Export der Börse vor (¹), steht unter "
        "dem Zufluss jeder einzelne Kauf (↳) mit Kaufdatum, Menge und Kaufpreis inkl. Gebühren "
        "(Rz. 20, 43, 59)."
        + ("" if FA else " „Heute vorhanden“ = davon noch in einer der betrachteten Wallets.")
        + " Danach "
        "jeder Abfluss an eine fremde Adresse im gleichen Aufbau: Abflusstag, Veräußerung (Verkaufstag "
        "laut Export, sonst „angenommen“ = Abflusstag), Empfänger, Menge, Kurs, Veräußerungspreis und "
        "Gewinn/Verlust wie in Abschnitt 3; die Transaktionsgebühr steht als eigene Zeile. "
        + (
            "Je Wallet folgt die Überleitung mit der Abstimmung gegen die Blockchain. „Tx“ = Kurzreferenz "
            "der Transaktion; den vollständigen Hash nennt das Transaktionsverzeichnis (Anhang D) für jeden "
            "Abfluss, der als Veräußerung gilt, und jeden Zufluss, aus dem ein veräußerter Teilbestand "
            f"stammt; übrige (T-Nummer mit {REQ_MARK}) auf Anforderung."
            if FA
            else "Je Wallet folgt die Überleitung zum Bestand"
            + (f" am {rep.generated_at:%d.%m.%Y}" if stichtag_in_future(rep) else " am Stichtag")
            + ". „Tx“ = Kurzreferenz der Transaktion; vollständige Hashes im Transaktionsverzeichnis (Anhang D)."
        )
        + " Die Wallets stehen in der Reihenfolge "
        "ihres ersten Zuflusses (älteste zuerst). Blau gedruckte Werte = Zufluss ohne Kaufbeleg: "
        "Anschaffung gilt am Zuflusstag zum Tageskurs.",
    )
    cut = rep.stichtag.isoformat()
    by_wallet: dict[str, list[InflowLine]] = {}
    for line in rep.inflows:
        by_wallet.setdefault(line.wallet, []).append(line)
    names = _wallets_by_age(rep, by_wallet)
    for n, name in enumerate(names):
        lines = by_wallet.get(name, [])
        wkey = next((w.key for w in rep.wallets if w.name == name), "")
        if pdf.get_y() > pdf.page_break_trigger - 30:
            pdf.add_page()
        pdf.ln(2 if n else 0)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        n_in = len({x.txid for x in lines})
        transit = _is_transit(rep, name) and not lines
        n_out = len(rep.recon[name].out_lines) if name in rep.recon else 0
        pdf.cell(
            pdf.epw,
            5.5,
            t(
                f"Wallet: „{_name(name, P)}“  ·  "
                + ("Durchgangs-Wallet" if transit else f"{n_in} {'Zufluss' if n_in == 1 else 'Zuflüsse'}")
                + (f"  ·  {n_out} {'Abfluss' if n_out == 1 else 'Abflüsse'}" if n_out else "")
            ),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        if wkey and not FA:
            para(_mask_name(wkey, P), 7, color=MUTED, h=3.6)
            pdf.ln(0.8)
        until = [x for x in lines if x.date <= cut]
        later = [x for x in lines if x.date > cut]
        if lines:
            def value(x: InflowLine) -> float | None:
                return x.price_eur * x.sats / SATS_PER_BTC if x.price_eur is not None else None

            groups: list[list[InflowLine]] = []
            for x in lines:  # one group per Zufluss (tx + day)
                if groups and (groups[-1][0].txid, groups[-1][0].date) == (x.txid, x.date):
                    groups[-1].append(x)
                else:
                    groups.append([x])
            rows = []
            for g in groups:
                x0 = g[0]
                zufluss = fmt_date(x0.date) + (" ²" if x0.date > cut else "")
                tx = tx_cell(x0.txid)
                if len(g) == 1 and not x0.acq_source:
                    v = value(x0)
                    rows.append(
                        [
                            zufluss,
                            fmt_date(x0.acquisition or x0.date),
                            _name(x0.source, P),
                            tx,
                            _Blue(fmt_btc(x0.sats, P, unit=False)),
                            _Blue(fmt_eur(x0.price_eur, P)),
                            _Blue(fmt_eur(v, P)) if v is not None else "Kurs fehlt",
                            fmt_btc(x0.remaining_sats, P, unit=False) if x0.remaining_sats else "—",
                        ]
                    )
                    continue
                # Zufluss aus belegten Käufen: Kopfzeile + je Kauf eine Zeile
                vals = [value(x) for x in g]
                pieces_src = [str(pc["source"]) for pc in (rep.withdrawal_recon.get(x0.txid) or {}).get("pieces", [])]
                n_open = sum(1 for src in pieces_src if not src)
                n_back = sum(1 for src in pieces_src if src and not src.startswith("Kauf"))
                n_buy = (len(pieces_src) - n_back - n_open) if pieces_src else len(g)
                head = f"{n_buy} " + ("Kauf" if n_buy == 1 else "Käufe") + " ¹"
                if n_back:
                    head += f" + {n_back} Rückzahlung" + ("" if n_back == 1 else "en")
                if n_open:
                    head += " + Rest ohne Kaufbeleg"
                rows.append(
                    [
                        zufluss,
                        head,
                        _name(x0.source, P),
                        tx,
                        fmt_btc(sum(x.sats for x in g), P, unit=False),
                        "",
                        fmt_eur(sum(v for v in vals if v is not None), P),
                        fmt_btc(sum(x.remaining_sats for x in g), P, unit=False)
                        if any(x.remaining_sats for x in g)
                        else "—",
                    ]
                )
                rc = rep.withdrawal_recon.get(x0.txid)
                if rc is None:
                    for x, v in zip(g, vals):
                        rows.append(
                            [
                                "",
                                "↳ " + fmt_date(x.acquisition or x.date),
                                _name(x.acq_source.replace(" lt. Export", ""), P) or "—",
                                "",
                                fmt_btc(x.sats, P, unit=False),
                                fmt_eur(x.price_eur, P),
                                fmt_eur(v, P) if v is not None else "Kurs fehlt",
                                fmt_btc(x.remaining_sats, P, unit=False) if x.remaining_sats else "—",
                            ]
                        )
                    continue
                # Belegte Käufe mit Menge laut Export, dann Gebühren → Eingang (exakt).
                paid = 0.0
                for i, pc in enumerate(rc["pieces"]):
                    pv = pc["price_eur"] * pc["sats"] / SATS_PER_BTC if pc.get("price_eur") is not None else None
                    paid += pv or 0.0
                    held = g[i].remaining_sats if i < len(g) and len(g) == len(rc["pieces"]) else None
                    src = str(pc["source"])
                    swap = "Tausch gegen" in src
                    where = _name(src.split(" (")[0].replace(" lt. Export", "").replace("Kauf auf ", ""), P)
                    if "Tageskurs" in src:
                        where += " (Tageskurs)"
                    if "Satoshi-Test" in src:
                        where = "Satoshi-Test ⁶, zurück"
                    if not src:
                        # Auszahlung größer als die Käufe davor: Rest ohne Kaufbeleg
                        rows.append(
                            [
                                "",
                                "↳ ohne Kaufbeleg " + fmt_date(pc["day"]),
                                "im Export kein Kauf davor",
                                "",
                                _Blue(fmt_btc(int(pc["sats"]), P, unit=False)),
                                _Blue(fmt_eur(pc.get("price_eur"), P)),
                                _Blue(fmt_eur(pv, P)) if pv is not None else "Kurs fehlt",
                                (fmt_btc(held, P, unit=False) if held else "—") if held is not None else "",
                            ]
                        )
                        continue
                    rows.append(
                        [
                            "",
                            ("↳ Kauf " if src.startswith("Kauf") else "↳ Rückzahlung, angeschafft ") + fmt_date(pc["day"]),
                            where,
                            "",
                            fmt_btc(int(pc["sats"]), P, unit=False),
                            "Tausch ⁵" if swap and pc.get("price_eur") is None else fmt_eur(pc.get("price_eur"), P),
                            ("nicht bewertet" if swap else "Kurs fehlt") if pv is None else fmt_eur(pv, P),
                            (fmt_btc(held, P, unit=False) if held else "—") if held is not None else "",
                        ]
                    )
                lot_value = sum(v for v in vals if v is not None)
                lost = max(0.0, paid - lot_value)
                fee, rest = int(rc["fee_sats"]), int(rc["rest_sats"])
                deduct = (0 if rc["fee_on_top"] else fee) + max(rest, 0)
                if fee and rc["fee_on_top"]:
                    rows.append(
                        ["", "Gebühr", ("Auszahlungsgebühr lt. Export, zusätzlich belastet", 2), fmt_btc(fee, P), "", "", ""]
                    )
                if fee and not rc["fee_on_top"]:
                    rows.append(
                        [
                            "",
                            "− Gebühr",
                            ("Auszahlungsgebühr lt. Export", 2),
                            "−" + fmt_btc(fee, P, unit=False),
                            "",
                            "−" + fmt_eur(lost * fee / deduct, P) if deduct else "",
                            "",
                        ]
                    )
                if rest:
                    rows.append(
                        [
                            "",
                            "− Kosten",
                            (
                                "Transaktionskosten (nicht einzeln im Export ausgewiesen)"
                                if rest > 0
                                else "Abweichung — prüfen!",
                                2,
                            ),
                            ("−" if rest > 0 else "+") + fmt_btc(abs(rest), P, unit=False),
                            "",
                            "−" + fmt_eur(lost * max(rest, 0) / deduct, P) if deduct and rest > 0 else "",
                            "",
                        ]
                    )
                rows.append(
                    [
                        "",
                        "= Eingang" + (" ✓" if rc["ok"] else " ✗"),
                        ("laut Blockchain", 2),
                        fmt_btc(int(rc["received_sats"]), P, unit=False),
                        "",
                        fmt_eur(lot_value, P),
                        "",
                    ]
                )
            rows.append(
                [
                    ("Zwischensumme", 2),
                    "",
                    "",
                    fmt_btc(sum(x.sats for x in until), P, unit=False),
                    "",
                    fmt_eur(sum(x.price_eur * x.sats / SATS_PER_BTC for x in until if x.price_eur is not None), P),
                    fmt_btc(sum(x.remaining_sats for x in until), P, unit=False),
                ]
            )
            heads = ["Zufluss", "Anschaffung", "Herkunft", "Tx", "Menge (BTC)", "Kurs (€/BTC)", "Anschaffungs- wert", "Heute vorhanden (BTC)"]
            widths = [18, 27, 24, 13, 22, 23, 24, 23]
            if FA:  # ohne „Heute vorhanden“ (Bestand); breitere Tx-Spalte für „auf Anforderung“
                rows = [r if len(r) == 2 else r[:-1] for r in rows]
                heads, widths = heads[:-1], [18, 27, 30, 22, 25, 26, 26]
            table(
                heads,
                rows,
                widths,
                ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"][: len(heads)],
                bold_last=True,
                size=7.3,
                head_size=6.8,
            )
        else:
            para(
                "Nur Umbuchung zwischen eigenen Wallets — keine Anschaffung und keine Veräußerung; "
                "die Anschaffung bleibt die der Herkunfts-Wallet (Haltefrist läuft weiter)."
                if transit
                else "Keine Zuflüsse von fremden Adressen.",
                8,
                color=MUTED,
                h=4,
            )
        r = rep.recon.get(name)
        if r is not None and r.out_lines:
            # Gleicher Aufbau wie die Zuflüsse: Abfluss-Zeile, Gebühr als eigene Zeile,
            # „= Abgang“ laut Blockchain; Bewertung wie Abschnitt 3.
            orows = []
            proceeds_sum = gain_sum = 0.0
            any_sale = False
            for o in r.out_lines:
                ds = [d for d in all_details if d.get("txid") == o["txid"]]
                d_sats = sum(int(d["btc_sats"]) for d in ds)
                share = o["sats"] / d_sats if d_sats else 0.0
                disposal = any(d.get("disposal", True) for d in ds)
                proceeds = sum(float(d["proceeds_eur"]) for d in ds if d.get("proceeds_eur") is not None)
                gain = sum(float(d["gain_eur"]) for d in ds if d.get("gain_eur") is not None)
                has_px = any(d.get("proceeds_eur") is not None for d in ds)
                proceeds, gain = proceeds * share, gain * share
                if disposal and has_px:
                    any_sale = True
                    proceeds_sum += proceeds
                    gain_sum += gain
                if not ds:
                    sale = "—"
                elif not disposal:
                    sale = "keine — Satoshi-Test ⁶" if any("Satoshi-Test" in str(d.get("status")) for d in ds) else "keine"
                elif any(d.get("status") for d in ds):
                    sale = fmt_date(ds[0]["exit_date"])
                else:
                    sale = "angenommen"
                tx = tx_cell(o["txid"])
                orows.append(
                    [
                        fmt_date(o["date"]),
                        sale,
                        _name(str(ds[0]["counterparty"]), P) if ds else "fremde Adresse",
                        tx,
                        fmt_btc(o["sats"], P, unit=False),
                        fmt_eur(proceeds / (o["sats"] / SATS_PER_BTC), P) if disposal and has_px and o["sats"] else "",
                        fmt_eur(proceeds, P) if disposal and has_px else "—",
                        fmt_eur(gain, P, signed=True) if disposal and has_px else "—",
                    ]
                )
                if o["fee"]:
                    orows.append(
                        [
                            "",
                            "+ Gebühr",
                            ("Transaktionsgebühr (Werbungskosten)" if disposal else "Transaktionsgebühr (keine Veräußerung, keine Werbungskosten)", 2),
                            fmt_btc(o["fee"], P, unit=False),
                            "",
                            "",
                            "",
                        ]
                    )
                    orows.append(
                        ["", "= Abgang", ("aus der Wallet laut Blockchain", 2), fmt_btc(o["sats"] + o["fee"], P, unit=False), "", "", ""]
                    )
            orows.append(
                [
                    ("Zwischensumme (Abgang inkl. Gebühren)", 4),
                    fmt_btc(r.ext_out + r.fees_out, P, unit=False),
                    "",
                    fmt_eur(proceeds_sum, P) if any_sale else "—",
                    fmt_eur(gain_sum, P, signed=True) if any_sale else "—",
                ]
            )
            table(
                ["Abfluss", "Veräußerung", "Empfänger", "Tx", "Menge (BTC)", "Kurs (€/BTC)", "Veräußerungs- preis", "Gewinn / Verlust"],
                orows,
                [18, 27, 24, 13, 22, 23, 24, 23],
                ["LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
                bold_last=True,
                size=7.3,
                head_size=6.8,
            )
        if r is not None:
            target = (rep.stichtag_balances or {}).get(name, r.balance)
            ok = r.computed == r.balance == target
            fees_trans = r.fees - r.fees_out
            rows = [
                ["Zwischensumme Zuflüsse von fremden Adressen", fmt_btc(r.ext_in, P, unit=False)],
                *_transfer_rows(r, P),
                *(
                    [["− Zwischensumme Abflüsse an fremde Adressen (inkl. Gebühren)", fmt_btc(r.ext_out + r.fees_out, P, unit=False)]]
                    if r.ext_out
                    else []
                ),
                *([["− Transaktionsgebühren der Umbuchungen", fmt_btc(fees_trans, P, unit=False)]] if fees_trans else []),
                [
                    ("= " + bestand_label(rep, note=False) if stichtag_in_future(rep) else f"= Bestand am {fmt_date(cut)}")
                    + (" ✓" if ok else " — Abweichung!"),
                    fmt_btc(r.computed, P, unit=False),
                ]
                if not FA
                # Finanzamt-Fassung: statt des Bestands nur das Ergebnis der Abstimmung
                else [
                    "✓ stimmt mit der Blockchain überein" if ok else "✗ Abweichung zur Blockchain — prüfen",
                    "",
                ],
            ]
            table(
                ["Überleitung (Abstimmung mit der Blockchain)" if FA
                 else "Überleitung zum Bestand" + ("" if stichtag_in_future(rep) else " am Stichtag"), "BTC"],
                rows,
                [130, 44],
                ["LEFT", "RIGHT"],
                bold_last=True,
                size=7.5,
                head_size=7,
            )
            if not ok:
                para(
                    "Achtung: Überleitung weicht vom Blockchain-Bestand ab." if FA else
                    f"Achtung: Überleitung ergibt {fmt_btc(r.computed, P)}, Blockchain-Bestand "
                    f"{fmt_btc(target, P)}.",
                    8.5,
                    bold=True,
                    color=(170, 40, 40),
                )
        if later:
            para(f"² Zufluss nach dem Stichtag {fmt_date(cut)} — nicht in Zwischensumme und Bestand.", 7.8, color=MUTED, h=4)
    para(
        "Umbuchungen = Saldo je Transaktion zwischen den betrachteten Wallets (Wechselgeld an "
        "dieselbe Wallet zählt nicht); Abflüsse und Gebühren je Wallet anteilig nach den "
        "ausgegebenen Einzelbeträgen. Summe über alle Wallets: Umbuchungen heben sich auf — siehe "
        "Mengenabstimmung in Abschnitt 1."
        + (
            " ¹ Belegte Käufe (↳ je Kauf) laut Export der Börse (Rz. 20, 43, 59); Aufstellung aller "
            "Export-Zeilen in Anhang C; "
            "Zuordnung der Auszahlung zum Zufluss siehe Anhang A."
            if any(x.acq_source for x in rep.inflows)
            else ""
        ),
        7.8,
        color=MUTED,
        h=4,
    )
    if rep.recon:  # Finanzamt-Fassung: nur die Struktur, ohne Mengen und Bestände
        pdf.add_page()
        pdf.start_section(t("2.1  Übersicht: Fluss zwischen den Wallets"), level=1)
        para(
            "Übersicht: Fluss zwischen den Wallets bis "
            + (f"{rep.generated_at:%d.%m.%Y}" if stichtag_in_future(rep) else "zum Stichtag"),
            10,
            bold=True,
            color=ACCENT,
        )
        para(
            "Die Flüsse der Überleitungen oben als Bild, ohne Zahlen: Jeder Kreis ist eine Wallet, "
            "angeordnet nach Alter im Uhrzeigersinn ab links oben. Links kommen die Zuflüsse von fremden "
            "Adressen herein, rechts gehen die Abflüsse an fremde Adressen hinaus; Pfeil = Richtung. "
            "Linienstärke = Menge, Kreisgröße = Durchfluss der Wallet (alles, was hineinkam) — nur als "
            "Größenordnung; die Mengen stehen in den Tabellen oben."
            if FA
            else "Dieselben Zahlen wie die Überleitungen oben, als Bild: Jeder Kreis ist eine Wallet "
            f"(Größe = {bestand_label(rep, note=False)}), angeordnet nach Alter im Uhrzeigersinn ab links oben. "
            "Links kommen die Zuflüsse von fremden Adressen herein, rechts gehen die Abflüsse an "
            "fremde Adressen hinaus. Linienstärke = BTC-Menge, Zahl an der Linie = Menge in BTC, "
            "Pfeil = Richtung. Transaktionsgebühren sind nicht eingezeichnet.",
            8,
            color=MUTED,
            h=4,
        )
        _draw_wallet_flow(pdf, rep, names, t, FONT, structure_only=FA)
        legend = [
            (FLOW_PROVEN, True, "Zufluss mit Kaufbeleg (Börsen-Export)"),
            (UNPROVEN, True, "Zufluss ohne Kaufbeleg"),
            (FLOW_TRANSFER, False, "Umbuchung zwischen den Wallets"),
            (FLOW_EXIT, False, "Abfluss an fremde Adressen"),
        ]
        if pdf.get_y() + 10 > pdf.page_break_trigger:  # Legende zusammen auf eine Seite
            pdf.add_page()
        y = pdf.get_y()
        for i, (col, dashed, text) in enumerate(legend):
            x = pdf.l_margin + (i % 2) * pdf.epw / 2
            if i and i % 2 == 0:
                y += 4.6
            pdf.set_draw_color(*col)
            pdf.set_line_width(1.0)
            if dashed:
                pdf.set_dash_pattern(dash=2.2, gap=1.0)
            pdf.line(x, y + 1.7, x + 8, y + 1.7)
            pdf.set_dash_pattern()
            pdf.set_xy(x + 9.5, y)
            pdf.set_font(FONT, "", 7)
            pdf.set_text_color(*INK)
            pdf.cell(0, 3.4, t(text))
        pdf.set_line_width(0.2)
        pdf.set_draw_color(*RULE)
        pdf.set_y(y + 6)
    _keep(c, locals(), ('ds', 'fee', 'g', 'head', 'heads', 'line', 'lines', 'n', 'n_open', 'proceeds', 'r', 'rc', 'rows', 'src', 'text', 'vals', 'x', 'y'))
