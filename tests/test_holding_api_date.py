"""API holding-period: acquisition 2025-01-15 vs today/as_of 2026-09-27 qualifies."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.enrichment import SessionEnricher
from btc_origin.merger import Ledger
from btc_origin.tx_ingestor import Flow


def test_flows_haltefrist_2025_01_15_vs_2026_09_27() -> None:
    as_of = date(2026, 9, 27)
    with TestClient(app) as client:
        state = app.state.session
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",),
        )
        persist_ledger(
            state.db,
            Ledger(
                flows=[
                    Flow(
                        txid="buy1",
                        address="bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
                        direction="in",
                        amount_sats=100_000,
                        wallet_id=1,
                        block_time="2025-01-15T12:00:00",
                        vout=0,
                    )
                ]
            ),
        )
        SessionEnricher(clock=state.clock).enrich(state.db, as_of=as_of)
        body = client.get("/api/flows").json()
        flow = body["flows"][0]
        assert flow["haltefrist_hint"] is True
        assert int(flow["haltefrist_days"]) >= 365
        assert str(flow.get("lot_date") or "").startswith("2025-01-15")
