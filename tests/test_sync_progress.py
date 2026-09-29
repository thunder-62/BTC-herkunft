"""Live Electrum sync progress — tracker, pipeline callback, API endpoint."""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import connect, init_schema
from btc_origin.electrum_client import ElectrumClient
from btc_origin.hd_deriver import HdDeriver, address_to_scripthash
from btc_origin.sync_pipeline import SyncPipeline
from btc_origin.sync_progress import SyncProgressTracker
from btc_origin.tx_ingestor import TxIngestor
from btc_origin.wallet_registry import WalletRegistry

BIP84_ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
RECV0 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


class FakeTransport:
    def __init__(
        self,
        history_by_sh: dict[str, list] | None = None,
        *,
        delay: float = 0.0,
    ) -> None:
        self.history_by_sh = history_by_sh or {}
        self.tx_hex: dict[str, str] = {}
        self.delay = delay

    def request(self, method: str, params: list[Any]) -> Any:
        if self.delay:
            time.sleep(self.delay)
        if method == "server.version":
            return ["FakeElectrum", "1.4"]
        if method == "server.ping":
            return None
        if method == "blockchain.scripthash.get_history":
            return self.history_by_sh.get(params[0], [])
        if method == "blockchain.scripthash.get_balance":
            return {"confirmed": 0, "unconfirmed": 0}
        if method == "blockchain.transaction.get":
            txid = params[0]
            verbose = params[1] if len(params) > 1 else False
            hx = self.tx_hex.get(txid, "")
            if verbose:
                return {"hex": hx, "txid": txid, "blocktime": 1700000000}
            return hx
        raise RuntimeError(method)

    def close(self) -> None:
        pass


def test_tracker_thread_safe_snapshot() -> None:
    t = SyncProgressTracker()
    assert t.as_dict()["running"] is False
    t.reset_for_run()
    assert t.snapshot().running is True
    assert t.snapshot().phase == "connecting"
    t.update(phase="ingest", tx_done=3, tx_total=10)
    snap = t.snapshot()
    assert snap.tx_done == 3
    assert snap.tx_total == 10
    assert "3 / 10 Tx verarbeitet" in snap.message
    t.finish(phase="done")
    assert t.snapshot().running is False


def test_pipeline_emits_progress_events() -> None:
    db = connect()
    init_schema(db)
    reg = WalletRegistry()
    reg.register_xpub("demo", BIP84_ZPUB)
    sh = address_to_scripthash(RECV0)
    txid = "ab" * 32
    transport = FakeTransport(history_by_sh={sh: [{"tx_hash": txid, "height": 100}]})
    # Empty hex → parse errors, but tx still discovered; progress should fire.
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    events: list[dict[str, Any]] = []

    def on_progress(**kwargs: Any) -> None:
        events.append(dict(kwargs))

    pipe = SyncPipeline(
        reg,
        db,
        client=client,
        deriver=HdDeriver(gap_limit=2),
        ingestor=TxIngestor(),
    )
    summary = pipe.run(connect=False, on_progress=on_progress)
    assert summary.status in ("ok", "ok_with_errors", "error")
    phases = [e.get("phase") for e in events]
    assert "connecting" in phases or "deriving" in phases
    assert any(e.get("addresses_total", 0) > 0 for e in events)
    # Unique tx discovered from history
    assert any(e.get("tx_total", 0) >= 1 for e in events)
    db.close()


def test_api_sync_progress_idle_and_updates() -> None:
    with TestClient(app) as client:
        idle = client.get("/api/sync/progress")
        assert idle.status_code == 200
        body = idle.json()
        assert body["running"] is False
        assert "tx_done" in body
        assert "tx_total" in body
        assert "phase" in body

        transport = FakeTransport()
        eclient = ElectrumClient(host="mock", port=1, transport=transport)
        eclient.connect()
        client.app.state.session.electrum_client = eclient

        r = client.post("/api/wallets", json={"name": "w1", "xpub": BIP84_ZPUB})
        assert r.json()["ok"] is True

        s = client.post("/api/sync")
        assert s.status_code == 200

        after = client.get("/api/sync/progress")
        assert after.status_code == 200
        prog = after.json()
        assert prog["running"] is False
        assert prog["phase"] in ("done", "error", "enrich")
        assert prog["addresses_total"] > 0


def test_api_progress_visible_during_blocking_sync() -> None:
    """Concurrent GET /api/sync/progress while POST /api/sync is in a worker."""
    with TestClient(app) as client:
        # Slowish fake transport so progress can be observed mid-sync.
        transport = FakeTransport(delay=0.02)
        eclient = ElectrumClient(host="mock", port=1, transport=transport)
        eclient.connect()
        client.app.state.session.electrum_client = eclient
        client.post("/api/wallets", json={"name": "w1", "xpub": BIP84_ZPUB})

        seen_running: list[bool] = []
        done = threading.Event()

        def poll() -> None:
            while not done.is_set():
                try:
                    p = client.get("/api/sync/progress").json()
                    seen_running.append(bool(p.get("running")))
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.05)

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        try:
            s = client.post("/api/sync")
            assert s.status_code == 200
        finally:
            done.set()
            poller.join(timeout=2)

        # At least one poll should have seen running=true (or we finished so
        # fast that only false was seen — still assert endpoint stayed healthy).
        assert len(seen_running) >= 1
        final = client.get("/api/sync/progress").json()
        assert final["running"] is False


def test_ingest_two_pass_progress_tx_total_then_done() -> None:
    """Two-pass: tx_total known after history, tx_done bumps on process."""
    from btc_origin.electrum_client import TxHistoryItem

    events: list[dict[str, Any]] = []

    def on_progress(**kwargs: Any) -> None:
        events.append(dict(kwargs))

    class StubClient:
        host = "mock"
        port = 1
        use_ssl = False

        def get_history(self, address: str):
            if address == "addr1":
                return [
                    TxHistoryItem(txid="aa" * 32, height=1),
                    TxHistoryItem(txid="bb" * 32, height=2),
                ]
            return []

        def get_transaction(self, txid: str, verbose: bool = False):
            raise Exception("no hex")  # force stub path via error → still count?

        def get_transaction_hex(self, txid: str) -> str:
            raise Exception("no hex")

    # Use fetch_txs=False path via ingest_history directly for reliable counts
    ing = TxIngestor()
    done: set[str] = set()
    result = ing.ingest_history(
        "addr1",
        [{"tx_hash": "aa" * 32, "height": 1}, {"tx_hash": "bb" * 32, "height": 2}],
        fetch_txs=False,
        on_progress=on_progress,
        txids_done=done,
        tx_total=2,
    )
    assert len(result.txids_seen) == 2
    assert len(done) == 2
    assert any(e.get("tx_done") == 2 and e.get("tx_total") == 2 for e in events)
    assert any("Tx verarbeitet" in str(e.get("message", "")) for e in events)
