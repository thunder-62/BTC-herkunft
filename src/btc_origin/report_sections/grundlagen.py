"""Abschnitte 6 und 7 — Rechtsgrundlagen und Methodik."""

from __future__ import annotations

import re
from types import SimpleNamespace

from btc_origin.origin_report import (  # noqa: F401
    rule_years,
    INK,
    MUTED,
    fmt_date,
)
from btc_origin.regelwerk import methodik_vermerk, rules_for, rules_used
from btc_origin.report_sections import _keep


def _legal_sources(c: SimpleNamespace) -> list[tuple[str, str]]:
    """Rechtsgrundlagen aus dem Regelwerk des Berichtsjahres (Stichtag): texte der Regeldatei,
    danach das BMF-Schreiben aus quelle. Umfasst der Bericht Jahre mit anderer Freigrenze,
    nennt der Freigrenzen-Baustein alle verwendeten Werte („1.000 € (VZ 2022–2023: 600 €)“)."""
    rw = rules_for(c.rep.stichtag.year)
    used = rules_used(rule_years(c.rep))
    out = []
    for x in rw.texte:
        text = x.text
        for marker, attr in (("§ 23 Abs. 3 Satz 5", "freigrenze_23_eur"), ("§ 22 Nr. 3", "freigrenze_22_3_eur")):
            if marker in x.titel:
                text = with_other_years(text, getattr(rw, attr), {r.veranlagungsjahr: getattr(r, attr) for r in used})
        out.append((x.titel, text))
    return out + [("Verwaltungsauffassung", rw.bmf_quelle)]


def _eur(v: int) -> str:
    return f"{v:,}".replace(",", ".") + " €"


def with_other_years(text: str, value: int, by_year: dict[int, int]) -> str:
    """Ersten Betrag im Text durch „1.000 € (VZ 2022–2023: 600 €)“ ersetzen, wenn Jahre des
    Berichts einen anderen Wert haben; zusammenhängende Jahre als Spanne."""
    other = sorted(y for y, v in by_year.items() if v != value)
    if not other:
        return text
    groups: list[tuple[int, int, int]] = []  # (von, bis, Wert)
    for y in other:
        if groups and groups[-1][1] == y - 1 and groups[-1][2] == by_year[y]:
            groups[-1] = (groups[-1][0], y, by_year[y])
        else:
            groups.append((y, y, by_year[y]))
    note = "; ".join(f"VZ {a}{'' if a == b else f'–{b}'}: {_eur(v)}" for a, b, v in groups)
    return re.sub(r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:€|EUR)", lambda m: f"{_eur(value)} ({note})", text, count=1)


def render_rechtsgrundlagen(c: SimpleNamespace) -> None:
    """6 Sources of law"""
    FA = c.FA
    FONT = c.FONT
    para = c.para
    pdf = c.pdf
    section = c.section
    t = c.t
    _v = vars(c)
    if "text" in _v:
        text = _v["text"]
    section(
        "6",
        "Rechtsgrundlagen und Quellen",
        "Die Aufstellung folgt den nachstehenden Vorschriften und der Verwaltungsauffassung.",
    )
    for ref, text in _legal_sources(c):
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 8.3)
        pdf.set_text_color(*INK)
        pdf.multi_cell(pdf.epw, 4.3, t(ref), align="L")
        para(text, 8.1, color=MUTED, h=4.1)
        pdf.ln(1.2)
    if not FA:  # Ausblick Reform / Altbestand nicht in der Finanzamt-Fassung
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 8.3)
        pdf.set_text_color(*INK)
        pdf.multi_cell(pdf.epw, 4.3, t("Ausblick: Kryptosteuer-Reform (kein geltendes Recht)"), align="L")
        para(
            rules_for(c.rep.stichtag.year).reform_ausblick + " Diese Aufstellung wendet ausschließlich geltendes Recht an; Abschnitt 8.2 "
            "und das separate Dokument „Nachweis Altbestand Bitcoin“ kennzeichnen lediglich, welche "
            "Teilbestände Altbestand wären.",
            8.1,
            color=MUTED,
            h=4.1,
        )
        pdf.ln(1.2)


