"""Wallet-Cloud Flows: GET /api/cloud/flows (Eintritt/Austritt, not raw ledger)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.enrichment import SessionEnricher
from btc_origin.holding_clock import HoldingClock, cloud_flows_from_lot_result
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.merger import Ledger
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow
from btc_origin.wallet_registry import WalletEntry


def _scenario_in_move_partial_out() -> tuple[list[Flow], date, str, str]:
    """External in → internal A→B → partial external out from B."""
    as_of = date(2026, 9, 27)
    buy_day = (as_of - timedelta(days=500)).isoformat()
    move_day = (as_of - timedelta(days=50)).isoformat()
    sell_day = (as_of - timedelta(days=10)).isoformat()
    flows = [
        Flow(
            "buytxid",
            "addrA",
            "in",
            100_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "movetxid",
            "addrA",
            "out",
            100_000,
            wallet_id=1,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=100_000,
        ),
        Flow(
            "movetxid",
            "addrB",
            "in",
            100_000,
            wallet_id=2,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=100_000,
            vout=0,
        ),
        Flow(
            "selltxid",
            "addrB",
            "out",
            30_000,
            wallet_id=2,
            block_time=f"{sell_day}T12:00:00Z",
            external_amount_sats=30_000,
            tx_total_output_sats=30_000,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"addrA", "addrB"})
    return flows, as_of, buy_day, sell_day


def test_cloud_flows_helper_in_remaining_and_out_disposal() -> None:
    flows, as_of, buy_day, sell_day = _scenario_in_move_partial_out()
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    assert len(result.lots) == 1
    assert result.lots[0].remaining_sats == 70_000

    shaped = cloud_flows_from_lot_result(
        result,
        as_of=as_of,
        wallet_names={1: "Cold", 2: "Hot"},
        own_addresses={"addrA", "addrB"},
        tx_io={
            "selltxid": {
                "output_addresses": ["addrB", "bc1qexternaldest000000000000001"],
            }
        },
    )
    assert [r["direction"] for r in shaped] == ["in", "out"]

    inn = shaped[0]
    assert inn["direction"] == "in"
    # Betrag beim Cloud-Eintritt; davon noch vorhanden + aktueller Ort.
    assert inn["amount_sats"] == 100_000
    assert inn["remaining_sats"] == 70_000
    assert inn["status"] == "teilweise"
    assert inn["amount_basis"] == "entry_sats"
    assert inn["time"] == buy_day
    assert inn["address"] == "addrA"
    assert inn["wallet_name"] == "Cold"
    assert "Cold (addrA)" in (inn["address_display"] or "")
    assert inn["current_locations"] == [
        {"wallet_id": 2, "wallet_name": "Hot", "address": "addrB", "remaining_sats": 70_000}
    ]
    assert inn["path_labels"] == ["Cold", "Hot"]
    assert inn["haltefrist_hint"] is True

    out = shaped[1]
    assert out["direction"] == "out"
    assert out["amount_sats"] == 30_000
    assert out["time"] == sell_day
    assert out["txid"] == "selltxid"
    assert out["address"] == "bc1qexternaldest000000000000001"
    assert out["path_labels"] == ["Cold", "Hot"]
    assert out["lot_date"] == buy_day


def test_cloud_flows_outside_cloud_when_no_dest() -> None:
    flows, as_of, _buy, sell_day = _scenario_in_move_partial_out()
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    shaped = cloud_flows_from_lot_result(
        result,
        as_of=as_of,
        wallet_names={1: "Cold", 2: "Hot"},
        own_addresses={"addrA", "addrB"},
        tx_io=None,
    )
    out = next(r for r in shaped if r["direction"] == "out")
    assert out["address_display"] == "außerhalb Cloud"
    assert out["time"] == sell_day


def test_api_cloud_flows_empty_then_after_enrich() -> None:
    with TestClient(app) as client:
        empty = client.get("/api/cloud/flows").json()
        assert empty["count"] == 0
        assert empty["flows"] == []
        assert empty["semantics"] == "wallet_cloud"
        assert empty["amount_basis_in"] == "entry_sats"
        assert empty["ephemeral"] is True

        state = app.state.session
        state.oracle = PriceOracle(
            historical_fetcher=lambda on: (50000.0, 45000.0),
            spot_fetcher=lambda: (None, None),
        )
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (1,'Cold',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) "
            "VALUES (2,'Hot',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("addrA",),
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (2,?,0)",
            ("addrB",),
        )
        state.db.commit()

        flows, as_of, buy_day, sell_day = _scenario_in_move_partial_out()
        persist_ledger(state.db, Ledger(flows=flows))
        state.registry._wallets = [
            WalletEntry(id=1, name="Cold", kind="address", address="addrA"),
            WalletEntry(id=2, name="Hot", kind="address", address="addrB"),
        ]
        state.registry._next_id = 3
        state.last_tx_io = {
            "selltxid": {
                "output_addresses": ["bc1qexternaldest000000000000001"],
            }
        }

        SessionEnricher(clock=state.clock).enrich(state.db, as_of=as_of)

        data = client.get("/api/cloud/flows").json()
        assert data["semantics"] == "wallet_cloud"
        assert data["count"] == 2
        by_dir = {r["direction"]: r for r in data["flows"]}
        assert by_dir["in"]["amount_sats"] == 100_000
        assert by_dir["in"]["remaining_sats"] == 70_000
        assert by_dir["in"]["time"] == buy_day
        assert by_dir["in"]["wallet_name"] == "Cold"
        assert by_dir["in"]["path_labels"] == ["Cold", "Hot"]
        assert by_dir["in"]["btc_price_eur"] == 45000.0
        assert by_dir["out"]["amount_sats"] == 30_000
        assert by_dir["out"]["time"] == sell_day
        assert by_dir["out"]["address"] == "bc1qexternaldest000000000000001"

        # Raw ledger still available and larger (includes internal hops).
        raw = client.get("/api/flows").json()
        assert raw["count"] >= data["count"]
        assert any(f.get("is_internal") for f in raw["flows"])


def test_api_cloud_flows_no_internal_as_rows() -> None:
    """Internal A→B must not appear as its own in/out cloud-flow rows."""
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(
            historical_fetcher=lambda on: (1.0, 1.0),
            spot_fetcher=lambda: (None, None),
        )
        for wid, name, addr in ((1, "Cold", "addrA"), (2, "Hot", "addrB")):
            state.db.execute(
                "INSERT INTO wallets (id, name, xpub, kind, created_at) "
                "VALUES (?,?,NULL,'address','2024-01-01')",
                (wid, name),
            )
            state.db.execute(
                "INSERT INTO addresses (wallet_id, address, is_change) VALUES (?,?,0)",
                (wid, addr),
            )
        state.db.commit()
        flows, as_of, _, _ = _scenario_in_move_partial_out()
        persist_ledger(state.db, Ledger(flows=flows))
        state.registry._wallets = [
            WalletEntry(id=1, name="Cold", kind="address", address="addrA"),
            WalletEntry(id=2, name="Hot", kind="address", address="addrB"),
        ]
        state.registry._next_id = 3
        SessionEnricher(clock=state.clock).enrich(state.db, as_of=as_of)

        cloud_flows = client.get("/api/cloud/flows").json()["flows"]
        txids = {r["txid"] for r in cloud_flows}
        assert "movetxid" not in txids
        assert "buytxid" in txids
        assert "selltxid" in txids
