"""Finanzamt-Fassung der Herkunftsanalyse: gleiche Daten wie der Full-Detail-Bericht, aber
ohne Bestände, xpubs, Adressen und ohne TxIDs, die keine Veräußerung betreffen."""

from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.merger import Ledger
from btc_origin.origin_report import ON_REQUEST, fa_redact
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow

OWN = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
DEST = "3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"
BUY, SELL, KEEP, LATE, NEW = "1a" * 32, "2b" * 32, "3c" * 32, "4d" * 32, "5e" * 32


def _text(data: bytes) -> str:
    pymupdf = pytest.importorskip("pymupdf")
    return "\n".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf"))


def test_redact_keeps_only_released_ids() -> None:
    assert fa_redact(f"TxID {BUY}", {BUY}) == f"TxID {BUY}"
    assert fa_redact(f"TxID {KEEP}", {BUY}) == f"TxID {ON_REQUEST}"
    assert fa_redact("c" * 64, set()) == ON_REQUEST  # jede 64-stellige Kennung
    assert fa_redact(f"an {OWN} und {DEST}", set()) == f"an {ON_REQUEST} und {ON_REQUEST}"
    assert fa_redact("Tx 1a2b3c4d5e…", set()) == f"Tx {ON_REQUEST}"  # auch gekürzt nicht
    keep = "Stand 2026-09-29 10:05:00 · 1.234,56 € · 0,12345678 · Build ab12cd3 · Ledger"
    assert fa_redact(keep, set()) == keep


@pytest.fixture()
def client():
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(historical_fetcher=lambda on: (None, 30_000.0), spot_fetcher=lambda: (None, None))
        state.db.execute("INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (1,'Wallet A',NULL,'address','2024-01-01')")
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)", (OWN,))
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,'bc1qchange',1)")
        from btc_origin.wallet_registry import WalletEntry

        state.db.execute("INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (2,'Wallet B',NULL,'address','2024-01-01')")
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (2,'bc1qwalletb',0)")
        state.registry._wallets = [WalletEntry(id=1, name="Wallet A", kind="address", address=OWN),
                                   WalletEntry(id=2, name="Wallet B", kind="address", address="bc1qwalletb")]
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
            Flow(NEW, "bc1qwalletb", "in", 1_000_000, wallet_id=2, block_time="2026-03-01T10:05:00Z", vout=0,
                 tx_total_output_sats=1_000_000),
        ]))
        state.last_tx_io = {SELL: {"output_addresses": [DEST, "bc1qchange"]}}
        client.post("/api/enrich")
        yield client


def test_tx_refs_numbered_by_block_time_and_stable() -> None:
    """T-Nummern nach Blockzeit (bei gleicher Zeit nach TxID) — unabhängig von der
    Reihenfolge der Zeilen; ein früherer Stichtag behält die Nummern der früheren Vorgänge."""
    from btc_origin.origin_report import build_tx_refs

    rows = [
        {"txid": "t3", "block_time": "2024-03-01T10:05:00Z", "wallet_id": 1},
        {"txid": "t1", "block_time": "2022-01-14T10:05:00Z", "wallet_id": 1},
        {"txid": "t2", "block_time": "2022-06-05T10:05:00Z", "wallet_id": 2},
        {"txid": "t2", "block_time": "2022-06-05T10:05:00Z", "wallet_id": 1},
    ]
    refs = build_tx_refs(rows, {1: "Wallet A", 2: "Ledger"}, {"t1"}, {"t2"})
    assert [(x.ref, x.txid, x.art, x.wallet) for x in refs] == [
        ("T-001", "t1", "Zufluss", "Wallet A"),
        ("T-002", "t2", "Abfluss", "Ledger, Wallet A"),
        ("T-003", "t3", "Umbuchung", "Wallet A"),
    ]
    assert [x.ref for x in build_tx_refs(list(reversed(rows)), {}, set(), set())] == ["T-001", "T-002", "T-003"]
    early = build_tx_refs([r for r in rows if r["block_time"] < "2023"], {}, set(), set())
    assert [(x.ref, x.txid) for x in early] == [("T-001", "t1"), ("T-002", "t2")]


