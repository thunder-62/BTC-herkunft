"""Cloud summary EUR aggregation (historical in/out/fees; Bestand × spot)."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.cloud_summary import summarize_cloud
from btc_origin.db import persist_ledger
from btc_origin.merger import Ledger
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow


def _oracle(
    *,
    hist: dict[date, tuple[float | None, float | None]] | None = None,
    spot: tuple[float | None, float | None] = (100_000.0, 90_000.0),
) -> PriceOracle:
    hist = hist or {}

    def historical(on: date) -> tuple[float | None, float | None]:
        return hist.get(on, (None, None))

    def spot_fn() -> tuple[float | None, float | None]:
        return spot

    return PriceOracle(spot_fetcher=spot_fn, historical_fetcher=historical)


def test_eur_inflow_outflow_fees_bestand_math() -> None:
    """1 BTC in @ 40k EUR, 0.4 BTC out @ 50k, 0.01 fee @ 45k; Bestand × 90k spot."""
    # Use sats: 1 BTC = 1e8
    oracle = _oracle(
        hist={
            date(2025, 1, 1): (42_000.0, 40_000.0),  # usd, eur
            date(2025, 6, 1): (55_000.0, 50_000.0),
            date(2025, 3, 1): (48_000.0, 45_000.0),
        },
        spot=(100_000.0, 90_000.0),
    )
    flows = [
        Flow(
            "in1",
            "A",
            "in",
            100_000_000,
            wallet_id=1,
            block_time="2025-01-01T12:00:00Z",
            lot_date="2025-01-01",
        ),
        Flow(
            "out1",
            "A",
            "out",
            40_000_000,
            wallet_id=1,
            block_time="2025-06-01T12:00:00Z",
            external_amount_sats=40_000_000,
        ),
        # Internal fee-only style: separate internal move with fee
        Flow(
            "fee1",
            "A",
            "out",
            1_000_000,
            wallet_id=1,
            is_internal=True,
            fee_sats=1_000_000,
            block_time="2025-03-01T12:00:00Z",
        ),
        Flow(
            "fee1",
            "B",
            "in",
            0,
            wallet_id=2,
            is_internal=True,
            block_time="2025-03-01T12:00:00Z",
        ),
    ]
    s = summarize_cloud(flows, oracle=oracle)
    assert s.inflow_sats == 100_000_000
    assert s.outflow_sats == 40_000_000
    assert s.internal_fees_sats == 1_000_000
    # Bestand = 1e8 - 4e7 - 1e6 = 59_000_000
    assert s.bestand_sats == 59_000_000
    assert s.inflow_eur == 40_000.0  # 1 BTC × 40k
    assert s.outflow_eur == 20_000.0  # 0.4 × 50k
    assert s.fees_eur == 450.0  # 0.01 × 45k
    assert s.bestand_eur == 59_000_000 / 1e8 * 90_000.0
    assert s.spot_eur == 90_000.0
    assert s.eur_partial is False
    d = s.as_dict()
    assert d["inflow_eur"] == 40_000.0
    assert d["fees_eur"] == 450.0


def test_eur_missing_rate_fail_open() -> None:
    oracle = _oracle(hist={}, spot=(None, None))
    flows = [
        Flow("in1", "A", "in", 10_000_000, wallet_id=1, block_time="2025-01-01T00:00:00Z"),
    ]
    s = summarize_cloud(flows, oracle=oracle)
    assert s.inflow_sats == 10_000_000
    assert s.inflow_eur is None
    assert s.bestand_eur is None
    assert s.eur_partial is True
    assert s.eur_note and "Kurs fehlt" in s.eur_note


def test_eur_prefers_lot_date_over_block_time() -> None:
    """Cloud-entry lot_date drives inflow EUR, not a later block_time."""
    oracle = _oracle(
        hist={
            date(2024, 1, 15): (None, 30_000.0),
            date(2025, 6, 1): (None, 99_000.0),
        },
        spot=(None, 90_000.0),
    )
    flows = [
        Flow(
            "in1",
            "A",
            "in",
            100_000_000,
            wallet_id=1,
            block_time="2025-06-01T00:00:00Z",
            lot_date="2024-01-15",
        ),
    ]
    s = summarize_cloud(flows, oracle=oracle)
    assert s.inflow_eur == 30_000.0


def test_api_cloud_includes_eur_with_mocked_oracle() -> None:
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = _oracle(
            hist={date(2025, 1, 1): (None, 40_000.0)},
            spot=(None, 90_000.0),
        )
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        persist_ledger(
            state.db,
            Ledger(
                flows=[
                    Flow(
                        "buy",
                        "A",
                        "in",
                        50_000_000,
                        wallet_id=1,
                        block_time="2025-01-01T00:00:00Z",
                        lot_date="2025-01-01",
                    ),
                ]
            ),
        )
        r = client.get("/api/cloud")
        assert r.status_code == 200
        body = r.json()
        assert body["inflow_sats"] == 50_000_000
        assert body["inflow_eur"] == 20_000.0  # 0.5 × 40k
        assert body["bestand_eur"] == 0.5 * 90_000.0
        assert "inflow_usd" not in body or body.get("inflow_usd") is None
