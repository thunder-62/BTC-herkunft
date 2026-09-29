"""Jahressteuerreport als PDF (Seite 1, Abschnitte 1–6), Jahresauswahl und Anschluss an den
Herkunftsnachweis — synthetische Daten."""

from __future__ import annotations

import re
import io

import pytest
from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.merger import Ledger
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow

OWN = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
DEST = "3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"
BUY, SELL, KEEP, LATE = "1a" * 32, "2b" * 32, "3c" * 32, "4d" * 32


def _text(data: bytes) -> str:
    pymupdf = pytest.importorskip("pymupdf")
    return " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf")).split())


@pytest.fixture()
def client():
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(historical_fetcher=lambda on: (None, 30_000.0), spot_fetcher=lambda: (None, None))
        state.db.execute("INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (1,'Wallet A',NULL,'address','2024-01-01')")
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)", (OWN,))
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,'bc1qchange',1)")
        from btc_origin.wallet_registry import WalletEntry

        state.registry._wallets = [WalletEntry(id=1, name="Wallet A", kind="address", address=OWN)]
        persist_ledger(state.db, Ledger(flows=[
            Flow(BUY, OWN, "in", 10_000_000, wallet_id=1, block_time="2022-01-14T10:05:00Z", vout=0,
                 tx_total_output_sats=10_000_000),
            Flow(SELL, OWN, "out", 10_000_000, wallet_id=1, block_time="2022-06-05T10:05:00Z", vin_index=0,
                 tx_total_output_sats=9_990_000),
            Flow(SELL, "bc1qchange", "in", 3_990_000, wallet_id=1, block_time="2022-06-05T10:05:00Z", vout=1,
                 tx_total_output_sats=9_990_000),
            Flow(KEEP, OWN, "in", 2_000_000, wallet_id=1, block_time="2025-03-01T10:05:00Z", vout=0,
                 tx_total_output_sats=2_000_000),
            Flow(LATE, OWN, "in", 1_000_000, wallet_id=1, block_time="2026-02-01T10:05:00Z", vout=0,
                 tx_total_output_sats=1_000_000),
        ]))
        state.last_tx_io = {SELL: {"output_addresses": [DEST, "bc1qchange"]}}
        client.post("/api/enrich")
        yield client


def test_year_list_with_rules_and_default(client) -> None:
    data = client.get("/api/report/steuer/jahre").json()
    assert [(e["jahr"], e["regelwerk"]) for e in data["jahre"]] == [(2022, True), (2025, True), (2026, True)]
    assert data["vorgabe"] == 2025  # das vergangene Jahr (heute 2026)


def test_internal_tax_report_2022(client) -> None:
    r = client.get("/api/report/steuer.pdf", params={"jahr": 2022})
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'inline; filename="steuerreport-bitcoin-2022-intern.pdf"'
    text = _text(r.content)
    assert "Jahressteuerreport Bitcoin 2022" in text and "Interne Fassung" in text
    assert "Regelwerk VZ 2022" in text
    # Seite 1: § 23 mit Freigrenze 600 € (VZ 2022), § 22 Nr. 3 ohne Einkünfte
    assert "innerhalb der Haltefrist (steuerbar)" in text and "Freigrenze 600 €" in text
    assert "Weitere private Veräußerungsgeschäfte (nicht in diesem Report) keine angegeben" in text
    assert "Keine Zuflüsse als Einkunft nach § 22 Nr. 3 EStG eingeordnet" in text
    assert "Die Einordnung ist vom Steuerpflichtigen zu prüfen; der Report ermittelt nur die Werte." in text
    # Abschnitte 1–6
    for head in ("1 Grundlagen und Mengenabstimmung", "2 Veräußerungen 2022", "3 Anschaffungen 2022",
                 "4 Sonstige Bewegungen", "5 Offene Punkte des Jahres", "6 Methodik (kurz)"):
        assert head in text, head
    assert "wird auf Anforderung vorgelegt." in text  # Standard: Herkunftsnachweis auf Anforderung
    assert text.startswith("Jahressteuerreport Bitcoin 2022 Anlage zur Einkommensteuererklärung 2022")
    assert "stimmt mit der Blockchain überein" in text and "0,10000000" in text  # Zuflüsse 2022
    assert "angenommen — Abfluss an eine fremde Adresse ohne Verkaufsbeleg" in text
    assert "Zuflusstag, Tageskurs; Zufluss T-001" in text  # Teilbestand aus dem Jahr
    assert "Haltefrist endet am" in text  # nur interne Fassung
    assert "Geltungsbereich — unterstützt" in text and "keine Steuerberatung" in text
    assert "ELSTER" not in text and not re.search(r"Zeile \d", text)  # keine Formular-Zeilen
    # nichts aus anderen Jahren außer den Anschaffungen veräußerter Teilbestände
    assert "01.03.2025" not in text and "01.02.2026" not in text