def test_fa_pdf_prints_only_released_txids_and_no_holdings(client) -> None:
    params = {"stichtag": "2025-12-31", "bis": "2025-12-31", "fassung": "finanzamt"}
    r = client.get("/api/report/herkunft.pdf", params=params)
    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith('-bis-2025-12-31-finanzamt.pdf"')
    text = _text(r.content)
    flat = " ".join(text.split())
    assert "Finanzamt-Fassung" in flat and "sie werden auf Anforderung vollständig vorgelegt" in flat
    # Tabellen: Kurzreferenzen; Verzeichnis: nur Veräußerung und ihre Herkunft
    assert "T-001" in flat and "T-002" in flat and "T-003°" in flat
    assert "° Transaktions-Hash auf Anforderung" in flat  # Fußnote auf der Seite
    assert "Transaktionsverzeichnis" in flat
    assert set(re.findall(r"[0-9a-f]{64}", text)) == {BUY, SELL}
    assert OWN not in text and DEST not in text and "bc1q" not in text
    # --bis: der Zufluss 2026 fehlt ganz
    assert "01.02.2026" not in flat and "T-004" not in flat
    # Wallet ohne Vorgänge bis zum Stichtag wird nicht aufgeführt
    assert "Wallet B" not in flat and "Aufgeführt sind die Wallets mit Vorgängen bis 31.12.2025" in flat
    for label in ("Heute vorhanden", "Bestand (BTC)", "Altbestand", "Bestand 0,"):
        assert label not in flat
    # Flussgrafik ohne Zahlen und ohne Bestand (Kreisgröße nach Durchfluss)
    assert "Übersicht: Fluss zwischen den Wallets" in flat and "Kreisgröße = Durchfluss der Wallet" in flat
    assert "stimmt mit der Blockchain überein" in flat
    assert "übrige auf Anforderung" in flat  # Methodik verweist nicht ins Leere
    # Veräußerung 2022, Berichtsjahr 2025: Freigrenzen-Baustein nennt beide Werte
    assert "weniger als 1.000 € (VZ 2022: 600 €) beträgt" in flat
    assert "Regelwerk VZ 2022, Stand 29.09.2026; Regelwerk VZ 2025, Stand 29.09.2026" in flat


def test_control_file_passes_all_checks(client) -> None:
    pytest.importorskip("pymupdf")
    params = {"stichtag": "2025-12-31", "bis": "2025-12-31"}
    r = client.get("/api/report/herkunft-kontrolle.txt", params=params)
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-disposition"].endswith('-bis-2025-12-31-kontrolle.txt"')
    body = r.text
    assert "alle Prüfungen bestanden" in body, body
    assert body.count("[OK]") == 3 + 8 + 2 and "[FEHLER]" not in body  # + Geltungsbereich
    # Wiederholbarkeit: verwendete Regeldateien (Jahr der Veräußerung + Berichtsjahr) mit Hash
    assert re.search(r"Regelwerk VZ 2022, Stand 29\.09\.2026 .* SHA-256 [0-9a-f]{64}", body)
    assert "Regelwerk VZ 2025, Stand 29.09.2026" in body and "Regelwerk VZ 2024" not in body
    assert re.search(r"kategorien\.yaml · SHA-256 [0-9a-f]{64}", body)
    listed = body.split("Freigabeliste", 1)[1]
    assert f"T-001   {BUY}" in listed and f"T-002   {SELL}" in listed and KEEP not in listed
    masked = client.get("/api/report/herkunft-kontrolle.txt", params={**params, "privacy": "true"}).text
    assert "alle Prüfungen bestanden" in masked, masked


def test_internal_report_uses_short_refs_and_lists_every_tx(client) -> None:
    r = client.get("/api/report/herkunft.pdf", params={"stichtag": "2026-12-31"})
    assert r.headers["content-disposition"].endswith('-intern.pdf"')
    text = _text(r.content)
    flat = " ".join(text.split())
    assert "Interne Fassung (Full-Detail)" in flat and "Finanzamt-Fassung" not in flat
    assert "Heute vorhanden" in flat and "Bestand (BTC)" in flat
    assert "01.02.2026" in flat  # ohne --bis alle Vorgänge
    assert set(re.findall(r"[0-9a-f]{64}", text)) == {BUY, SELL, KEEP, LATE, NEW}  # nur im Verzeichnis
    assert "Wallet B" in flat and "°" not in flat  # ohne bis alle Wallets; kein Kennzeichen intern
    assert not re.search(r"[0-9a-f]{8,}…", text)  # keine gekürzten Hashes in den Tabellen
    assert "Kurzreferenzen (T-Nummern)" in flat
    cut = client.get("/api/report/herkunft.pdf", params={"stichtag": "2026-12-31", "bis": "2025-12-31"})
    assert cut.headers["content-disposition"].endswith('-bis-2025-12-31-intern.pdf"')
    assert "01.02.2026" not in " ".join(_text(cut.content).split())


