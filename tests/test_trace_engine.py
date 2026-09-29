"""Trace engine — reverse BFS with mocks; no network."""

from __future__ import annotations

from btc_origin.trace_engine import DictParentProvider, TraceEngine


def test_linear_chain_within_depth() -> None:
    graph = {
        "root": ["a"],
        "a": ["b"],
        "b": ["c"],
        "c": [],
    }
    engine = TraceEngine(max_depth=5, provider=DictParentProvider(graph))
    result = engine.trace("root")
    txids = [s.txid for s in result.steps]
    assert txids == ["root", "a", "b", "c"]
    assert result.ambiguous is True  # c (and possibly empty parents) flagged
    assert result.depth_reached == 3
    assert result.truncated is False


def test_multi_parent_marks_ambiguous() -> None:
    graph = {"root": ["p1", "p2"], "p1": [], "p2": []}
    result = TraceEngine(max_depth=3).trace("root", graph=graph)
    assert result.ambiguous is True
    root_step = result.steps[0]
    assert root_step.ambiguous is True
    assert set(root_step.parents) == {"p1", "p2"}
    assert any("Ambiguous" in n or "ambiguous" in n.lower() for n in result.notes)


def test_depth_limit_truncates() -> None:
    graph = {"r": ["a"], "a": ["b"], "b": ["c"], "c": ["d"]}
    result = TraceEngine(max_depth=2).reverse_bfs("r", graph=graph)
    assert result.truncated is True
    assert result.max_depth == 2
    depths = {s.txid: s.depth for s in result.steps}
    assert depths["r"] == 0
    assert depths["a"] == 1
    # b is enqueued at depth 2 == limit → truncated step, no further crawl to c
    assert "b" in depths
    assert depths["b"] == 2
    assert any(s.truncated for s in result.steps)
    assert "c" not in depths
    assert "d" not in depths


def test_empty_root_ambiguous() -> None:
    result = TraceEngine(max_depth=3).trace("")
    assert result.ambiguous is True
    assert result.steps == []


def test_as_dict_structured() -> None:
    graph = {"x": ["y"], "y": []}
    d = TraceEngine(max_depth=4).trace("x", graph=graph).as_dict()
    assert d["root_txid"] == "x"
    assert "steps" in d and "nodes" in d
    assert isinstance(d["ambiguous"], bool)


def test_electrum_provider_mocked() -> None:
    class FakeClient:
        def get_transaction_hex(self, txid: str) -> str:
            # Return empty / invalid → no parents, ambiguity at root
            raise RuntimeError("no tx")

    result = TraceEngine(max_depth=2, client=FakeClient()).trace("deadbeef")
    assert result.steps[0].txid == "deadbeef"
    assert result.ambiguous is True

def test_no_client_root_note_distinguishes_missing_electrum() -> None:
    """Without client/graph, note must say no Electrum — not coinbase/empty."""
    result = TraceEngine(max_depth=2).trace("deadbeef")
    assert result.ambiguous is True
    assert result.depth_reached == 0
    joined = " ".join(result.notes)
    assert "No Electrum client" in joined or "kein Electrum" in joined
    assert "not a coinbase" in joined.lower() or "kein Electrum" in joined
    # Must not use the old ambiguous coinbase/empty-graph phrasing as the cause.
    assert "missing tx data, coinbase, or empty graph" not in joined


def test_electrum_empty_parents_note_mentions_electrum() -> None:
    class FakeClient:
        def get_transaction_hex(self, txid: str) -> str:
            raise RuntimeError("no tx")

    result = TraceEngine(max_depth=2, client=FakeClient()).trace("deadbeef")
    joined = " ".join(result.notes)
    assert "via Electrum" in joined or "Electrum" in joined



def test_dict_provider_supplies_input_addresses() -> None:
    graph = {
        "root": {
            "parents": ["parent"],
            "input_addresses": ["bc1qfund1", "bc1qfund2", "bc1qfund1"],
        },
        "parent": {"parents": [], "input_addresses": []},
    }
    result = TraceEngine(max_depth=3).trace("root", graph=graph)
    root = result.steps[0]
    assert root.input_addresses == ["bc1qfund1", "bc1qfund2"]  # deduped, order kept
    d = result.as_dict()
    assert d["steps"][0]["input_addresses"] == ["bc1qfund1", "bc1qfund2"]
    assert "address_labels" in d["steps"][0]


def test_address_map_overlay_and_labels() -> None:
    from btc_origin.trace_engine import DictParentProvider

    provider = DictParentProvider(
        {"root": ["p"], "p": []},
        address_map={"root": ["bc1qaaa", "bc1qbbb"]},
    )
    labels = {"bc1qaaa": "FTX"}
    result = TraceEngine(
        max_depth=2,
        provider=provider,
        label_lookup=lambda a: labels.get(a),
    ).trace("root")
    root = result.steps[0]
    assert root.input_addresses == ["bc1qaaa", "bc1qbbb"]
    assert root.address_labels == {"bc1qaaa": "FTX"}


def test_electrum_provider_resolves_funding_addresses() -> None:
    """FakeClient returns child + parent hex; funding address decoded from parent vout."""
    from embit.networks import NETWORKS
    from embit.script import p2wpkh
    from embit import bip32
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    from embit.script import address_to_scriptpubkey

    from btc_origin.hd_deriver import normalize_extended_public_key
    from btc_origin.trace_engine import ElectrumParentProvider, TraceEngine

    BIP84_ZPUB = (
        "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
        "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
    )
    norm = normalize_extended_public_key(BIP84_ZPUB)
    hd = bip32.HDKey.from_string(norm.normalized)
    fund_addr = p2wpkh(hd.derive([0, 0])).address(NETWORKS["main"])
    recv_addr = p2wpkh(hd.derive([0, 1])).address(NETWORKS["main"])

    # Parent tx: pays fund_addr at vout 0
    parent_vin = [
        TransactionInput(txid=bytes.fromhex("11" * 32), vout=0),
    ]
    parent_vout = [TransactionOutput(50_000, address_to_scriptpubkey(fund_addr))]
    parent_tx = Transaction(vin=parent_vin, vout=parent_vout)
    parent_hex = parent_tx.to_string()
    parent_txid = parent_tx.txid().hex()

    # Child spends parent:0 → recv_addr
    child_vin = [
        TransactionInput(txid=bytes.fromhex(parent_txid), vout=0),
    ]
    child_vout = [TransactionOutput(49_000, address_to_scriptpubkey(recv_addr))]
    child_tx = Transaction(vin=child_vin, vout=child_vout)
    child_hex = child_tx.to_string()
    child_txid = child_tx.txid().hex()

    store = {child_txid: child_hex, parent_txid: parent_hex}

    class FakeClient:
        def get_transaction_hex(self, txid: str) -> str:
            if txid not in store:
                raise RuntimeError(f"missing {txid}")
            return store[txid]

    engine = TraceEngine(max_depth=2, client=FakeClient())
    result = engine.trace(child_txid)
    root = result.steps[0]
    assert parent_txid in root.parents
    assert fund_addr in root.input_addresses
    assert recv_addr in root.output_addresses

    # Direct provider unit check + hex cache
    prov = ElectrumParentProvider(FakeClient())
    hop = prov.hop_of(child_txid)
    assert hop.input_addresses == [fund_addr]
    assert parent_txid in prov._hex_cache
    assert child_txid in prov._hex_cache


def test_spk_to_address_undecodable_returns_none() -> None:
    from embit.script import Script
    from btc_origin.trace_engine import _spk_to_address

    assert _spk_to_address(Script(b"\x6a\x01\x00")) is None
