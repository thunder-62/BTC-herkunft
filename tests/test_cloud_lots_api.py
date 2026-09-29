"""Lot genealogy: FIFO path across wallets + GET /api/cloud/lots."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.enrichment import SessionEnricher
from btc_origin.holding_clock import HoldingClock, lot_to_genealogy_dict
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.merger import Ledger
from btc_origin.tx_ingestor import Flow
from btc_origin.wallet_registry import WalletEntry


def _external_in_a_internal_a_to_b() -> tuple[list[Flow], date, str]:
    """Mock: external in → A, internal A→B, remaining on B with same lot_date."""
    as_of = date(2026, 9, 27)
    buy_day = (as_of - timedelta(days=500)).isoformat()
    move_day = (as_of - timedelta(days=50)).isoformat()
    flows = [
        Flow(
            "buytxid",
            "addrA",
            "in",
            80_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "movetxid",
            "addrA",
            "out",
            80_000,
            wallet_id=1,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
        ),
        Flow(
            "movetxid",
            "addrB",
            "in",
            80_000,
            wallet_id=2,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
            vout=0,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"addrA", "addrB"})
    return flows, as_of, buy_day


def test_fifo_genealogy_external_in_then_internal_move() -> None:
    flows, as_of, buy_day = _external_in_a_internal_a_to_b()
    assert all(f.is_internal for f in flows if f.txid == "movetxid")

    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    assert len(result.lots) == 1
    lot = result.lots[0]
    assert lot.remaining_sats == 80_000
    assert lot.acquisition_date.isoformat() == buy_day
    assert lot.wallet_id == 2  # remaining sits on B
    assert lot.origin_wallet_id == 1
    assert lot.txid == "buytxid"
    assert len(lot.path) == 2
    assert lot.path[0].kind == "inflow"
    assert lot.path[0].wallet_id == 1
    assert lot.path[0].txid == "buytxid"
    assert lot.path[1].kind == "transfer"
    assert lot.path[1].wallet_id == 2
    assert lot.path[1].txid == "movetxid"

    payload = lot_to_genealogy_dict(
        lot, as_of=as_of, wallet_names={1: "Cold", 2: "Hot"}
    )
    assert payload["lot_date"] == buy_day
    assert payload["remaining_sats"] == 80_000
    assert payload["original_amount_sats"] == 80_000
    assert payload["origin"]["wallet_name"] == "Cold"
    assert payload["current_wallet_id"] == 2
    assert payload["current_wallet_name"] == "Hot"
    assert payload["path"][0]["wallet_name"] == "Cold"
    assert payload["path"][1]["wallet_name"] == "Hot"
    assert payload["qualifies_haltefrist"] is True
    assert payload["days_held"] == 500


def test_api_cloud_lots_empty_then_after_enrich() -> None:
    with TestClient(app) as client:
        empty = client.get("/api/cloud/lots").json()
        assert empty["count"] == 0
        assert empty["lots"] == []
        assert empty["ephemeral"] is True

        state = app.state.session
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

        flows, as_of, buy_day = _external_in_a_internal_a_to_b()
        persist_ledger(state.db, Ledger(flows=flows))

        # Registry is a list — seed display names matching DB wallet ids.
        state.registry._wallets = [
            WalletEntry(id=1, name="Cold", kind="address", address="addrA"),
            WalletEntry(id=2, name="Hot", kind="address", address="addrB"),
        ]
        state.registry._next_id = 3

        SessionEnricher(clock=state.clock).enrich(state.db, as_of=as_of)

        data = client.get("/api/cloud/lots").json()
        assert data["count"] == 1
        lot = data["lots"][0]
        assert lot["lot_date"] == buy_day
        assert lot["remaining_sats"] == 80_000
        assert lot["origin"]["txid"] == "buytxid"
        assert lot["current_wallet_id"] == 2
        assert [p["wallet_id"] for p in lot["path"]] == [1, 2]
        assert lot["path"][-1]["txid"] == "movetxid"
        assert data["cloud"]["bestand_sats"] == 80_000
        # Names resolved from registry / DB
        assert lot["origin"]["wallet_name"] == "Cold"
        assert lot["current_wallet_name"] == "Hot"
