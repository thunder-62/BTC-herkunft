"""Historical BTC price on flow list (Anschaffungs-Referenz; fail-open)."""

from __future__ import annotations


from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.merger import Ledger
from btc_origin.price_oracle import HISTORICAL_UNAVAILABLE, PriceOracle
from btc_origin.tx_ingestor import Flow


def test_flows_include_btc_price_when_oracle_returns_value() -> None:
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(
            historical_fetcher=lambda on: (42000.0, 39000.0),
            spot_fetcher=lambda: (None, None),
        )
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("bc1qprice0000000000000000000000000001",),
        )
        persist_ledger(
            state.db,
            Ledger(
                flows=[
                    Flow(
                        txid="px1",
                        address="bc1qprice0000000000000000000000000001",
                        direction="in",
                        amount_sats=10_000,
                        wallet_id=1,
                        block_time="2025-01-15T12:00:00",
                    )
                ]
            ),
        )
        body = client.get("/api/flows").json()
        flow = body["flows"][0]
        assert flow["btc_price_eur"] == 39000.0
        assert flow["btc_price_usd"] == 42000.0
        assert "Anschaffungs-Referenz" in (flow.get("btc_price_note") or "")


def test_flows_price_fail_open_when_unavailable() -> None:
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(
            historical_fetcher=lambda on: (None, None),
            spot_fetcher=lambda: (None, None),
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
                        txid="px2",
                        address="addr",
                        direction="in",
                        amount_sats=1,
                        wallet_id=1,
                        block_time="2025-03-01T00:00:00Z",
                    )
                ]
            ),
        )
        flow = client.get("/api/flows").json()["flows"][0]
        assert flow["btc_price_eur"] is None
        note = flow.get("btc_price_note") or ""
        assert note == HISTORICAL_UNAVAILABLE
        assert "Kurs nicht ermittelbar" in note
        # Must not be a bare N/V marker from the API.
        assert note.lower() not in ("n/v", "n/a", "nv")


def test_flows_named_wallet_address_vs_external() -> None:
    """Session/cloud addresses get wallet_name; unknown addresses stay raw."""
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(
            historical_fetcher=lambda on: (1.0, 1.0),
            spot_fetcher=lambda: (None, None),
        )
        # Registry name (Name\\tXPub style) — primary for UI.
        state.registry.register_address("Sparkonto", "bc1qinternalwalletaddr000000000001")
        # DB mirror for address→name lookup (as after sync).
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'Sparkonto',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("bc1qinternalwalletaddr000000000001",),
        )
        # External counterparty address not in registry/DB wallets.
        persist_ledger(
            state.db,
            Ledger(
                flows=[
                    Flow(
                        txid="named1",
                        address="bc1qinternalwalletaddr000000000001",
                        direction="in",
                        amount_sats=50_000,
                        wallet_id=1,
                        block_time="2025-01-15T12:00:00Z",
                        is_internal=True,
                    ),
                    Flow(
                        txid="ext1",
                        address="bc1qexternalonly0000000000000000001",
                        direction="out",
                        amount_sats=10_000,
                        wallet_id=None,
                        block_time="2025-01-16T12:00:00Z",
                    ),
                ]
            ),
        )
        flows = {f["address"]: f for f in client.get("/api/flows").json()["flows"]}
        named = flows["bc1qinternalwalletaddr000000000001"]
        assert named["wallet_name"] == "Sparkonto"
        assert named["address_label"] == (
            "Sparkonto (bc1qinternalwalletaddr000000000001)"
        )

        external = flows["bc1qexternalonly0000000000000000001"]
        assert external["wallet_name"] is None
        assert external["address_label"] == "bc1qexternalonly0000000000000000001"


def test_slow_prices_do_not_block_ui_and_fill_in_later() -> None:
    """Price APIs slow → /api/cloud/flows answers fast with „Kurs wird geladen“,
    ``prices_pending`` true; after the background load, prices appear."""
    import threading
    import time

    from btc_origin.price_oracle import PRICE_LOADING

    release = threading.Event()

    def slow_hist(on):
        release.wait(10)
        return 42000.0, 39000.0

    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(
            historical_fetcher=slow_hist, spot_fetcher=lambda: (None, None)
        )
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("bc1qslow",),
        )
        persist_ledger(
            state.db,
            Ledger(
                flows=[
                    Flow(
                        txid="s1",
                        address="bc1qslow",
                        direction="in",
                        amount_sats=10_000,
                        wallet_id=1,
                        block_time="2024-02-01T10:00:00Z",
                    )
                ]
            ),
        )
        t0 = time.monotonic()
        body = client.get("/api/cloud/flows").json()
        assert time.monotonic() - t0 < 5
        assert body["prices_pending"] is True
        row = body["flows"][0]
        assert row["btc_price_eur"] is None
        assert row["btc_price_note"] == PRICE_LOADING

        release.set()
        assert state.oracle.wait_for_prefetch(5)
        body = client.get("/api/cloud/flows").json()
        assert body["prices_pending"] is False
        assert body["flows"][0]["btc_price_eur"] == 39000.0