def test_finanzamt_version_hides_holdings(client) -> None:
    r = client.get("/api/report/steuer.pdf", params={"jahr": 2022, "fassung": "finanzamt"})
    assert r.headers["content-disposition"].endswith('steuerreport-bitcoin-2022-finanzamt.pdf"')
    text = _text(r.content)
    assert "Finanzamt-Fassung" in text and "auf Anforderung" in text
    assert "✓ stimmt mit der Blockchain überein" in text
    assert "Haltefrist endet am" not in text
    assert OWN not in text and DEST not in text


def test_link_to_herkunftsnachweis_by_year(client) -> None:
    """Abschnitt 1.1 (Vorgabe der steuerlichen Prüfung): Standard „Herkunftsnachweis auf
    Anforderung“ ohne Warnung; ein bestimmter, in dieser Sitzung erzeugter Nachweis mit Stand,
    Stichtag und Build; „kein Herkunftsnachweis“ nur ausdrücklich, dann mit Warnung."""
    from btc_origin.tax_report import AUF_ANFORDERUNG

    text = _text(client.get("/api/report/steuer.pdf", params={"jahr": 2025}).content)
    assert " ".join(AUF_ANFORDERUNG.split()) in text and "Stichtag 31.12.2024" not in text
    res = client.get("/api/report/steuer-pruefung", params={"jahr": 2025}).json()
    assert res["ergebnis"] != "warnung" or res["warnungen"] == 0
    # nicht erzeugt → 422
    assert client.get("/api/report/steuer.pdf", params={"jahr": 2025, "anschluss_jahr": 2024}).status_code == 422
    assert client.get("/api/report/steuer/jahre").json()["herkunft"] == {}
    assert client.get("/api/report/herkunft.pdf", params={"stichtag": "2024-12-31", "bis": "2024-12-31"}).status_code == 200
    herkunft = client.get("/api/report/steuer/jahre").json()["herkunft"]
    assert list(herkunft) == ["2024"] and herkunft["2024"]["stichtag"] == "31.12.2024"
    text = _text(client.get("/api/report/steuer.pdf", params={"jahr": 2025, "anschluss_jahr": 2024}).content)
    assert f"Anschluss an Herkunftsnachweis Stand {herkunft['2024']['stand']}, Stichtag 31.12.2024" in text
    assert "T-Nummern identisch" in text
    # ausdrücklich kein Herkunftsnachweis
    text = _text(client.get("/api/report/steuer.pdf", params={"jahr": 2025, "anschluss_jahr": 0}).content)
    assert "Kein Herkunftsnachweis angegeben." in text


def test_missing_rules_refuse_the_report(client, tmp_path, monkeypatch) -> None:
    import os
    import shutil

    root = tmp_path / "btc-regeln"
    shutil.copytree(os.environ["BTC_ORIGIN_RULES_DIR"], root)
    (root / "regeln" / "2022.yaml").unlink()
    monkeypatch.setenv("BTC_ORIGIN_RULES_DIR", str(root))
    r = client.get("/api/report/steuer.pdf", params={"jahr": 2022})
    assert r.status_code == 422 and "regeln/2022.yaml" in r.json()["detail"]
    years = client.get("/api/report/steuer/jahre").json()["jahre"]
    assert years[0]["jahr"] == 2022 and years[0]["regelwerk"] is False


