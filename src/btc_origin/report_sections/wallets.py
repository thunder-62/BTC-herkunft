"""Abschnitt 1 — betrachtete Wallets."""

from __future__ import annotations

from types import SimpleNamespace

from btc_origin.origin_report import (  # noqa: F401
    INK,
    InflowLine,
    MUTED,
    ON_REQUEST,
    SATS_PER_BTC,
    _mask_name,
    _name,
    _wallets_by_age,
    bestand_label,
    deviations,
    fmt_btc,
    fmt_date,
    fmt_eur,
    stichtag_in_future,
)
from btc_origin.report_sections import _keep


def render_wallets(c: SimpleNamespace) -> None:
    """1 Wallets"""
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
    section(
        "1",
        "Betrachtete Wallets",
        "Ausschließlich die vom Steuerpflichtigen angegebenen öffentlichen Schlüssel (xpub) "
        "bzw. Adressen — zur Prüfung der Angaben dieses Berichts (Rz. 87). Keine privaten "
        "Schlüssel. Weitere Wallets sind nicht Teil dieser Analyse."
        + (f" Aufgeführt sind die Wallets mit Vorgängen bis {fmt_date(rep.until.isoformat())}."
           if rep.until is not None else "")
        + (" In dieser Fassung sind Schlüssel und Adressen nicht abgedruckt; sie werden auf "
           "Anforderung vorgelegt." if FA else ""),
    )
    any_address = any(w.kind != "xpub" for w in rep.wallets)
    at = rep.stichtag_balances
    bal = {w.name: (at.get(w.name, 0) if at is not None else w.balance_sats) for w in rep.wallets}
    total_at = sum(bal.values())
    stichtag_s = fmt_date(rep.stichtag.isoformat())
    # gleiche Reihenfolge wie Abschnitt 2: nach erstem Zufluss, älteste zuerst
    inflows_by_wallet: dict[str, list[InflowLine]] = {}
    for line in rep.inflows:
        inflows_by_wallet.setdefault(line.wallet, []).append(line)
    by_name = {w.name: w for w in rep.wallets}
    wallets_sorted = [by_name[n] for n in _wallets_by_age(rep, inflows_by_wallet) if n in by_name]
    rows = [
        [
            _name(w.name, P),
            ON_REQUEST if FA else _mask_name(w.key or "—", P),
            f"{fmt_date(w.first)} – {fmt_date(w.last)}" if w.first else "keine Transaktionen",
            str(w.tx_count),
        ]
        for w in wallets_sorted
    ]
    table(
        ["Wallet", "xpub / Adresse" if any_address else "xpub", "Aktivität", "Tx"],
        rows,
        [30, 88, 42, 14],
        ["LEFT", "LEFT", "LEFT", "RIGHT"],
        size=7.5,
    )

    # Bestand zum Stichtag (Rz. 104) — only wallets holding coins that day; nicht in der
    # Finanzamt-Fassung (keine Bestände).
    holding = [w for w in wallets_sorted if bal[w.name] > 0]
    px = rep.stichtag_price

    def worth(sats: int) -> str:
        return fmt_eur(px * sats / SATS_PER_BTC, P) if px is not None else "Kurs fehlt"

    if not FA:
        pdf.ln(1)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5.5, t(bestand_label(rep)), align="L")

        if holding:
            rows = [[_name(w.name, P), fmt_btc(bal[w.name], P, unit=False), worth(bal[w.name])] for w in holding]
            rows.append(["Summe", fmt_btc(total_at, P, unit=False), worth(total_at)])
            table(
                [
                    "Wallet",
                    "Bestand (BTC)",
                    "Wert (Tageskurs Stichtag)"
                    if rep.stichtag_price_day in (None, rep.stichtag)
                    else f"Wert (Tageskurs {fmt_date(rep.stichtag_price_day.isoformat())})",
                ],
                rows,
                [80, 47, 47],
                ["LEFT", "RIGHT", "RIGHT"],
                bold_last=True,
                size=7.5,
            )
        empty = [w for w in wallets_sorted if bal[w.name] <= 0]
        bal_day = (
            f"am {rep.generated_at:%d.%m.%Y}" if stichtag_in_future(rep) else f"am Stichtag {stichtag_s}"
        )
        if not holding:
            rest = f"Alle betrachteten Wallets haben {bal_day} einen Bestand von 0 BTC. "
        elif empty:
            rest = (
                f"Die übrigen {len(empty)} Wallet(s) ("
                + ", ".join(_name(w.name, P) for w in empty)
                + f") haben {bal_day} einen Bestand von 0 BTC. "
            )
        else:
            rest = ""
        para(
            rest
            + (
                f"Bestand = alle Zu- und Abgänge der Wallet bis {rep.generated_at:%d.%m.%Y}; bis zum Stichtag "
                "ohne weitere Bewegungen fortgeschrieben (Rz. 104)."
                if stichtag_in_future(rep)
                else "Bestand = alle Zu- und Abgänge der Wallet bis Tagesende (UTC) des Stichtags (Rz. 104)."
            ),
            7.8,
            color=MUTED,
            h=4,
        )

    # Mengenabstimmung — still part of section 1
    all_details = [d for y in rep.years for d in y.details]
    details = [d for d in all_details if d.get("origin", "wallet") == "wallet"]
    in_sum = sum(x.sats for x in rep.inflows) if rep.inflows else rep.inflow_sats
    out_sum = sum(int(d["btc_sats"]) for d in details) if details else rep.outflow_sats
    lot_sum = sum(x.sats for x in rep.lots)
    if rep.disposal_fees_sats is None or rep.transfer_fees_sats is None:
        fee_disp, fee_trans = max(0, in_sum - out_sum - lot_sum), 0
    else:
        fee_disp, fee_trans = rep.disposal_fees_sats, rep.transfer_fees_sats
    # Abflüsse und ihre Gebühren je Teilbestand — must add up to the chain's.
    fee_disp_annex = sum(int(d.get("fee_sats") or 0) for d in details) if details else fee_disp
    chain = rep.chain_balance_sats if rep.chain_balance_sats is not None else rep.balance_sats
    computed = in_sum - out_sum - fee_disp - fee_trans
    exact = (
        computed == lot_sum == chain == rep.balance_sats
        and fee_disp_annex == fee_disp
        and rep.consistent
    )
    pdf.ln(1)
    pdf.set_x(pdf.l_margin)
    pdf.set_font(FONT, "B", 9.5)
    pdf.set_text_color(*INK)
    pdf.cell(pdf.epw, 5.5, t("Mengenabstimmung"), new_x="LMARGIN", new_y="NEXT")
    rows = [
        ["Zuflüsse von fremden Adressen", "Summe Abschnitt 2", fmt_btc(in_sum, P, unit=False)],
        ["− Abflüsse an fremde Adressen", "Summe Abschnitt 2", fmt_btc(out_sum, P, unit=False)],
        ["− Transaktionsgebühren der Abflüsse", "Summe Abschnitt 2", fmt_btc(fee_disp, P, unit=False)],
        ["− Transaktionsgebühren der Umbuchungen", "zwischen den Wallets", fmt_btc(fee_trans, P, unit=False)],
        [
            f"= Bestand am {(rep.until or rep.generated_at):%d.%m.%Y}",
            "Summe der Teilbestände",
            fmt_btc(lot_sum, P, unit=False),
        ],
        [
            "Kontrolle: unverbrauchte Einzelbeträge (UTXO)" + (" ✓" if exact else " ✗"),
            "laut Blockchain" + (" — stimmt überein" if exact else " — Abweichung"),
            fmt_btc(chain, P, unit=False),
        ],
    ]
    if FA:  # alle Positionen, aber keine Mengen — das Ergebnis (✓) bleibt
        rows = [r[:2] + [ON_REQUEST] for r in rows]
    table(
        ["Position", "Nachweis", "BTC"],
        rows,
        [80, 50, 44],
        ["LEFT", "LEFT", "RIGHT"],
        bold_last=True,
        size=8,
    )
    if exact:
        # Absatz ohne Sonderzeichen am Anfang: Ersatzschriften (✓) zerlegen sonst die Folgezeile
        para(
            "Die Rechnung geht auf: Zuflüsse − Abflüsse − Transaktionsgebühren = Bestand = unverbrauchte "
            "Einzelbeträge laut Blockchain. Umbuchungen zwischen den Wallets sind weder Zu- noch Abfluss; "
            "nur ihre Gebühren mindern den Bestand.",
            8,
            color=MUTED,
            h=4,
        )
        devs = deviations(rep)
        if devs:
            para(
                f"Einzelprüfungen: {len(devs)} Abweichung{'en' if len(devs) > 1 else ''} ✗ in Abschnitt 2 "
                f"({'; '.join(devs)}) — die Gesamtmenge stimmt, die Zuordnung dort ist zu prüfen.",
                8.5,
                bold=True,
                color=(170, 40, 40),
            )
    else:
        para(
            "Achtung: Mengenabstimmung geht nicht auf"
            + ("" if FA else f" — Differenz {fmt_btc(computed - lot_sum, P)} bzw. Blockchain {fmt_btc(chain - lot_sum, P)}")
            + ". Zahlen nicht verwenden.",
            9,
            bold=True,
            color=(170, 40, 40),
        )
    _keep(c, locals(), ('all_details', 'details', 'line', 'rest', 'rows'))