def render_methodik(c: SimpleNamespace) -> None:
    """7 Method"""
    FA = c.FA
    declared_rules_text = c.declared_rules_text
    para = c.para
    rep = c.rep
    section = c.section
    _v = vars(c)
    if "line" in _v:
        line = _v["line"]
    if "n" in _v:
        n = _v["n"]
    if "src" in _v:
        src = _v["src"]
    if "x" in _v:
        x = _v["x"]
    section("7", "Methodik und Annahmen")
    for line in (
        *([rep.geltungsbereich] if rep.geltungsbereich else []),
        "Datenquelle: öffentliche Bitcoin-Blockchain (Electrum-Server), nur für die angegebenen "
        "öffentlichen Schlüssel (xpub) bzw. Adressen. Es wurden keine privaten Schlüssel "
        "verwendet. Transaktionen sind mit Kurzreferenzen (T-Nummern) bezeichnet; die vollständigen "
        "Transaktions-Hashes stehen im Transaktionsverzeichnis im Anhang"
        + (" (Veräußerungen und ihre Herkunft), übrige auf Anforderung" if FA else "")
        + " (Rz. 104)."
        + (
            f" Berücksichtigt sind alle Vorgänge bis {fmt_date(rep.until.isoformat())} (Tagesende UTC); "
            "spätere Käufe, Umbuchungen und Verkäufe sind nicht enthalten, Abstimmung und Überleitungen "
            "erfolgen zu diesem Tag."
            if rep.until is not None
            else ""
        ),
        "Verwendungsreihenfolge: Einzelbetrachtung (Rz. 61). Für jeden ausgegebenen Einzelbetrag "
        "(UTXO) ist aus der Blockchain bekannt, welcher Zufluss ihn begründet hat; Wechselgeld "
        "führt die ursprünglichen Anschaffungsdaten fort (Rz. 56). Nur wo mehrere Einzelbeträge in einer "
        "Transaktion zusammengeführt werden, gilt innerhalb dieser Transaktion FiFo. Die "
        "Methode wird für alle Wallets und Jahre einheitlich angewendet (Rz. 62, 103).",
        "Umbuchungen zwischen den betrachteten Wallets sind keine Veräußerung, da nicht auf "
        "Dritte übertragen wird (Rz. 54); das Anschaffungsdatum bleibt erhalten.",
        "Transaktionsgebühren einer Veräußerung sind Werbungskosten (Rz. 59). Umfasst eine "
        "Transaktion Teilbestände unter und über einem Jahr Haltedauer, wird die Gebühr nach "
        "Menge auf die Teilbestände aufgeteilt (Rz. 57). Gebühren reiner Umbuchungen bleiben unberücksichtigt.",
        f"Kurse nach fester, dokumentierter Regel (Rz. 91): {rep.price_source}. Dieselbe Regel "
        "für Anschaffungskosten und Veräußerungspreise; Wochenende/Feiertag: letzter "
        "veröffentlichter EZB-Kurs. Referenzwerte, keine tatsächlichen Kauf- oder Verkaufspreise"
        + (
            " (Ausnahme: Erlöse laut Börsen-Export)."
            if rep.exchange_exports
            else "."
        )
        + (
            " Verwendet: "
            + "; ".join(f"{src} {n} Tage" for src, n in sorted(rep.price_source_counts.items(), key=lambda x: -x[1]))
            + "."
            if rep.price_source_counts
            else ""
        ),
        "Zuflüsse von einer Börse, deren Export die Auszahlung enthält: Anschaffung = Kaufdatum "
        "und Kaufpreis inkl. Gebühren laut Export (Rz. 20, 43, 59); sonst gilt der Tag des "
        "Zuflusses in die Wallets mit Tageskurs. "
        "Abflüsse an eine Börse, deren Export Verkäufe enthält: Verkaufstag, Erlös und "
        "Börsengebühr laut Export (Rz. 20, 55; ohne Erlös im Export Tageskurs des Verkaufstags); eingezahlte und auf der Börse gekaufte Bitcoin "
        "werden dort nach FiFo verkauft (Rz. 61), da sie auf dem Börsenkonto nicht einzeln "
        "unterscheidbar sind. Abflüsse an eine Börse, deren Export die Einzahlung enthält: Umbuchung "
        "auf das eigene Börsenkonto (Rz. 54), keine Veräußerung; kleine Einzahlungen (≤ 0,001 BTC, ab "
        "30.12.2024), die binnen 7 Tagen mit einer mindestens zehnmal größeren Auszahlung zurückgehen, "
        "sind als Satoshi-Test gekennzeichnet (Nachweis der Wallet-Inhaberschaft). Alle übrigen Abflüsse an fremde "
        "Adressen gelten als Veräußerung am Abflusstag (Annahme).",
        "Zuflüsse ohne Kaufbeleg gelten als Anschaffung am Zuflusstag zum Tageskurs. Ob ein Zufluss "
        "selbst eine Einkunft ist (z. B. Bonus, Prämie oder Empfehlungsprämie, § 22 Nr. 3 EStG), "
        "prüft dieser Bericht nicht; er erfasst nur private Veräußerungsgeschäfte (§ 23 EStG).",
        "Namen der Gegenstellen: ohne Kennzeichen aus einem Börsen-Export (Transaktion oder Adresse "
        "laut Export) oder einer öffentlichen Adressliste; mit Fußnote „Angabe des Steuerpflichtigen“ "
        "aus einer eigenen Benennung"
        + (f" oder Datumsregel ({declared_rules_text})" if declared_rules_text else "")
        + " — nicht durch Label oder Export belegt. Dieselbe Plattform kann daher unter zwei Namen "
        "erscheinen (z. B. belegt und als eigene Angabe); die Erläuterungen ordnen sie zu. Bündelung "
        "von Absenderadressen ist heuristisch.",
        methodik_vermerk({y.year for y in rep.years if y.details} | {rep.stichtag.year})
        + " Die Werte stammen aus den Regeldateien regeln/<Jahr>.yaml; fehlt die Datei eines "
        "benötigten Jahres, wird kein Bericht erstellt.",
        "Diese Aufstellung ist eine Arbeitsgrundlage und keine Steuerberatung.",
    ):
        para(f"• {line}", 8.3, h=4.3)
    _keep(c, locals(), ('line',))
