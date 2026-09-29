"""Session-RAM cache for Electrum chain queries (no disk, no persistence).

Avoids repeated Electrum round-trips within one app session:

* **Transactions** (``blockchain.transaction.get``) are immutable per txid —
  raw hex is cached for the whole session. Verbose dicts are cached only when
  confirmed (``blocktime`` present), because confirmations/time change.
* **Block headers** (``blockchain.block.header``) → timestamp per height,
  cached for the whole session.
* **Address histories** (``blockchain.scripthash.get_history``) change when
  new txs arrive, so they are cached only for the duration of one sync run
  (``begin_run`` clears them). This removes the double query between the
  gap-limit probe and the ingest pass.

Hard Boundary: RAM only. ``clear()`` on session reset / process exit.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChainCacheStats:
    hits: int = 0
    misses: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}


@dataclass
class ChainCache:
    """Thread-safe in-memory cache shared by all Electrum clients of a session."""

    _tx_hex: dict[str, str] = field(default_factory=dict)
    _tx_verbose: dict[str, dict[str, Any]] = field(default_factory=dict)
    _header_time: dict[int, str] = field(default_factory=dict)
    _history: dict[str, list[Any]] = field(default_factory=dict)
    stats: ChainCacheStats = field(default_factory=ChainCacheStats)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- lifecycle ---
    def begin_run(self) -> None:
        """Start a sync run: drop mutable histories, reset stats."""
        with self._lock:
            self._history.clear()
            self.stats = ChainCacheStats()

    def clear(self) -> None:
        with self._lock:
            self._tx_hex.clear()
            self._tx_verbose.clear()
            self._header_time.clear()
            self._history.clear()
            self.stats = ChainCacheStats()

    def _hit(self, value: Any) -> Any:
        if value is None:
            self.stats.misses += 1
        else:
            self.stats.hits += 1
        return value

    # --- transactions ---
    def get_tx_hex(self, txid: str) -> str | None:
        with self._lock:
            return self._hit(self._tx_hex.get(txid))

    def put_tx_hex(self, txid: str, tx_hex: str) -> None:
        if txid and tx_hex:
            with self._lock:
                self._tx_hex[txid] = tx_hex

    def get_tx_verbose(self, txid: str) -> dict[str, Any] | None:
        with self._lock:
            return self._hit(self._tx_verbose.get(txid))

    def put_tx_verbose(self, txid: str, verbose: dict[str, Any]) -> None:
        if not txid or not isinstance(verbose, dict):
            return
        hx = verbose.get("hex")
        with self._lock:
            if hx:
                self._tx_hex[txid] = str(hx)
            # Unconfirmed verbose payloads change (confirmations / time).
            if verbose.get("blocktime") or verbose.get("time"):
                self._tx_verbose[txid] = dict(verbose)

    def peek_tx_hex(self, txid: str) -> str | None:
        """Lookup without touching hit/miss stats (prefetch planning)."""
        with self._lock:
            return self._tx_hex.get(txid)

    # --- headers ---
    def get_header_time(self, height: int) -> str | None:
        with self._lock:
            return self._hit(self._header_time.get(int(height)))

    def put_header_time(self, height: int, iso: str) -> None:
        if iso:
            with self._lock:
                self._header_time[int(height)] = iso

    def peek_header_time(self, height: int) -> str | None:
        with self._lock:
            return self._header_time.get(int(height))

    # --- histories (per sync run) ---
    def get_history(self, scripthash: str) -> list[Any] | None:
        with self._lock:
            cached = self._history.get(scripthash)
            self._hit(cached)
            return list(cached) if cached is not None else None

    def put_history(self, scripthash: str, items: list[Any]) -> None:
        with self._lock:
            self._history[scripthash] = list(items)

    def as_dict(self) -> dict[str, int]:
        with self._lock:
            return {
                "transactions": len(self._tx_hex),
                "headers": len(self._header_time),
                "histories": len(self._history),
                **self.stats.as_dict(),
            }