def test_unnamed_counterparty_address_is_not_printed() -> None:
    """Ohne Namen stünde die Zieladresse als Gegenstelle im Bericht — in der
    Finanzamt-Fassung wird sie nie gezeichnet; die Veräußerung bleibt mit TxID."""
    from datetime import date, datetime

    from btc_origin.origin_report import HerkunftReport, check_reports
    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2024)
    y.details.append(dict(
        exit_date="2024-05-02", acquisition_date="2023-01-10", days_held=478, short_term=False,
        btc_sats=100_000, fee_sats=1_000, proceeds_eur=60.0, cost_eur=10.0, fee_eur=1.0, gain_eur=49.0,
        counterparty=DEST, address=DEST, txid=SELL, origin_txid=BUY, origin="wallet", status="", disposal=True,
    ))
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), wallets=[],
                         sources=[], years=[y], lots=[])
    _full, data, control = check_reports(rep)
    text = _text(data)
    assert DEST not in text and ON_REQUEST in text
    assert "[OK] 3a " in control and "[OK] 3d " in control
    assert SELL in control and BUY in control  # Freigabeliste


def test_section_4_compare_ignores_repeated_table_heads() -> None:
    """Die Finanzamt-Fassung ist vor Abschnitt 4 anders lang als der Full-Detail-Bericht;
    reicht eine Tabelle über eine Seite, wiederholt sich ihr Kopf an anderer Stelle —
    das ist keine Abweichung der Werte."""
    from datetime import date, datetime

    from btc_origin.origin_report import HerkunftReport, InflowLine, check_reports
    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2024)
    base = dict(acquisition_date="2023-01-10", days_held=478, short_term=False, fee_sats=0, proceeds_eur=60.0,
                cost_eur=10.0, fee_eur=1.0, gain_eur=49.0, status="Verkauf lt. Export", disposal=True)
    y.details += [dict(base, exit_date=f"2024-{1 + i % 12:02d}-{1 + i // 12:02d}", btc_sats=100_000,
                       counterparty="Kraken", origin="exchange") for i in range(60)]
    y.details.append(dict(base, exit_date="2024-05-02", btc_sats=100_000, counterparty="ext-001",
                          origin="wallet", txid=SELL, origin_txid=BUY, status=""))
    inflows = [InflowLine("W", f"2023-01-{1 + i:02d}", "ext-002", BUY if i == 0 else f"t{i}", 100_000, 0, 20_000.0)
               for i in range(13)]
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[],
                         sources=[], years=[y], lots=[], inflows=inflows)
    _full, _fa, control = check_reports(rep)
    assert "[OK] 4 " in control, control


def test_section_compare_still_reports_value_differences() -> None:
    from btc_origin.origin_report import _section_text

    start, end = "4  Mögliche Gewinne und Verluste", "5  Belege und Ersatzwerte"
    full = [(1, False, start), (1, False, "Jahr"), (1, False, "1.800,00 €"), (1, False, end)]
    fa = [(1, False, start), (1, False, "Jahr"), (1, False, "1.900,00 €"), (1, False, end)]
    assert _section_text(full, start, end, {"Jahr"}) != _section_text(fa, start, end, {"Jahr"})
    assert _section_text(full, start, end, {"Jahr"}) == [start, "1.800,00 €"]


def test_section_1_lists_wallets_by_first_activity() -> None:
    """Abschnitt 1 in derselben Reihenfolge wie Abschnitt 3: älteste Wallet zuerst,
    unabhängig von der Reihenfolge, in der die Wallets angelegt wurden."""
    from datetime import date, datetime

    from btc_origin.origin_report import HerkunftReport, WalletInfo, render_pdf

    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), sources=[], years=[], lots=[],
        wallets=[
            WalletInfo("Wallet Neu", "xpub", "", "2025-08-13", "2025-08-13", 1, 0),
            WalletInfo("Wallet Alt", "xpub", "", "2022-02-07", "2025-01-15", 19, 0),
            WalletInfo("Wallet Mitte", "xpub", "", "2024-01-02", "2025-01-15", 53, 0),
        ],
    )
    for fassung in ("full", "finanzamt"):
        rep.fassung = fassung
        flat = " ".join(_text(render_pdf(rep)).split())
        section_1 = flat.split("1 Betrachtete Wallets", 1)[1]
        assert section_1.index("Wallet Alt") < section_1.index("Wallet Mitte") < section_1.index("Wallet Neu")