def test_annexes_and_released_hashes(client) -> None:
    intern = _text(client.get("/api/report/steuer.pdf", params={"jahr": 2022}).content)
    assert "Anhang A Abgleich mit Börsen-Exporten 2022" in intern and "Anhang B Export-Zeilen 2022" in intern
    assert "Anhang C Transaktionsverzeichnis" in intern
    assert BUY in intern and SELL in intern and KEEP not in intern  # nur verwendete T-Nummern
    fa = _text(client.get("/api/report/steuer.pdf", params={"jahr": 2025, "fassung": "finanzamt"}).content)
    # 2025: nur eine Anschaffung, keine Veräußerung → nur als Anzahl, keine T-Nummer, Anhang C leer
    assert KEEP not in fa and "T-003" not in fa
    assert "Im Jahr 2025 gab es 1 Zufluss von fremden Adressen (Anschaffungen); die vollständige " \
           "Aufstellung mit Belegen wird auf Anforderung vorgelegt." in fa
    assert fa.split("Anhang C Transaktionsverzeichnis", 1)[1].split("Referenz", 1)[0].count("keine") == 1
    intern25 = _text(client.get("/api/report/steuer.pdf", params={"jahr": 2025}).content)
    assert "T-003" in intern25.split("Anhang C", 1)[0] and KEEP in intern25  # interne Fassung unverändert


def test_protocol_passes_all_checks(client) -> None:
    pytest.importorskip("pymupdf")
    client.get("/api/report/herkunft.pdf", params={"stichtag": "2021-12-31", "bis": "2021-12-31"})  # Anschluss
    r = client.get("/api/report/steuer-pruefprotokoll.txt", params={"jahr": 2022, "anschluss_jahr": 2021})
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('pruefprotokoll.txt"')
    body = r.text
    assert "[FEHLER]" not in body, body
    for n in range(1, 9):
        assert f"[OK] {n} " in body, n
    assert re.search(r"Rechenergebnisse: SHA-256 [0-9a-f]{64}", body)
    assert "Regelwerk VZ 2022" in body and "kategorien.yaml · SHA-256" in body
    assert f"T-001   {BUY}" in body and f"T-002   {SELL}" in body
    assert r.headers["x-btc-herkunft-pruefergebnis"] == "bestanden" and "ERGEBNIS: BESTANDEN" in body
    assert re.search(r"Anschluss an Herkunftsnachweis Stand [\d-]+ [\d:]+, Stichtag 31\.12\.2021, Build", body)
    # Standard „auf Anforderung“: keine Warnung
    r = client.get("/api/report/steuer-pruefprotokoll.txt", params={"jahr": 2022})
    assert r.headers["x-btc-herkunft-pruefergebnis"] == "bestanden" and "auf Anforderung vorgelegt" in r.text
    # ausdrücklich kein Herkunftsnachweis: Warnung
    r = client.get("/api/report/steuer-pruefprotokoll.txt", params={"jahr": 2022, "anschluss_jahr": 0})
    assert r.headers["x-btc-herkunft-pruefergebnis"] == "warnung"
    assert "ERGEBNIS: BESTANDEN mit 1 Warnung(en)" in r.text


def test_protocol_is_reproducible(client) -> None:
    pytest.importorskip("pymupdf")
    a = client.get("/api/report/steuer-pruefprotokoll.txt", params={"jahr": 2022}).text
    b = client.get("/api/report/steuer-pruefprotokoll.txt", params={"jahr": 2022}).text
    ha = re.search(r"Rechenergebnisse: SHA-256 ([0-9a-f]{64})", a)
    hb = re.search(r"Rechenergebnisse: SHA-256 ([0-9a-f]{64})", b)
    assert ha and hb and ha.group(1) == hb.group(1)


def test_footnote_names_the_annex_of_this_report(client) -> None:
    fa = _text(client.get("/api/report/steuer.pdf", params={"jahr": 2025, "fassung": "finanzamt"}).content)
    assert "nicht im Transaktionsverzeichnis, Anhang C" in fa and "Anhang D" not in fa


