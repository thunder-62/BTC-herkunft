"""Public Electrum server client — history/balance read-only (SSL/TCP).

Session data stays in memory only. Never opens wallet files or writes disk
caches (an optional session-RAM ``ChainCache`` dedupes repeated queries).
Tests should inject a mock transport / mock client — no live network required
for CI.

Default public servers (user-configurable via settings / constructor):

* electrum.blockstream.info:50002 (SSL)
* electrum.emzy.de:50002 (SSL)
* fortress.qtornado.com:443 (SSL)
* electrum.bitar.nl:50002 (SSL)
* e2.keff.org:50002 (SSL)
* electrum.jochen-hoenicke.de:50006 (SSL)

Hard Boundary: read-only public chain data. No seeds, no signing.
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from btc_origin.chain_cache import ChainCache
from btc_origin.config import get_settings
from btc_origin.hd_deriver import address_to_scripthash

# Well-known public Electrum servers (host, port, use_ssl).
DEFAULT_ELECTRUM_SERVERS: list[tuple[str, int, bool]] = [
    ("electrum.blockstream.info", 50002, True),
    ("electrum.emzy.de", 50002, True),
    ("fortress.qtornado.com", 443, True),
    ("electrum.bitar.nl", 50002, True),
    ("e2.keff.org", 50002, True),
    ("electrum.jochen-hoenicke.de", 50006, True),
]


class ElectrumError(Exception):
    """Base error for Electrum client failures."""


class ElectrumConnectionError(ElectrumError):
    """Server unreachable / TLS / socket failure."""


class ElectrumProtocolError(ElectrumError):
    """Malformed response or JSON-RPC error from server."""


@dataclass
class AddressBalance:
    address: str
    confirmed_sats: int = 0
    unconfirmed_sats: int = 0
    scripthash: str | None = None


@dataclass
class TxHistoryItem:
    txid: str
    height: int | None = None
    fee: int | None = None


@dataclass
class ElectrumServerInfo:
    host: str
    port: int
    use_ssl: bool
    version: list[Any] | None = None


class Transport(Protocol):
    """Minimal line-oriented JSON transport (for mocks)."""

    def request(self, method: str, params: list[Any]) -> Any: ...

    def close(self) -> None: ...


class SslJsonTransport:
    """Synchronous JSON-RPC over SSL/TCP (newline-delimited Electrum protocol)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        use_ssl: bool = True,
        timeout: float = 20.0,
    ) -> None:
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.timeout = timeout
        self._sock: socket.socket | ssl.SSLSocket | None = None
        self._buf = b""
        self._next_id = 1

    def connect(self) -> None:
        try:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
            raw.settimeout(self.timeout)
            if self.use_ssl:
                ctx = ssl.create_default_context()
                # Public Electrum servers often use mismatched / self-signed certs.
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                self._sock = ctx.wrap_socket(raw, server_hostname=self.host)
            else:
                self._sock = raw
            self._buf = b""
        except OSError as exc:
            raise ElectrumConnectionError(
                f"Cannot connect to {self.host}:{self.port}: {exc}"
            ) from exc

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def request(self, method: str, params: list[Any]) -> Any:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        req_id = self._next_id
        self._next_id += 1
        payload = json.dumps(
            {"id": req_id, "method": method, "params": params},
            separators=(",", ":"),
        )
        try:
            self._sock.sendall(payload.encode("utf-8") + b"\n")
            while True:
                line = self._readline()
                msg = json.loads(line.decode("utf-8"))
                # Ignore unsolicited server notifications (no id / mismatched).
                if msg.get("id") != req_id:
                    continue
                if "error" in msg and msg["error"] is not None:
                    raise ElectrumProtocolError(str(msg["error"]))
                return msg.get("result")
        except (OSError, json.JSONDecodeError, TimeoutError) as exc:
            self.close()
            raise ElectrumConnectionError(f"Electrum request failed: {exc}") from exc

    def request_many(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        """Pipeline several requests on one connection (send all, then read).

        Returns results in call order; per-call JSON-RPC errors are returned as
        ``ElectrumProtocolError`` instances instead of raising, so one bad item
        does not abort the batch.
        """
        if not calls:
            return []
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        pending: dict[int, int] = {}
        lines: list[str] = []
        for idx, (method, params) in enumerate(calls):
            req_id = self._next_id
            self._next_id += 1
            pending[req_id] = idx
            lines.append(
                json.dumps(
                    {"id": req_id, "method": method, "params": params},
                    separators=(",", ":"),
                )
            )
        out: list[Any] = [None] * len(calls)
        try:
            self._sock.sendall(("\n".join(lines) + "\n").encode("utf-8"))
            while pending:
                msg = json.loads(self._readline().decode("utf-8"))
                for m in msg if isinstance(msg, list) else [msg]:
                    if not isinstance(m, dict) or m.get("id") not in pending:
                        continue
                    idx = pending.pop(m["id"])
                    if m.get("error") is not None:
                        out[idx] = ElectrumProtocolError(str(m["error"]))
                    else:
                        out[idx] = m.get("result")
        except (OSError, json.JSONDecodeError, TimeoutError) as exc:
            self.close()
            raise ElectrumConnectionError(f"Electrum batch request failed: {exc}") from exc
        return out

    def _readline(self) -> bytes:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ElectrumConnectionError("Electrum connection closed by server")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line


# Requests per pipelined batch (public servers tolerate ~100 in flight).
BATCH_SIZE = 100


def _parse_history(result: Any) -> list[TxHistoryItem]:
    items: list[TxHistoryItem] = []
    if not isinstance(result, list):
        return items
    for entry in result:
        if not isinstance(entry, dict):
            continue
        txid = entry.get("tx_hash") or entry.get("txid") or ""
        height = entry.get("height")
        fee = entry.get("fee")
        items.append(
            TxHistoryItem(
                txid=str(txid),
                height=int(height) if height is not None else None,
                fee=int(fee) if fee is not None else None,
            )
        )
    # Chronological: confirmed ascending, mempool (height<=0) last.
    items.sort(key=lambda x: (x.height is None or x.height <= 0, x.height or 0))
    return items


def _header_time_iso(header_hex: Any) -> str | None:
    """ISO-8601 UTC time from an 80-byte block header (hex)."""
    if not isinstance(header_hex, str) or len(header_hex) < 160:
        return None
    try:
        from datetime import datetime, timezone

        ts = int.from_bytes(bytes.fromhex(header_hex)[68:72], "little")
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, OSError):
        return None