def test_explanation_block_is_not_split_across_pages() -> None:
    """Ein kurzer Erläuterungsblock (Überschrift + Text) steht ganz auf einer Seite — kein
    Rest allein auf der nächsten (Abschnitt 9, hier der Block „Lightning“)."""
    from datetime import date, datetime

    pymupdf = pytest.importorskip("pymupdf")
    from btc_origin.origin_report import HerkunftReport, WalletInfo, render_pdf

    for n_wallets in range(0, 40, 4):  # verschiebt den Seitenumbruch in Abschnitt 2
        rep = HerkunftReport(
            generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), sources=[], years=[], lots=[],
            wallets=[WalletInfo(f"W{i}", "xpub", "", "2024-01-02", "2025-01-15", 1, 0) for i in range(n_wallets)],
            fassung="finanzamt",
        )
        doc = pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf")
        head = next(i for i, p in enumerate(doc) if "Lightning ist ein Zahlungsnetz" in " ".join(p.get_text().split()))
        assert "über Lightning ist keine Veräußerung" in " ".join(doc[head].get_text().split()), n_wallets


def test_body_text_never_runs_into_page_footnotes() -> None:
    """Reicht der reservierte Platz für die Legende einer Seite nicht (lange Legende, breitere
    Schrift), erkennt render_pdf die Überschneidung und rendert mit größerem Rand neu."""
    from datetime import date, datetime

    from btc_origin.origin_report import _OVERLAP, _PAGE_MARGIN, HerkunftReport, _render_pdf_once, render_pdf
    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2024)
    base = dict(acquisition_date="2023-01-10", days_held=478, short_term=False, fee_sats=0, proceeds_eur=60.0,
                cost_eur=10.0, fee_eur=1.0, gain_eur=49.0, status="Verkauf lt. Export", disposal=True)
    y.details += [dict(base, exit_date=f"2024-{1 + i % 12:02d}-{1 + i // 12:02d}", btc_sats=100_000,
                       counterparty="Kraken", origin="exchange") for i in range(80)]  # jede Zeile mit ¹
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), wallets=[],
                         sources=[], years=[y], lots=[])
    token = _PAGE_MARGIN.set(18.0)  # absichtlich zu knapp für Legende und Fußzeile
    try:
        _render_pdf_once(rep)
        assert _OVERLAP.get()[0] > 0  # erkannt
        render_pdf(rep)
        assert _OVERLAP.get()[0] <= 0  # behoben
    finally:
        _PAGE_MARGIN.reset(token)
    render_pdf(rep)
    assert _OVERLAP.get()[0] <= 0  # Standardrand: keine Überschneidung


def test_flow_graphic_with_many_wallets_keeps_legend_on_page() -> None:
    """Mit vielen Wallets ist die Grafik hoch; ihre Farblegende muss trotzdem auf derselben
    Seite bleiben — sonst entstehen Leerseiten und der Rand wächst für das ganze Dokument."""
    from datetime import date, datetime

    pymupdf = pytest.importorskip("pymupdf")
    from btc_origin.origin_report import (
        _OVERLAP, _PAGE_MARGIN, HerkunftReport, InflowLine, WalletInfo, _render_pdf_once, render_pdf,
        wallet_reconciliation,
    )

    names = {i: f"Wallet {chr(65 + i)}" for i in range(12)}
    rows = []
    for i in range(12):
        rows.append(dict(txid=f"in{i}", wallet_id=i, direction="in", amount_sats=10_000_000,
                         block_time=f"2022-{1 + i:02d}-01"))
        if i:
            rows.append(dict(txid=f"mv{i}", wallet_id=i - 1, direction="out", amount_sats=5_000_000, is_internal=0,
                             external_amount_sats=0, fee_sats=0, block_time=f"2023-{i:02d}-01"))
            rows.append(dict(txid=f"mv{i}", wallet_id=i, direction="in", amount_sats=5_000_000, is_internal=1,
                             block_time=f"2023-{i:02d}-01"))
    rec = wallet_reconciliation(rows, names, date(2025, 12, 31))
    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), sources=[], years=[], lots=[],
        wallets=[WalletInfo(n, "xpub", "", f"2022-{1 + i:02d}-01", "2023-12-01", 2, 0) for i, n in names.items()],
        inflows=[InflowLine(n, f"2022-{1 + i:02d}-01", "ext-001", f"in{i}", 10_000_000, 0, 20_000.0)
                 for i, n in names.items()],
        recon=rec,
    )
    for fassung in ("full", "finanzamt"):
        rep.fassung = fassung
        _render_pdf_once(rep)
        assert _OVERLAP.get()[0] <= 0, fassung  # Rand muss nicht wachsen
        assert _PAGE_MARGIN.get() == 40.0
        doc = pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf")
        graph = next(i for i, p in enumerate(doc) if "Übersicht: Fluss zwischen den Wallets" in p.get_text())
        page = " ".join(doc[graph].get_text().split())
        assert "Zufluss ohne Kaufbeleg" in page and "Abfluss an fremde Adressen" in page, fassung
        for p in doc:  # keine Seite nur mit Kopf- und Fußzeile
            body = [ln for ln in p.get_text().splitlines()
                    if ln.strip() and not ln.startswith(("Herkunftsanalyse Bitcoin", "Erstellt mit", "Seite "))]
            assert body, (fassung, p.number + 1)


