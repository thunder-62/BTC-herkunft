"""„Herkunftsanalyse Bitcoin“: data assembly + inline PDF endpoint."""

from __future__ import annotations

import re
from datetime import date, datetime

import pytest

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.merger import Ledger
from btc_origin.origin_report import (
    HerkunftReport,
    WalletInfo,
    address_type_label,
    build_lot_lines,
    build_sources,
    fmt_btc,
    fmt_eur,
    render_pdf,
)
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow

OWN = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
DEST = "3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"


def _shaped():
    return [
        {"direction": "in", "lot_id": "L1", "amount_sats": 10_000_000, "time": "2022-01-09",
         "source_name": "Relai", "current_locations": [{"wallet_name": "Bitcoin 2023", "remaining_sats": 4_000_000}]},
        {"direction": "in", "lot_id": "L2", "amount_sats": 5_000_000, "time": "2022-02-09",
         "source_addresses": ["3PxY3oQaaxnmPhEcsCkHuCHBYzPFb5ME8k"], "current_locations": []},
        {"direction": "in", "lot_id": "L3", "amount_sats": 625_000, "time": "2022-03-09",
         "source_coinbase": True, "current_locations": [{"wallet_name": "Mining", "remaining_sats": 625_000}]},
        {"direction": "out", "lot_id": "L1", "amount_sats": 5_990_000, "time": "2023-01-01", "external_name": "Bison"},
        {"direction": "out", "lot_id": "L2", "amount_sats": 5_000_000, "time": "2023-01-01", "address": DEST},
    ]


def test_build_sources_where_coins_are_and_went() -> None:
    by = {s.name: s for s in build_sources(_shaped())}
    relai = by["Relai"]
    assert (relai.entered_sats, relai.held, relai.exited) == (
        10_000_000, {"Bitcoin 2023": 4_000_000}, {"Bison": 5_990_000})
    assert relai.fees_sats == 10_000
    assert by["3PxY3oQa…ME8k"].exited == {DEST: 5_000_000}
    assert by["Mining (neu gemint)"].held == {"Mining": 625_000}


def test_lot_lines_haltefrist_to_stichtag() -> None:
    lots = [{"lot_id": "L1", "lot_date": "2025-06-01", "remaining_sats": 4_000_000, "current_wallet_name": "W"}]
    (line,) = build_lot_lines(lots, {"L1": "Relai"}, date(2026, 5, 31), lambda d: 90_000.0)
    assert (line.source, line.days_held, line.qualifies, line.price_eur) == ("Relai", 364, False, 90_000.0)
    (line,) = build_lot_lines(lots, {"L1": "Relai"}, date(2026, 6, 1), lambda d: None)
    assert line.qualifies is True and line.price_eur is None


def test_formatting_and_types() -> None:
    assert fmt_btc(12_345_678) == "0,12345678 BTC"
    assert fmt_btc(1, privacy=True, unit=False) == "•••"
    assert fmt_eur(-1234.5) == "−1.234,50 €"
    assert fmt_eur(1234.5, signed=True) == "+1.234,50 €"
    assert fmt_eur(None) == "Kurs fehlt"
    assert address_type_label([OWN, "bc1pxyz", "3abc", OWN]) == "Native SegWit (bc1q…), Taproot (bc1p…), SegWit kompatibel (3…)"


def test_render_pdf_multipage_both_variants() -> None:
    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 27, 18, 42, 10),
        stichtag=date(2026, 12, 31),
        wallets=[WalletInfo("Bitcoin 2023", "xpub", "Native SegWit (bc1q…)", "2023-01-01", "2024-01-01", 12, 4_000_000)],
        sources=build_sources(_shaped()),
        years=[],
        lots=build_lot_lines(
            [{"lot_id": f"L{i}", "lot_date": "2023-01-01", "remaining_sats": 1000 + i, "current_wallet_name": "W"} for i in range(120)],
            {}, date(2026, 12, 31), lambda d: 20_000.0,
        ),
        person_name="Erika Muster",
        tax_id="12 345 678 901",
    )
    pdf = render_pdf(rep)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 2 or len(pdf) > 5000
    rep.privacy = True
    assert render_pdf(rep).startswith(b"%PDF")


