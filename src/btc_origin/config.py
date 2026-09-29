"""Load non-secret settings from environment / .env.

Hard rule: durable on-disk storage is forbidden. Session state lives in
process memory only (SQLite ``:memory:`` and/or pure RAM structures).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Canonical in-memory SQLite URI — the only supported database mode.
MEMORY_SQLITE = ":memory:"


class Settings(BaseSettings):
    """Application settings. Never load seeds or private keys.

    ``sqlite_path`` is fixed to ``:memory:``. File paths are not supported;
    durable DB is out of scope / forbidden by architecture.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Public Electrum — SSL port 50002 is typical; see electrum_client.DEFAULT_ELECTRUM_SERVERS
    electrum_host: str = "electrum.blockstream.info"
    electrum_port: int = 50002
    electrum_ssl: bool = True
    # Durability forbidden: always in-process memory. Env override of a file
    # path is ignored by db.connect(); kept only so misconfigured .env does
    # not crash settings load.
    sqlite_path: str = MEMORY_SQLITE
    gap_limit: int = 20
    # Heuristic ownership (co-spend / change) — OFF: only pasted xpubs/addresses
    # count as own. Co-inputs can belong to others (exchanges, payjoin, coinjoin).
    # OWNERSHIP_INFERENCE=true re-enables the heuristic.
    ownership_inference: bool = False
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000

    # Soft UX hint only — no hard cap on wallet/xpub count.
    large_wallet_warn_threshold: int = 50

    # Public read-only BTC reference price (no API key). Used only on
    # explicit report Save/Print — never persisted.
    price_oracle_url: str = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=usd,eur"
    )
    # Reverse-provenance BFS depth cap (TraceEngine); never unbounded.
    trace_max_depth: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