def test_wrapped_footnote_is_indented_under_its_text() -> None:
    """Eine umbrechende Fußnote läuft mit hängendem Einzug weiter: Folgezeilen beginnen unter
    dem Text, nicht unter dem Fußnotenzeichen (das ⁶ zum Satoshi-Test ist mehrzeilig)."""
    from datetime import date, datetime

    pymupdf = pytest.importorskip("pymupdf")
    from btc_origin.origin_report import HerkunftReport, InflowLine, render_pdf

    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), wallets=[], sources=[], years=[], lots=[],
        inflows=[InflowLine("W", "2024-01-02", "Satoshi-Test ⁶", "t1", 100_000, 0, 20_000.0)],
    )
    doc = pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf")
    marks = "¹²³⁴⁵⁶°↳✓✗*"
    left = 18 * 72 / 25.4  # linker Rand in pt
    checked = 0
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                spans = [sp for sp in ln["spans"] if sp["text"].strip()]
                if not spans or spans[0]["size"] > 7 or ln["bbox"][1] < page.rect.height - 150:
                    continue  # nur die Legende (6,8 pt) am Seitenfuß
                if spans[0]["text"].strip()[0] in marks:
                    continue  # Zeile mit Fußnotenzeichen
                assert ln["bbox"][0] > left + 5, spans[0]["text"]  # Folgezeile eingerückt
                checked += 1
    assert checked, "keine umbrechende Fußnote gefunden"


def test_structure_note_under_table_of_contents() -> None:
    """„So ist der Bericht aufgebaut“ steht auf der Seite des Inhaltsverzeichnisses, passend
    zur Fassung; Abschnitt 9 (Begriffe) steht vor den Anhängen."""
    from datetime import date, datetime

    pymupdf = pytest.importorskip("pymupdf")
    from btc_origin.origin_report import HerkunftReport, render_pdf

    for fassung, bestand in (("full", True), ("finanzamt", False)):
        rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), wallets=[],
                             sources=[], years=[], lots=[], fassung=fassung)
        pages = [" ".join(p.get_text().split()) for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf")]
        toc = next(p for p in pages if "Inhaltsverzeichnis" in p)
        assert "So ist der Bericht aufgebaut" in toc and "Abschnitt 9 erklärt die Begriffe" in toc
        assert ("den Bestand und die Abstimmung" in toc) is bestand
        assert sum("So ist der Bericht aufgebaut" in p for p in pages) == 1
        order = [i for i, p in enumerate(pages) if "9 Erläuterungen für Leser ohne Bitcoin-Vorkenntnisse" in p]
        assert order and order[-1] > next(i for i, p in enumerate(pages) if "7 Methodik und Annahmen" in p and i > 1)


def test_annex_a_lists_counts_and_origin_per_file() -> None:
    """Anhang A: Zahlen je Datei (nicht die Börsensumme doppelt) und die Herkunft der Datei
    laut erlaeuterungen.csv (Art „Datei“)."""
    from datetime import date, datetime

    from btc_origin.origin_report import HerkunftReport, render_pdf

    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2025, 12, 31), wallets=[], sources=[], years=[], lots=[],
        exchange_files=[
            {"file": "bitvavo-alt.csv", "name": "Bitvavo", "txids": 0, "buys": 50, "sells": 0, "withdrawals": 30, "deposits": 0},
            {"file": "bitvavo.csv", "name": "Bitvavo", "txids": 0, "buys": 40, "sells": 0, "withdrawals": 20, "deposits": 0},
        ],
        explanations=[("bitvavo-alt.csv", date(2026, 9, 15), "Datei", "von der Börse auf Anfrage per E-Mail bereitgestellt")],
    )
    flat = " ".join(_text(render_pdf(rep)).split())
    assert "bitvavo.csv Bitvavo 40 0 20 0" in flat
    # Herkunft als zweite Zeile unter dem Dateinamen, nicht als Absatz unter der Tabelle
    assert "Herkunft der Dateien" not in flat
    i = flat.index("bitvavo-alt.csv")
    assert "von der Börse auf Anfrage per E-Mail bereitgestellt am 15.09.2026" in flat[i : i + 400]
    assert "Bitvavo 50 0 30 0" in flat[i : i + 400]
