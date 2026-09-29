"""Batched Electrum round-trips + parallel price prefetch (speed fixes)."""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import date, timedelta

from btc_origin.electrum_client import (
    ElectrumClient,
    ElectrumProtocolError,
    SslJsonTransport,
)
from btc_origin.price_oracle import PriceOracle, days_from


def _serve_out_of_order(server: socket.socket, n: int) -> None:
    """Read n requests, answer in reverse order (plus one notification)."""
    buf = b""
    reqs = []
    while len(reqs) < n:
        buf += server.recv(65536)
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            reqs.append(json.loads(line))
    out = [json.dumps({"method": "blockchain.headers.subscribe", "params": []})]
    for r in reversed(reqs):
        if r["params"][0] == "bad":
            out.append(json.dumps({"id": r["id"], "error": {"message": "nope"}}))
        else:
            out.append(json.dumps({"id": r["id"], "result": r["params"][0] * 2}))
    server.sendall(("\n".join(out) + "\n").encode())


def test_transport_request_many_pipelines_and_matches_ids() -> None:
    client_sock, server_sock = socket.socketpair()
    t = SslJsonTransport("x", 1, use_ssl=False)
    t._sock = client_sock
    calls = [("echo", ["a"]), ("echo", ["bad"]), ("echo", ["c"])]
    th = threading.Thread(target=_serve_out_of_order, args=(server_sock, 3))
    th.start()
    res = t.request_many(calls)
    th.join()
    assert res[0] == "aa" and res[2] == "cc"
    assert isinstance(res[1], ElectrumProtocolError)


class _CountingBatch:
    def __init__(self) -> None:
        self.round_trips = 0

    def request(self, method, params):
        self.round_trips += 1
        return ["v"] if method == "server.version" else []

    def request_many(self, calls):
        self.round_trips += 1
        return [[] for _ in calls]

    def close(self) -> None:
        pass


def test_get_histories_uses_one_round_trip_per_batch() -> None:
    tr = _CountingBatch()
    c = ElectrumClient(host="m", port=1, transport=tr)
    c.connect()
    before = tr.round_trips
    addrs = [
        "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
        "bc1qzs4lwwnll38kxtkvd8997pggr9fs78480dhhu0",
    ] * 3
    out = c.get_histories(addrs)
    assert tr.round_trips - before == 1
    assert set(out) == set(addrs) and all(v == [] for v in out.values())


def test_price_prefetch_parallel_once_per_day() -> None:
    calls: list[date] = []
    lock = threading.Lock()

    def slow_fetch(on: date):
        time.sleep(0.05)
        with lock:
            calls.append(on)
        return 10.0, 9.0

    oracle = PriceOracle(historical_fetcher=slow_fetch, spot_fetcher=lambda: (None, None))
    days = [date(2022, 1, 1) + timedelta(days=i) for i in range(32)]
    t0 = time.monotonic()
    assert oracle.prefetch_historical(days + days) == 32
    elapsed = time.monotonic() - t0
    assert len(calls) == 32
    assert elapsed < 32 * 0.05 / 2  # clearly parallel, not sequential
    # Per-row lookups now hit the cache.
    assert oracle.get_acquisition_reference(days[0]).eur == 9.0
    assert len(calls) == 32
    assert oracle.prefetch_historical(days) == 0


def test_days_from_parses_mixed_values() -> None:
    assert days_from(["2024-05-01T10:00:00Z", None, "", date(2023, 1, 2), "x"]) == [
        date(2024, 5, 1),
        date(2023, 1, 2),
    ]
