"""Einzelbetrachtung je Coin (BMF-Schreiben 06.03.2025, Rz. 61)."""

from __future__ import annotations

from btc_origin.cloud_summary import summarize_cloud
from btc_origin.holding_clock import HoldingClock
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.tx_ingestor import Flow

M = 1_000_000


def _flows(spend_old: bool) -> list[Flow]:
    """Wallet B: bought 2023 directly; later receives a coin bought 2021 in A.
    Then B sells ONE coin — either the 2021 one (old) or the 2023 one (new)."""
    sold_prev = ("t", 0) if spend_old else ("buyB", 0)
    return [
        Flow("buyA", "a1", "in", 10 * M, wallet_id=1, block_time="2021-01-10T00:00:00Z", vout=0, tx_total_output_sats=10 * M),
        Flow("buyB", "b1", "in", 10 * M, wallet_id=2, block_time="2023-06-01T00:00:00Z", vout=0, tx_total_output_sats=10 * M),
        # transfer A → B (2024-01-01), fee 10k
        Flow("t", "a1", "out", 10 * M, wallet_id=1, block_time="2024-01-01T00:00:00Z", vin_index=0,
             tx_total_output_sats=9_990_000, prev_txid="buyA", prev_vout=0),
        Flow("t", "b2", "in", 9_990_000, wallet_id=2, block_time="2024-01-01T00:00:00Z", vout=0, tx_total_output_sats=9_990_000),
        # sale of one specific coin from B (2024-03-01), no change, fee 10k
        Flow("sell", "b2" if spend_old else "b1", "out", 9_990_000 if spend_old else 10 * M, wallet_id=2,
             block_time="2024-03-01T00:00:00Z", vin_index=0,
             tx_total_output_sats=9_980_000 if spend_old else 9_990_000,
             prev_txid=sold_prev[0], prev_vout=sold_prev[1]),
    ]


def _run(spend_old: bool):
    flows = _flows(spend_old)
    InternalTransferTagger().tag_inplace(flows, {"a1", "b1", "b2"})
    return flows, HoldingClock().apply_fifo_lots(flows, as_of="2026-09-27")


def test_selling_the_2021_coin_is_long_term() -> None:
    _, res = _run(spend_old=True)
    (out,) = [c for c in res.consumptions if c.kind == "outflow"]
    assert out.acquisition_date.isoformat() == "2021-01-10"
    assert out.qualifies_haltefrist_hint is True
    # the 2023 coin is untouched and still held
    assert [(lot.acquisition_date.isoformat(), lot.remaining_sats) for lot in res.lots] == [("2023-06-01", 10 * M)]


def test_selling_the_2023_coin_is_short_term() -> None:
    _, res = _run(spend_old=False)
    (out,) = [c for c in res.consumptions if c.kind == "outflow"]
    assert out.acquisition_date.isoformat() == "2023-06-01"
    assert out.qualifies_haltefrist_hint is False
    # the transferred 2021 coin keeps its acquisition date in wallet B
    (lot,) = res.lots
    assert (lot.acquisition_date.isoformat(), lot.wallet_id, lot.remaining_sats) == ("2021-01-10", 2, 9_990_000)
    assert [m.kind for m in lot.path] == ["inflow", "transfer"]


def test_transfer_is_not_a_disposal_and_books_balance() -> None:
    flows, res = _run(spend_old=True)
    assert not [c for c in res.consumptions if c.txid == "t" and c.kind == "outflow"]
    fees = sum(c.consumed_sats for c in res.consumptions if c.kind == "fee")
    assert fees == 20_000
    s = summarize_cloud(flows)
    assert s.consistent and s.bestand_sats == sum(lot.remaining_sats for lot in res.lots)


def test_fifo_only_inside_one_transaction() -> None:
    """Two coins (2021, 2023) spent together: 0.06 external + change.
    The external part takes the first-acquired slice (FIFO in the tx)."""
    flows = [
        Flow("b21", "x1", "in", 5 * M, wallet_id=1, block_time="2021-01-01T00:00:00Z", vout=0, tx_total_output_sats=5 * M),
        Flow("b23", "x2", "in", 5 * M, wallet_id=1, block_time="2023-01-01T00:00:00Z", vout=0, tx_total_output_sats=5 * M),
        Flow("mix", "x1", "out", 5 * M, wallet_id=1, block_time="2024-01-01T00:00:00Z", vin_index=0, tx_total_output_sats=9_990_000, prev_txid="b21", prev_vout=0),
        Flow("mix", "x2", "out", 5 * M, wallet_id=1, block_time="2024-01-01T00:00:00Z", vin_index=1, tx_total_output_sats=9_990_000, prev_txid="b23", prev_vout=0),
        Flow("mix", "chg", "in", 3_990_000, wallet_id=1, block_time="2024-01-01T00:00:00Z", vout=1, tx_total_output_sats=9_990_000),
    ]
    InternalTransferTagger().tag_inplace(flows, {"x1", "x2", "chg"})
    res = HoldingClock().apply_fifo_lots(flows, as_of="2026-09-27")
    outs = sorted((c.acquisition_date.isoformat(), c.consumed_sats) for c in res.consumptions if c.kind == "outflow")
    # 6.000.000 external: the whole 2021 coin first, then 1.000.000 of the 2023 coin
    assert outs == [("2021-01-01", 5 * M), ("2023-01-01", 1_000_000)]
    assert sum(c.consumed_sats for c in res.consumptions if c.kind == "fee") == 10_000
    # change: rest of the 2023 coin
    assert sorted((lot.acquisition_date.isoformat(), lot.remaining_sats) for lot in res.lots) == [("2023-01-01", 3_990_000)]


def test_same_block_parent_after_child_in_input_order() -> None:
    """Child tx listed before its parent (same timestamp) is still processed after it."""
    flows = [
        Flow("child", "a1", "out", 10 * M, wallet_id=1, block_time="2024-01-01T00:00:00Z", vin_index=0, tx_total_output_sats=9_990_000, prev_txid="parent", prev_vout=0),
        Flow("child", "a2", "in", 9_990_000, wallet_id=1, block_time="2024-01-01T00:00:00Z", vout=0, tx_total_output_sats=9_990_000),
        Flow("parent", "a1", "in", 10 * M, wallet_id=1, block_time="2024-01-01T00:00:00Z", vout=0, tx_total_output_sats=10 * M),
    ]
    InternalTransferTagger().tag_inplace(flows, {"a1", "a2"})
    res = HoldingClock().apply_fifo_lots(flows, as_of="2026-09-27")
    assert [(lot.address, lot.remaining_sats) for lot in res.lots] == [("a2", 9_990_000)]