def test_api_report_inline_pdf() -> None:
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(historical_fetcher=lambda on: (None, 30_000.0), spot_fetcher=lambda: (None, None))
        state.db.execute("INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (1,'Bitcoin 2022',NULL,'address','2024-01-01')")
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)", (OWN,))
        from btc_origin.wallet_registry import WalletEntry
        state.registry._wallets = [WalletEntry(id=1, name="Bitcoin 2022", kind="address", address=OWN)]
        persist_ledger(state.db, Ledger(flows=[
            Flow("buy", OWN, "in", 10_000_000, wallet_id=1, block_time="2022-01-14T00:00:00Z", vout=0, tx_total_output_sats=10_000_000),
            Flow("sell", OWN, "out", 10_000_000, wallet_id=1, block_time="2022-06-05T00:00:00Z", vin_index=0, tx_total_output_sats=9_990_000),
            Flow("sell", "bc1qchange", "in", 3_990_000, wallet_id=1, block_time="2022-06-05T00:00:00Z", vout=1, tx_total_output_sats=9_990_000),
        ]))
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,'bc1qchange',1)")
        state.last_tx_io = {"sell": {"output_addresses": [DEST, "bc1qchange"]}}
        client.post("/api/enrich")
        r = client.get("/api/report/herkunft.pdf", params={"stichtag": "2026-12-31", "name": "Erika", "steuer_id": "123"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"].startswith("inline;")
        assert r.headers["x-btc-herkunft-disk-written"] == "false"
        assert r.content.startswith(b"%PDF")
        rep = __import__("btc_origin.api.routes.report", fromlist=["_build_herkunft_report"])._build_herkunft_report(
            state, stichtag=date(2026, 12, 31), privacy=False)
        assert rep.wallets[0].tx_count == 2 and rep.wallets[0].balance_sats == 3_990_000
        assert rep.inflow_sats == 10_000_000 and rep.balance_sats == 3_990_000 and rep.consistent
        (y,) = rep.years
        assert y.year == 2022 and y.short.btc_sats == 6_000_000
        assert client.get("/api/report/herkunft.pdf", params={"privacy": "true"}).status_code == 200


def test_wallet_reconciliation_nets_change_and_splits_fees() -> None:
    from btc_origin.origin_report import wallet_reconciliation

    rows = [
        dict(txid="buy", wallet_id=1, direction="in", amount_sats=20_000_000, block_time="2021-03-05"),
        # A → B, fee 10k
        dict(txid="move", wallet_id=1, direction="out", amount_sats=20_000_000, is_internal=1, fee_sats=10_000, block_time="2022-01-14"),
        dict(txid="move", wallet_id=2, direction="in", amount_sats=19_990_000, is_internal=1, block_time="2022-01-14"),
        # B sells 0.1, change back to B, fee 10k
        dict(txid="sell", wallet_id=2, direction="out", amount_sats=19_990_000, is_internal=0,
             external_amount_sats=10_000_000, fee_sats=10_000, block_time="2023-05-06"),
        dict(txid="sell", wallet_id=2, direction="in", amount_sats=9_980_000, is_internal=1, block_time="2023-05-06"),
        # after the Stichtag: ignored
        dict(txid="late", wallet_id=2, direction="in", amount_sats=5, block_time="2025-01-01"),
    ]
    from datetime import date as _date

    rec = wallet_reconciliation(rows, {1: "A", 2: "B"}, _date(2024, 12, 31))
    a, b = rec["A"], rec["B"]
    assert (a.ext_in, a.int_out, a.fees, a.computed, a.balance) == (20_000_000, 19_990_000, 10_000, 0, 0)
    # change to the same wallet is no Umbuchung
    assert (b.int_in, b.int_out, b.ext_out, b.fees) == (19_990_000, 0, 10_000_000, 10_000)
    assert b.computed == b.balance == 9_980_000


def test_wallet_reconciliation_lists_transfers_per_wallet() -> None:
    from datetime import date as _date

    from btc_origin.origin_report import _transfer_rows, wallet_reconciliation

    rows = [
        dict(txid="buy", wallet_id=1, direction="in", amount_sats=30_000_000, block_time="2021-03-05"),
        # A verteilt an B und C, Gebühr 10k
        dict(txid="split", wallet_id=1, direction="out", amount_sats=30_000_000, is_internal=1, fee_sats=10_000, block_time="2022-01-14"),
        dict(txid="split", wallet_id=2, direction="in", amount_sats=20_000_000, is_internal=1, block_time="2022-01-14"),
        dict(txid="split", wallet_id=3, direction="in", amount_sats=9_990_000, is_internal=1, block_time="2022-01-14"),
    ]
    rec = wallet_reconciliation(rows, {1: "A", 2: "B", 3: "C"}, _date(2024, 12, 31))
    a = rec["A"]
    assert a.int_out_to == {"B": 20_000_000, "C": 9_990_000}
    assert sum(a.int_out_to.values()) == a.int_out
    assert rec["B"].int_in_from == {"A": 20_000_000} and rec["C"].int_in_from == {"A": 9_990_000}
    labels = [r[0] for r in _transfer_rows(a, False)]
    assert labels == ["− Umbuchung an „B“", "− Umbuchung an „C“"]  # 0-Zeilen entfallen
    assert _transfer_rows(rec["B"], False) == [["+ Umbuchung von „A“", "0,20000000"]]


def test_wallet_flow_graphic_uses_reconciliation_numbers() -> None:
    import io
    from datetime import date as _date

    from btc_origin.origin_report import InflowLine, wallet_flow_edges, wallet_reconciliation

    rows = [
        dict(txid="t1", wallet_id=1, direction="in", amount_sats=30_000_000, block_time="2019-03-05"),
        dict(txid="t2", wallet_id=1, direction="in", amount_sats=10_000_000, block_time="2020-03-05"),
        dict(txid="t3", wallet_id=1, direction="out", amount_sats=40_000_000, is_internal=0,
             external_amount_sats=5_000_000, fee_sats=10_000, block_time="2022-01-14"),
        dict(txid="t3", wallet_id=2, direction="in", amount_sats=34_990_000, is_internal=1, block_time="2022-01-14"),
    ]
    rec = wallet_reconciliation(rows, {1: "Alt", 2: "Neu"}, _date(2026, 12, 31))
    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], years=[], lots=[],
        wallets=[WalletInfo("Neu", "xpub", "p2wpkh", "2022-01-14", None, 1, 0),
                 WalletInfo("Alt", "xpub", "p2wpkh", "2019-03-05", None, 2, 0)],
        inflows=[InflowLine("Alt", "2019-03-05", "Bitvavo", "t1", 30_000_000, 0, 3500.0, acq_source="Kauf auf Bitvavo lt. Export"),
                 InflowLine("Alt", "2020-03-05", "ext-001", "t2", 10_000_000, 0, 8000.0)],
        recon=rec, stichtag_balances={"Alt": 0, "Neu": 34_990_000},
    )
    edges = {(a, b, k): v for a, b, k, v in wallet_flow_edges(rep, ["Alt", "Neu"])}
    assert edges[("\x00in", "Alt", "proven")] == 30_000_000
    assert edges[("\x00in", "Alt", "unproven")] == 10_000_000
    assert edges[("Alt", "Neu", "transfer")] == 34_990_000
    assert edges[("Alt", "\x00out", "exit")] == 5_000_000
    data = render_pdf(rep)
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf"))
    assert "Übersicht: Fluss zwischen den Wallets" in text and "Zufluss ohne Kaufbeleg" in text
    assert rec["Alt"].out_lines == [{"date": "2022-01-14", "txid": "t3", "sats": 5_000_000, "fee": 10_000}]
    assert "„Alt“  ·  2 Zuflüsse  ·  1 Abfluss" in text
    assert "Zwischensumme Abflüsse an fremde Adressen (inkl. Gebühren)" in text
    assert "+ Gebühr" in text and "= Abgang" in text and "Zwischensumme (Abgang inkl. Gebühren)" in text


