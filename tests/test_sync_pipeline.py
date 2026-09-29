"""Sync pipeline + API with mocked Electrum — no network."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import connect, init_schema, list_flows
from btc_origin.electrum_client import ElectrumClient
from btc_origin.hd_deriver import HdDeriver
from btc_origin.sync_pipeline import SyncPipeline
from btc_origin.tx_ingestor import TxIngestor
from btc_origin.wallet_registry import WalletRegistry

# Minimal valid P2WPKH-looking empty tx is hard; use ingest_history without hex
# via a custom client that returns history + a handcrafted flow path.

BIP84_ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
RECV0 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


class FakeTransport:
    """Returns empty history for all scripthashes; version ok."""

    def __init__(self, history_by_sh: dict[str, list] | None = None) -> None:
        self.history_by_sh = history_by_sh or {}
        self.tx_hex: dict[str, str] = {}

    def request(self, method: str, params: list[Any]) -> Any:
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


def test_sync_empty_registry() -> None:
    db = connect()
    init_schema(db)
    pipe = SyncPipeline(WalletRegistry(), db, client=ElectrumClient(host="m", port=1, transport=FakeTransport()))
    summary = pipe.run(connect=False)
    assert summary.status == "empty"
    db.close()


def test_sync_derives_addresses_and_persists_memory() -> None:
    from btc_origin.hd_deriver import address_to_scripthash

    db = connect()
    init_schema(db)
    reg = WalletRegistry()
    reg.register_xpub("demo", BIP84_ZPUB)
    sh = address_to_scripthash(RECV0)
    transport = FakeTransport(history_by_sh={sh: []})  # no txs — still derives
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    pipe = SyncPipeline(
        reg,
        db,
        client=client,
        deriver=HdDeriver(gap_limit=2),
    )
    summary = pipe.run(connect=False)
    assert summary.status in ("ok", "ok_with_errors")
    assert summary.wallets == 1
    assert summary.addresses_derived == 4  # 2 recv + 2 change
    # addresses in memory DB
    n = db.execute("SELECT COUNT(*) FROM addresses").fetchone()[0]
    assert n == 4
    db.close()


def test_ingest_history_stub_without_client() -> None:
    ing = TxIngestor()
    result = ing.ingest_history(
        RECV0,
        [{"tx_hash": "aa" * 32, "height": 100}],
        wallet_id=1,
        fetch_txs=False,
    )
    assert len(result.flows) == 1
    assert result.flows[0].txid == "aa" * 32
    assert result.flows[0].amount_sats == 0


def test_api_sync_with_injected_mock_client() -> None:
    with TestClient(app) as client:
        # inject mock electrum into session
        transport = FakeTransport()
        eclient = ElectrumClient(host="mock", port=1, transport=transport)
        eclient.connect()
        client.app.state.session.electrum_client = eclient

        r = client.post(
            "/api/wallets",
            json={"name": "w1", "xpub": BIP84_ZPUB},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Shrink gap for speed via deriver on pipeline — sync uses settings gap.
        # Still fine with gap 20; mock returns empty history quickly.
        s = client.post("/api/sync")
        assert s.status_code == 200
        body = s.json()
        assert body["ephemeral"] is True
        assert body["persistence"] == "memory_only"
        assert body["wallets"] == 1
        assert body["addresses_derived"] > 0
        assert body["status"] in ("ok", "ok_with_errors")
        # Injected client host/port surfaced in sync summary for UI
        assert body.get("electrum") is not None
        assert body["electrum"]["host"] == "mock"
        assert body["electrum"]["port"] == 1

        flows = client.get("/api/flows")
        assert flows.status_code == 200
        assert flows.json()["ephemeral"] is True
        assert "flows" in flows.json()


def test_api_batch_xpubs_and_servers_list() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/wallets/batch",
            json={
                "items": [
                    {"name": "a", "xpub": BIP84_ZPUB},
                    {"name": "b", "address": RECV0},
                ]
            },
        )
        assert r.json()["imported"] == 2
        assert r.json()["unlimited_xpubs"] is True
        servers = client.get("/api/electrum/servers")
        assert servers.status_code == 200
        assert len(servers.json()["defaults"]) >= 3


def test_api_electrum_status_without_probe() -> None:
    """Status endpoint without probe=1 must not touch the network."""
    with TestClient(app) as client:
        r = client.get("/api/electrum/status")
        assert r.status_code == 200
        body = r.json()
        assert "configured" in body
        assert body["configured"]["host"]
        assert body["connected"] is None
        assert body["server"] is None
        assert body.get("probed") is False


def test_api_electrum_status_query_accepted() -> None:
    """probe query param is accepted; without live net we only assert shape when probe=0."""
    with TestClient(app) as client:
        r = client.get("/api/electrum/status", params={"probe": 0})
        assert r.status_code == 200
        assert r.json()["connected"] is None


def test_address_wallet_with_many_transactions_is_not_synced() -> None:
    """Eine Adresse mit sehr vielen Vorgängen ist fast sicher eine Börsen-/Dienstadresse:
    nicht synchronisieren (keine fremden Transaktionen, kein stundenlanger Sync), warnen."""
    from btc_origin.hd_deriver import address_to_scripthash
    from btc_origin.sync_pipeline import ADDRESS_WALLET_MAX_TXS

    db = connect()
    init_schema(db)
    reg = WalletRegistry()
    reg.register_address("Börse", RECV0)
    busy = [{"tx_hash": f"{i:064x}", "height": 100 + i} for i in range(ADDRESS_WALLET_MAX_TXS + 50)]
    transport = FakeTransport(history_by_sh={address_to_scripthash(RECV0): busy})
    client = ElectrumClient(host="mock", port=1, transport=transport)
    client.connect()
    summary = SyncPipeline(reg, db, client=client, deriver=HdDeriver(gap_limit=2)).run(connect=False)
    assert any("nicht synchronisiert" in n and "150 Transaktionen" in n for n in summary.notes)
    assert list_flows(db) == []
    db.close()
