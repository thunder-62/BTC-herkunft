"""Regression: a spend of several own coins must count its external amount once.

Before: every spent input carried the whole tx-level external amount, so a
3-input payment counted the outflow 3× and Bestand went negative.
"""

from __future__ import annotations

from btc_origin.cloud_summary import summarize_cloud
from btc_origin.db import connect, init_schema, list_flows, persist_ledger
from btc_origin.enrichment import SessionEnricher
from btc_origin.holding_clock import HoldingClock
from btc_origin.internal_transfer_tagger import InternalTransferTagger
from btc_origin.merger import Ledger
from btc_origin.tx_ingestor import Flow

M = 1_000_000


def _flows(same_address: bool = False) -> list[Flow]:
    a2 = "a1" if same_address else "a2"
    return [
        Flow("k1", "a1", "in", 10 * M, wallet_id=1, block_time="2022-01-01T00:00:00Z", vout=0, tx_total_output_sats=10 * M),
        Flow("k2", a2, "in", 20 * M, wallet_id=1, block_time="2022-02-01T00:00:00Z", vout=0, tx_total_output_sats=20 * M),
        Flow("k3", "a3", "in", 30 * M, wallet_id=1, block_time="2022-03-01T00:00:00Z", vout=0, tx_total_output_sats=30 * M),
        # 3 coins → 0.5 BTC to a foreign address + 0.0999 change, fee 0.0001
        Flow("x", "a1", "out", 10 * M, wallet_id=1, block_time="2023-01-01T00:00:00Z", vin_index=0, tx_total_output_sats=59_990_000),
        Flow("x", a2, "out", 20 * M, wallet_id=1, block_time="2023-01-01T00:00:00Z", vin_index=1, tx_total_output_sats=59_990_000),
        Flow("x", "a3", "out", 30 * M, wallet_id=1, block_time="2023-01-01T00:00:00Z", vin_index=2, tx_total_output_sats=59_990_000),
        Flow("x", "c1", "in", 9_990_000, wallet_id=1, block_time="2023-01-01T00:00:00Z", vout=1, tx_total_output_sats=59_990_000),
    ]


def test_multi_input_payment_counts_outflow_once() -> None:
    flows = _flows()
    InternalTransferTagger().tag_inplace(flows, {"a1", "a2", "a3", "c1"})
    s = summarize_cloud(flows)
    assert s.inflow_sats == 60 * M
    assert s.outflow_sats == 50 * M
    assert s.external_fees_sats == 10_000
    assert s.bestand_sats == 9_990_000 == s.chain_balance_sats
    assert s.consistent

    lots = HoldingClock().apply_fifo_lots(flows, as_of="2026-09-27")
    consumed = sum(c.consumed_sats for c in lots.consumptions if c.kind == "outflow")
    assert consumed == 50 * M
    assert sum(lot.remaining_sats for lot in lots.lots) == 9_990_000


def test_persist_keeps_values_per_outpoint_for_same_address() -> None:
    """Two coins of one address spent in one tx keep their own split values."""
    db = connect()
    init_schema(db)
    db.execute(
        "INSERT INTO wallets (id, name, xpub, kind, created_at) "
        "VALUES (1,'w',NULL,'address','2024-01-01')"
    )
    for a in ("a1", "a3", "c1"):
        db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)", (a,))
    persist_ledger(db, Ledger(flows=_flows(same_address=True)))
    SessionEnricher().enrich(db, as_of="2026-09-27")
    s = summarize_cloud(list_flows(db))
    assert s.outflow_sats == 50 * M
    assert s.bestand_sats == 9_990_000 == s.chain_balance_sats


def test_inconsistent_ledger_is_flagged() -> None:
    flows = _flows()
    InternalTransferTagger().tag_inplace(flows, {"a1", "a2", "a3", "c1"})
    for f in flows:
        if f.direction == "out":
            f.external_amount_sats = 50 * M  # the old bug
    s = summarize_cloud(flows)
    assert not s.consistent
    assert any("Plausibilitätsprüfung" in n for n in s.notes)


def test_fully_spent_coins_still_show_their_cloud_entry() -> None:
    """Case: 3 own coins swept to one foreign address.

    Each coin's Cloud-Eintritt must stay visible as an IN row (status
    „vollständig abgeflossen“), so every OUT has its IN.
    """
    from btc_origin.holding_clock import cloud_flows_from_lot_result

    ins = [200_000, 7_500_000, 17_000_000]
    out = 24_690_000
    flows = [
        Flow(f"buy{i}", f"own{i}", "in", v, wallet_id=1,
             block_time=f"2021-0{i + 1}-01T00:00:00Z", vout=0, tx_total_output_sats=v)
        for i, v in enumerate(ins)
    ] + [
        Flow("sweep001", f"own{i}", "out", v, wallet_id=1,
             block_time="2021-06-01T00:00:00Z", vin_index=i, tx_total_output_sats=out)
        for i, v in enumerate(ins)
    ]
    InternalTransferTagger().tag_inplace(flows, {f"own{i}" for i in range(3)})
    result = HoldingClock().apply_fifo_lots(flows, as_of="2026-09-27")
    rows = cloud_flows_from_lot_result(
        result, tx_io={"sweep001": {"output_addresses": ["35L6hCgDNdWVyrF4AQqsGEKCw2Cutba3GK"]}}
    )
    in_rows = [r for r in rows if r["direction"] == "in"]
    out_rows = [r for r in rows if r["direction"] == "out"]
    assert sorted(r["amount_sats"] for r in in_rows) == sorted(ins)
    assert all(r["status"] == "abgeflossen" and r["remaining_sats"] == 0 for r in in_rows)
    assert all(r["haltefrist_hint"] is None for r in in_rows)
    assert sum(r["amount_sats"] for r in out_rows) == out
    assert {r["address"] for r in out_rows} == {"35L6hCgDNdWVyrF4AQqsGEKCw2Cutba3GK"}
    assert sum(r["amount_sats"] for r in in_rows) == out + 10_000  # + fee
