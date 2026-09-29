"""API /api/trace — Electrum on-demand + injected client."""

from __future__ import annotations

from embit.script import Script
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.electrum_client import ElectrumConnectionError


def _tx_hex_with_parent(parent_txid_hex: str) -> str:
    vin = [
        TransactionInput(
            txid=bytes.fromhex(parent_txid_hex),
            vout=0,
        )
    ]
    vout = [TransactionOutput(1000, Script(b"\x00\x14" + b"\x11" * 20))]
    return Transaction(vin=vin, vout=vout).to_string()


def test_trace_with_graph_stays_offline() -> None:
    """Existing offline path: graph provided → no Electrum required."""
    with TestClient(app) as client:
        r = client.post(
            "/api/trace",
            json={
                "txid": "root",
                "max_depth": 3,
                "graph": {"root": ["a"], "a": []},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["root_txid"] == "root"
        assert body["depth_reached"] >= 1
        assert "electrum" not in body or body.get("electrum") is None
        assert body.get("status") != "electrum_unreachable"


def test_trace_injected_client_resolves_parents() -> None:
    """Without graph, mock client on state → vin parents resolve."""
    parent = "ab" * 32
    root = "cd" * 32
    root_hex = _tx_hex_with_parent(parent)

    class FakeClient:
        host = "mock.electrum"
        port = 50002
        use_ssl = True

        def get_transaction_hex(self, txid: str) -> str:
            if txid == root:
                return root_hex
            raise RuntimeError(f"no further hops for {txid}")

        def close(self) -> None:
            pass

    with TestClient(app) as client:
        client.app.state.session.trace_client = FakeClient()
        r = client.post(
            "/api/trace",
            json={"txid": root, "max_depth": 3},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["root_txid"] == root
        assert body["depth_reached"] >= 1
        root_step = body["steps"][0]
        assert parent in root_step["parents"]
        assert body.get("electrum") == {
            "host": "mock.electrum",
            "port": 50002,
            "ssl": True,
        }
        assert body.get("status") != "electrum_unreachable"
        # Must not pretend empty graph / coinbase at root
        joined = " ".join(body.get("notes") or [])
        assert "No Electrum client" not in joined
        assert "empty graph" not in joined.lower() or parent in root_step["parents"]


def test_trace_no_client_failed_connect_surfaces_error(monkeypatch) -> None:
    """No injected client + connect failure → clear Electrum error, not coinbase."""

    def boom(*_a, **_k):
        raise ElectrumConnectionError(
            "No Electrum server reachable. Tried: mock:1 → down"
        )

    monkeypatch.setattr(
        "btc_origin.api.routes.trace.try_connect_first_available",
        boom,
    )

    with TestClient(app) as client:
        assert client.app.state.session.trace_client is None
        assert client.app.state.session.electrum_client is None
        r = client.post(
            "/api/trace",
            json={"txid": "aa" * 32, "max_depth": 2},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ambiguous"] is True
        assert body["depth_reached"] == 0
        assert body["status"] == "electrum_unreachable"
        assert body["steps"] == []
        notes = " ".join(body.get("notes") or [])
        assert "Electrum" in notes
        assert "unerreichbar" in notes.lower() or "unreachable" in notes.lower()
        # Must not look like a resolved empty/coinbase graph
        assert "coinbase" not in notes.lower()


def test_trace_graph_includes_input_addresses_and_labels() -> None:
    """Offline richer graph → JSON carries input_addresses + pack/session labels."""
    from btc_origin.label_service import LabelService

    fund = "bc1q04pmdphrn2t8gtkur5rfza5l4pzh5q390pljyy"
    with TestClient(app) as client:
        # Inject a known pack address label in-session (RAM only).
        svc: LabelService = client.app.state.session.labels
        svc.load_pack_dict(
            {
                "id": "ftx",
                "name": "FTX",
                "addresses": [fund],
            }
        )
        r = client.post(
            "/api/trace",
            json={
                "txid": "root",
                "max_depth": 3,
                "graph": {
                    "root": {
                        "parents": ["parent"],
                        "input_addresses": [fund, "bc1qother"],
                    },
                    "parent": {"parents": [], "input_addresses": []},
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        root_step = body["steps"][0]
        assert "input_addresses" in root_step
        assert fund in root_step["input_addresses"]
        assert "bc1qother" in root_step["input_addresses"]
        assert root_step["address_labels"].get(fund) == "FTX"
        assert "address_labels" in root_step


def test_trace_electrum_funding_address_in_json() -> None:
    """Injected FakeClient with parent+child hex exposes funding address in steps."""
    from embit.networks import NETWORKS
    from embit.script import p2wpkh, address_to_scriptpubkey
    from embit import bip32
    from embit.transaction import Transaction, TransactionInput, TransactionOutput

    from btc_origin.hd_deriver import normalize_extended_public_key

    BIP84_ZPUB = (
        "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
        "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
    )
    norm = normalize_extended_public_key(BIP84_ZPUB)
    hd = bip32.HDKey.from_string(norm.normalized)
    fund_addr = p2wpkh(hd.derive([0, 0])).address(NETWORKS["main"])
    recv_addr = p2wpkh(hd.derive([0, 1])).address(NETWORKS["main"])

    parent_tx = Transaction(
        vin=[TransactionInput(txid=bytes.fromhex("11" * 32), vout=0)],
        vout=[TransactionOutput(50_000, address_to_scriptpubkey(fund_addr))],
    )
    parent_hex = parent_tx.to_string()
    parent_txid = parent_tx.txid().hex()

    child_tx = Transaction(
        vin=[TransactionInput(txid=bytes.fromhex(parent_txid), vout=0)],
        vout=[TransactionOutput(49_000, address_to_scriptpubkey(recv_addr))],
    )
    child_hex = child_tx.to_string()
    child_txid = child_tx.txid().hex()
    store = {child_txid: child_hex, parent_txid: parent_hex}

    class FakeClient:
        host = "mock.electrum"
        port = 50002
        use_ssl = True

        def get_transaction_hex(self, txid: str) -> str:
            if txid not in store:
                raise RuntimeError(f"no {txid}")
            return store[txid]

        def close(self) -> None:
            pass

    with TestClient(app) as client:
        client.app.state.session.trace_client = FakeClient()
        r = client.post(
            "/api/trace",
            json={"txid": child_txid, "max_depth": 3},
        )
        assert r.status_code == 200
        body = r.json()
        root_step = body["steps"][0]
        assert fund_addr in root_step["input_addresses"]
        assert recv_addr in root_step.get("output_addresses", [])