def test_transit_wallet_is_marked() -> None:
    import io
    from datetime import date as _date

    from btc_origin.origin_report import _is_transit, wallet_reconciliation

    rows = [
        dict(txid="t1", wallet_id=1, direction="in", amount_sats=20_000_000, block_time="2019-03-05"),
        dict(txid="t2", wallet_id=1, direction="out", amount_sats=20_000_000, is_internal=1, fee_sats=10_000, block_time="2021-01-14"),
        dict(txid="t2", wallet_id=2, direction="in", amount_sats=19_990_000, is_internal=1, block_time="2021-01-14"),
        dict(txid="t3", wallet_id=2, direction="out", amount_sats=19_990_000, is_internal=1, fee_sats=10_000, block_time="2022-01-14"),
        dict(txid="t3", wallet_id=3, direction="in", amount_sats=19_980_000, is_internal=1, block_time="2022-01-14"),
    ]
    rec = wallet_reconciliation(rows, {1: "Alt", 2: "Zwischen", 3: "Neu"}, _date(2026, 12, 31))
    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], years=[], lots=[],
        wallets=[WalletInfo(n, "xpub", "p2wpkh", None, None, 1, 0) for n in ("Alt", "Zwischen", "Neu")],
        recon=rec, stichtag_balances={k: v.computed for k, v in rec.items()},
    )
    assert _is_transit(rep, "Zwischen")
    assert not _is_transit(rep, "Neu") and not _is_transit(rep, "Alt")
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf"))
    assert "„Zwischen“  ·  Durchgangs-Wallet" in text and "Haltefrist läuft weiter" in text


def test_chapter4_names_defunct_exchange_without_export() -> None:
    import io

    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2022)
    y.details.append(dict(exit_date="2022-02-05", acquisition_date="2019-03-05", days_held=900, short_term=False,
                          btc_sats=1_000_000, fee_sats=500, proceeds_eur=400.0, cost_eur=100.0, fee_eur=0.5,
                          gain_eur=300.0, counterparty="FTX", txid="a", origin="wallet", status="", disposal=True))
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31),
                         wallets=[], sources=[], years=[y], lots=[], exchange_exports=["Binance"])
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf"))
    assert "Exporte mit Verkäufen lagen vor für: Binance." in " ".join(text.split())
    assert "Für Abflüsse an FTX gibt es keinen" in text


def test_summary_status_fails_on_single_deviation() -> None:
    from btc_origin.origin_report import InflowLine, deviations, summary_rows

    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], years=[], lots=[],
        wallets=[WalletInfo("consolidation", "xpub", "p2wpkh", None, None, 1, 0)],
        inflows=[InflowLine("consolidation", "2025-08-31", "Coinbase", "t1", 1000, 1000, 1.0, "2025-08-30",
                            "Kauf auf Coinbase lt. Export")],
        inflow_sats=1000, balance_sats=1000, chain_balance_sats=1000, disposal_fees_sats=0, transfer_fees_sats=0,
    )
    rep.lots = []
    assert deviations(rep) == []
    rep.withdrawal_recon = {"t1": {"ok": False, "day": "2025-08-31", "exchange": "Coinbase"}}
    assert deviations(rep) == ["31.08.2025, consolidation"]
    from btc_origin.origin_report import LotLine

    rep.lots = [LotLine("2025-08-30", "Coinbase", "consolidation", 1000, 400, False, 1.0)]
    row = next(r for r in summary_rows(rep) if r[0] == "Abstimmung mit der Blockchain")
    assert row[1] == "1 Abweichung (31.08.2025, consolidation) ✗" and row[2] == "2"


def test_wallets_sorted_by_first_inflow() -> None:
    from btc_origin.origin_report import InflowLine, _wallets_by_age

    def w(name: str, first: str | None) -> WalletInfo:
        return WalletInfo(name=name, kind="xpub", address_type="p2wpkh", first=first, last=None, tx_count=0, balance_sats=0)

    rep = HerkunftReport.__new__(HerkunftReport)
    rep.wallets = [w("Neu", "2023-01-01T00:00:00Z"), w("Leer", None), w("Alt", "2020-05-05T00:00:00Z")]
    line = InflowLine.__new__(InflowLine)
    line.date = "2019-02-06"
    assert _wallets_by_age(rep, {"Neu": [line]}) == ["Neu", "Alt", "Leer"]
    assert _wallets_by_age(rep, {}) == ["Alt", "Neu", "Leer"]


def test_build_id_reads_commit_from_git(tmp_path, monkeypatch) -> None:
    import btc_origin.origin_report as orr

    monkeypatch.setattr(orr, "_BUILD_ID", None)
    monkeypatch.setenv("BTC_ORIGIN_COMMIT", "0123456789abcdef")
    assert orr.build_id() == "0123456"


def test_privacy_keeps_names_masks_addresses() -> None:
    from btc_origin.origin_report import MASK, _name

    assert _name("Ledger 2021", True) == "Ledger 2021"
    assert _name("3Commas", True) == "3Commas"
    assert _name("ext-001", True) == "ext-001"
    assert _name("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu", True) == MASK
    assert _name("bc1qcr8t…6fyu", True) == MASK
    assert _name("bc1qcr8t…6fyu", False) == "bc1qcr8t…6fyu"


def test_nacherklaerung_pdf_is_a_draft_with_checklist() -> None:
    import io

    from btc_origin.origin_report import nacherklaerung_years, render_nacherklaerung_pdf
    from btc_origin.year_summary import yearly_summary

    rows = [
        {"direction": "out", "kind": "outflow", "txid": "s", "time": "2024-03-05",
         "lot_date": "2023-06-05", "amount_sats": 10_000_000, "haltefrist_days": 274},
    ]
    prices = {date(2023, 6, 5): 20_000.0, date(2024, 3, 5): 60_000.0}
    years = yearly_summary(rows, prices.get)
    assert nacherklaerung_years(years) == [2024]  # 4.000 € gain ≥ 1.000 € Freigrenze
    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 27, 12, 0),
        stichtag=date(2026, 12, 31),
        wallets=[],
        sources=[],
        years=years,
        lots=[],
        person_name="Max Mustermann",
    )
    data = render_nacherklaerung_pdf(rep, [2024])
    assert data[:4] == b"%PDF"
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf"))
    assert "ENTWURF" in text and "§ 153" in text and "§ 371" in text
    assert "Max Mustermann" in text and "2024" in text


def test_nacherklaerung_only_relevant_years() -> None:
    import io

    from btc_origin.origin_report import render_nacherklaerung_pdf
    from btc_origin.year_summary import yearly_summary

    rows = [
        # 2023: small short-term gain (below the 600 € Freigrenze) → not relevant
        {"direction": "out", "kind": "outflow", "txid": "a", "time": "2023-06-05",
         "lot_date": "2023-01-14", "amount_sats": 100_000, "haltefrist_days": 142},
        # 2024: 4.000 € short-term gain → relevant
        {"direction": "out", "kind": "outflow", "txid": "b", "time": "2024-03-05",
         "lot_date": "2023-06-05", "amount_sats": 10_000_000, "haltefrist_days": 274},
    ]
    prices = {date(2023, 1, 14): 20_000.0, date(2023, 6, 5): 20_000.0 + 1, date(2024, 3, 5): 60_000.0}
    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 27, 12, 0), stichtag=date(2026, 12, 31),
        wallets=[], sources=[], years=yearly_summary(rows, prices.get), lots=[],
    )
    data = render_nacherklaerung_pdf(rep, [2023, 2024])
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf"))
    assert "Einkommensteuer 2024:" in text
    assert "2023, 2024" not in text and "Einkommensteuer 2023" not in text
    assert "600 €" not in text  # no 2023 row (Freigrenze 600 €) in the year table


