"""Session-RAM chain cache: no repeated Electrum queries within a session."""

from __future__ import annotations

from collections import Counter
from typing import Any

from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from btc_origin.chain_cache import ChainCache
from btc_origin.db import connect, init_schema, list_flows
from btc_origin.electrum_client import ElectrumClient, ElectrumProtocolError
from btc_origin.hd_deriver import HdDeriver, address_to_scripthash
from btc_origin.sync_pipeline import SyncPipeline
from btc_origin.wallet_registry import WalletRegistry

BIP84_ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
RECV0 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
EXTERNAL = "bc1qzs4lwwnll38kxtkvd8997pggr9fs78480dhhu0"
# Valid 80-byte header; timestamp bytes [68:72] = 1700000000 LE.
HEADER = "00" * 68 + (1700000000).to_bytes(4, "little").hex() + "00" * 8


def _tx(prev: str, outs: list[tuple[str, int]]) -> Transaction:
    return Transaction(
        vin=[TransactionInput(bytes.fromhex(prev), 0)],
        vout=[TransactionOutput(v, address_to_scriptpubkey(a)) for a, v in outs],
    )


class CountingElectrs:
    """electrs-like mock: rejects verbose tx requests, counts every call."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        # Exchange funding tx: parent of the purchase (sender lookup).
        fund = _tx("11" * 32, [(EXTERNAL, 60_000)])
        buy = _tx(fund.txid().hex(), [(RECV0, 50_000)])
        spend = _tx(buy.txid().hex(), [(EXTERNAL, 40_000)])
        self.txs = {t.txid().hex(): t.serialize().hex() for t in (fund, buy, spend)}
        sh = address_to_scripthash(RECV0)
        self.history = {
            sh: [
                {"tx_hash": buy.txid().hex(), "height": 800_000},
                {"tx_hash": spend.txid().hex(), "height": 800_000},
            ]
        }

    def request(self, method: str, params: list[Any]) -> Any:
        self.calls[method] += 1
        if method == "server.version":
            return ["electrs-mock", "1.4"]
        if method == "blockchain.scripthash.get_history":
            return self.history.get(params[0], [])
        if method == "blockchain.transaction.get":
            if len(params) > 1 and params[1]:
                raise ElectrumProtocolError("verbose transactions are currently unsupported")
            return self.txs[params[0]]
        if method == "blockchain.block.header":
            return HEADER
        raise RuntimeError(method)

    def close(self) -> None:
        pass


def _sync(cache: ChainCache | None) -> tuple[CountingElectrs, SyncPipeline]:
    transport = CountingElectrs()
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    reg = WalletRegistry()
    reg.register_xpub("demo", BIP84_ZPUB)
    db = connect()
    init_schema(db)
    pipe = SyncPipeline(
        reg, db, client=client, deriver=HdDeriver(gap_limit=2), cache=cache
    )
    pipe.run(connect=False)
    return transport, pipe


def test_sync_with_cache_queries_each_item_once() -> None:
    transport, pipe = _sync(ChainCache())
    derived = 4  # gap_limit=2 → 2 receive + 2 change (+ extension probe)
    # Histories: probe + ingest pass share one query per address.
    # (+1: history of the foreign destination — deposit-sweep bundling.)
    assert transport.calls["blockchain.scripthash.get_history"] <= derived + 3
    # 2 own txs prefetched as hex (batch) + 1 parent for the sender address
    # — no failing verbose attempts.
    assert transport.calls["blockchain.transaction.get"] == 2 + 1
    # Both txs share one block height → one header query.
    assert transport.calls["blockchain.block.header"] == 1
    flows = list_flows(pipe.db)
    assert {f["direction"] for f in flows} == {"in", "out"}
    assert all(f["block_time"] == "2023-11-14T22:13:20Z" for f in flows)  # privacy: ok (Unix-Zeit 1_700_000_000)


def test_verbose_rejection_is_remembered_per_client() -> None:
    transport = CountingElectrs()
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    for txid in transport.txs:
        client.get_transaction(txid, verbose=True)
    # Verbose rejected once per client, not once per tx (3 txs in the mock).
    assert transport.calls["blockchain.transaction.get"] == 1 + len(transport.txs)


def test_second_sync_reuses_txs_but_refreshes_histories() -> None:
    cache = ChainCache()
    transport = CountingElectrs()
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    reg = WalletRegistry()
    reg.register_xpub("demo", BIP84_ZPUB)
    db = connect()
    init_schema(db)
    pipe = SyncPipeline(
        reg, db, client=client, deriver=HdDeriver(gap_limit=2), cache=cache
    )
    pipe.run(connect=False)
    first = Counter(transport.calls)
    pipe.run(connect=False)
    second = transport.calls - first
    # New run: histories re-queried (new txs may exist) …
    assert second["blockchain.scripthash.get_history"] == first[
        "blockchain.scripthash.get_history"
    ]
    # … but immutable tx hex + headers come from RAM.
    assert second["blockchain.transaction.get"] == 0
    assert second["blockchain.block.header"] == 0


def test_cache_does_not_keep_unconfirmed_verbose() -> None:
    cache = ChainCache()
    cache.put_tx_verbose("aa", {"hex": "00", "confirmations": 0})
    assert cache.get_tx_verbose("aa") is None
    assert cache.get_tx_hex("aa") == "00"
    cache.put_tx_verbose("bb", {"hex": "01", "blocktime": 1700000000})
    assert cache.get_tx_verbose("bb") == {"hex": "01", "blocktime": 1700000000}
    cache.clear()
    assert cache.get_tx_hex("aa") is None
