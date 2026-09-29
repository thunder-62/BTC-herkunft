"""Jahres-Resümee: hypothetical gain/loss of Cloud-Austritte per year."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.merger import Ledger
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow
from btc_origin.year_summary import freigrenze_eur, yearly_summary

PRICES = {
    date(2023, 1, 10): 20_000.0,
    date(2023, 6, 1): 25_000.0,
    date(2023, 11, 1): 32_000.0,
    date(2024, 3, 1): 60_000.0,
    date(2025, 2, 1): 90_000.0,
}


def test_yearly_summary_splits_short_and_long() -> None:
    rows = [
        # 2023: short (bought 2023-01-10, out 2023-06-01): 0.1 BTC, +500 €
        {"direction": "out", "time": "2023-06-01", "lot_date": "2023-01-10", "amount_sats": 10_000_000, "haltefrist_days": 142, "external_name": "ext-001"},
        # 2023: short, loss: bought 2023-06-01 @25k, out 2023-11-01 @32k → gain
        {"direction": "out", "time": "2023-11-01", "lot_date": "2023-06-01", "amount_sats": 5_000_000, "haltefrist_days": 153},
        # 2024: long (bought 2023-01-10 → 2024-03-01): tax-free part
        {"direction": "out", "time": "2024-03-01", "lot_date": "2023-01-10", "amount_sats": 1_000_000, "haltefrist_days": 416},
        # 2025: price missing on acquisition day
        {"direction": "out", "time": "2025-02-01", "lot_date": "2024-12-24", "amount_sats": 1_000_000, "haltefrist_days": 39},
        {"direction": "in", "time": "2023-01-10", "amount_sats": 99},  # ignored
    ]
    years = {y.year: y.as_dict() for y in yearly_summary(rows, PRICES.get)}
    y23 = years[2023]
    assert y23["disposals"] == 2
    assert y23["short"]["btc_sats"] == 15_000_000
    assert y23["short"]["gain_eur"] == 500.0 + 350.0
    assert y23["long"]["btc_sats"] == 0
    assert y23["freigrenze_eur"] == 600 and y23["over_freigrenze"] is True
    y24 = years[2024]
    assert y24["short"]["btc_sats"] == 0
    assert y24["long"]["gain_eur"] == 400.0  # 0.01 × (60k − 20k)
    assert y24["over_freigrenze"] is False  # nothing short-term
    y25 = years[2025]
    assert y25["short"]["missing_price"] == 1
    assert y25["complete"] is False and y25["over_freigrenze"] is None
    assert y23["details"][0]["counterparty"] == "ext-001"


def test_freigrenze_years() -> None:
    assert freigrenze_eur(2023) == 600
    assert freigrenze_eur(2024) == 1000


def test_api_years() -> None:
    own = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(
            historical_fetcher=lambda on: (None, PRICES.get(on)), spot_fetcher=lambda: (None, None)
        )
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)", (own,))
        persist_ledger(
            state.db,
            Ledger(
                flows=[
                    Flow("buy", own, "in", 10_000_000, wallet_id=1, block_time="2023-01-10T00:00:00Z", vout=0, tx_total_output_sats=10_000_000),
                    Flow("sell", own, "out", 10_000_000, wallet_id=1, block_time="2023-06-01T00:00:00Z", vin_index=0, tx_total_output_sats=9_990_000),
                ]
            ),
        )
        state.last_tx_io = {"sell": {"output_addresses": ["3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"]}}
        client.post("/api/enrich")
        body = client.get("/api/cloud/years").json()
        (y,) = body["years"]
        assert y["year"] == 2023
        assert y["short"]["btc_sats"] == 9_990_000
        # Gebühr 10.000 sats × 25.000 € = 2,50 € Werbungskosten (Rz. 59)
        assert y["short"]["fees_eur"] == 2.5
        assert y["short"]["gain_eur"] == round(0.0999 * 5000 - 2.5, 2)
        assert y["details"][0]["counterparty"] == "ext-001"
        assert "hypothetisch" in body["assumption"].lower()


def test_fees_of_a_disposal_are_werbungskosten() -> None:
    """Rz. 59: fee of the sale tx lowers the gain, split by amount across coins;
    fees of pure transfers (tx without outflow) are ignored."""
    rows = [
        {"direction": "out", "kind": "outflow", "txid": "s", "time": "2024-03-01", "lot_date": "2023-01-10", "amount_sats": 3_000_000, "haltefrist_days": 416},
        {"direction": "out", "kind": "outflow", "txid": "s", "time": "2024-03-01", "lot_date": "2023-06-01", "amount_sats": 1_000_000, "haltefrist_days": 274},
        {"direction": "out", "kind": "fee", "txid": "s", "time": "2024-03-01", "lot_date": "2023-06-01", "amount_sats": 20_000},
        {"direction": "out", "kind": "fee", "txid": "transfer", "time": "2024-03-01", "lot_date": "2023-06-01", "amount_sats": 50_000},
    ]
    (y,) = [y.as_dict() for y in yearly_summary(rows, PRICES.get)]
    assert y["disposals"] == 2
    # 20.000 sats × 60.000 € = 12 € → 3/4 long, 1/4 short
    assert y["long"]["fees_eur"] == 9.0 and y["short"]["fees_eur"] == 3.0
    assert y["long"]["gain_eur"] == round(0.03 * 40_000 - 9.0, 2)
    assert y["short"]["gain_eur"] == round(0.01 * 35_000 - 3.0, 2)
    assert sorted(d["fee_sats"] for d in y["details"]) == [5_000, 15_000]


def test_fee_shares_add_up_to_the_satoshi() -> None:
    """Fee split across coins is integer and sums exactly to the tx fee."""
    rows = [
        {"direction": "out", "kind": "outflow", "txid": "s", "time": "2024-03-01", "lot_date": "2023-01-10", "amount_sats": n}
        for n in (3_333_333, 1_111_111, 7)
    ] + [{"direction": "out", "kind": "fee", "txid": "s", "time": "2024-03-01", "amount_sats": 10_001}]  # privacy: ok (Rundungstest)
    (y,) = yearly_summary(rows, PRICES.get)
    shares = [d["fee_sats"] for d in y.details]
    assert all(isinstance(x, int) for x in shares)
    assert sum(shares) == 10_001  # privacy: ok
