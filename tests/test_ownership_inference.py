"""Ownership inference — co-input / consolidation heuristics (session RAM)."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.config import get_settings
from btc_origin.db import persist_ledger
from btc_origin.holding_clock import HoldingClock
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.merger import Ledger
from btc_origin.ownership_inference import (
    OwnershipInferencer,
    TxIoView,
    infer_own_addresses,
    tx_graph_from_flows,
)
from btc_origin.tx_ingestor import Flow


def _flow(
    txid: str,
    address: str,
    direction: str,
    amount: int = 1000,
    wallet_id: int | None = 1,
    *,
    block_time: str = "2024-01-01T00:00:00Z",
    tx_total_output_sats: int | None = None,
) -> Flow:
    return Flow(
        txid=txid,
        address=address,
        direction=direction,
        amount_sats=amount,
        wallet_id=wallet_id,
        block_time=block_time,
        tx_total_output_sats=tx_total_output_sats,
    )


def test_co_input_and_consolidation_infers_b_c_d() -> None:
    """Seed A; consolidate A+B+C → D → B,C,D inferred own."""
    graph = [
        TxIoView(
            txid="cons9to1",
            input_addresses=["A", "B", "C"],
            output_addresses=["D"],
            vin_count=3,
            vout_count=1,
        )
    ]
    result = infer_own_addresses({"A"}, graph)
    assert result.seed == {"A"}
    assert result.inferred == {"B", "C", "D"}
    assert result.own == {"A", "B", "C", "D"}
    assert result.inferred_by["B"] == "co_input"
    assert result.inferred_by["D"] == "consolidation_out"
    assert result.as_dict()["label_de"] == "abgeleitet / heuristisch"
    assert result.as_dict()["heuristic"] is True


def test_external_payment_destination_not_inferred() -> None:
    """2-out payment peel: default max_outputs_for_change=1 → E/C not inferred.

    Co-input expansion does not apply (E is only an output). Output expansion
    is consolidation-only by default so merchant destinations stay foreign.
    """
    graph = [
        TxIoView(
            txid="pay",
            input_addresses=["A"],
            output_addresses=["E", "C"],
            vin_count=1,
            vout_count=2,
        ),
    ]
    result = infer_own_addresses({"A"}, graph)
    assert "E" not in result.own
    assert "C" not in result.inferred


def test_external_pay_with_default_does_not_eat_via_co_input() -> None:
    """E only appears as output of a payment — never as co-input → not via step2."""
    graph = [
        TxIoView(
            txid="pay",
            input_addresses=["A"],
            output_addresses=["E"],
            vin_count=2,  # unresolved foreign vin → skip output expansion
            vout_count=1,
        )
    ]
    result = infer_own_addresses({"A"}, graph)
    assert "E" not in result.own
    assert result.inferred == set()


def test_fanout_skipped() -> None:
    outs = [f"O{i}" for i in range(25)]
    graph = [
        TxIoView(
            txid="fan",
            input_addresses=["A"],
            output_addresses=outs,
            vin_count=1,
            vout_count=25,
        )
    ]
    result = infer_own_addresses({"A"}, graph)
    assert result.inferred == set()


def test_flow_graph_co_inputs() -> None:
    flows = [
        _flow("t1", "A", "out", 3000, tx_total_output_sats=2990),
        _flow("t1", "B", "out", 0, wallet_id=None),  # amount irrelevant
        _flow("t1", "C", "out", 0, wallet_id=None),
        _flow("t1", "D", "in", 2990, wallet_id=2),
    ]
    # Fix B/C amounts for realism
    flows[1].amount_sats = 1000
    flows[2].amount_sats = 1000
    graph = tx_graph_from_flows(flows)
    result = infer_own_addresses({"A"}, graph)
    assert {"B", "C", "D"} <= result.inferred


def test_enrichment_uses_inferred_set_for_internal_and_fifo() -> None:
    """Lots keep acquisition across internal move using inferred own set."""
    # External buy into A on day 1; consolidate A+B+C → D on day 2.
    # Only A is seeded; B,C,D come from explicit graph.
    buy_day = "2023-01-15T00:00:00Z"
    move_day = "2024-06-01T00:00:00Z"
    flows = [
        _flow("buy", "A", "in", 10_000, wallet_id=1, block_time=buy_day),
        _flow(
            "cons",
            "A",
            "out",
            4000,
            wallet_id=1,
            block_time=move_day,
            tx_total_output_sats=8990,
        ),
        _flow(
            "cons",
            "B",
            "out",
            3000,
            wallet_id=None,
            block_time=move_day,
            tx_total_output_sats=8990,
        ),
        _flow(
            "cons",
            "C",
            "out",
            2000,
            wallet_id=None,
            block_time=move_day,
            tx_total_output_sats=8990,
        ),
        _flow(
            "cons",
            "D",
            "in",
            8990,
            wallet_id=None,
            block_time=move_day,
            tx_total_output_sats=8990,
        ),
    ]
    graph = [
        TxIoView(
            txid="cons",
            input_addresses=["A", "B", "C"],
            output_addresses=["D"],
            vin_count=3,
            vout_count=1,
        )
    ]
    seed = {"A"}
    own_inf = OwnershipInferencer().infer(seed, graph, flows=flows)
    assert {"B", "C", "D"} <= own_inf.inferred

    InternalTransferTagger().tag_inplace(flows, own_inf.own)
    assert all(f.is_internal for f in flows if f.txid == "cons")

    result = HoldingClock().apply_fifo_lots(flows, as_of=date(2026, 9, 27))
    assert result.lots
    # Acquisition must remain the external buy day, not the consolidation day.
    assert result.lots[0].acquisition_date.isoformat() == "2023-01-15"


def _seed_consolidation_session(state) -> None:
    state.db.execute(
        "INSERT INTO wallets (id, name, xpub, kind, created_at) "
        "VALUES (1,'w',NULL,'address','2024-01-01')"
    )
    state.db.execute(
        "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
        ("A",),
    )
    persist_ledger(
        state.db,
        Ledger(
            flows=[
                _flow("cons", "A", "out", 1000, tx_total_output_sats=2990),
                _flow("cons", "D", "in", 2990, wallet_id=None, tx_total_output_sats=2990),
            ]
        ),
    )
    state.last_tx_io = {
        "cons": {
            "input_addresses": ["A", "B", "C"],
            "output_addresses": ["D"],
            "vin_count": 3,
            "vout_count": 1,
        }
    }


def test_default_only_pasted_addresses_are_own() -> None:
    """Default: co-spend heuristic is off — foreign co-inputs stay foreign."""
    assert get_settings().ownership_inference is False
    with TestClient(app) as client:
        _seed_consolidation_session(app.state.session)
        body = client.post("/api/enrich").json()
        assert body["ownership"] is None  # no heuristic run → UI box hidden
        assert any(
            "nur aus eingefügten" in n for n in body["enrichment"]["notes"]
        )
        assert client.get("/api/cloud/ownership").json()["inferred_count"] == 0


def test_session_enricher_ownership_in_payload(monkeypatch) -> None:
    # Heuristic is opt-in (OWNERSHIP_INFERENCE=true).
    monkeypatch.setattr(get_settings(), "ownership_inference", True)
    with TestClient(app) as client:
        state = app.state.session
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("A",),
        )
        ledger = Ledger(
            flows=[
                _flow("cons", "A", "out", 1000, tx_total_output_sats=2990),
                _flow("cons", "B", "out", 1000, wallet_id=None, tx_total_output_sats=2990),
                _flow("cons", "C", "out", 1000, wallet_id=None, tx_total_output_sats=2990),
                _flow("cons", "D", "in", 2990, wallet_id=None, tx_total_output_sats=2990),
            ]
        )
        persist_ledger(state.db, ledger)
        state.last_tx_io = {
            "cons": {
                "input_addresses": ["A", "B", "C"],
                "output_addresses": ["D"],
                "vin_count": 3,
                "vout_count": 1,
            }
        }
        r = client.post("/api/enrich")
        assert r.status_code == 200
        body = r.json()
        own = body["ownership"]
        assert own["seed_count"] == 1
        assert own["inferred_count"] >= 3
        assert own["heuristic"] is True
        assert "abgeleitet" in own["label_de"]

        g = client.get("/api/cloud/ownership")
        assert g.status_code == 200
        assert g.json()["inferred_count"] >= 3

        masked = client.get("/api/cloud/ownership?privacy=true")
        assert masked.status_code == 200
        sample = masked.json().get("inferred_addresses_sample") or []
        if sample:
            assert all(a == "************" or "*" in a for a in sample)


def test_health_lists_ownership_feature() -> None:
    with TestClient(app) as client:
        feats = client.get("/api/health").json()["features"]
        assert "ownership_inference" in feats
