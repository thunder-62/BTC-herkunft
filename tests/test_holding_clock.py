"""Holding clock: 365-day hint + FIFO lot consumption."""

from __future__ import annotations

from datetime import date, timedelta

from btc_origin.holding_clock import HoldingClock, qualifies_haltefrist
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.tx_ingestor import Flow


def test_inflow_older_than_365_days_flagged() -> None:
    as_of = date(2026, 9, 27)
    old = as_of - timedelta(days=400)
    clock = HoldingClock()
    flag = clock.flag_inflow("flow-1", old, as_of=as_of)
    assert flag.days_held == 400
    assert flag.qualifies_haltefrist_hint is True


def test_inflow_younger_than_365_not_flagged() -> None:
    as_of = date(2026, 9, 27)
    young = as_of - timedelta(days=100)
    assert qualifies_haltefrist(young, as_of) is False
    clock = HoldingClock()
    flag = clock.flag_inflow("flow-2", young, as_of=as_of)
    assert flag.qualifies_haltefrist_hint is False
    assert flag.days_held == 100


def test_exactly_365_days_qualifies() -> None:
    as_of = date(2026, 9, 27)
    edge = as_of - timedelta(days=365)
    assert qualifies_haltefrist(edge, as_of) is True


def test_fifo_buy_partial_sell_remaining_holding() -> None:
    """Kauf → Teilverkauf mit Change → Resthaltefrist on remaining lot balance."""
    as_of = date(2026, 9, 27)
    buy_day = (as_of - timedelta(days=400)).isoformat()
    sell_day = (as_of - timedelta(days=10)).isoformat()
    total_out = 100_000  # 40k external + 60k change; fee 0
    flows = [
        Flow(
            "buy",
            "A",
            "in",
            100_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "sell",
            "A",
            "out",
            100_000,
            wallet_id=1,
            block_time=f"{sell_day}T00:00:00Z",
            tx_total_output_sats=total_out,
        ),
        Flow(
            "sell",
            "C",
            "in",
            60_000,
            wallet_id=1,
            block_time=f"{sell_day}T00:00:00Z",
            tx_total_output_sats=total_out,
            vout=1,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"A", "C"})
    assert flows[1].external_amount_sats == 40_000
    assert flows[2].is_internal is True
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    assert len(result.remaining_flags) == 1
    assert result.remaining_flags[0].amount_sats == 60_000
    assert result.remaining_flags[0].days_held == 400
    assert result.remaining_flags[0].qualifies_haltefrist_hint is True
    assert sum(c.consumed_sats for c in result.consumptions if c.kind == "outflow") == 40_000
    assert flows[1].lot_date == buy_day
    assert flows[1].holding_days == (
        date.fromisoformat(sell_day) - date.fromisoformat(buy_day)
    ).days


def test_fifo_internal_transfer_keeps_acquisition_date() -> None:
    """Kauf → Umbuchung anderes xpub → Lot-Datum bleibt."""
    as_of = date(2026, 9, 27)
    buy_day = (as_of - timedelta(days=500)).isoformat()
    move_day = (as_of - timedelta(days=50)).isoformat()
    flows = [
        Flow(
            "buy",
            "A",
            "in",
            80_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "move",
            "A",
            "out",
            80_000,
            wallet_id=1,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
        ),
        Flow(
            "move",
            "B",
            "in",
            80_000,
            wallet_id=2,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
            vout=0,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"A", "B"})
    assert all(f.is_internal for f in flows if f.txid == "move")
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    # Internal move must not create a new lot on B; wallet-1 lot remains.
    assert len(result.remaining_flags) == 1
    assert result.remaining_flags[0].inflow_date.isoformat() == buy_day
    assert result.remaining_flags[0].days_held == 500
    assert result.consumptions == []
    # Change/internal annotation propagates old lot date
    assert flows[2].lot_date == buy_day


def test_fifo_payment_with_change_keeps_old_date_on_change() -> None:
    """Zahlung mit Change → Change behält Altdatum; kein neues Lot."""
    as_of = date(2026, 9, 27)
    buy_day = (as_of - timedelta(days=400)).isoformat()
    pay_day = (as_of - timedelta(days=20)).isoformat()
    total_out = 100_000
    flows = [
        Flow(
            "buy",
            "A",
            "in",
            100_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "pay",
            "A",
            "out",
            100_000,
            wallet_id=1,
            block_time=f"{pay_day}T00:00:00Z",
            tx_total_output_sats=total_out,
        ),
        Flow(
            "pay",
            "C",
            "in",
            30_000,
            wallet_id=1,
            block_time=f"{pay_day}T00:00:00Z",
            tx_total_output_sats=total_out,
            vout=1,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"A", "C"})
    assert flows[1].external_amount_sats == 70_000
    assert flows[2].is_internal is True
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    # Remaining = 30k change tracked via original lot (same wallet)
    assert len(result.remaining_flags) == 1
    assert result.remaining_flags[0].amount_sats == 30_000
    assert result.remaining_flags[0].inflow_date.isoformat() == buy_day
    assert flows[2].lot_date == buy_day
    # No new lot created for change inflow — remaining lot still from buy
    assert all(lot.txid == "buy" for lot in result.lots)


