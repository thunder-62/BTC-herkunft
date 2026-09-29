"""Internal transfer tagger — tx-centric netting (external vs change)."""

from __future__ import annotations

from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.tx_ingestor import Flow


def _flow(
    txid: str,
    address: str,
    direction: str,
    amount: int = 1000,
    wallet_id: int | None = 1,
    *,
    tx_total_output_sats: int | None = None,
) -> Flow:
    return Flow(
        txid=txid,
        address=address,
        direction=direction,
        amount_sats=amount,
        wallet_id=wallet_id,
        block_time="2024-01-01T00:00:00Z",
        tx_total_output_sats=tx_total_output_sats,
    )


def test_cross_wallet_same_txid_tagged_internal() -> None:
    """Pure cross-xpub move (no external outputs) is fully internal."""
    own = {"addrA", "addrB"}
    flows = [
        _flow("tx1", "addrA", "out", amount=1000, wallet_id=1, tx_total_output_sats=1000),
        _flow("tx1", "addrB", "in", amount=1000, wallet_id=2, tx_total_output_sats=1000),
    ]
    tagged = InternalTransferTagger().tag(flows, own)
    assert all(f.is_internal for f in tagged)
    # Original list unchanged
    assert flows[0].is_internal is False


def test_external_inflow_not_internal() -> None:
    own = {"addrA"}
    flows = [_flow("tx2", "addrA", "in")]
    InternalTransferTagger().tag_inplace(flows, own)
    assert flows[0].is_internal is False


def test_external_outflow_not_internal() -> None:
    own = {"addrA"}
    flows = [_flow("tx3", "addrA", "out")]
    InternalTransferTagger().tag_inplace(flows, own)
    assert flows[0].is_internal is False
    assert flows[0].external_amount_sats == 1000


def test_pure_consolidation_internal() -> None:
    """Self-spend / consolidation with no foreign outputs → fully internal."""
    own = {"addrSpend", "addrChange"}
    flows = [
        _flow("tx4", "addrSpend", "out", amount=50_000, tx_total_output_sats=49_900),
        _flow("tx4", "addrChange", "in", amount=49_900, tx_total_output_sats=49_900),
    ]
    InternalTransferTagger().tag_inplace(flows, own)
    assert all(f.is_internal for f in flows)
    assert flows[0].fee_sats == 100
    assert flows[0].external_amount_sats == 0


def test_payment_with_change_external_out_change_internal() -> None:
    """1.0 BTC in → 0.7 external + 0.3 change: change internal, net external visible."""
    own = {"A", "C"}
    # fee = 0 for clarity; total outputs = 100_000_000
    flows = [
        _flow("t1", "A", "out", 100_000_000, tx_total_output_sats=100_000_000),
        _flow("t1", "C", "in", 30_000_000, tx_total_output_sats=100_000_000),
    ]
    InternalTransferTagger().tag_inplace(flows, own)
    assert flows[0].is_internal is False
    assert flows[0].external_amount_sats == 70_000_000
    assert flows[0].fee_sats == 0
    assert flows[1].is_internal is True  # change


def test_payment_with_change_and_fee() -> None:
    own = {"A", "C"}
    # inputs 100_000_000; outputs 70_000_000 external + 29_900_000 change; fee 100_000
    total_out = 99_900_000
    flows = [
        _flow("tfee", "A", "out", 100_000_000, tx_total_output_sats=total_out),
        _flow("tfee", "C", "in", 29_900_000, tx_total_output_sats=total_out),
    ]
    InternalTransferTagger().tag_inplace(flows, own)
    assert flows[0].is_internal is False
    assert flows[0].external_amount_sats == 70_000_000
    assert flows[0].fee_sats == 100_000
    assert flows[1].is_internal is True


def test_payment_without_change() -> None:
    own = {"A"}
    total_out = 99_000
    flows = [_flow("tnc", "A", "out", 100_000, tx_total_output_sats=total_out)]
    InternalTransferTagger().tag_inplace(flows, own)
    assert flows[0].is_internal is False
    assert flows[0].external_amount_sats == 99_000
    assert flows[0].fee_sats == 1_000


def test_cross_xpub_with_external_output() -> None:
    """Own out + own in + foreign output → not fully internal."""
    own = {"addrA", "addrB"}
    total = 1000
    flows = [
        _flow("tx5", "addrA", "out", amount=1000, tx_total_output_sats=total),
        _flow("tx5", "addrB", "in", amount=400, tx_total_output_sats=total),
        # foreign recipient recorded (optional) — external = total - own_in
    ]
    InternalTransferTagger().tag_inplace(flows, own)
    assert flows[0].is_internal is False
    assert flows[0].external_amount_sats == 600
    assert flows[1].is_internal is True


def test_foreign_flow_in_list_nets_external() -> None:
    own = {"addrA", "addrB"}
    flows = [
        _flow("tx5b", "addrA", "out", amount=1000),
        _flow("tx5b", "addrB", "in", amount=300),
        _flow("tx5b", "foreign", "in", amount=700, wallet_id=None),
    ]
    InternalTransferTagger().tag_inplace(flows, own)
    assert flows[0].is_internal is False
    assert flows[0].external_amount_sats == 700
    assert flows[1].is_internal is True
    assert flows[2].is_internal is False


def test_empty_own_set_still_tags_when_wallet_ids_present() -> None:
    """Session wallet_id on flows is enough — own address set may be sparse."""
    flows = [
        _flow("tx6", "addrA", "out", wallet_id=1, tx_total_output_sats=1000),
        _flow("tx6", "addrB", "in", wallet_id=2, tx_total_output_sats=1000),
    ]
    InternalTransferTagger().tag_inplace(flows, set())
    assert all(f.is_internal for f in flows)


def test_no_wallet_ids_and_empty_own_does_not_mark_in_internal() -> None:
    """Without own set and without wallet_id, receive stays external."""
    flows = [
        Flow(
            txid="tx6b",
            address="addrA",
            direction="out",
            amount_sats=1000,
            wallet_id=None,
            block_time="2024-01-01T00:00:00Z",
            tx_total_output_sats=1000,
        ),
        Flow(
            txid="tx6b",
            address="addrB",
            direction="in",
            amount_sats=1000,
            wallet_id=None,
            block_time="2024-01-01T00:00:00Z",
            tx_total_output_sats=1000,
        ),
    ]
    InternalTransferTagger().tag_inplace(flows, set())
    assert flows[1].is_internal is False


def test_synthetic_own_in_out_without_total_still_internal() -> None:
    """Heuristic: own in+out without foreign metadata → consolidation."""
    own = {"addrSpend", "addrChange"}
    flows = [
        _flow("tx4b", "addrSpend", "out", amount=50_000),
        _flow("tx4b", "addrChange", "in", amount=50_000),
    ]
    InternalTransferTagger().tag_inplace(flows, own)
    assert all(f.is_internal for f in flows)


def test_session_out_in_tagged_internal_even_if_source_addr_not_in_own() -> None:
    """Outflows are session spends; wallet_id on in is enough for full-internal."""
    own = {"addrB"}  # source addrA missing from own set
    flows = [
        _flow("txCloud", "addrA", "out", amount=1000, wallet_id=1, tx_total_output_sats=1000),
        _flow("txCloud", "addrB", "in", amount=1000, wallet_id=2, tx_total_output_sats=1000),
    ]
    InternalTransferTagger().tag_inplace(flows, own)
    assert all(f.is_internal for f in flows)
