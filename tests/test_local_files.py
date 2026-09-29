"""local/ folder: names CSV + exchange exports, read-only and git-ignored."""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.external_book import ExternalBook
from btc_origin.local_files import load_local_data, match_amount_rows
from btc_origin.merger import Ledger
from btc_origin.price_oracle import PriceOracle
from btc_origin.tx_ingestor import Flow

OWN = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
SENDER = "3PxY3oQaaxnmPhEcsCkHuCHBYzPFb5ME8k"
DEST = "3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"
BUY_TXID = "ab" * 32
SELL_TXID = "cd" * 32


def _make_local(base: Path) -> None:
    (base / "boersen").mkdir(parents=True)
    (base / "externe-adressen.csv").write_text(f"name;adresse\nMein Cold Storage Kumpel;{DEST}\n", "utf-8")
    # Export layout does not matter — only 64-hex txids are picked up.
    (base / "boersen" / "relai.csv").write_text(
        f"Datum;Typ;Betrag;TxID\n2023-01-01;Auszahlung;0,001;{BUY_TXID.upper()}\n", "cp1252"
    )
    (base / "boersen" / "notizen.pdf").write_bytes(b"%PDF ignored")


def test_load_local_data(tmp_path: Path) -> None:
    _make_local(tmp_path)
    data = load_local_data(tmp_path)
    assert data.names == [("Mein Cold Storage Kumpel", DEST)]
    assert data.txid_names == {BUY_TXID: "Relai"}
    assert all(len(f.pop("sha256")) == 64 for f in data.exchange_files)  # Prüfsumme je Datei
    assert data.exchange_files == [
        {"file": "relai.csv", "name": "Relai", "txids": 1, "amount_rows": 0, "trades": 0,
         "buys": 0, "sells": 0, "withdrawals": 0, "deposits": 0}
    ]
    assert load_local_data(tmp_path / "missing").names == []  # no folder = fine


def test_name_priority_session_over_local_over_export_over_label() -> None:
    book = ExternalBook()
    in_rows = [{"source_addresses": [SENDER], "amount_sats": 1, "time": "2023-01-01", "txid": BUY_TXID}]
    label = lambda a: "Label-X"  # noqa: E731
    assert book.build([], in_rows, label_lookup=label, txid_names={BUY_TXID: "Relai"})[0].name == "Relai"
    book.set_local_names([("Aus Datei", SENDER)])
    assert book.build([], in_rows, txid_names={BUY_TXID: "Relai"})[0].name == "Aus Datei"
    book.set_names([("Aus Sitzung", SENDER)])
    assert book.build([], in_rows)[0].name == "Aus Sitzung"
    book.clear()  # session names gone, local file names stay
    assert book.build([], in_rows)[0].name == "Aus Datei"


def test_api_uses_local_folder(tmp_path: Path, monkeypatch) -> None:
    _make_local(tmp_path)
    monkeypatch.setenv("BTC_ORIGIN_LOCAL_DIR", str(tmp_path))
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
                    Flow(BUY_TXID, OWN, "in", 100_000, wallet_id=1, block_time="2023-01-01T00:00:00Z", vout=0, tx_total_output_sats=100_000),
                    Flow(SELL_TXID, OWN, "out", 100_000, wallet_id=1, block_time="2024-01-01T00:00:00Z", vin_index=0, tx_total_output_sats=99_000),
                ]
            ),
        )
        state.last_tx_io = {
            BUY_TXID: {"source_addresses": [SENDER], "output_addresses": [OWN], "vout_count": 1},
            SELL_TXID: {"output_addresses": [DEST]},
        }
        client.post("/api/enrich")
        body = client.get("/api/external").json()
        names = {e["address"]: e["name"] for e in body["external"]}
        assert names == {SENDER: "Relai", DEST: "Mein Cold Storage Kumpel"}
        assert body["local"]["names_count"] == 1
        assert body["local"]["exchange_files"][0]["name"] == "Relai"

        # Edit the file → reload picks it up; the app never writes there.
        before = sorted(p.name for p in tmp_path.rglob("*"))
        (tmp_path / "externe-adressen.csv").write_text(f"Neu,{DEST}\n", "utf-8")
        client.post("/api/local/reload")
        names = {e["address"]: e["name"] for e in client.get("/api/external").json()["external"]}
        assert names[DEST] == "Neu"
        client.post("/api/external/csv")
        assert sorted(p.name for p in tmp_path.rglob("*")) == before




def _rows():
    return [
        {"direction": "in", "txid": "a1", "time": "2024-03-01", "amount_sats": 1_000_000},
        {"direction": "in", "txid": "a2", "time": "2024-06-10", "amount_sats": 250_000},
        # deposit to exchange split over 2 FIFO lots (same tx)
        {"direction": "out", "txid": "d1", "time": "2024-09-01", "amount_sats": 3_000_000},
        {"direction": "out", "txid": "d1", "time": "2024-09-01", "amount_sats": 2_000_000},
    ]