def test_defunct_platforms_detected_in_names() -> None:
    from btc_origin.origin_report import defunct_platforms_in

    found = [label for label, _ in defunct_platforms_in(["FTX³", "ext-003", "Mt.Gox", "Bitvavo"])]
    assert found == ["FTX", "Mt. Gox"]
    assert defunct_platforms_in(["Bison", "Relai"]) == []


def test_page_footer_explains_markers_used_on_that_page() -> None:
    import io

    from btc_origin.origin_report import InflowLine, LotLine

    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 27, 12, 0), stichtag=date(2026, 12, 31),
        wallets=[], sources=[], years=[],
        lots=[LotLine("2021-01-09", "Bitvavo", "Ledger", 1000, 2000, True, 25_000.0, acq_source="Kauf auf Bitvavo lt. Export")],
        inflows=[InflowLine("Ledger", "2021-01-10", "Bitvavo", "t1", 1000, 1000, 25_000.0, "2021-01-09", "Kauf auf Bitvavo lt. Export")],
    )
    data = render_pdf(rep)
    try:
        import pymupdf
    except ImportError:
        return
    pages = [" ".join(p.get_text().split()) for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf")]
    annex = next(t for t in pages if "Wallet: „Ledger“" in t)
    assert "¹ Anschaffung laut Börsen-Export: Kaufdatum belegt" in annex
    assert all("² Zufluss nach dem Stichtag" not in t for t in pages)  # unused → not listed


def test_tax_free_from_is_day_after_anniversary() -> None:
    from btc_origin.origin_report import tax_free_from

    assert tax_free_from(date(2025, 3, 10)) == date(2026, 3, 11)
    assert tax_free_from(date(2024, 3, 4)) == date(2025, 3, 5)


def test_unclear_open_purchases_section() -> None:
    import io

    from btc_origin.origin_report import LotLine

    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 27, 12, 0), stichtag=date(2026, 12, 31),
        wallets=[], sources=[], years=[],
        lots=[
            LotLine("2026-03-10", "ext-040", "Ledger", 100_000, 296, False, 80_000.0),  # unbelegt, offen
            LotLine("2026-04-01", "Bitvavo", "Ledger", 200_000, 274, False, 80_000.0, acq_source="Kauf auf Bitvavo lt. Export"),
            LotLine("2021-01-09", "ext-001", "Ledger", 300_000, 2186, True, 30_000.0),  # Frist erfüllt
        ],
    )
    data = render_pdf(rep)
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf"))
    sec = text.rsplit("Unklare Transaktionen", 1)[1].split("Anhang A")[0]
    assert "ext-040" in sec and "11.03.2027" in sec
    assert "Bitvavo" not in sec and "ext-001" not in sec


def _reform_report(**kw):
    from btc_origin.origin_report import LotLine

    return HerkunftReport(
        generated_at=datetime(2026, 9, 28, 12, 0), stichtag=date(2026, 12, 31),
        wallets=[WalletInfo("Ledger", "xpub", "bech32", None, None, 3, 600_000, key="xpub6ABC")],
        sources=[], years=[], balance_sats=650_000,
        lots=[
            LotLine("2021-01-09", "ext-001", "Ledger", 300_000, 2186, True, 30_000.0,
                    origin_txid="a" * 64, address=OWN),  # Alt, unbelegt
            LotLine("2026-04-01", "Bitvavo", "Ledger", 300_000, 274, False, 80_000.0,
                    acq_source="Kauf auf Bitvavo lt. Export", origin_txid="b" * 64),  # Alt, belegt
            LotLine("2027-01-02", "ext-002", "Ledger", 50_000, 0, False, 90_000.0),  # Neu
        ],
        **kw,
    )


def _pdf_text(data: bytes) -> str | None:
    import io

    try:
        import pymupdf
    except ImportError:
        return None
    return "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(data), filetype="pdf"))


def test_is_altbestand_cutoff() -> None:
    from btc_origin.origin_report import is_altbestand

    cut = date(2026, 12, 31)  # reform.stichtag_altbestand im Regelwerk
    assert is_altbestand("2026-12-31", cut) and is_altbestand(date(2020, 1, 1), cut)
    assert not is_altbestand("2027-01-01T00:00:00", cut)


def test_herkunft_marks_alt_neu_and_unproven_altbestand() -> None:
    text = _pdf_text(render_pdf(_reform_report()))
    if text is None:
        return
    assert "Heutiger Bestand je Teilbestand" not in text  # Anhang gestrichen
    sec9 = text.split("unbelegte Teilbestände im Altbestand")[1].split("Anhang A")[0]
    assert "ohne Kaufbeleg: 1" in sec9 and "50 %" in sec9
    assert "Ausblick: Kryptosteuer-Reform" in text


def test_altbestand_pdf_lists_only_old_coins() -> None:
    from btc_origin.origin_report import render_altbestand_pdf

    rep = _reform_report(person_name="Erika Muster")
    text = _pdf_text(render_altbestand_pdf(rep))
    if text is None:
        return
    assert "Nachweis Altbestand Bitcoin – Erika Muster" in text
    assert "kein geltendes Recht" in text and "nach dem Stichtag erneut erzeugen" in text
    detail = text.split("Einzelaufstellung je Teilbestand")[1].split("Erläuterungen")[0]
    assert "a" * 20 in detail.replace("\n", "") and OWN[:12] in detail.replace("\n", "")
    assert "ext-002" not in detail
    with_doc, without = detail.split("3.2  Teilbestände ohne Kaufbeleg")
    assert "3.1  Teilbestände mit Kaufbeleg (1)" in with_doc and "Bitvavo (Export)" in with_doc
    assert "ext-001" not in with_doc and "(1)" in without and "ext-001" in without
    assert "xpub6ABC" in text
    masked = _pdf_text(render_altbestand_pdf(_reform_report(privacy=True)))
    assert "xpub6ABC" not in masked and OWN[:12] not in masked.replace("\n", "")