def test_protocol_as_pdf_and_result_for_the_button(client) -> None:
    pytest.importorskip("pymupdf")
    client.get("/api/report/herkunft.pdf", params={"stichtag": "2021-12-31", "bis": "2021-12-31"})  # Anschluss
    res = client.get("/api/report/steuer-pruefung", params={"jahr": 2022}).json()
    assert res == {"ergebnis": "bestanden", "fehler": [], "warnungen": 0}
    res = client.get("/api/report/steuer-pruefung", params={"jahr": 2022, "anschluss_jahr": 0}).json()
    assert res["ergebnis"] == "warnung" and res["warnungen"] == 1
    r = client.get("/api/report/steuer-pruefprotokoll.pdf", params={"jahr": 2022})
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'inline; filename="steuerreport-bitcoin-2022-pruefprotokoll.pdf"'
    text = _text(r.content)
    assert "Prüfprotokoll Jahressteuerreport Bitcoin 2022" in text and "ERGEBNIS: BESTANDEN" in text
    assert "[OK] 3 Werte = Herkunftsnachweis" in text


def test_protocol_result_reads_errors_and_warnings() -> None:
    from btc_origin.tax_report import protocol_result

    text = ("Abnahmeprüfungen:\n  [OK] 1 Summen: ja\n  [FEHLER] 8 Keine nicht unterstützten Vorgänge: 2\n"
            "  [WARNUNG] Kein Anschluss\n\nERGEBNIS: FEHLER — 8 Keine nicht unterstützten Vorgänge\n")
    assert protocol_result(text) == {"ergebnis": "fehler", "fehler": ["8 Keine nicht unterstützten Vorgänge"],
                                     "warnungen": 1}


def test_fa_annexes_only_show_evidence_for_disposals_and_income() -> None:
    """Abnahme (Vorgabe der steuerlichen Prüfung): In der Finanzamt-Fassung kommt keine Kaufzeile
    mit Menge oder Betrag vor, die nicht einem veräußerten Teilbestand oder einer Einkunft des
    Jahres zugeordnet ist. Dateiliste bleibt; je Börse eine Abschlusszeile; intern unverändert."""
    pytest.importorskip("pymupdf")
    from datetime import date

    from test_tax_year import NAMES, _report, _rows, price

    from btc_origin.tax_report import check_tax_reports
    from btc_origin.tax_year import build_tax_year

    rep = _report()
    # Auszahlung ohne Bezug zu einer Veräußerung (u1 = T-004): nur intern im Abgleich
    rep.withdrawal_recon["u1"] = {"exchange": "Bison", "day": "2025-02-01", "bought_sats": 4_000_000,
                                  "fee_sats": 0, "received_sats": 4_000_000, "ok": True, "pieces": []}
    tax = build_tax_year(rep, 2025, rows=_rows(), wallet_names=NAMES, price_eur=price, today=date(2026, 9, 29))
    intern, fa, text = check_tax_reports(tax, rep)
    assert "[FEHLER]" not in text, text
    fa_a, fa_b = _text(fa).split("Anhang A", 1)[1].split("Anhang B", 1)
    fa_b = fa_b.split("Anhang C", 1)[0]
    in_a, in_b = _text(intern).split("Anhang A", 1)[1].split("Anhang B", 1)
    assert "T-004" not in fa_a and "T-004" in in_a
    # Relai-Käufe 30./31.07.2025 → Zufluss t6, nicht veräußert: nur intern
    assert "30.07.2025" not in fa_b and "31.07.2025" not in fa_b and "30.07.2025" in in_b
    assert "Relai: 2 weitere Zeile(n) des Jahres ohne Bezug zu einer Veräußerung oder Einkunft; auf " \
           "Anforderung" in fa_b
    # Kauf auf Bison 25.06.2025, auf der Börse verkauft → Beleg; Empfehlungsprämie → Einkunft
    assert "25.06.2025" in fa_b and "01.09.2025" in fa_b