def test_match_payouts_with_fee_and_deposits() -> None:
    export = [
        ("Bitvavo", [_date(2024, 3, 2)], [1_050_000]),  # +0.0005 BTC withdrawal fee, 1 day later
        ("Bitvavo", [_date(2024, 9, 1)], [5_000_000]),  # deposit, sum of lots
        ("Bitvavo", [_date(2023, 1, 1)], [1_000_000]),  # no cloud tx that day
    ]
    assert match_amount_rows(export, _rows()) == {"a1": "Bitvavo", "d1": "Bitvavo"}


def test_no_match_when_ambiguous_or_outside_tolerance() -> None:
    rows = _rows() + [{"direction": "in", "txid": "a3", "time": "2024-03-02", "amount_sats": 1_000_000}]
    # two identical payouts within ±2 days → ambiguous → skipped
    assert match_amount_rows([("Relai", [_date(2024, 3, 1)], [1_000_000])], rows) == {}
    # received MORE than exported → not a payout fee pattern
    assert match_amount_rows([("Relai", [_date(2024, 6, 10)], [200_000])], _rows()) == {}
    # 3 days apart → outside ±2 days
    assert match_amount_rows([("Relai", [_date(2024, 6, 13)], [250_000])], _rows()) == {}
    # same tx claimed by two different exchanges → skipped
    both = [("Relai", [_date(2024, 6, 10)], [250_000]), ("Bison", [_date(2024, 6, 10)], [250_000])]
    assert match_amount_rows(both, _rows()) == {}


def test_export_rows_parsed_from_file(tmp_path: Path) -> None:
    (tmp_path / "boersen").mkdir()
    (tmp_path / "boersen" / "bison.csv").write_text(
        "Datum;Typ;Menge;Kurs\n02.03.2024 11:00;Auszahlung;0,01050000;58.000,00\n", "utf-8"
    )
    data = load_local_data(tmp_path)
    assert data.amount_rows == [("Bison", [_date(2024, 3, 2)], [1_050_000], [], "in")]
    assert data.exchange_files[0]["amount_rows"] == 1
    assert match_amount_rows(data.amount_rows, _rows()) == {"a1": "Bison"}


RELAI_HEADER = (
    "Date,Transaction Type,BTC Amount,BTC Price,Currency Pair,Fiat Amount (excl. fees),"
    "Fiat Currency,Fee,Fee Currency,Destination,Operation ID,Counterparty"
)


def test_relai_export_matches_by_destination_address(tmp_path: Path) -> None:
    """Relai layout: payouts to the SAME own address (savings plan) are told
    apart by date; buy rows without destination do not interfere."""
    (tmp_path / "boersen").mkdir()
    lines = [
        RELAI_HEADER,
        f"2024-03-01 09:00:00,Buy,0.00100000,58000.00,BTC/EUR,58.00,EUR,0.87,EUR,{OWN},op-7f3a-11,Relai AG",
        f"2024-03-08 09:10:00,Buy,0.00098000,59000.00,BTC/EUR,58.00,EUR,0.87,EUR,{OWN},op-7f3a-12,Relai AG",
        "2024-03-09 09:10:00,Buy,0.00098000,59000.00,BTC/EUR,58.00,EUR,0.87,EUR,,op-7f3a-13,Relai AG",
    ]
    (tmp_path / "boersen" / "relai.csv").write_text("\n".join(lines) + "\n", "utf-8")
    data = load_local_data(tmp_path)
    assert len(data.exchange_files[0].pop("sha256")) == 64
    assert data.exchange_files[0] == {
        "file": "relai.csv", "name": "Relai", "txids": 0, "amount_rows": 3, "trades": 3,
        "buys": 3, "sells": 0, "withdrawals": 0, "deposits": 0
    }
    rows = [
        {"direction": "in", "txid": "r1", "time": "2024-03-01", "amount_sats": 100_000, "address": OWN},
        {"direction": "in", "txid": "r2", "time": "2024-03-08", "amount_sats": 98_000, "address": OWN},
        {"direction": "in", "txid": "x9", "time": "2024-03-08", "amount_sats": 98_000, "address": "bc1qother"},
    ]
    # r1/r2 via destination address + date; x9 (other address, same amount/day)
    # stays unnamed — the address-less third row is ambiguous (r2 vs x9).
    assert match_amount_rows(data.amount_rows, rows) == {"r1": "Relai", "r2": "Relai"}


BISON_HEADER = (
    "Transaction ID\tTransaction type\tCurrency\tAsset\tEur (amount)\tAsset (amount)\t"
    "Asset (market price)\tFee\tDate (UTC - Coordinated Universal Time)"
)