def test_api_altbestand_pdf() -> None:
    with TestClient(app) as c:
        r = c.get("/api/report/altbestand.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert r.headers["X-BTC-Herkunft-Disk-Written"] == "false"


def test_font_subsetting_warnings_are_silenced() -> None:
    import logging

    import btc_origin.origin_report  # noqa: F401

    assert logging.getLogger("fontTools.subset").getEffectiveLevel() >= logging.ERROR


def test_herkunft_has_summary_and_toc() -> None:
    text = _pdf_text(render_pdf(_reform_report()))
    if text is None:
        return
    first, rest = text.split("Inhaltsverzeichnis", 1)
    assert "Zusammenfassung" in first and "Bestand zum Stichtag" in first
    assert "Unbelegt gekauft" in first and "Abstimmung mit der Blockchain" in first
    for gone in ("Altbestand bis 31.12.2026", "Mögliche Gewinne", "Heutiger Bestand nach Nachweis",
                 "Abflüsse an fremde Adressen"):
        assert gone not in first
    toc = rest.split("1  Betrachtete Wallets", 1)[0] + "1  Betrachtete Wallets"
    assert "5  Nicht durch Belege nachgewiesene Transaktionen" in rest
    assert "5.2  " not in text and "Verkäufe ohne Beleg" not in text
    assert "8.1  Unbelegte Teilbestände innerhalb der Haltefrist" in text
    # Einsteiger-Erläuterungen am Ende (Abschnitt 9), mit Lightning
    basics = rest.index("9  Erläuterungen für Leser ohne Bitcoin-Vorkenntnisse", rest.index("8.1  Unbelegte"))
    assert basics > rest.index("7  Methodik und Annahmen", len(toc)) and "Lightning" in rest[basics:]
    assert "Coin" not in text.replace("Coin-Control", "")
    assert toc


def test_summary_names_price_date() -> None:
    from btc_origin.origin_report import summary_rows

    rep = _reform_report(stichtag_price=80_000.0, stichtag_price_day=date(2026, 9, 28),
                         stichtag_balances={"Ledger": 600_000})
    row = next(r for r in summary_rows(rep) if r[0].startswith("Bestand"))
    if rep.generated_at.date() < rep.stichtag:  # Stichtag in der Zukunft (P6)
        assert row[0].startswith(f"Bestand am {rep.generated_at:%d.%m.%Y} (Stichtag 31.12.2026 liegt in der Zukunft")
    else:
        assert row[0] == "Bestand zum Stichtag 31.12.2026"
    assert "480,00 €" in row[1] and "Kurs vom 28.09.2026" in row[1]


def test_past_stichtag_keeps_label() -> None:
    from btc_origin.origin_report import bestand_label

    rep = HerkunftReport(generated_at=datetime(2027, 1, 5), stichtag=date(2026, 12, 31), wallets=[],
                         sources=[], years=[], lots=[])
    assert bestand_label(rep) == "Bestand zum Stichtag 31.12.2026"
    rep.generated_at = datetime(2026, 9, 28)
    assert bestand_label(rep, note=False) == "Bestand am 28.09.2026"
    assert "fortgeschrieben ohne weitere Bewegungen" in bestand_label(rep)


def test_anlage_so_rows_list_every_year() -> None:
    import io

    from btc_origin.origin_report import anlage_so_rows
    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2023, disposals=1)
    y.details.append(dict(exit_date="2023-05-05", disposal=True, short_term=True, btc_sats=1))
    y.short.proceeds_eur, y.short.cost_eur = 900.0, 100.0
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], lots=[],
                         wallets=[WalletInfo("W", "xpub", "p2wpkh", "2021-03-05", "2025-01-01", 3, 0)], years=[y])
    rows = anlage_so_rows(rep)
    assert [r["year"] for r in rows] == [2021, 2022, 2023, 2024, 2025]
    assert rows[0]["note"] == "keine Veräußerung"
    assert rows[2]["count"] == 1 and rows[2]["note"] == "Freigrenze 600 € erreicht"
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf"))
    assert "Jahresübersicht für die Anlage SO" in text and "andere Geschäfte sind hier nicht" in text


def test_annex_b_lists_open_exchange_items() -> None:
    import io

    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[],
                         sources=[], years=[], lots=[], exchange_files=[{"name": "Strike", "file": "s.csv"}],
                         open_exchange=[{"exchange": "Strike", "day": date(2024, 5, 10), "kind": "Auszahlung",
                                         "sats": 50_000, "status": "keinem Zufluss in die Wallets zugeordnet",
                                         "note": "offen"}])
    try:
        import pymupdf
    except ImportError:
        return
    doc = pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf")
    assert "Anhang B  Nicht zugeordnete Börsenvorgänge" in [x[1] for x in doc.get_toc()]
    text = "".join(p.get_text() for p in doc)
    assert "1 Vorgang/Vorgänge ohne Erläuterung" in text


def test_inflow_without_receipt_gets_explanation() -> None:
    import io

    from btc_origin.origin_report import InflowLine, inflow_explanation

    rules = [("Bitvavo", None, "Zufluss", "Altes Konto, Abrechnung angefordert"),
             ("Bitvavo", None, "Auszahlung", "nicht für Zuflüsse")]
    x = InflowLine("Ledger 2024", "2024-02-06", "Bitvavo", "t1", 1000, 1000, 40_000.0)
    assert inflow_explanation(rules, x) == "Altes Konto, Abrechnung angefordert"
    assert inflow_explanation(rules, InflowLine("W", "2024-02-06", "FTX³", "t2", 1, 1, 1.0)) == ""
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[], sources=[],
                         years=[], lots=[], inflows=[x], explanations=rules)
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf"))
    sec6 = " ".join(text.split("Nicht durch Belege nachgewiesene Transaktionen")[-1].split())
    assert "Erläuterung" in sec6 and "Abrechnung angefordert" in sec6


def test_quantity_check_shows_utxo_control_row_with_tick() -> None:
    import io

    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[], sources=[],
                         years=[], lots=[], inflow_sats=0, balance_sats=0, chain_balance_sats=0,
                         disposal_fees_sats=0, transfer_fees_sats=0)
    try:
        import pymupdf
    except ImportError:
        return
    text = "".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf"))
    text = " ".join(text.split())
    assert "= Bestand am 28.09.2026" in text and "Kontrolle: unverbrauchte Einzelbeträge (UTXO) ✓" in text
    assert "laut Blockchain — stimmt überein" in text
    assert "Die Rechnung geht auf" in text


