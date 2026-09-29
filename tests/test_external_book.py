"""External destination addresses: ext-NNN names, CSV import/export."""

from __future__ import annotations

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.external_book import ExternalBook, parse_csv, to_csv
from btc_origin.merger import Ledger
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow

EXT1 = "35L6hCgDNdWVyrF4AQqsGEKCw2Cutba3GK"
EXT2 = "bc1qzs4lwwnll38kxtkvd8997pggr9fs78480dhhu0"
OWN = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def test_build_numbers_by_first_exit_and_keeps_user_names() -> None:
    book = ExternalBook()
    rows = [
        {"address": EXT2, "amount_sats": 5, "time": "2024-01-01"},
        {"address": EXT1, "amount_sats": 7, "time": "2023-01-01"},
        {"address": EXT1, "amount_sats": 3, "time": "2025-01-01"},
        {"address": "", "amount_sats": 1, "time": "2022-01-01"},  # außerhalb Cloud
    ]
    entries = book.build(rows)
    assert [(e.name, e.address, e.out_count, e.total_sats) for e in entries] == [
        ("ext-001", EXT1, 2, 10),
        ("ext-002", EXT2, 1, 5),
    ]
    book.set_names([("Kraken", EXT1)])
    names = {e.address: e.name for e in book.build(rows)}
    assert names == {EXT1: "Kraken", EXT2: "ext-002"}  # numbering stable


def test_parse_csv_variants_and_rejects_non_addresses() -> None:
    text = "﻿Name;Adresse\nKraken;35L6hCgDNdWVyrF4AQqsGEKCw2Cutba3GK\nBad;xprv9s21ZrQH143K\n;" + EXT2
    pairs, errors = parse_csv(text)
    assert pairs == [("Kraken", EXT1)]
    assert len(errors) == 2
    pairs, _ = parse_csv(f"Börse A,{EXT2}\n")
    assert pairs == [("Börse A", EXT2)]


def test_to_csv_roundtrip() -> None:
    book = ExternalBook()
    entries = book.build([{"address": EXT1, "amount_sats": 1, "time": "2024-01-01"}])
    text = to_csv(entries)
    assert text.splitlines()[0] == "name,adresse"
    # automatische Namen werden beim Einlesen übergangen; umbenannte Zeilen zählen
    assert parse_csv(text)[0] == []
    assert parse_csv(text.replace("ext-001", "Kraken"))[0] == [("Kraken", EXT1)]


def test_api_names_exits_and_import_export() -> None:
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = PriceOracle(historical_fetcher=lambda on: (1.0, 1.0), spot_fetcher=lambda: (None, None))
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute("INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)", (OWN,))
        persist_ledger(
            state.db,
            Ledger(
                flows=[
                    Flow("buy", OWN, "in", 100_000, wallet_id=1, block_time="2023-01-01T00:00:00Z", vout=0, tx_total_output_sats=100_000),
                    Flow("sell", OWN, "out", 100_000, wallet_id=1, block_time="2024-01-01T00:00:00Z", vin_index=0, tx_total_output_sats=99_000),
                ]
            ),
        )
        state.last_tx_io = {"sell": {"output_addresses": [EXT1]}}
        client.post("/api/enrich")

        out = next(r for r in client.get("/api/cloud/flows").json()["flows"] if r["direction"] == "out")
        assert out["external_name"] == "ext-001"
        assert "price_status" in client.get("/api/cloud/flows").json()

        ext = client.get("/api/external").json()["external"]
        assert ext[0]["name"] == "ext-001" and ext[0]["address"] == EXT1

        r = client.post("/api/external/csv")
        assert r.headers["x-btc-herkunft-disk-written"] == "false"
        assert EXT1 in r.content.decode("utf-8-sig")

        res = client.post("/api/external/import", json={"csv": f"name,adresse\nKraken,{EXT1}\nIch,{OWN}\n"}).json()
        assert res["imported"] == 1
        assert any("eingefügten Wallet" in e for e in res["errors"])
        out = next(r for r in client.get("/api/cloud/flows").json()["flows"] if r["direction"] == "out")
        assert out["external_name"] == "Kraken"

        client.delete("/api/session")
        assert client.get("/api/external").json()["count"] == 0


EXT3 = "bc1qpn7amn0egma99wz0dlje8vcvd9u9nlljz969r4"


