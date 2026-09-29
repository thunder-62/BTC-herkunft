"""Jahressteuerreport — Datenbasis: Jahresfilter, Veräußerungsblöcke, Anschaffungen,
Einkünfte nach § 22 Nr. 3 EStG (aus kategorien.yaml), sonstige Bewegungen, Mengenabstimmung, steuer.csv."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.exchange_sales import SATOSHI_TEST_STATUS
from btc_origin.merger import Ledger
from btc_origin.origin_report import HerkunftReport, InflowLine, build_tx_refs
from btc_origin.price_oracle import PriceOracle
from btc_origin.tax_year import TaxConfig, build_tax_year, parse_tax_config
from btc_origin.tx_ingestor import Flow
from btc_origin.year_summary import yearly_summary

PX = 60_000.0
NAMES = {1: "Wallet A", 2: "Wallet B", 3: "Wallet C"}
SALE = "Verkauf am 01.07.2025 lt. Export"


def price(_day: date) -> float:
    return PX


def test_parse_tax_config() -> None:
    text = (
        "# weitere Angaben zum Jahressteuerreport\n"
        "Jahr;Art;Betrag\n"
        "2025;Weitere Veräußerungsgeschäfte;-1.250,00 €\n"
        "2024,Weitere Veräußerungsgeschäfte,300\n"
        "Altbestand bis;31.12.2027\n"
        "Unsinn;x\n"
        "2023;Weitere Veräußerungsgeschäfte;viel\n"
    )
    cfg, errors = parse_tax_config(text)
    assert cfg.other_sales == {2025: -1250.0, 2024: 300.0}
    # „Altbestand bis“ steht jetzt im Regelwerk (reform.stichtag_altbestand)
    assert len(errors) == 3 and "Regelwerk" in errors[0] and "Zeile 5" in errors[1] and "viel" in errors[2]


def _rows() -> list[dict]:
    """Rohdaten: Wallet A kauft 2023 und 2025 und gibt 2025 ab; Wallet B bucht 2025 auf C um."""
    t = "T10:05:00Z"
    return [
        {"txid": "t0", "direction": "in", "wallet_id": 1, "amount_sats": 2_000_000, "block_time": "2023-01-01" + t},
        {"txid": "t5", "direction": "in", "wallet_id": 2, "amount_sats": 4_000_000, "block_time": "2024-05-01" + t},
        {"txid": "t1", "direction": "in", "wallet_id": 1, "amount_sats": 1_010_000, "block_time": "2025-01-01" + t},
        {"txid": "u1", "direction": "out", "wallet_id": 2, "amount_sats": 4_000_000, "fee_sats": 1_000,
         "is_internal": True, "block_time": "2025-03-01" + t},
        {"txid": "u1", "direction": "in", "wallet_id": 3, "amount_sats": 3_999_000, "block_time": "2025-03-01" + t},
        {"txid": "t2", "direction": "out", "wallet_id": 1, "amount_sats": 3_010_000, "fee_sats": 10_000,
         "external_amount_sats": 3_000_000, "block_time": "2025-06-01" + t},
        {"txid": "t9", "direction": "in", "wallet_id": 3, "amount_sats": 1_000_000, "block_time": "2026-02-01" + t},
    ]


def _outs() -> list[dict]:
    """Abflüsse wie von replay_exchanges: angenommene Veräußerung t2 (zwei Teilbestände),
    Verkauf lt. Export (Einzahlung t3 + Kauf auf der Börse), Satoshi-Test t4, Vorjahr."""
    return [
        {"direction": "out", "txid": "t2", "time": "2025-06-01", "lot_date": "2023-01-01", "amount_sats": 2_000_000,
         "external_name": "ext-001", "origin_txid": "t0"},
        {"direction": "out", "txid": "t2", "time": "2025-06-01", "lot_date": "2025-01-01", "amount_sats": 1_000_000,
         "external_name": "ext-001", "origin_txid": "t1"},
        {"direction": "out", "kind": "fee", "txid": "t2", "time": "2025-06-01", "amount_sats": 10_000},
        {"direction": "out", "kind": "exchange_sale", "txid": "t3", "origin": "wallet", "time": "2025-07-01",
         "lot_date": "2024-12-01", "amount_sats": 500_000, "external_name": "Bison", "status": SALE,
         "deposit_date": "2025-06-20", "origin_txid": "t5", "fee_sats": 1_000, "proceeds_eur": 350.0, "fee_eur": 1.0},
        {"direction": "out", "kind": "exchange_sale", "origin": "exchange", "time": "2025-07-01",
         "lot_date": "2025-06-25", "amount_sats": 200_000, "external_name": "Bison", "status": SALE,
         "fee_sats": 0, "proceeds_eur": 140.0, "fee_eur": 0.5, "acq_price_eur": 50_000.0,
         "acq_source": "Kauf auf Bison lt. Export"},
        {"direction": "out", "kind": "exchange_held", "txid": "t4", "origin": "wallet", "time": "2025-02-01",
         "lot_date": "2023-01-01", "amount_sats": 10_000, "external_name": "Bison", "fee_sats": 1_000,
         "status": SATOSHI_TEST_STATUS + ", wieder ausgezahlt am 03.02.2025 lt. Export", "not_disposal": True,
         "deposit_date": "2025-02-01"},
        {"direction": "out", "txid": "t8", "time": "2024-03-01", "lot_date": "2023-01-01", "amount_sats": 100_000,
         "external_name": "ext-009", "origin_txid": "t0"},
    ]


def _inflow(txid: str, day: str, sats: int, source: str = "ext-002", acq: str = "", acq_source: str = "",
            px: float | None = PX) -> InflowLine:
    return InflowLine(wallet="Wallet A", date=day, source=source, txid=txid, sats=sats, remaining_sats=0,
                      price_eur=px, acquisition=acq or day, acq_source=acq_source)


def _report() -> HerkunftReport:
    relai = "Kauf auf Relai lt. Export"
    return HerkunftReport(
        generated_at=datetime(2026, 1, 10, 10, 5),
        stichtag=date(2025, 12, 31),
        until=date(2025, 12, 31),
        wallets=[],
        sources=[],
        lots=[],
        years=yearly_summary(_outs(), price),
        transactions=build_tx_refs(_rows(), NAMES, {"t0", "t1", "t5", "t9"}, {"t2"}),
        stichtag_balances={"Wallet A": 0, "Wallet B": 0, "Wallet C": 3_999_000},
        inflows=[
            _inflow("t1", "2025-01-01", 1_010_000),
            _inflow("t7", "2025-01-05", 100_000, "Relai", "2024-12-30", relai, 40_000.0),  # Kauf im Vorjahr
            _inflow("t6", "2025-08-01", 100_000, "Relai", "2025-07-30", relai, 50_000.0),
            _inflow("t6", "2025-08-01", 100_000, "Relai", "2025-07-31", relai, 50_000.0),
            _inflow("t10", "2025-10-01", 20_000, "ext-003"),
        ],
        withdrawal_recon={"t6": {"exchange": "Relai", "pieces": [
            {"day": "2025-07-30", "sats": 100_000, "source": relai},
            {"day": "2025-07-31", "sats": 100_000, "source": relai},
        ]}},
        exchange_trades=[
            {"exchange": "Relai", "day": "2025-07-30", "kind": "Kauf", "sats": 100_000, "eur": 50.0},
            {"exchange": "Relai", "day": "2025-07-31", "kind": "Kauf", "sats": 100_000, "eur": 50.0},
            {"exchange": "Bison", "day": "2025-06-25", "kind": "Kauf", "sats": 200_000, "eur": 100.0},
            # Export-Art „Referral“ in kategorien.yaml → empfehlung (einkunft_22_3)
            {"exchange": "Bison", "day": "2025-09-01", "kind": "Kauf", "sats": 10_000, "eur": None,
             "art": "Referral", "kategorie": "empfehlung", "einkunft": True},
        ],
        # zuordnung_manuell (local/kategorien.yaml): Zufluss t10 → cashback
        manual_categories={"t10": {"tnr": "T-009", "kategorie": "cashback", "behandlung": "einkunft_22_3",
                                   "erlaeuterung": "Cashback auf Einkäufe"}},
        explanations=[
            ("ext-001", None, "Abfluss", "Zahlung an Dritte"),
        ],
        open_exchange=[
            {"exchange": "Bison", "day": date(2025, 3, 3), "kind": "Auszahlung", "sats": 10_000, "note": "offen"},
            {"exchange": "Bison", "day": date(2024, 3, 3), "kind": "Auszahlung", "sats": 10_000, "note": "offen"},
        ],
    )


def _tax(**kw):
    kw.setdefault("config", TaxConfig(other_sales={2025: -100.0}))
    return build_tax_year(_report(), 2025, rows=_rows(), wallet_names=NAMES, price_eur=price,
                          today=date(2026, 9, 29), **kw)


def test_disposals_grouped_per_transaction_and_sale() -> None:
    tax = _tax()
    out, sale = tax.disposals
    assert (out.day, out.txid, out.assumed, out.counterparty, out.sats, out.fee_sats) == (
        "2025-06-01", "t2", True, "ext-001", 3_000_000, 10_000)
    assert out.note == "Zahlung an Dritte" and out.proceeds_estimated
    assert [(x.acquisition, x.taxable, x.origin_txid) for x in out.lots] == [
        ("2023-01-01", False, "t0"), ("2025-01-01", True, "t1")]
    # Gebühr anteilig nach Menge: 2/3 bzw. 1/3 von 10.000 sats
    assert [x.fee_sats for x in out.lots] == [6_666, 3_334]
    assert out.has_taxable and out.has_free
    # Verkauf lt. Export: ein Block, Teilbestände aus Einzahlung und Kauf auf der Börse
    assert (sale.art, sale.txid, sale.assumed, sale.proceeds_estimated) == ("Verkauf lt. Export", "", False, False)
    assert [(x.origin, x.beleg, x.origin_txid) for x in sale.lots] == [
        ("wallet", "Tageskurs", "t5"), ("exchange", "Export", "")]
    assert sale.proceeds_eur == pytest.approx(490.0) and sale.fee_eur == pytest.approx(1.5)
    assert tax.lot_in_year(sale.lots[1]) and not tax.lot_in_year(sale.lots[0])


def test_year_totals_match_herkunftsnachweis() -> None:
    tax = _tax()
    assert tax.summary is not None
    short = tax.summary.short
    assert tax.total(True, "gain_eur") == pytest.approx(short.gain_eur, abs=0.01)
    assert tax.total(True, "proceeds_eur") == pytest.approx(short.proceeds_eur, abs=0.01)
    assert tax.total(True, "cost_eur") == pytest.approx(short.cost_eur, abs=0.01)
    assert tax.sats(False) == tax.summary.long.btc_sats == 2_000_000
    assert (tax.count(True), tax.count(False)) == (2, 1)
    assert tax.freigrenze_eur == 1000 and tax.other_sales_given
    assert tax.gain_total == pytest.approx(tax.gain_taxable - 100.0)
    # nichts aus 2024 (Abfluss t8) im Jahr 2025
    assert all(d.day.startswith("2025") for d in tax.disposals)
    assert build_tax_year(_report(), 2024, price_eur=price).disposals[0].txid == "t8"


def test_loss_and_freigrenze() -> None:
    tax = _tax(config=TaxConfig(other_sales={2025: -1_000_000.0}))
    assert tax.loss and tax.freigrenze_reached is False
    tax = _tax(config=TaxConfig(other_sales={2025: 1_000_000.0}))
    assert not tax.loss and tax.freigrenze_reached is True
    tax = _tax(config=TaxConfig())
    assert tax.other_sales_eur == 0.0 and not tax.other_sales_given


def test_income_from_categories_only() -> None:
    tax = _tax()
    assert [(i.day, i.category, i.source, i.sats, i.note) for i in tax.income] == [
        ("2025-09-01", "empfehlung", "Bison", 10_000, "Art laut Export „Referral“"),
        ("2025-10-01", "cashback", "ext-003", 20_000, "Cashback auf Einkäufe"),
    ]
    assert tax.income_total == pytest.approx(18.0) and tax.income_freigrenze_reached is False
    # ohne Einordnung keine Einkunft — auch nicht aus Erläuterungstexten
    rep = _report()
    rep.manual_categories = {}
    rep.exchange_trades = [t for t in rep.exchange_trades if not t.get("einkunft")]
    rep.explanations = [("Bison", date(2025, 9, 1), "Einzahlung", "Bonus")]
    assert build_tax_year(rep, 2025, price_eur=price).income == []


def test_acquisitions_of_the_year() -> None:
    tax = _tax()
    rows = [(a.day, a.wallet, a.txid, a.sats, a.count, a.category, a.beleg) for a in tax.acquisitions]
    assert rows == [
        ("2025-01-01", "Wallet A", "t1", 1_010_000, 1, "", "Tageskurs"),
        ("2025-06-25", "Börse Bison", "", 200_000, 1, "", "Kauf auf Bison lt. Export"),
        ("2025-07-30", "Wallet A", "t6", 200_000, 2, "", "Kauf auf Relai lt. Export"),
        ("2025-09-01", "Börse Bison", "", 10_000, 1, "empfehlung", "Tageskurs"),
        ("2025-10-01", "Wallet A", "t10", 20_000, 1, "cashback", "Tageskurs"),
    ]
    by_tx = {a.txid: a for a in tax.acquisitions if a.txid}
    assert by_tx["t6"].cost_eur == pytest.approx(100.0) and by_tx["t6"].holding_end == "2026-07-31"
    assert by_tx["t10"].cost_eur == pytest.approx(12.0)  # Einkunft § 22 Nr. 3 zum Tageskurs
    assert "t7" not in by_tx  # Kauf im Vorjahr, 2025 nur ausgezahlt


def test_other_movements() -> None:
    tax = _tax()
    assert [(m.kind, m.day, m.txid, m.sats, m.fee_sats) for m in tax.movements] == [
        ("Satoshi-Test", "2025-02-01", "t4", 10_000, 1_000),
        ("Umbuchung", "2025-03-01", "u1", 3_999_000, 1_000),
        ("Einzahlung auf eigenes Börsenkonto", "2025-06-20", "t3", 500_000, 1_000),
    ]
    assert tax.movements[1].detail == "Wallet B → Wallet C"


def test_quantity_recon_and_wallet_activity() -> None:
    tax = _tax()
    r = tax.recon
    assert r is not None
    assert (r.opening, r.inflows, r.outflows, r.fees, r.closing) == (6_000_000, 1_010_000, 3_000_000, 11_000, 3_999_000)
    assert r.ok and r.check_sats == 3_999_000 and "31.12." in r.check_label
    assert [(w.name, w.first, w.last, w.tx_count) for w in tax.wallets] == [
        ("Wallet A", "2025-01-01", "2025-06-01", 2),
        ("Wallet B", "2025-03-01", "2025-03-01", 1),
        ("Wallet C", "2025-03-01", "2025-03-01", 1),
    ]


def test_open_points_and_refs() -> None:
    tax = _tax()
    assert [x.txid for x in tax.unproven_inflows] == ["t1"]  # t10 ist Einkunft (Cashback) → kein offener Punkt
    assert [d.txid for d in tax.assumed_disposals] == ["t2"]
    assert [r["day"] for r in tax.open_exchange] == [date(2025, 3, 3)]
    assert tax.ref("t2") == "T-005" and tax.ref("t0") == "T-001"


def test_reform_flag_from_rules(tmp_path, monkeypatch) -> None:
    import os
    import shutil

    tax = _tax()
    assert not tax.show_reform and all(x.altbestand for d in tax.disposals for x in d.lots)
    assert tax.freigrenze_22_3_eur == 256 and tax.regelwerk is not None
    # Stichtag Altbestand kommt aus regeln/2025.yaml
    root = tmp_path / "btc-regeln"
    shutil.copytree(os.environ["BTC_ORIGIN_RULES_DIR"], root)
    y = root / "regeln" / "2025.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("stichtag_altbestand: 2026-12-31",
                                                       "stichtag_altbestand: 2025-06-20"), encoding="utf-8")
    monkeypatch.setenv("BTC_ORIGIN_RULES_DIR", str(root))
    tax = _tax()
    sale = tax.disposals[1]
    assert [x.altbestand for x in sale.lots] == [True, False]
    assert tax.show_reform  # Veranlagungsjahr nach dem Stichtag


# ---------------------------------------------------------------------------
# Über die Sitzung: derselbe Herkunftsnachweis bis 31.12. des Jahres
# ---------------------------------------------------------------------------

OWN = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
DEST = "3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"
BUY, SELL, KEEP, LATE = "1a" * 32, "2b" * 32, "3c" * 32, "4d" * 32


@pytest.fixture()
def state():
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
        yield state


def test_session_tax_year_matches_herkunftsnachweis(state) -> None:
    from btc_origin.api.routes.report import _tax_year

    tax = _tax_year(state, 2022)
    (d,) = tax.disposals
    assert (d.day, d.txid, d.assumed, d.sats) == ("2022-06-05", SELL, True, 6_000_000)
    assert [x.taxable for x in d.lots] == [True] and tax.ref(SELL) == "T-002"
    assert tax.summary is not None and tax.gain_taxable == pytest.approx(tax.summary.short.gain_eur, abs=0.01)
    assert [(a.day, a.txid, a.sats) for a in tax.acquisitions] == [("2022-01-14", BUY, 10_000_000)]
    r = tax.recon
    assert r is not None and r.ok
    assert (r.opening, r.inflows, r.outflows, r.fees, r.closing) == (0, 10_000_000, 6_000_000, 10_000, 3_990_000)

    tax = _tax_year(state, 2025)
    assert tax.disposals == [] and [a.txid for a in tax.acquisitions] == [KEEP]
    r = tax.recon
    assert r is not None and r.ok and (r.opening, r.closing) == (3_990_000, 5_990_000)
    assert [w.tx_count for w in tax.wallets] == [1]  # der Zufluss 2026 zählt nicht


def test_local_folder_reads_steuer_csv(tmp_path) -> None:
    from btc_origin.local_files import load_local_data

    assert load_local_data(tmp_path).tax_config is None  # ohne Datei → Vorgaben
    (tmp_path / "steuer.csv").write_text("2025;Weitere Veräußerungsgeschäfte;250\nfalsch\n", encoding="utf-8")
    data = load_local_data(tmp_path)
    assert data.tax_config.other_sales == {2025: 250.0}
    assert len(data.errors) == 1 and data.errors[0].startswith("steuer.csv: Zeile 2")
