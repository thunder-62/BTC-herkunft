"""Electrum client with mocked transport — no network."""

from __future__ import annotations

from typing import Any

import pytest

from btc_origin.electrum_client import (
    DEFAULT_ELECTRUM_SERVERS,
    ElectrumClient,
    ElectrumConnectionError,
    ElectrumProtocolError,
    electrum_server_candidates,
    try_connect_first_available,
)
from btc_origin.hd_deriver import address_to_scripthash

ADDR = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


class MockTransport:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, list[Any]]] = []
        self.closed = False

    def request(self, method: str, params: list[Any]) -> Any:
        self.calls.append((method, params))
        if method in self.responses:
            val = self.responses[method]
            if isinstance(val, Exception):
                raise val
            if callable(val):
                return val(params)
            return val
        raise ElectrumProtocolError(f"unexpected method {method}")

    def close(self) -> None:
        self.closed = True


def test_default_servers_list_nonempty() -> None:
    assert len(DEFAULT_ELECTRUM_SERVERS) >= 3
    assert ElectrumClient.default_servers()[0]["ssl"] is True


def test_get_history_and_balance_mocked() -> None:
    sh = address_to_scripthash(ADDR)
    transport = MockTransport(
        {
            "server.version": ["ElectrumX", "1.4"],
            "blockchain.scripthash.get_history": [
                {"tx_hash": "abcd" * 16, "height": 800000},
            ],
            "blockchain.scripthash.get_balance": {
                "confirmed": 1000,
                "unconfirmed": 0,
            },
        }
    )
    client = ElectrumClient(host="mock", port=1, transport=transport)
    info = client.connect()
    assert info.version == ["ElectrumX", "1.4"]
    hist = client.get_history(ADDR)
    assert len(hist) == 1
    assert hist[0].txid == "abcd" * 16
    assert hist[0].height == 800000
    bal = client.get_balance(ADDR)
    assert bal.confirmed_sats == 1000
    assert bal.scripthash == sh
    # scripthash passed to server
    hist_call = [c for c in transport.calls if c[0].endswith("get_history")][0]
    assert hist_call[1] == [sh]
    client.close()
    assert transport.closed is True


def test_protocol_error_surfaces() -> None:
    transport = MockTransport(
        {"server.version": ["ok", "1.4"], "blockchain.transaction.get": ElectrumProtocolError("nope")}
    )
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    with pytest.raises(ElectrumProtocolError):
        client.get_transaction("ff" * 32)


def test_connection_error_on_connect() -> None:
    class BoomTransport:
        def connect(self):  # not used — we pass pre-built failing request
            pass

        def request(self, method, params):
            raise ElectrumConnectionError("down")

        def close(self):
            pass

    # Simulate unreachable by using real SslJsonTransport against localhost closed port
    client = ElectrumClient(host="127.0.0.1", port=1, use_ssl=False, timeout=0.2)
    with pytest.raises(ElectrumConnectionError):
        client.connect()


def test_get_block_timestamp_from_header() -> None:
    # Minimal fake 80-byte header with time=1700000000 at bytes 68:72
    import struct

    header = bytearray(80)
    struct.pack_into("<I", header, 68, 1_700_000_000)
    transport = MockTransport(
        {
            "server.version": ["ok", "1.4"],
            "blockchain.block.header": header.hex(),
        }
    )
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    assert client.get_block_timestamp(123) == "2023-11-14T22:13:20Z"  # privacy: ok (Unix-Zeit 1_700_000_000)
    assert client.get_block_timestamp(0) is None


def test_try_connect_first_available_failover_mocked() -> None:
    """First candidate fails, second succeeds — no real network."""
    attempts: list[tuple[str, int]] = []

    class FlakyFactory:
        def __new__(cls, host="", port=0, use_ssl=True, timeout=10.0, **kwargs):
            attempts.append((host, port))
            transport = MockTransport({"server.version": ["ok", "1.4"]})
            if host == "bad.example":
                class Boom:
                    def __init__(self, *a, **k):
                        self.host = host
                        self.port = port
                        self.use_ssl = use_ssl
                        self.timeout = timeout
                        self.transport = None
                        self._server_version = None

                    def connect(self):
                        raise ElectrumConnectionError("refused")

                    def close(self):
                        pass

                return Boom()
            return ElectrumClient(
                host=host, port=port, use_ssl=use_ssl, timeout=timeout, transport=transport
            )

    client = try_connect_first_available(
        [("bad.example", 50002, True), ("good.example", 50002, True)],
        timeout=1.0,
        factory=FlakyFactory,
    )
    assert client.host == "good.example"
    assert attempts == [("bad.example", 50002), ("good.example", 50002)]
    client.close()


def test_electrum_server_candidates_prefers_configured() -> None:
    cands = electrum_server_candidates(
        prefer_host="custom.example", prefer_port=1234, prefer_ssl=True
    )
    assert cands[0] == ("custom.example", 1234, True)
    assert ("electrum.blockstream.info", 50002, True) in cands