def test_senders_with_common_inputs_are_bundled_and_share_numbering() -> None:
    book = ExternalBook()
    in_rows = [
        # exchange withdrawal funded by two exchange addresses → one sender
        {"source_addresses": [EXT1, EXT3], "amount_sats": 100, "time": "2022-01-01"},
        {"source_addresses": [EXT3], "amount_sats": 50, "time": "2022-06-05"},
        {"source_addresses": ["coinbase"], "amount_sats": 7, "time": "2022-07-05"},
    ]
    out_rows = [
        {"address": EXT2, "amount_sats": 30, "time": "2023-01-01"},
        {"address": EXT1, "amount_sats": 20, "time": "2024-01-01"},  # back to sender
    ]
    entries = book.build(out_rows, in_rows)
    assert [(e.name, sorted(e.addresses), e.in_count, e.in_sats, e.out_count, e.total_sats) for e in entries] == [
        ("ext-001", sorted([EXT1, EXT3]), 2, 150, 1, 20),
        ("ext-002", [EXT2], 0, 0, 1, 30),
    ]
    book.set_names([("Börse", EXT3)])
    assert book.build(out_rows, in_rows)[0].name == "Börse"
    assert to_csv(book.build(out_rows, in_rows)).count("Börse") == 2  # one line per address


def test_resolve_source_addresses_from_parents() -> None:
    from embit.script import address_to_scriptpubkey
    from embit.transaction import Transaction, TransactionInput, TransactionOutput

    from btc_origin.tx_ingestor import resolve_source_addresses

    def tx(ins, outs):
        return Transaction(
            vin=[TransactionInput(bytes.fromhex(t), v) for t, v in ins],
            vout=[TransactionOutput(a, address_to_scriptpubkey(x)) for x, a in outs],
        )

    p1 = tx([("11" * 32, 0)], [(EXT1, 10)])
    p2 = tx([("22" * 32, 0)], [(EXT2, 5), (EXT3, 20)])
    child = tx([(p1.txid().hex(), 0), (p2.txid().hex(), 1)], [(OWN, 29)])
    coinbase = tx([("00" * 32, 0xFFFFFFFF)], [(OWN, 625)])
    store = {t.txid().hex(): t.serialize().hex() for t in (p1, p2, child, coinbase)}

    class Client:
        def get_transaction_hex(self, txid):
            return store[txid]

    io: dict = {}
    n = resolve_source_addresses(Client(), [child.txid().hex(), coinbase.txid().hex()], io)
    assert n == 2
    assert io[child.txid().hex()]["source_addresses"] == [EXT1, EXT3]
    assert io[child.txid().hex()]["source_complete"] is True
    assert io[coinbase.txid().hex()]["source_addresses"] == ["coinbase"]


BINANCE_HOT = "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h"
DEP1 = "3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"
DEP2 = "36YG9HzwdkWf3GFfFLbqpyBxwPqkimih3b"


def test_links_bundle_deposit_addresses_and_label_names_group() -> None:
    book = ExternalBook()
    out_rows = [
        {"address": DEP1, "amount_sats": 10, "time": "2021-11-10"},
        {"address": DEP2, "amount_sats": 5, "time": "2025-08-30"},
    ]
    in_rows = [
        {"source_addresses": [BINANCE_HOT], "amount_sats": 7, "time": "2022-12-02", "entry_vout_count": 40},
        {"source_addresses": [BINANCE_HOT], "amount_sats": 7, "time": "2022-12-28", "entry_vout_count": 2},
        {"source_addresses": [BINANCE_HOT], "amount_sats": 7, "time": "2023-02-01", "entry_vout_count": 2},
        {"source_addresses": [EXT3], "amount_sats": 1, "time": "2021-01-01", "entry_vout_count": 2},
    ]
    # deposit sweep: DEP1 + DEP2 swept into the hot wallet (single output)
    links = [[DEP1, "3Other" , DEP2, BINANCE_HOT]]
    entries = book.build(
        out_rows,
        in_rows,
        links=links,
        label_lookup=lambda a: "Binance" if a == BINANCE_HOT else None,
    )
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"ext-001", "Binance"}
    b = by_name["Binance"]
    assert sorted(b.addresses) == sorted([DEP1, DEP2, BINANCE_HOT])  # no foreign sweep inputs
    assert (b.in_count, b.out_count, b.total_sats) == (3, 2, 15)
    assert set(b.exchange_hints) == {"Sammelauszahlung", "Hot-Wallet"}
    assert by_name["ext-001"].exchange_hints == []
    # user names still win over labels
    book.set_names([("Mein Binance-Konto", DEP1)])
    assert any(e.name == "Mein Binance-Konto" for e in book.build(out_rows, in_rows, links=links))