def test_fifo_fee_consumes_lots() -> None:
    as_of = date(2026, 9, 27)
    buy_day = (as_of - timedelta(days=10)).isoformat()
    pay_day = as_of.isoformat()
    total_out = 99_000
    flows = [
        Flow(
            "buy",
            "A",
            "in",
            100_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "pay",
            "A",
            "out",
            100_000,
            wallet_id=1,
            block_time=f"{pay_day}T00:00:00Z",
            tx_total_output_sats=total_out,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"A"})
    assert flows[1].fee_sats == 1_000
    assert flows[1].external_amount_sats == 99_000
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    fee_consumed = sum(c.consumed_sats for c in result.consumptions if c.kind == "fee")
    out_consumed = sum(c.consumed_sats for c in result.consumptions if c.kind == "outflow")
    assert fee_consumed == 1_000
    assert out_consumed == 99_000
    assert result.remaining_flags == []


def test_acquisition_2025_01_15_qualifies_vs_2026_09_27() -> None:
    """Regression: 2025-01-15T12:00:00 vs as_of 2026-09-27 must qualify (≥365d)."""
    as_of = date(2026, 9, 27)
    inflow = "2025-01-15T12:00:00"
    assert qualifies_haltefrist(inflow, as_of) is True
    clock = HoldingClock()
    flag = clock.flag_inflow("lot-2025", inflow, as_of=as_of)
    assert flag.qualifies_haltefrist_hint is True
    assert flag.days_held >= 365
    assert flag.days_held == (as_of - date(2025, 1, 15)).days
    # FIFO remaining lot from that acquisition also qualifies
    flows = [
        Flow(
            "buy",
            "A",
            "in",
            50_000,
            wallet_id=1,
            block_time="2025-01-15T12:00:00",
            vout=0,
        ),
    ]
    result = clock.apply_fifo_lots(flows, as_of=as_of)
    assert len(result.remaining_flags) == 1
    assert result.remaining_flags[0].qualifies_haltefrist_hint is True
    assert result.remaining_flags[0].days_held >= 365


def test_fifo_multi_wallet_cloud_entry_keeps_day_a_not_move_day_b() -> None:
    """External buy day A on wallet1, internal move day B → lot.acquisition_date == A."""
    as_of = date(2026, 9, 27)
    buy_day = (as_of - timedelta(days=500)).isoformat()  # A
    move_day = (as_of - timedelta(days=50)).isoformat()  # B
    flows = [
        Flow(
            "buy",
            "addrA",
            "in",
            80_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "move",
            "addrA",
            "out",
            80_000,
            wallet_id=1,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
        ),
        Flow(
            "move",
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
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    assert len(result.lots) == 1
    lot = result.lots[0]
    assert lot.acquisition_date.isoformat() == buy_day
    assert lot.wallet_id == 2
    assert lot.path[0].kind == "inflow"
    assert lot.path[0].date.isoformat() == buy_day


def test_fifo_same_txid_own_out_heuristic_when_own_set_incomplete() -> None:
    """Source out present but source addr missing from own set → still keep day A."""
    as_of = date(2026, 9, 27)
    buy_day = "2024-01-01"
    move_day = "2025-06-01"
    flows = [
        Flow(
            "buy",
            "addrA",
            "in",
            80_000,
            wallet_id=1,
            block_time=f"{buy_day}T00:00:00Z",
            vout=0,
        ),
        Flow(
            "move",
            "addrA",
            "out",
            80_000,
            wallet_id=1,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
        ),
        Flow(
            "move",
            "addrB",
            "in",
            80_000,
            wallet_id=2,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
            vout=0,
        ),
    ]
    # Incomplete own: only dest address registered (gap / sync quirk).
    InternalTransferTagger().tag_inplace(flows, {"addrB"})
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    assert len(result.lots) == 1
    assert result.lots[0].acquisition_date.isoformat() == buy_day
    assert result.lots[0].wallet_id == 2


def test_fifo_dest_only_no_source_out_creates_lot_on_receive_day_unavoidable() -> None:
    """Regression: only dest inflow in session (no source out) → lot on day B.

    Unavoidable without the source wallet: the receive looks like an external
    cloud entry. Documented so FA dates are not silently 'fixed' incorrectly.
    """
    as_of = date(2026, 9, 27)
    move_day = "2025-06-01"
    flows = [
        Flow(
            "move",
            "addrB",
            "in",
            80_000,
            wallet_id=2,
            block_time=f"{move_day}T00:00:00Z",
            tx_total_output_sats=80_000,
            vout=0,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, {"addrB"})
    result = HoldingClock().apply_fifo_lots(flows, as_of=as_of)
    assert len(result.lots) == 1
    assert result.lots[0].acquisition_date.isoformat() == move_day
    assert result.lots[0].wallet_id == 2