def test_bison_export_header_aware(tmp_path: Path) -> None:
    """Bison layout (tab-separated): BTC rows only, 2-decimal amounts, type
    decides direction; Bison's internal transaction id is not a txid."""
    (tmp_path / "boersen").mkdir()
    lines = [
        BISON_HEADER,
        "b1a2c3d4-0001\tBuy\tEUR\tBTC\t2500.00\t0.05\t50000.00\t0.00\t2024-03-01 09:00:00",
        "b1a2c3d4-0002\tWithdraw\tEUR\tBTC\t0.00\t0.05\t50100.00\t0.00\t2024-03-02 10:00:00",
        "b1a2c3d4-0003\tWithdraw\tEUR\tETH\t0.00\t0.05\t3000.00\t0.00\t2024-03-02 10:00:00",
        "b1a2c3d4-0004\tDeposit\tEUR\tBTC\t0.00\t0.02\t61000.00\t0.00\t2024-09-01 12:00:00",
    ]
    (tmp_path / "boersen" / "bison.csv").write_text("\n".join(lines) + "\n", "utf-8")
    data = load_local_data(tmp_path)
    assert data.exchange_files[0]["amount_rows"] == 3  # ETH row skipped
    assert data.amount_rows[1] == ("Bison", [_date(2024, 3, 2)], [5_000_000], [], "in")
    rows = [
        # payout 0.05 BTC arrived minus network fee
        {"direction": "in", "txid": "w1", "time": "2024-03-02", "amount_sats": 4_998_000, "address": OWN},
        # deposit 0.02 BTC to Bison
        {"direction": "out", "txid": "d1", "time": "2024-09-01", "amount_sats": 2_000_000, "address": DEST},
        # an unrelated out of 0.05 on the payout day must NOT match the Withdraw row
        {"direction": "out", "txid": "x1", "time": "2024-03-02", "amount_sats": 5_000_000, "address": DEST},
    ]
    assert match_amount_rows(data.amount_rows, rows) == {"w1": "Bison", "d1": "Bison"}


def test_semicolon_german_bison_variant(tmp_path: Path) -> None:
    (tmp_path / "boersen").mkdir()
    text = (
        "Transaction ID;Transaction type;Currency;Asset;Eur (amount);Asset (amount);"
        "Asset (market price);Fee;Date (UTC - Coordinated Universal Time)\n"
        "x-1;Withdraw;EUR;BTC;0,00;0,0123;58.000,00;0,00;02.03.2024 10:00\n"
    )
    (tmp_path / "boersen" / "bison.csv").write_text(text, "cp1252")
    data = load_local_data(tmp_path)
    assert data.amount_rows == [("Bison", [_date(2024, 3, 2)], [1_230_000], [], "in")]


def test_rules_file_plural_name_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "zuordnungen.csv").write_text("name,bis\r\nFTX,2022-11-07", "utf-8")
    data = load_local_data(tmp_path)
    assert [(n, d.isoformat()) for n, d in data.date_rules] == [("FTX", "2022-11-07")]


def test_exchange_name_ignores_file_suffixes() -> None:
    from pathlib import Path

    from btc_origin.local_files import exchange_name_from_file

    assert exchange_name_from_file(Path("binance-auszahlungen.csv")) == "Binance"
    assert exchange_name_from_file(Path("bitvavo_2024.csv")) == "Bitvavo"
    assert exchange_name_from_file(Path("21bitcoin.csv")) == "21bitcoin"
    assert exchange_name_from_file(Path("relai.csv")) == "Relai"


def test_binance_order_history_ignored_when_statement_present(tmp_path) -> None:
    from btc_origin.local_files import load_local_data

    ex = tmp_path / "boersen"
    ex.mkdir()
    (ex / "binance.csv").write_text(
        "User ID,Time,Account,Operation,Coin,Change,Remark\n"
        "1,2022-01-10 09:00:00,Spot,Transaction Buy,BTC,0.01000000,\n"
        "1,2022-01-10 09:00:00,Spot,Transaction Spend,EUR,-400.00,\n",
        encoding="utf-8",
    )
    (ex / "binance-orders.csv").write_text(
        "Time,OrderNo,Pair,Type,Side,Order Price,Order Amount,Time,Executed,Average Price,Trading total,Status\n"
        "2022-01-10 09:00:00,1,BTCEUR,MARKET,BUY,0,0.01BTC,2022-01-10 09:00:00,0.01000000BTC,40000,400.00EUR,FILLED\n",
        encoding="utf-8",
    )
    data = load_local_data(tmp_path)
    assert [(t.kind, t.sats) for t in data.trades] == [("buy", 1_000_000)]
    assert any("Order-Historie nicht verwendet" in e for e in data.errors)