def test_fa_annexes_without_disposal_or_income() -> None:
    import dataclasses
    from datetime import date

    from test_tax_year import NAMES, _report, _rows, price

    from btc_origin.tax_report import render_tax_pdf
    from btc_origin.tax_year import build_tax_year

    rep = _report()
    tax = build_tax_year(rep, 2025, rows=_rows(), wallet_names=NAMES, price_eur=price, today=date(2026, 9, 29))
    tax.disposals, tax.income = [], []
    fa = _text(render_tax_pdf(tax, dataclasses.replace(rep, fassung="finanzamt")))
    annex = fa.split("Anhang A", 1)[1].split("Anhang C", 1)[0]
    assert annex.count("Im Jahr 2025 keine Vorgänge mit Veräußerung oder Einkunft.") == 2
    for day in ("30.07.2025", "31.07.2025", "25.06.2025"):  # keine Kaufzeile
        assert day not in annex


def test_masked_tax_report_hides_amounts_and_hashes_and_still_passes_the_checks() -> None:
    import re
    from datetime import date

    from test_tax_year import NAMES, _report, _rows, price

    from btc_origin.tax_report import check_tax_reports
    from btc_origin.tax_year import build_tax_year

    rep = _report()
    rep.privacy = True
    tax = build_tax_year(rep, 2025, rows=_rows(), wallet_names=NAMES, price_eur=price, today=date(2026, 9, 29))
    intern, fa, text = check_tax_reports(tax, rep)
    assert "ERGEBNIS: BESTANDEN" in text, text
    for pdf in (intern, fa):
        body = _text(pdf)
        assert not re.search(r"\d,\d{8}|\d,\d\d €", body)
        assert "Wallet A" in body  # Namen bleiben lesbar
        assert " t1 " not in body.split("Anhang C", 1)[1]


def test_fa_version_lists_purchases_only_as_count() -> None:
    """Finanzamt-Fassung (Vorgabe der steuerlichen Prüfung): Käufe nur als Anzahl, Einkünfte nach
    § 22 Nr. 3 EStG einzeln; unbelegte Zuflüsse nur mit Bezug zu Veräußerung/Einkunft des Jahres;
    Anhang C nur die verbleibenden T-Nummern. Interne Fassung vollständig."""
    from datetime import date

    from test_tax_year import NAMES, _report, _rows, price

    from btc_origin.tax_report import check_tax_reports
    from btc_origin.tax_year import build_tax_year

    rep = _report()
    tax = build_tax_year(rep, 2025, rows=_rows(), wallet_names=NAMES, price_eur=price, today=date(2026, 9, 29))
    intern, fa, text = check_tax_reports(tax, rep)
    assert "[FEHLER]" not in text, text
    fa_t, in_t = _text(fa), _text(intern)
    s3 = fa_t.split("3 Anschaffungen 2025", 1)[1].split("4 Sonstige Bewegungen", 1)[0]
    assert "Im Jahr 2025 gab es 3 Zuflüsse von fremden Adressen (Anschaffungen)" in s3
    assert "Empfehlungsprämie" in s3 and "Cashback" in s3 and "Kauf auf" not in s3
    t1 = tax.ref("t1")
    assert t1 and t1 not in s3
    s51 = fa_t.split("5.1 Zuflüsse ohne Kaufbeleg", 1)[1].split("5.2", 1)[0]
    assert t1 in s51  # gehört zu einer Veräußerung des Jahres
    # ein unbelegter Zufluss ohne Bezug zu Veräußerung/Einkunft (u1 = T-004) erscheint nur intern
    import dataclasses

    from btc_origin.tax_report import render_tax_pdf

    tax.unproven_inflows.append(dataclasses.replace(tax.unproven_inflows[0], txid="u1"))
    u1 = tax.ref("u1")
    fa51 = _text(render_tax_pdf(tax, dataclasses.replace(rep, fassung="finanzamt"))).split("5.1 Zuflüsse", 1)[1]
    in51 = _text(render_tax_pdf(tax, rep)).split("5.1 Zuflüsse", 1)[1]
    assert u1 not in fa51.split("5.2", 1)[0] and u1 in in51.split("5.2", 1)[0]
    assert t1 in in_t.split("3 Anschaffungen 2025", 1)[1].split("4 Sonstige Bewegungen", 1)[0]  # intern vollständig


