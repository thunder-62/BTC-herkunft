"""Assert durable file DBs are not used — :memory: only."""

from __future__ import annotations

import sqlite3

from btc_origin.config import MEMORY_SQLITE, get_settings
from btc_origin.db import connect, init_schema, is_memory_only


def test_settings_default_is_memory() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings().sqlite_path == MEMORY_SQLITE
        assert is_memory_only() is True
    finally:
        get_settings.cache_clear()


def test_connect_uses_memory_even_if_file_path_passed() -> None:
    conn = connect("./data/should_not_exist.db")
    try:
        # SQLite :memory: connections report empty filename / :memory:
        rows = conn.execute("PRAGMA database_list").fetchall()
        file_field = rows[0]["file"] if isinstance(rows[0], sqlite3.Row) else rows[0][2]
        assert file_field == "" or file_field == ":memory:"
        init_schema(conn)
        conn.execute(
            "INSERT INTO wallets (name, xpub, kind, created_at) VALUES (?,?,?,?)",
            ("t", "xpubTEST", "xpub", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_separate_memory_connections_do_not_share_by_default() -> None:
    a = connect()
    b = connect()
    try:
        init_schema(a)
        a.execute(
            "INSERT INTO wallets (name, kind, created_at) VALUES (?,?,?)",
            ("only-a", "address", "2026-01-01T00:00:00Z"),
        )
        a.commit()
        init_schema(b)
        n = b.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
        assert n == 0
    finally:
        a.close()
        b.close()


def test_flow_roundtrip_preserves_vout_height_and_net_fields() -> None:
    from btc_origin.db import list_flows, persist_ledger
    from btc_origin.merger import Ledger
    from btc_origin.tx_ingestor import Flow

    conn = connect()
    try:
        init_schema(conn)
        ledger = Ledger(
            flows=[
                Flow(
                    txid="abcd",
                    address="bc1qtest",
                    direction="in",
                    amount_sats=1234,
                    wallet_id=1,
                    block_time="2024-02-01T00:00:00Z",
                    block_height=800000,
                    vout=2,
                    tx_total_output_sats=5000,
                    external_amount_sats=None,
                    fee_sats=None,
                    lot_date="2024-02-01",
                    holding_days=10,
                ),
                Flow(
                    txid="ef01",
                    address="bc1qtest",
                    direction="out",
                    amount_sats=1234,
                    wallet_id=1,
                    block_time="2024-03-01T00:00:00Z",
                    block_height=801000,
                    vin_index=0,
                    tx_total_output_sats=1200,
                    external_amount_sats=1200,
                    fee_sats=34,
                ),
            ]
        )
        persist_ledger(conn, ledger)
        rows = list_flows(conn)
        assert len(rows) == 2
        inn = rows[0]
        assert inn["vout"] == 2
        assert inn["block_height"] == 800000
        assert inn["tx_total_output_sats"] == 5000
        assert inn["lot_date"] == "2024-02-01"
        assert inn["holding_days"] == 10
        out = rows[1]
        assert out["vin_index"] == 0
        assert out["external_amount_sats"] == 1200
        assert out["fee_sats"] == 34

        from btc_origin.enrichment import flows_from_db_rows

        restored = flows_from_db_rows(rows)
        assert restored[0].vout == 2
        assert restored[0].block_height == 800000
        assert restored[1].vin_index == 0
        assert restored[1].external_amount_sats == 1200
        assert restored[1].fee_sats == 34
    finally:
        conn.close()