@dataclass
class ElectrumClient:
    """Electrum protocol client. Inject ``transport`` for tests (no network)."""

    host: str = ""
    port: int = 0
    use_ssl: bool = True
    timeout: float = 20.0
    transport: Transport | None = None
    cache: ChainCache | None = None
    _connected: bool = field(default=False, repr=False)
    # Set once the server rejects verbose tx requests (e.g. electrs) so we do
    # not pay a failing round-trip for every transaction.
    _verbose_unsupported: bool = field(default=False, repr=False)
    _own_transport: bool = field(default=False, repr=False)
    _server_version: list[Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        settings = get_settings()
        using_defaults = not self.host
        if not self.host:
            self.host = settings.electrum_host
        if not self.port:
            self.port = settings.electrum_port
        if using_defaults:
            self.use_ssl = settings.electrum_ssl

    @classmethod
    def from_settings(cls) -> ElectrumClient:
        s = get_settings()
        return cls(host=s.electrum_host, port=s.electrum_port, use_ssl=s.electrum_ssl)

    @staticmethod
    def default_servers() -> list[dict[str, Any]]:
        return [
            {"host": h, "port": p, "ssl": ssl_flag}
            for h, p, ssl_flag in DEFAULT_ELECTRUM_SERVERS
        ]

    def connect(self) -> ElectrumServerInfo:
        """Open transport and negotiate ``server.version``."""
        if self.transport is None:
            self.transport = SslJsonTransport(
                self.host, self.port, use_ssl=self.use_ssl, timeout=self.timeout
            )
            self._own_transport = True
            assert isinstance(self.transport, SslJsonTransport)
            self.transport.connect()
        try:
            ver = self.transport.request(
                "server.version", ["btc-origin", "1.4"]
            )
            self._server_version = ver if isinstance(ver, list) else [ver]
        except ElectrumError:
            # Some mocks / minimal servers may not implement version — continue.
            self._server_version = None
        self._connected = True
        return ElectrumServerInfo(
            host=self.host,
            port=self.port,
            use_ssl=self.use_ssl,
            version=self._server_version,
        )

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            if self._own_transport:
                self.transport = None
                self._own_transport = False
        self._connected = False

    def __enter__(self) -> ElectrumClient:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _call(self, method: str, params: list[Any]) -> Any:
        if not self._connected or self.transport is None:
            self.connect()
        assert self.transport is not None
        return self.transport.request(method, params)

    def ping(self) -> bool:
        try:
            self._call("server.ping", [])
            return True
        except ElectrumError:
            return False

    def server_version(self) -> list[Any] | None:
        if self._server_version is None:
            ver = self._call("server.version", ["btc-origin", "1.4"])
            self._server_version = ver if isinstance(ver, list) else [ver]
        return self._server_version

    def get_balance(self, address: str) -> AddressBalance:
        sh = address_to_scripthash(address)
        result = self._call("blockchain.scripthash.get_balance", [sh])
        confirmed = int(result.get("confirmed", 0)) if isinstance(result, dict) else 0
        unconfirmed = int(result.get("unconfirmed", 0)) if isinstance(result, dict) else 0
        return AddressBalance(
            address=address,
            confirmed_sats=confirmed,
            unconfirmed_sats=unconfirmed,
            scripthash=sh,
        )

    def _call_many(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        """Batch calls (pipelined when the transport supports it).

        Failed items come back as exception instances, never raised — except a
        lost connection, which aborts the batch.
        """
        if not calls:
            return []
        if not self._connected or self.transport is None:
            self.connect()
        assert self.transport is not None
        many = getattr(self.transport, "request_many", None)
        out: list[Any] = []
        for i in range(0, len(calls), BATCH_SIZE):
            chunk = calls[i : i + BATCH_SIZE]
            if callable(many):
                out.extend(many(chunk))
                continue
            for method, params in chunk:
                try:
                    out.append(self.transport.request(method, params))
                except ElectrumConnectionError:
                    raise
                except Exception as exc:  # noqa: BLE001 — per-item failure
                    out.append(exc)
        return out

    def get_histories(
        self, addresses: list[str]
    ) -> dict[str, list[TxHistoryItem] | Exception]:
        """Histories for many addresses in batched round-trips (cache-aware)."""
        result: dict[str, list[TxHistoryItem] | Exception] = {}
        todo: list[tuple[str, str]] = []
        for addr in dict.fromkeys(addresses):
            sh = address_to_scripthash(addr)
            if self.cache is not None:
                cached = self.cache.get_history(sh)
                if cached is not None:
                    result[addr] = cached
                    continue
            todo.append((addr, sh))
        responses = self._call_many(
            [("blockchain.scripthash.get_history", [sh]) for _, sh in todo]
        )
        for (addr, sh), resp in zip(todo, responses):
            if isinstance(resp, Exception):
                result[addr] = resp
                continue
            items = _parse_history(resp)
            if self.cache is not None:
                self.cache.put_history(sh, items)
            result[addr] = items
        return result

    def prefetch_transactions(self, txids: list[str]) -> int:
        """Load raw tx hex for many txids into the session cache (batched)."""
        if self.cache is None:
            return 0
        todo = [t for t in dict.fromkeys(txids) if t and self.cache.peek_tx_hex(t) is None]
        responses = self._call_many(
            [("blockchain.transaction.get", [t, False]) for t in todo]
        )
        n = 0
        for txid, resp in zip(todo, responses):
            if isinstance(resp, str) and resp:
                self.cache.put_tx_hex(txid, resp)
                n += 1
        return n

    def prefetch_block_times(self, heights: list[int]) -> int:
        """Load block timestamps for many heights into the session cache."""
        if self.cache is None:
            return 0
        todo = [
            int(h)
            for h in dict.fromkeys(heights)
            if h and int(h) > 0 and self.cache.peek_header_time(int(h)) is None
        ]
        responses = self._call_many(
            [("blockchain.block.header", [h]) for h in todo]
        )
        n = 0
        for height, resp in zip(todo, responses):
            iso = _header_time_iso(resp)
            if iso:
                self.cache.put_header_time(height, iso)
                n += 1
        return n

    def get_history(self, address: str) -> list[TxHistoryItem]:
        sh = address_to_scripthash(address)
        if self.cache is not None:
            cached = self.cache.get_history(sh)
            if cached is not None:
                return cached
        result = self._call("blockchain.scripthash.get_history", [sh])
        items = _parse_history(result)
        if self.cache is not None:
            self.cache.put_history(sh, items)
        return items

    def get_transaction(self, txid: str, *, verbose: bool = False) -> dict[str, Any] | str:
        """Return verbose dict or raw hex string depending on ``verbose``.

        Some servers (e.g. electrs) reject ``verbose=True`` — we fall back to hex.
        """
        cache = self.cache
        if cache is not None:
            if verbose:
                hit = cache.get_tx_verbose(txid)
                if hit is not None:
                    return dict(hit)
            # Hex is immutable; verbose callers accept hex (time via header).
            hx = cache.get_tx_hex(txid)
            if hx is not None:
                return hx
        if verbose and self._verbose_unsupported:
            verbose = False
        try:
            result = self._call("blockchain.transaction.get", [txid, verbose])
        except ElectrumProtocolError:
            if not verbose:
                raise
            self._verbose_unsupported = True
            result = self._call("blockchain.transaction.get", [txid, False])
        if cache is not None:
            if isinstance(result, dict):
                cache.put_tx_verbose(txid, result)
            elif isinstance(result, str):
                cache.put_tx_hex(txid, result)
        return result


    def get_block_timestamp(self, height: int) -> str | None:
        """ISO-8601 UTC time from ``blockchain.block.header`` (80-byte header)."""
        if height is None or height <= 0:
            return None
        if self.cache is not None:
            hit = self.cache.get_header_time(height)
            if hit is not None:
                return hit
        try:
            header_hex = self._call("blockchain.block.header", [int(height)])
        except ElectrumError:
            return None
        iso = _header_time_iso(header_hex)
        if iso is None:
            return None
        if self.cache is not None:
            self.cache.put_header_time(height, iso)
        return iso

    def get_transaction_hex(self, txid: str) -> str:

        result = self.get_transaction(txid, verbose=False)
        if isinstance(result, str):
            return result
        if isinstance(result, dict) and "hex" in result:
            return str(result["hex"])
        raise ElectrumProtocolError(f"Unexpected tx payload for {txid}")


def try_connect_first_available(
    servers: list[tuple[str, int, bool]] | None = None,
    *,
    timeout: float = 10.0,
    factory: Callable[..., ElectrumClient] | None = None,
    cache: ChainCache | None = None,
) -> ElectrumClient:
    """Try default (or given) servers until one connects. Raises if all fail.

    ``cache`` (session RAM) is attached to the connected client.
    """
    candidates = servers or DEFAULT_ELECTRUM_SERVERS
    errors: list[str] = []
    make = factory or ElectrumClient
    for host, port, use_ssl in candidates:
        client = make(host=host, port=port, use_ssl=use_ssl, timeout=timeout)
        if cache is not None:
            client.cache = cache
        try:
            client.connect()
            return client
        except ElectrumError as exc:
            errors.append(f"{host}:{port} → {exc}")
            client.close()
    raise ElectrumConnectionError(
        "No Electrum server reachable. Tried: " + "; ".join(errors)
    )


def electrum_server_candidates(
    prefer_host: str | None = None,
    prefer_port: int | None = None,
    prefer_ssl: bool | None = None,
) -> list[tuple[str, int, bool]]:
    """Configured host first (if given), then DEFAULT_ELECTRUM_SERVERS (deduped)."""
    settings = get_settings()
    host = prefer_host if prefer_host is not None else settings.electrum_host
    port = prefer_port if prefer_port is not None else settings.electrum_port
    use_ssl = prefer_ssl if prefer_ssl is not None else settings.electrum_ssl
    configured = (host, port, use_ssl)
    out: list[tuple[str, int, bool]] = [configured]
    for entry in DEFAULT_ELECTRUM_SERVERS:
        if entry != configured:
            out.append(entry)
    return out


def probe_electrum_status(
    *,
    timeout: float = 4.0,
    host: str | None = None,
    port: int | None = None,
    use_ssl: bool | None = None,
) -> dict[str, Any]:
    """Best-effort probe of the configured (or given) Electrum host.

    Fail-open: never raises; returns connected bool + server/error. Short timeout.
    Does not walk the full failover list (that is reserved for sync).
    """
    settings = get_settings()
    h = host if host is not None else settings.electrum_host
    p = port if port is not None else settings.electrum_port
    ssl_flag = use_ssl if use_ssl is not None else settings.electrum_ssl
    configured = {"host": h, "port": p, "ssl": ssl_flag}
    client = ElectrumClient(host=h, port=p, use_ssl=ssl_flag, timeout=timeout)
    try:
        info = client.connect()
        return {
            "configured": configured,
            "connected": True,
            "server": {
                "host": info.host,
                "port": info.port,
                "ssl": info.use_ssl,
                "version": info.version,
            },
            "error": None,
        }
    except ElectrumError as exc:
        return {
            "configured": configured,
            "connected": False,
            "server": None,
            "error": str(exc),
        }
    finally:
        client.close()