def test_auditor_feedback_annex_c_section_4_page_1_and_manual_income() -> None:
    """Rückmeldung der steuerlichen Prüfung: Anhang C der Finanzamt-Fassung ohne „°“-Einträge,
    Art wie Abschnitt 4 (Satoshi-Test); Abschnitt 4 nach T-Nummer; Seite 1 „—“ bei 0 Geschäften;
    Einkunft aus zuordnung_manuell einzeln in Anhang B."""
    import dataclasses
    import re
    from datetime import date

    from test_tax_year import NAMES, _report, _rows, price

    from btc_origin.tax_report import check_tax_reports, render_tax_pdf, tax_released
    from btc_origin.tax_year import build_tax_year

    rep = _report()
    tax = build_tax_year(rep, 2025, rows=_rows(), wallet_names=NAMES, price_eur=price, today=date(2026, 9, 29))
    intern, fa, text = check_tax_reports(tax, rep)
    assert "[FEHLER]" not in text, text
    fa_c = _text(fa).split("Anhang C Transaktionsverzeichnis", 1)[1]
    table_c = fa_c.split("Transaktions-Hash", 1)[1]
    listed = set(re.findall(r"T-\d{3}°?", table_c.split("° Transaktions-Hash", 1)[0]))
    assert listed == {tax.ref(x) for x in tax_released(tax)}  # nur freigegebene, keine „°“-Einträge
    # Anhang C nennt die Art wie Abschnitt 4 (z. B. Satoshi-Test statt Abfluss/Umbuchung)
    i = next(n for n, m in enumerate(tax.movements) if tax.ref(m.txid))
    ref = tax.ref(tax.movements[i].txid)
    tax.movements[i] = dataclasses.replace(tax.movements[i], kind="Satoshi-Test")
    in_c = _text(render_tax_pdf(tax, rep)).split("Anhang C Transaktionsverzeichnis", 1)[1]
    assert re.search(rf"{ref} \S+ [^T]*?Satoshi-Test", in_c), in_c
    # Abschnitt 4 nach T-Nummer
    s4 = _text(intern).split("4 Sonstige Bewegungen", 1)[1].split("5 Offene Punkte", 1)[0]
    refs = re.findall(r"T-\d{3}", s4)
    assert refs and refs == sorted(refs)
    # Einkunft aus zuordnung_manuell (t10, Cashback) einzeln in Anhang B — beide Fassungen
    for pdf in (intern, fa):
        b = _text(pdf).split("Anhang B", 1)[1].split("Anhang C", 1)[0]
        assert "Cashback (§ 22 Nr. 3 EStG), Tageskurs — Zufluss laut Blockchain, keine Export-Zeile" in b
    # Seite 1: 0 Geschäfte → „—“ statt Beträgen
    tax.disposals = []
    page1 = _text(render_tax_pdf(tax, dataclasses.replace(rep, fassung="finanzamt"))).split("1 Grundlagen", 1)[0]
    assert "innerhalb der Haltefrist (steuerbar) 0 — — — — —" in page1
    assert "Summe steuerbarer Gewinn/Verlust —" in page1


def test_manual_assignment_is_stored_by_transaction_hash(client) -> None:
    """Oberfläche: Einordnung per T-Nummer gewählt → gespeichert per Transaktions-Hash; ältere
    Einträge mit T-Nummer werden dabei umgeschrieben."""
    from btc_origin.regelwerk import session_categories

    r = client.post("/api/regelwerk/kategorien/anwenden",
                    json={"manuell": {"T-001": {"kategorie": "empfehlung", "datum": "2022-01-14"}}})
    assert r.status_code == 200, r.text
    text = session_categories() or ""
    assert BUY in text and "T-001" not in text
    data = client.get("/api/regelwerk/kategorien").json()
    assert data["manuell"]["T-001"]["kategorie"] == "empfehlung"  # Anzeige per T-Nummer
    assert all("key" not in z for z in data["zufluesse"])
    # älterer Eintrag per T-Nummer → beim nächsten Übernehmen in den Hash umgeschrieben
    from btc_origin.regelwerk import set_session_categories

    set_session_categories("zuordnung_manuell:\n  T-001:\n    kategorie: empfehlung\n    datum: 2022-01-14\n")
    client.post("/api/regelwerk/kategorien/anwenden", json={})
    text = session_categories() or ""
    assert BUY in text and "T-001" not in text