def test_exchange_only_sale_listed_and_counted_as_one_deal() -> None:
    import io

    from btc_origin.origin_report import anlage_so_rows, summary_rows
    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2022, disposals=3)
    base = dict(acquisition_date="2022-03-23", days_held=0, short_term=True, btc_sats=100, fee_sats=0,
                proceeds_eur=10.0, cost_eur=9.0, fee_eur=0.1, gain_eur=0.9, disposal=True)
    y.details += [
        {**base, "exit_date": "2022-03-23", "counterparty": "Binance", "origin": "exchange",
         "status": "Verkauf am 23.03.2022 lt. Export"},
        {**base, "exit_date": "2022-05-05", "txid": "t1", "counterparty": "FTX", "origin": "wallet"},
        {**base, "exit_date": "2022-05-05", "txid": "t1", "counterparty": "FTX", "origin": "wallet"},
    ]
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], lots=[],
                         wallets=[WalletInfo("W", "xpub", "p2wpkh", "2022-01-01", "2022-06-05", 3, 0)], years=[y],
                         open_exchange=[{"exchange": "Relai", "day": date(2024, 4, 9), "kind": "Kauf", "sats": 1,
                                         "status": "x", "note": "Verbleib wird geklärt"},
                                        {"exchange": "Strike", "day": date(2024, 4, 28), "kind": "Einzahlung",
                                         "sats": 1, "status": "x", "note": "Lightning-Eingang"}])
    assert anlage_so_rows(rep)[0]["count"] == 2  # 2 Geschäfte, 3 Teilbestände
    row = next(r for r in summary_rows(rep) if r[0] == "Nicht zugeordnete Börsenvorgänge")
    assert row[1] == "2, davon 1 offen bzw. in Klärung"
    try:
        import pymupdf
    except ImportError:
        return
    doc = pymupdf.open(stream=io.BytesIO(render_pdf(rep)), filetype="pdf")
    toc = [x[1] for x in doc.get_toc()]
    assert "3.1  Veräußerungen auf Börsen ohne Abfluss aus den Wallets" in toc
    assert "3.2  Jahresübersicht für die Anlage SO" in toc
    text = " ".join("".join(p.get_text() for p in doc).split())
    assert "auf der Börse gekauft ¹" in text and "einzeln in 3.1" in text


def test_base_name_ignores_account_suffix() -> None:
    from btc_origin.origin_report import InflowLine, inflow_explanation

    rules = [("Bitvavo", None, "Zufluss", "altes Konto")]
    assert inflow_explanation(rules, InflowLine("W", "2024-02-06", "bitvavo-alt³", "t", 1, 1, 1.0)) == "altes Konto"


def test_satoshi_test_marked_in_chapter_3() -> None:
    import io
    from datetime import date as _date

    from btc_origin.origin_report import wallet_reconciliation
    from btc_origin.year_summary import YearSummary

    rows = [
        dict(txid="in1", wallet_id=1, direction="in", amount_sats=500_000, block_time="2023-02-05"),
        dict(txid="st", wallet_id=1, direction="out", amount_sats=500_000, is_internal=0,
             external_amount_sats=12_000, fee_sats=500, block_time="2025-03-14"),
        dict(txid="st", wallet_id=1, direction="in", amount_sats=487_500, is_internal=1, block_time="2025-03-14"),
    ]
    rec = wallet_reconciliation(rows, {1: "W"}, _date(2026, 12, 31))
    y = YearSummary(year=2025)
    y.details.append(dict(exit_date="2025-03-14", acquisition_date="2023-02-05", days_held=None, short_term=False,
                          btc_sats=12_000, fee_sats=500, counterparty="Kraken", txid="st", origin="wallet",
                          status="Satoshi-Test (Nachweis der Wallet-Inhaberschaft, VO (EU) 2023/1113), wieder "
                                 "ausgezahlt am 15.03.2025 lt. Export", disposal=False))
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], lots=[],
                         wallets=[WalletInfo("W", "xpub", "p2wpkh", "2023-02-05", None, 2, 0)], years=[y],
                         recon=rec, stichtag_balances={"W": rec["W"].computed})
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                               filetype="pdf")).split())
    # Fußnoten fortlaufend: hier nur ¹ (Anschaffung laut Export) und Satoshi-Test → ¹ und ² (nicht ⁶)
    assert "keine — Satoshi-Test ²" in text and "² Satoshi-Test: kleiner Betrag aus der eigenen Wallet" in text
    assert set(re.findall(r"[¹²³⁴⁵⁶]", text)) == {"¹", "²"}
    # unbenutzte Fußnoten fallen weg — keine zweite „¹“ bzw. „²“ mit anderem Text im Fuß
    assert "Zufluss nach dem Stichtag" not in text
    assert "Zuordnung nach Angabe des Steuerpflichtigen" not in text
    assert "keine Veräußerung, keine Werbungskosten" in text


def test_exchange_sale_with_two_lots_is_one_deal_in_4_1() -> None:
    import io

    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2022, disposals=2)
    base = dict(exit_date="2022-03-25", days_held=0, short_term=True, fee_sats=0, cost_eur=10.0, fee_eur=0.0,
                disposal=True, counterparty="Binance", origin="exchange", status="Verkauf am 25.03.2022 lt. Export",
                proceeds_estimated=True)
    y.details += [{**base, "acquisition_date": "2022-03-24", "btc_sats": 100, "proceeds_eur": 11.0, "gain_eur": 1.0},
                  {**base, "acquisition_date": "2022-03-25", "btc_sats": 200, "proceeds_eur": 22.0, "gain_eur": 12.0}]
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], lots=[],
                         wallets=[WalletInfo("W", "xpub", "p2wpkh", "2022-01-01", "2022-06-05", 1, 0)], years=[y])
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                               filetype="pdf")).split())
    sec = text.split("4.1 Veräußerungen auf Börsen ohne Abfluss aus den Wallets")[-1]
    assert "1 Verkauf, 2 Teilbestände" in sec and "33,00 € (Tageskurs)" in sec and "↳" in sec
    assert "fehlt ein Kurs" not in text  # kein Fall ohne Kurs → keine Sternchen-Legende