def test_explanations_accept_mixed_delimiters_per_line() -> None:
    """„Name;Datum;Art;Text“ und „Name,,Art,Text“ in derselben Datei; im Text dürfen
    Kommas und Semikolons vorkommen."""
    from btc_origin.local_files import parse_explanations

    text = (
        "Börse;Datum;Art;Erläuterung\n"
        "Wallet A;2024-06-07;Zufluss;Eigene Bitcoin, übertragen\n"
        "Dienst X,,Abfluss,Übertragung an einen Dienst; Empfänger unbekannt, vorsichtshalber Veräußerung\n"
    )
    rules, errors = parse_explanations(text)
    assert errors == []
    assert [(n, d.isoformat() if d else None, a) for n, d, a, _t in rules] == [
        ("Wallet A", "2024-06-07", "Zufluss"), ("Dienst X", None, "Abfluss")]
    assert rules[0][3] == "Eigene Bitcoin, übertragen"
    assert rules[1][3] == "Übertragung an einen Dienst; Empfänger unbekannt, vorsichtshalber Veräußerung"


def test_two_exports_of_one_exchange_are_counted_per_file(tmp_path) -> None:
    """Früheres und aktuelles Konto derselben Börse: Anhang A zählt je Datei, nicht die
    Börsensumme in beiden Zeilen."""
    from btc_origin.local_files import load_local_data

    head = "Timezone,Date,Time,Type,Currency,Amount,Price (EUR),EUR received / paid,Fee currency,Fee amount,Status,Transaction ID,Address\n"
    (tmp_path / "boersen").mkdir()
    (tmp_path / "boersen" / "bitvavo-alt.csv").write_text(
        head
        + "Europe/Berlin,2023-01-19,10:05:00,buy,BTC,0.01,42000,-420.00,EUR,1.05,Completed,x1,\n"
        + "Europe/Berlin,2023-01-20,10:05:00,buy,BTC,0.01,42000,-420.00,EUR,1.05,Completed,x2,\n"
        + "Europe/Berlin,2023-01-21,10:05:00,withdrawal,BTC,-0.02,,,BTC,0.0001,Completed,x3,\n",
        encoding="utf-8",
    )
    (tmp_path / "boersen" / "bitvavo.csv").write_text(
        head + "Europe/Berlin,2024-09-01,10:05:00,buy,BTC,0.01,50000,-500.00,EUR,1.25,Completed,x4,\n",
        encoding="utf-8",
    )
    data = load_local_data(tmp_path)
    counts = {f["file"]: (f["name"], f["buys"], f["withdrawals"]) for f in data.exchange_files}
    assert counts == {"bitvavo-alt.csv": ("Bitvavo", 2, 1), "bitvavo.csv": ("Bitvavo", 1, 0)}
    assert len(data.trades) == 4  # beide Dateien werden gemeinsam ausgewertet


def test_file_counts_follow_the_report_cut(tmp_path) -> None:
    """Bericht „bis“: Anhang A zählt je Datei nur die Zeilen bis zu diesem Tag — wie der
    Abgleich darunter; spätere Käufe tauchen auch als Zahl nicht auf."""
    from datetime import date

    from btc_origin.local_files import file_counts, load_local_data

    head = "Timezone,Date,Time,Type,Currency,Amount,Price (EUR),EUR received / paid,Fee currency,Fee amount,Status,Transaction ID,Address\n"
    (tmp_path / "boersen").mkdir()
    (tmp_path / "boersen" / "bitvavo.csv").write_text(
        head
        + "Europe/Berlin,2025-12-01,10:05:00,buy,BTC,0.01,50000,-500.00,EUR,1.25,Completed,x1,\n"
        + "Europe/Berlin,2025-12-02,10:05:00,withdrawal,BTC,-0.01,,,BTC,0.0001,Completed,x2,\n"
        + "Europe/Berlin,2026-01-05,10:05:00,buy,BTC,0.01,50000,-500.00,EUR,1.25,Completed,x3,\n"
        + "Europe/Berlin,2026-01-06,10:05:00,withdrawal,BTC,-0.01,,,BTC,0.0001,Completed,x4,\n",
        encoding="utf-8",
    )
    data = load_local_data(tmp_path)
    assert all(t.file == "bitvavo.csv" for t in data.trades)
    full = file_counts(data.exchange_files, data.trades)
    assert (full[0]["buys"], full[0]["withdrawals"]) == (2, 2)
    cut = file_counts(data.exchange_files, [t for t in data.trades if t.day <= date(2025, 12, 31)])
    assert (cut[0]["buys"], cut[0]["withdrawals"], cut[0]["file"]) == (1, 1, "bitvavo.csv")