def test_link_destination_sweeps_finds_sweep_inputs() -> None:
    from embit.script import address_to_scriptpubkey
    from embit.transaction import Transaction, TransactionInput, TransactionOutput

    from btc_origin.electrum_client import TxHistoryItem
    from btc_origin.tx_ingestor import link_destination_sweeps

    def tx(ins, outs):
        return Transaction(
            vin=[TransactionInput(bytes.fromhex(t), v) for t, v in ins],
            vout=[TransactionOutput(a, address_to_scriptpubkey(x)) for x, a in outs],
        )

    pay1 = tx([("11" * 32, 0)], [(DEP1, 10)])  # cloud → deposit 1
    pay2 = tx([("22" * 32, 0)], [(DEP2, 5)])  # cloud → deposit 2
    other = tx([("33" * 32, 0)], [(EXT3, 9)])  # someone else's deposit
    sweep = tx(
        [(pay1.txid().hex(), 0), (pay2.txid().hex(), 0), (other.txid().hex(), 0)],
        [(BINANCE_HOT, 23)],
    )
    store = {t.txid().hex(): t.serialize().hex() for t in (pay1, pay2, other, sweep)}

    class Client:
        def get_transaction_hex(self, txid):
            return store[txid]

        def get_histories(self, addrs):
            return {
                a: [TxHistoryItem(txid=sweep.txid().hex(), height=2)]
                for a in addrs
            }

    io = {pay1.txid().hex(): {}, pay2.txid().hex(): {}}
    groups = link_destination_sweeps(Client(), [DEP1, DEP2], io, {OWN})
    assert len(groups) == 1
    assert set(groups[0]) == {DEP1, DEP2, EXT3, BINANCE_HOT}


def test_date_rule_names_unnamed_counterparties_before_cutoff() -> None:
    from datetime import date

    from btc_origin.local_files import parse_date_rules

    rules, errors = parse_date_rules("name,bis\nFTX,2022-11-11\n")
    assert rules == [("FTX", date(2022, 11, 11))] and errors == []
    book = ExternalBook()
    outs = [
        {"address": "bc1qold", "time": "2022-05-05", "amount_sats": 1},
        {"address": "bc1qnew", "time": "2023-01-01", "amount_sats": 1},
    ]
    ins = [{"source_addresses": ["3Old"], "time": "2021-03-05", "amount_sats": 1}]
    entries = {e.address: e for e in book.build(outs, ins, date_rules=rules)}
    assert entries["bc1qold"].name == "FTX" and entries["bc1qold"].declared
    assert entries["3Old"].name == "FTX"
    assert entries["bc1qnew"].name.startswith("ext-") and entries["bc1qnew"].declared is None
    # an own name (CSV/UI) wins over the rule — but it is the taxpayer's statement too (³)
    book.set_names([("Kraken", "bc1qold")])
    entries = {e.address: e for e in book.build(outs, ins, date_rules=rules)}
    assert entries["bc1qold"].name == "Kraken"
    assert entries["bc1qold"].declared == "Angabe: Gegenstelle benannt als Kraken"


def test_csv_import_ignores_auto_names_from_export() -> None:
    from btc_origin.external_book import parse_csv

    text = f"name,adresse\next-032,{EXT1}\nrelai-swap,{EXT2}\n"
    pairs, errors = parse_csv(text)
    assert pairs == [("relai-swap", EXT2)] and errors == []


def test_unknown_own_name_is_not_a_declared_assignment() -> None:
    """Ein eigener Name wie „Dienst unbekannt 2024“ ordnet nichts zu — kein ³."""
    book = ExternalBook()
    outs = [{"address": "bc1qold", "time": "2024-04-08", "amount_sats": 1},
            {"address": "bc1qnew", "time": "2024-05-18", "amount_sats": 1}]
    book.set_names([("Dienst unbekannt 2024", "bc1qold"), ("Kraken", "bc1qnew")])
    entries = {e.address: e for e in book.build(outs, [])}
    assert entries["bc1qold"].name == "Dienst unbekannt 2024" and entries["bc1qold"].declared is None
    assert entries["bc1qnew"].declared == "Angabe: Gegenstelle benannt als Kraken"
