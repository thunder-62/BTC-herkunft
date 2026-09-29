"""Cloud-of-wallets: Inflow / Bestand / Outflow / interne Transaktionskosten."""

from __future__ import annotations

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.cloud_summary import summarize_cloud
from btc_origin.db import persist_ledger
from btc_origin.enrichment import SessionEnricher
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.merger import Ledger
from btc_origin.tx_ingestor import Flow


def test_external_in_out_and_bestand() -> None:
    flows = [
        Flow("in1", "A", "in", 100_000, wallet_id=1, block_time="2025-01-01T00:00:00Z"),
        Flow(
            "out1",
            "A",
            "out",
            40_000,
            wallet_id=1,
            block_time="2025-06-01T00:00:00Z",
            external_amount_sats=40_000,
        ),
    ]
    s = summarize_cloud(flows)
    assert s.inflow_sats == 100_000
    assert s.outflow_sats == 40_000
    assert s.internal_fees_sats == 0
    assert s.bestand_sats == 60_000


def test_cross_wallet_internal_move_net_zero_fee_accumulates() -> None:
    flows = [
        Flow("buy", "A", "in", 80_000, wallet_id=1, block_time="2025-01-01T00:00:00Z"),
        Flow(
            "move",
            "A",
            "out",
            80_000,
            wallet_id=1,
            block_time="2025-02-01T00:00:00Z",
            tx_total_output_sats=79_000,
        ),
        Flow(
            "move",
            "B",
            "in",
            79_000,
            wallet_id=2,
            block_time="2025-02-01T00:00:00Z",
            tx_total_output_sats=79_000,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"A", "B"})
    assert all(f.is_internal for f in flows if f.txid == "move")
    s = summarize_cloud(flows)
    assert s.inflow_sats == 80_000
    assert s.outflow_sats == 0  # internal move is not cloud outflow
    assert s.internal_fees_sats == 1_000
    assert s.bestand_sats == 79_000


def test_api_cloud_endpoint() -> None:
    with TestClient(app) as client:
        state = app.state.session
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("addrA",),
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("addrB",),
        )
        flows = [
            Flow("buy", "addrA", "in", 50_000, wallet_id=1, block_time="2025-01-01T00:00:00Z"),
            Flow(
                "move",
                "addrA",
                "out",
                50_000,
                wallet_id=1,
                block_time="2025-02-01T00:00:00Z",
                tx_total_output_sats=49_500,
            ),
            Flow(
                "move",
                "addrB",
                "in",
                49_500,
                wallet_id=1,
                block_time="2025-02-01T00:00:00Z",
                tx_total_output_sats=49_500,
            ),
        ]
        persist_ledger(state.db, Ledger(flows=flows))
        SessionEnricher(clock=state.clock).enrich(state.db, as_of="2026-09-27")
        r = client.get("/api/cloud")
        assert r.status_code == 200
        body = r.json()
        assert body["inflow_sats"] == 50_000
        assert body["outflow_sats"] == 0
        assert body["internal_fees_sats"] == 500
        assert body["bestand_sats"] == 49_500
        assert body["labels"]["internal_fees"] == "Interne Transaktionskosten"
        flows_body = client.get("/api/flows").json()
        assert "cloud" in flows_body
        assert flows_body["cloud"]["bestand_sats"] == 49_500