def test_linked_withdrawal_note_explains_inflow_in_section_6() -> None:
    import io

    from btc_origin.origin_report import InflowLine

    x = InflowLine("Ledger 2024", "2024-08-09", "Strike", "tx1", 1000, 1000, 50_000.0)
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[], sources=[],
                         years=[], lots=[], inflows=[x],
                         open_exchange=[{"exchange": "Strike", "day": date(2024, 8, 9), "kind": "Auszahlung",
                                         "sats": 1000, "status": "Zufluss …", "note": "Lightning-Test",
                                         "entry_txid": "tx1"}])
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                               filetype="pdf")).split())
    sec6 = text.split("Nicht durch Belege nachgewiesene Transaktionen")[-1]
    assert "Erläuterung" in sec6 and "Lightning-Test" in sec6


def test_withdrawal_rest_without_receipt_is_shown_not_a_deviation() -> None:
    """Auszahlung größer als die Käufe davor: Rest „ohne Kaufbeleg“ in Abschnitt 3, kein ✗."""
    import io

    from btc_origin.origin_report import InflowLine, deviations

    rep = HerkunftReport(
        generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], years=[], lots=[],
        wallets=[WalletInfo("W", "xpub", "p2wpkh", "2024-03-05T12:00:00Z", None, 1, 150_000)],
        inflows=[
            InflowLine("W", "2024-03-05", "Strike", "t1", 100_000, 100_000, 60_000.0, "2024-03-04",
                       "Kauf auf Strike lt. Export"),
            InflowLine("W", "2024-03-05", "Strike", "t1", 50_000, 50_000, 61_000.0, "2024-03-05", ""),
        ],
        inflow_sats=150_000, balance_sats=150_000, chain_balance_sats=150_000,
        disposal_fees_sats=0, transfer_fees_sats=0,
    )
    rep.withdrawal_recon = {"t1": {
        "exchange": "Strike", "day": "2024-03-04", "export_sats": 150_000,
        "pieces": [{"day": "2024-03-04", "sats": 100_000, "price_eur": 60_000.0, "source": "Kauf auf Strike lt. Export"},
                   {"day": "2024-03-05", "sats": 50_000, "price_eur": 61_000.0, "source": ""}],
        "bought_sats": 150_000, "fee_sats": 0, "fee_on_top": False, "rest_sats": 0,
        "received_sats": 150_000, "ok": True,
    }}
    assert deviations(rep) == []
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                                filetype="pdf")).split())
    assert "1 Kauf ¹ + Rest ohne Kaufbeleg" in text
    assert "↳ ohne Kaufbeleg 05.03.2024 im Export kein Kauf davor" in text
    assert "Rückzahlung" not in text


def test_assumed_disposals_listed_with_explanation() -> None:
    """Abflüsse ohne Verkaufsbeleg (Veräußerung angenommen): eigene Liste in Abschnitt 6 mit
    Erläuterung aus erlaeuterungen.csv (Art „Abfluss“), Zeile in der Zusammenfassung."""
    import io

    from btc_origin.origin_report import assumed_disposals, summary_rows
    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2024)
    base = dict(acquisition_date="2023-01-10", days_held=500, short_term=False, fee_sats=0, cost_eur=10.0,
                fee_eur=0.0, origin="wallet", status="", disposal=True)
    y.details += [
        dict(base, exit_date="2024-05-02", btc_sats=100_000, proceeds_eur=60.0, gain_eur=50.0,
             counterparty="ext-007", txid="t1"),
        dict(base, exit_date="2024-05-02", btc_sats=50_000, proceeds_eur=30.0, gain_eur=20.0,
             counterparty="ext-007", txid="t1"),
        dict(base, exit_date="2024-06-03", btc_sats=20_000, proceeds_eur=12.0, gain_eur=2.0,
             counterparty="Kraken", txid="t2", status="Verkauf am 03.06.2024 lt. Export"),
    ]
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[],
                         sources=[], years=[y], lots=[],
                         explanations=[("ext-007", None, "Abfluss", "Zahlung an Handwerker")])
    (g,) = assumed_disposals(rep)
    assert (g["day"], g["counterparty"], g["sats"], g["proceeds"], g["note"]) == (
        "2024-05-02", "ext-007", 150_000, 90.0, "Zahlung an Handwerker")
    row = next(r for r in summary_rows(rep) if r[0].startswith("Abflüsse ohne Verkaufsbeleg"))
    assert row[1:] == ["1, alle mit Erläuterung", "5"]
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                               filetype="pdf")).split())
    assert "Abflüsse ohne Verkaufsbeleg (Veräußerung angenommen)" in text
    assert "Zahlung an Handwerker" in text


def test_explanations_for_ext_names_do_not_bleed_into_each_other() -> None:
    from btc_origin.origin_report import InflowLine, _base_name, inflow_explanation, outflow_explanation

    assert _base_name("ext-012") == "ext-012" and _base_name("ext-018³") == "ext-018"
    rules = [("ext-012", None, "Zufluss", "Text A"), ("ext-018", None, "Abfluss", "Text B")]
    assert inflow_explanation(rules, InflowLine("W", "2024-01-16", "ext-018", "t", 1, 1, 1.0)) == ""
    assert inflow_explanation(rules, InflowLine("W", "2024-01-16", "ext-012", "t", 1, 1, 1.0)) == "Text A"
    assert outflow_explanation(rules, "ext-018", "2024-06-07") == "Text B"
    assert outflow_explanation(rules, "ext-012", "2024-06-07") == ""


def test_renumbered_footnotes_match_text_and_footer() -> None:
    """¹, ³ und ⁶ verwendet (² nicht) → ¹ ² ³; Text und Seitenfuß tragen dieselbe Nummer."""
    import io

    from btc_origin.origin_report import InflowLine, wallet_reconciliation
    from btc_origin.year_summary import YearSummary

    rows = [
        dict(txid="in1", wallet_id=1, direction="in", amount_sats=500_000, block_time="2023-02-05"),
        dict(txid="st", wallet_id=1, direction="out", amount_sats=500_000, is_internal=0,
             external_amount_sats=12_000, fee_sats=500, block_time="2025-03-14"),
        dict(txid="st", wallet_id=1, direction="in", amount_sats=487_500, is_internal=1, block_time="2025-03-14"),
    ]
    rec = wallet_reconciliation(rows, {1: "W"}, date(2026, 12, 31))
    y = YearSummary(year=2025)
    y.details.append(dict(exit_date="2025-03-14", acquisition_date="2023-02-05", days_held=None, short_term=False,
                          btc_sats=12_000, fee_sats=500, counterparty="Kraken", txid="st", origin="wallet",
                          status="Satoshi-Test (Nachweis der Wallet-Inhaberschaft), wieder ausgezahlt", disposal=False))
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], lots=[],
                         wallets=[WalletInfo("W", "xpub", "p2wpkh", "2023-02-05", None, 2, 0)], years=[y],
                         recon=rec, stichtag_balances={"W": rec["W"].computed},
                         inflows=[InflowLine("W", "2023-02-05", "FTX³", "in1", 500_000, 487_500, 1.0, "2023-02-01")])
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                               filetype="pdf")).split())
    assert "Satoshi-Test ³" in text and "³ Satoshi-Test: kleiner Betrag" in text
    assert "FTX²" in text and "² Zuordnung der Gegenstelle nach Angabe" in text
    assert "² Satoshi-Test:" not in text and "⁶" not in text


