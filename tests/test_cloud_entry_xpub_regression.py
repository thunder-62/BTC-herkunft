"""Regression: Wallet-Cloud entry/exit across year wallets pasted as plain xpub.

Ledger Live / BitBoxApp / Trezor Suite show ``xpub`` also for native-segwit
accounts. Deriving those as legacy P2PKH left the first wallet empty, so the
internal sweep into wallet 2 showed up as Cloud-Eintritt (wrong date/Haltefrist).

Scenario: 3 purchases → A (2022) · sweep A→B (2023) · purchase → B ·
B→C with change (2024) · sale from C with change (2025).
Test keys are derived from a fixed dummy seed inside the test only; the app
itself only ever receives the public xpub.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from embit import bip32
from embit.networks import NETWORKS
from embit.script import address_to_scriptpubkey, p2sh, p2tr, p2wpkh
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from btc_origin.chain_cache import ChainCache
from btc_origin.db import connect, init_schema, list_flows
from btc_origin.electrum_client import ElectrumClient
from btc_origin.enrichment import flows_from_db_rows
from btc_origin.hd_deriver import HdDeriver, address_to_scripthash
from btc_origin.holding_clock import HoldingClock, cloud_flows_from_lot_result
from btc_origin.sync_pipeline import SyncPipeline
from btc_origin.wallet_registry import WalletRegistry

NET = NETWORKS["main"]
EXCHANGE = "bc1qzs4lwwnll38kxtkvd8997pggr9fs78480dhhu0"
BUYER = "bc1qpn7amn0egma99wz0dlje8vcvd9u9nlljz969r4"

ADDR = {
    "p2wpkh": lambda k: p2wpkh(k).address(NET),
    "p2sh-p2wpkh": lambda k: p2sh(p2wpkh(k)).address(NET),
    "p2tr": lambda k: p2tr(k).address(NET),
}


def _account(seed: int) -> bip32.HDKey:
    return bip32.HDKey.from_seed(bytes([seed]) * 32).derive("m/84h/0h/0h")


def _xpub(acct: bip32.HDKey) -> str:
    return acct.to_public().to_base58(version=NET["xpub"])


def _tx(ins: list[tuple[str, int]], outs: list[tuple[str, int]]) -> Transaction:
    return Transaction(
        vin=[TransactionInput(bytes.fromhex(t), v) for t, v in ins],
        vout=[TransactionOutput(amt, address_to_scriptpubkey(a)) for a, amt in outs],
    )


class ChainMock:
    def __init__(self) -> None:
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.tx_hex: dict[str, str] = {}
        self.blocktime: dict[str, int] = {}
        self.height_time: dict[int, int] = {}
        self._height = 700_000

    def add(self, tx: Transaction, day: str, addresses: list[str]) -> str:
        txid = tx.txid().hex()
        self._height += 1000
        self.tx_hex[txid] = tx.serialize().hex()
        self.blocktime[txid] = int(
            datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()
        )
        self.height_time[self._height] = self.blocktime[txid]
        for a in addresses:
            self.history.setdefault(address_to_scripthash(a), []).append(
                {"tx_hash": txid, "height": self._height}
            )
        return txid

    def request(self, method: str, params: list[Any]) -> Any:
        if method == "server.version":
            return ["mock", "1.4"]
        if method == "blockchain.scripthash.get_history":
            return self.history.get(params[0], [])
        if method == "blockchain.transaction.get":
            txid = params[0]
            if len(params) > 1 and params[1]:
                return {"hex": self.tx_hex[txid], "blocktime": self.blocktime[txid]}
            return self.tx_hex[txid]
        if method == "blockchain.block.header":
            ts = self.height_time[int(params[0])]
            return "00" * 68 + ts.to_bytes(4, "little").hex() + "00" * 8
        raise RuntimeError(method)

    def close(self) -> None:
        pass


def _run(script_type: str) -> tuple[list[dict[str, Any]], dict[str, str], Any]:
    a, b, c = _account(1), _account(2), _account(3)
    mk = ADDR[script_type]
    A0, A1, A2 = (mk(a.derive([0, i])) for i in range(3))
    B0, B1, B_ch = mk(b.derive([0, 0])), mk(b.derive([0, 1])), mk(b.derive([1, 0]))
    C0, C_ch = mk(c.derive([0, 0])), mk(c.derive([1, 0]))

    chain = ChainMock()
    fund = "11" * 32
    p1 = chain.add(_tx([(fund, 0)], [(A0, 10_000_000), (EXCHANGE, 1)]), "2022-02-01", [A0])
    p2 = chain.add(_tx([(fund, 1)], [(A1, 20_000_000)]), "2022-05-01", [A1])
    p3 = chain.add(_tx([(fund, 2)], [(A2, 30_000_000)]), "2022-09-01", [A2])
    s1 = chain.add(
        _tx([(p1, 0), (p2, 0), (p3, 0)], [(B0, 59_990_000)]),
        "2023-01-15",
        [A0, A1, A2, B0],
    )
    chain.add(_tx([(fund, 3)], [(B1, 5_000_000)]), "2023-03-01", [B1])
    m2 = chain.add(
        _tx([(s1, 0)], [(C0, 40_000_000), (B_ch, 19_980_000)]),
        "2024-01-20",
        [B0, C0, B_ch],
    )
    chain.add(
        _tx([(m2, 0)], [(BUYER, 25_000_000), (C_ch, 14_990_000)]),
        "2025-06-01",
        [C0, C_ch],
    )

    reg = WalletRegistry()
    for name, acct in (("2022", a), ("2023", b), ("2024", c)):
        reg.register_xpub(name, _xpub(acct))
    db = connect()
    init_schema(db)
    client = ElectrumClient(host="mock", port=1, transport=chain)
    client.connect()
    pipe = SyncPipeline(
        reg, db, client=client, deriver=HdDeriver(gap_limit=3), cache=ChainCache()
    )
    summary = pipe.run(connect=False)
    lots = HoldingClock().apply_fifo_lots(
        flows_from_db_rows(list_flows(db)), as_of="2026-09-27"
    )
    rows = cloud_flows_from_lot_result(
        lots,
        tx_io=pipe.last_tx_io,
        own_addresses={A0, A1, A2, B0, B1, B_ch, C0, C_ch},
    )
    addrs = {"B_ch": B_ch, "C_ch": C_ch, "B1": B1, "B0": B0, "C0": C0}
    return rows, addrs, summary


@pytest.mark.parametrize("script_type", ["p2wpkh", "p2sh-p2wpkh", "p2tr"])
def test_xpub_detects_script_type_and_keeps_purchase_dates(script_type: str) -> None:
    rows, addrs, summary = _run(script_type)
    ins = [r for r in rows if r["direction"] == "in"]
    outs = [r for r in rows if r["direction"] == "out"]

    # Cloud-Eintritt = every purchase with its entry amount — never the
    # internal sweep (2023-01-15) or the B→C move (2024-01-20).
    assert sorted((r["time"], r["amount_sats"]) for r in ins) == [
        ("2022-02-01", 10_000_000),
        ("2022-05-01", 20_000_000),
        ("2022-09-01", 30_000_000),
        ("2023-03-01", 5_000_000),
    ]
    remaining = sum(r["remaining_sats"] for r in ins)
    assert remaining == 10_000_000 + 9_980_000 + 14_990_000 + 5_000_000

    # Cloud-Austritt: sale 2025 to the buyer, acquisition dates from 2022.
    assert {r["time"] for r in outs} == {"2025-06-01"}
    assert {r["address"] for r in outs} == {BUYER}
    assert sum(r["amount_sats"] for r in outs) == 25_000_000
    assert all(r["lot_date"].startswith("2022-") for r in outs)
    assert all(r["haltefrist_hint"] for r in outs)

    # Books balance: entries = exits + fees + still held (fees 3 × 10k sats).
    entered = sum(r["amount_sats"] for r in ins)
    assert entered == 25_000_000 + remaining + 30_000

    # Where sats sit now: current UTXOs, never already-spent addresses.
    current = {loc["address"] for r in ins for loc in r["current_locations"]}
    assert current == {addrs["B_ch"], addrs["C_ch"], addrs["B1"]}
    assert not current & {addrs["B0"], addrs["C0"]}

    assert any("Adresstyp erkannt" in n for n in summary.notes)


def test_empty_wallet_is_reported() -> None:
    reg = WalletRegistry()
    reg.register_xpub("leer", _xpub(_account(9)))
    db = connect()
    init_schema(db)
    client = ElectrumClient(host="mock", port=1, transport=ChainMock())
    client.connect()
    summary = SyncPipeline(
        reg, db, client=client, deriver=HdDeriver(gap_limit=2)
    ).run(connect=False)
    assert any("keine Transaktionen" in n for n in summary.notes)


def test_gap_scan_follows_long_address_chains() -> None:
    """More used addresses than the old 2-round scan could reach (~3×gap)."""
    acct = _account(4)
    chain = ChainMock()
    n_used = 12  # gap_limit=2 → old scan stopped after ~6 addresses
    for i in range(n_used):
        addr = ADDR["p2wpkh"](acct.derive([0, i]))
        chain.add(_tx([("22" * 32, i)], [(addr, 1_000 + i)]), "2022-01-01", [addr])
    reg = WalletRegistry()
    reg.register_xpub("dca", _xpub(acct))
    db = connect()
    init_schema(db)
    client = ElectrumClient(host="mock", port=1, transport=chain)
    client.connect()
    SyncPipeline(reg, db, client=client, deriver=HdDeriver(gap_limit=2)).run(
        connect=False
    )
    ins = [f for f in list_flows(db) if f["direction"] == "in"]
    assert len(ins) == n_used


def test_address_count_is_not_capped_at_1000() -> None:
    """A wallet with 1500 used receive addresses: all found, count > 1000."""
    acct = _account(6)
    chain = ChainMock()
    n_used = 1500
    for i in range(n_used):
        addr = ADDR["p2wpkh"](acct.derive([0, i]))
        chain.add(_tx([("44" * 32, i)], [(addr, 1_000)]), "2022-01-01", [addr])
    reg = WalletRegistry()
    reg.register_xpub("viele", _xpub(acct))
    db = connect()
    init_schema(db)
    client = ElectrumClient(host="mock", port=1, transport=chain)
    client.connect()
    summary = SyncPipeline(reg, db, client=client, deriver=HdDeriver(gap_limit=20)).run(
        connect=False
    )
    assert len([f for f in list_flows(db) if f["direction"] == "in"]) == n_used
    assert summary.addresses_derived > 1000
    assert not any("Sicherheitsgrenze" in n for n in summary.notes)
