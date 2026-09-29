"""Spurensuche (trail.py) auf einer nachgebauten Mini-Blockchain."""

from __future__ import annotations

import hashlib

from embit import ec
from embit.script import address_to_scriptpubkey, p2wpkh
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from btc_origin.trail import search


def _addr(n: int) -> str:
    key = ec.PrivateKey(hashlib.sha256(f"btc-origin trail test {n}".encode()).digest())
    return p2wpkh(key.get_public_key()).address()


EXT, LABELED, OWN, NEXT, OTHER = (_addr(i) for i in range(5))


class FakeChain:
    def __init__(self) -> None:
        self.txs: dict[str, str] = {}
        self.hist: dict[str, list[tuple[str, int]]] = {}
        self.height = 800_000

    def add(self, inputs: list[tuple[str, int]], outputs: list[tuple[str, int]]) -> str:
        tx = Transaction(
            vin=[TransactionInput(bytes.fromhex(t), v) for t, v in inputs],
            vout=[TransactionOutput(v, address_to_scriptpubkey(a)) for a, v in outputs],
        )
        txid = tx.txid().hex()
        self.txs[txid] = tx.to_string()
        self.height += 1
        touched = {a for a, _v in outputs}
        for t, v in inputs:
            if t in self.txs:
                prev = Transaction.from_string(self.txs[t])
                touched.add(prev.vout[v].script_pubkey.address())
        for a in touched:
            self.hist.setdefault(a, []).append((txid, self.height))
        return txid

    # ChainProvider
    def history(self, address: str) -> list[tuple[str, int | None]]:
        return list(self.hist.get(address, []))

    def tx_hex(self, txid: str) -> str:
        return self.txs[txid]

    def block_time(self, height: int | None) -> str | None:
        return "2024-01-01T00:00:00Z" if height else None


def _labels(a: str) -> str | None:
    return "Binance" if a == LABELED else None


def test_forward_to_labeled_exchange() -> None:
    c = FakeChain()
    t0 = c.add([("11" * 32, 0)], [(EXT, 100_000)])  # unser Abfluss an EXT
    c.add([(t0, 0)], [(LABELED, 99_000)])  # EXT leitet weiter an Binance
    res = search(c, name="ext-001", out_addresses=[EXT], in_addresses=[], label_of=_labels, own={OWN})
    assert res.guess == "Binance" and res.confidence == "hoch"
    assert res.steps[0]["text"].startswith("weitergeleitet an Binance (Label)")


def test_forward_two_hops_gives_medium_confidence() -> None:
    c = FakeChain()
    t0 = c.add([("11" * 32, 0)], [(EXT, 100_000)])
    t1 = c.add([(t0, 0)], [(NEXT, 99_000)])
    c.add([(t1, 0)], [(LABELED, 98_000)])
    res = search(c, name="ext-001", out_addresses=[EXT], in_addresses=[], label_of=_labels, own=set())
    assert res.guess == "Binance" and res.confidence == "mittel"


def test_forward_consolidation_looks_like_exchange() -> None:
    c = FakeChain()
    t0 = c.add([("11" * 32, 0)], [(EXT, 100_000)])
    inputs = [(t0, 0)] + [(f"{i:02x}" * 32, 0) for i in range(20, 31)]
    c.add(inputs, [(OTHER, 1_000_000)])
    res = search(c, name="ext-002", out_addresses=[EXT], in_addresses=[], label_of=_labels, own=set())
    assert res.guess == "Börse oder Zahlungsdienst (Name unbekannt)"
    assert "12 Eingänge" in res.reason


def test_forward_back_to_own_wallet() -> None:
    c = FakeChain()
    t0 = c.add([("11" * 32, 0)], [(EXT, 100_000)])
    c.add([(t0, 0)], [(OWN, 99_000)])
    res = search(c, name="ext-003", out_addresses=[EXT], in_addresses=[], label_of=_labels, own={OWN})
    assert res.guess == "eigene, nicht erfasste Wallet?" and res.confidence == "mittel"


def test_forward_unspent_quiet_address_is_private() -> None:
    c = FakeChain()
    c.add([("11" * 32, 0)], [(EXT, 100_000)])
    res = search(c, name="ext-004", out_addresses=[EXT], in_addresses=[], label_of=_labels, own=set())
    assert res.guess == "private Wallet (keine Börse erkennbar)" and res.confidence == "niedrig"
    assert "liegen noch" in res.steps[0]["text"]


def test_backward_sender_funded_by_labeled_exchange() -> None:
    c = FakeChain()
    tl = c.add([("22" * 32, 0)], [(LABELED, 500_000)])
    tf = c.add([(tl, 0)], [(EXT, 400_000), (OTHER, 99_000)])  # Börse zahlt an EXT
    c.add([(tf, 0)], [(OWN, 390_000)])  # EXT zahlt an uns (unser Zufluss)
    res = search(c, name="ext-005", out_addresses=[], in_addresses=[EXT], label_of=_labels, own={OWN})
    assert res.direction == "in" and res.guess == "Binance"


def test_network_error_keeps_partial_result() -> None:
    class Broken(FakeChain):
        def history(self, address: str) -> list[tuple[str, int | None]]:
            raise OSError("Electrum weg")

    res = search(Broken(), name="ext-006", out_addresses=[EXT], in_addresses=[], label_of=_labels, own=set())
    assert len(res.errors) == 1 and "Electrum weg" in res.errors[0] and res.guess is None


def test_api_trail_unknown_counterparty_and_empty_search_list() -> None:
    from fastapi.testclient import TestClient

    from btc_origin.api.app import app

    with TestClient(app) as client:
        r = client.post("/api/external/trail", json={"name": "ext-999"})
        assert r.status_code == 404
        r = client.get("/api/external/suchliste.csv")
        assert r.status_code == 200
        assert r.content.decode("utf-8-sig").startswith("Gegenstelle;Richtung;Datum;BTC")


def test_too_many_history_entries_counts_as_exchange_hint() -> None:
    class Huge(FakeChain):
        def history(self, address: str) -> list[tuple[str, int | None]]:
            if address == EXT:
                raise RuntimeError("{'code': 1, 'message': 'Too many history entries'}")
            return super().history(address)

    res = search(Huge(), name="ext-038", out_addresses=[EXT], in_addresses=[], label_of=_labels, own=set())
    assert res.errors == [] and res.guess == "Börse oder Zahlungsdienst (Name unbekannt)"
    assert "nicht liefert" in res.reason


def test_backward_via_transactions_without_history() -> None:
    class NoHistory(FakeChain):
        def history(self, address: str) -> list[tuple[str, int | None]]:
            raise RuntimeError("Too many history entries")

    c = NoHistory()
    tl = c.add([("22" * 32, 0)], [(LABELED, 500_000)])
    tf = c.add([(tl, 0)], [(EXT, 400_000), (OTHER, 99_000)])
    tin = c.add([(tf, 0)], [(OWN, 390_000)])  # unser Zufluss von EXT
    res = search(c, name="ext-038", out_addresses=[], in_addresses=[EXT], label_of=_labels,
                 own={OWN}, in_txids=[tin])
    assert res.guess == "Binance" and res.confidence == "mittel"  # Label in Schritt 2
    assert res.steps[0]["txid"] == tin