def test_exchange_account_without_matching_export_is_explained() -> None:
    import io

    from btc_origin.exchange_sales import ExchangeReport
    from btc_origin.year_summary import YearSummary

    y = YearSummary(year=2024)
    y.details.append(dict(exit_date="2024-05-02", acquisition_date="2023-01-10", days_held=478, short_term=False,
                          fee_sats=0, cost_eur=10.0, fee_eur=0.0, origin="wallet", status="", disposal=True,
                          btc_sats=100_000, proceeds_eur=60.0, gain_eur=50.0, counterparty="Kraken³", txid="t1"))
    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[],
                         sources=[], years=[y], lots=[], exchange_reports=[ExchangeReport("Kraken")])
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                               filetype="pdf")).split())
    assert "Börsenkonten ohne passenden Export: Abflüsse an Kraken stehen in keinem" in text
    assert "Einzahlung auf ein eigenes Börsenkonto ist selbst keine Veräußerung" in text


def test_declared_rules_only_in_methodology_and_inflow_note() -> None:
    import io

    from btc_origin.origin_report import InflowLine

    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], years=[],
                         lots=[], wallets=[WalletInfo("W", "xpub", "p2wpkh", "2022-03-01", None, 1, 100_000)],
                         inflows=[InflowLine("W", "2022-03-01", "FTX³", "t1", 100_000, 100_000, 1.0)],
                         declared_rules=[("FTX", "2022-11-07")])
    try:
        import pymupdf
    except ImportError:
        return
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(render_pdf(rep)),
                                                               filetype="pdf")).split())
    assert "belegt: unbenannte Gegenstellen" not in text  # alte Fußnotenfassung am Ende von Abschnitt 3
    assert "oder Datumsregel (unbenannte Gegenstellen mit Zu- und Abflüssen bis 07.11.2022 = FTX)" in text
    assert "ohne Nachweis des ursprünglichen Kaufs gilt vorsichtshalber der Zuflusstag als Anschaffung" in text


def test_fallback_glyph_at_page_start_keeps_wrapped_text_readable(tmp_path, monkeypatch) -> None:
    """Grundschrift ohne „↳“ (wie Segoe UI unter Windows): Steht eine umbrechende
    „↳ Rückzahlung, angeschafft …“-Zeile direkt nach einem Seitenumbruch, darf der
    umbrochene Rest nicht in der falschen Schrift (Zeichensalat) erscheinen."""
    import io
    from pathlib import Path

    pymupdf = pytest.importorskip("pymupdf")
    from fontTools.ttLib import TTFont

    import btc_origin.origin_report as o
    from btc_origin.origin_report import InflowLine

    dejavu = Path("/usr/share/fonts/truetype/dejavu")
    if not (dejavu / "DejaVuSans.ttf").is_file() or not (dejavu / "DejaVuSans-Bold.ttf").is_file():
        pytest.skip("DejaVu nicht installiert")
    body = []
    for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        font = TTFont(dejavu / name)
        for table in font["cmap"].tables:
            table.cmap.pop(0x21B3, None)  # „↳“ fehlt in der Grundschrift
        font.save(tmp_path / name)
        body.append(str(tmp_path / name))
    monkeypatch.setattr(o, "_FONT_CANDIDATES", [tuple(body)])
    monkeypatch.setattr(o, "_SYMBOL_FONT_CANDIDATES", [str(dejavu / "DejaVuSans.ttf")])

    def report(k: int) -> HerkunftReport:
        fill = [InflowLine("W", f"2025-0{1 + i // 28}-{1 + i % 28:02d}", "Strike", f"f{i}", 10_000, 10_000,
                           60_000.0, "", "") for i in range(k)]
        rep = HerkunftReport(
            generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), sources=[], years=[], lots=[],
            wallets=[WalletInfo("W", "xpub", "p2wpkh", "2025-01-01T12:00:00Z", None, 1, 150_000)],
            inflows=fill + [
                InflowLine("W", "2025-09-25", "Coinbase", "t1", 100_000, 100_000, 60_000.0, "2025-09-20",
                           "Kauf auf Coinbase lt. Export"),
                InflowLine("W", "2025-09-25", "Coinbase", "t1", 50_000, 50_000, 60_000.0, "2025-04-22", ""),
            ],
            inflow_sats=150_000, balance_sats=150_000, chain_balance_sats=150_000,
            disposal_fees_sats=0, transfer_fees_sats=0, fassung="finanzamt",
        )
        rep.withdrawal_recon = {"t1": {
            "exchange": "Coinbase", "day": "2025-09-25", "export_sats": 150_000,
            "pieces": [{"day": "2025-04-22", "sats": 50_000, "price_eur": 60_000.0,
                        "source": "Satoshi-Test, zurück ausgezahlt"},
                       {"day": "2025-09-20", "sats": 100_000, "price_eur": 60_000.0,
                        "source": "Kauf auf Coinbase lt. Export"}],
            "bought_sats": 150_000, "fee_sats": 0, "fee_on_top": False, "rest_sats": 0,
            "received_sats": 150_000, "ok": True,
        }}
        return rep

    at_page_start = 0
    for k in range(46, 55):  # Seitenumbruch direkt vor der Zeile bei k = 49…51 (DejaVu)
        for page in pymupdf.open(stream=io.BytesIO(render_pdf(report(k))), filetype="pdf"):
            words = page.get_text().split()
            if "Rückzahlung," not in words:
                continue
            i = words.index("Rückzahlung,")
            assert words[i + 1 : i + 3] == ["angeschafft", "22.04.2025"], words[i - 3 : i + 3]
            at_page_start += "Tx" in words[:i] and "T-" not in " ".join(words[:i])
    assert at_page_start  # mindestens ein Fall direkt nach dem Seitenumbruch geprüft
