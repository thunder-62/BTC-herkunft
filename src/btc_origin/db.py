"""In-memory SQLite only — durable file DBs are not supported.

Architecture (Hard Boundary): after process exit nothing remains. No cache
files, no session files, no ``*.db`` on disk. Schema is applied to a
process-lifetime ``:memory:`` connection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterator

from btc_origin.config import MEMORY_SQLITE, get_settings

if TYPE_CHECKING:
    from btc_origin.hd_deriver import DerivedAddress
    from btc_origin.merger import Ledger
    from btc_origin.wallet_registry import WalletEntry

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    xpub TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('xpub', 'address', 'sparrow_csv')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_id INTEGER NOT NULL REFERENCES wallets(id),
    address TEXT NOT NULL,
    derivation_path TEXT,
    is_change INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT
);

CREATE TABLE IF NOT EXISTS txs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txid TEXT NOT NULL UNIQUE,
    block_height INTEGER,
    block_time TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txid TEXT NOT NULL,
    address TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    amount_sats INTEGER NOT NULL,
    wallet_id INTEGER REFERENCES wallets(id),
    is_internal INTEGER NOT NULL DEFAULT 0,
    block_time TEXT,
    block_height INTEGER,
    vout INTEGER,
    vin_index INTEGER,
    tx_total_output_sats INTEGER,
    external_amount_sats INTEGER,
    fee_sats INTEGER,
    lot_date TEXT,
    holding_days INTEGER,
    prev_txid TEXT,
    prev_vout INTEGER
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Soft-add columns for older in-memory schemas created mid-process.
_FLOW_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("block_time", "TEXT"),
    ("block_height", "INTEGER"),
    ("vout", "INTEGER"),
    ("vin_index", "INTEGER"),
    ("tx_total_output_sats", "INTEGER"),
    ("external_amount_sats", "INTEGER"),
    ("fee_sats", "INTEGER"),
    ("lot_date", "TEXT"),
    ("holding_days", "INTEGER"),
)


def _resolve_memory_path(db_path: str | None) -> str:
    """Force ``:memory:``. File paths are rejected / ignored by design."""
    candidate = (db_path if db_path is not None else get_settings().sqlite_path) or MEMORY_SQLITE
    if candidate != MEMORY_SQLITE and candidate != "file::memory:?cache=shared":
        # Soft ignore: never open a durable file DB.
        return MEMORY_SQLITE
    return candidate


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open an in-memory SQLite connection. File DBs are not supported."""
    path = _resolve_memory_path(db_path)
    # check_same_thread=False: FastAPI runs sync routes in a worker thread;
    # the :memory: connection is process-local session state (never shared
    # across processes / never durable).
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create stub tables on an in-memory connection."""
    own = conn is None
    c = conn or connect()
    try:
        c.executescript(SCHEMA_SQL)
        cols = {row[1] for row in c.execute("PRAGMA table_info(flows)").fetchall()}
        for name, col_type in _FLOW_EXTRA_COLUMNS:
            if name not in cols:
                c.execute(f"ALTER TABLE flows ADD COLUMN {name} {col_type}")
        c.commit()
    finally:
        if own:
            c.close()


def connection_scope(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def is_memory_only() -> bool:
    """True when the configured / effective DB mode is ephemeral."""
    return _resolve_memory_path(None) == MEMORY_SQLITE


def persist_wallet_addresses(
    conn: sqlite3.Connection,
    wallet: WalletEntry,
    addresses: list[DerivedAddress],
    *,
    created_at: str | None = None,
) -> int:
    """Upsert wallet + derived addresses into the in-memory DB. Returns wallet id."""
    ts = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wid = wallet.id
    if wid is None:
        cur = conn.execute(
            "INSERT INTO wallets (name, xpub, kind, created_at) VALUES (?,?,?,?)",
            (wallet.name, wallet.xpub, wallet.kind, ts),
        )
        wid = int(cur.lastrowid)
    else:
        existing = conn.execute("SELECT id FROM wallets WHERE id=?", (wid,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (?,?,?,?,?)",
                (wid, wallet.name, wallet.xpub, wallet.kind, ts),
            )
        else:
            conn.execute(
                "UPDATE wallets SET name=?, xpub=?, kind=? WHERE id=?",
                (wallet.name, wallet.xpub, wallet.kind, wid),
            )
        # Clear prior addresses for this wallet (session re-sync)
        conn.execute("DELETE FROM addresses WHERE wallet_id=?", (wid,))

    for d in addresses:
        conn.execute(
            "INSERT INTO addresses (wallet_id, address, derivation_path, is_change, first_seen) "
            "VALUES (?,?,?,?,?)",
            (wid, d.address, d.derivation_path, 1 if d.is_change else 0, ts),
        )
    if wallet.kind == "address" and wallet.address:
        conn.execute(
            "INSERT INTO addresses (wallet_id, address, derivation_path, is_change, first_seen) "
            "VALUES (?,?,?,?,?)",
            (wid, wallet.address, None, 0, ts),
        )
    conn.commit()
    return int(wid)


_FLOW_INSERT_SQL = (
    "INSERT INTO flows ("
    "txid, address, direction, amount_sats, wallet_id, is_internal, block_time, "
    "block_height, vout, vin_index, tx_total_output_sats, external_amount_sats, "
    "fee_sats, lot_date, holding_days, prev_txid, prev_vout"
    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def persist_ledger(conn: sqlite3.Connection, ledger: Ledger) -> None:
    """Replace session flows/txs with the merged ledger (memory only)."""
    conn.execute("DELETE FROM flows")
    # Keep txs table as a set of seen txids
    for flow in ledger.flows:
        conn.execute(
            "INSERT OR IGNORE INTO txs (txid, block_height, block_time, raw_json) VALUES (?,?,?,?)",
            (
                flow.txid,
                flow.block_height,
                flow.block_time,
                json.dumps({"wallet_id": flow.wallet_id}),
            ),
        )
        conn.execute(
            _FLOW_INSERT_SQL,
            (
                flow.txid,
                flow.address,
                flow.direction,
                flow.amount_sats,
                flow.wallet_id,
                1 if flow.is_internal else 0,
                flow.block_time,
                flow.block_height,
                flow.vout,
                flow.vin_index,
                flow.tx_total_output_sats,
                flow.external_amount_sats,
                flow.fee_sats,
                flow.lot_date,
                flow.holding_days,
                flow.prev_txid,
                flow.prev_vout,
            ),
        )
    conn.commit()


def list_flows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, txid, address, direction, amount_sats, wallet_id, is_internal, "
        "block_time, block_height, vout, vin_index, tx_total_output_sats, "
        "external_amount_sats, fee_sats, lot_date, holding_days, prev_txid, prev_vout "
        "FROM flows ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def clear_session_data(conn: sqlite3.Connection) -> None:
    """Wipe all session tables (still :memory:)."""
    for table in ("flows", "txs", "addresses", "labels", "wallets"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
