"""M2 enrichment + trace + report API (no network)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.db import persist_ledger
from btc_origin.enrichment import SessionEnricher
from btc_origin.merger import Ledger
from btc_origin.tx_ingestor import Flow


def test_health_milestone_m5() -> None:
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["milestone"] == "M5"
        assert "localhost_ui" in r.json().get("features", [])


def test_enrich_and_labels_endpoint() -> None:
    with TestClient(app) as client:
        state = app.state.session
        # Seed addresses + flows in session DB
        state.db.execute(
            "INSERT INTO wallets (id, name, xpub, kind, created_at) VALUES (1,'w',NULL,'address','2024-01-01')"
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("bc1q8umytnw6dh6f909c9r53ae4n08uqmtyw88v0ge",),
        )
        state.db.execute(
            "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,?,0)",
            ("bc1qowninternal00000000000000000000001",),
        )
        ledger = Ledger(
            flows=[
                Flow(
                    txid="t_int",
                    address="bc1qowninternal00000000000000000000001",
                    direction="out",
                    amount_sats=100,
                    wallet_id=1,
                    block_time="2023-01-01T00:00:00Z",
                ),
                Flow(
                    txid="t_int",
                    address="bc1q8umytnw6dh6f909c9r53ae4n08uqmtyw88v0ge",
                    direction="in",
                    amount_sats=100,
                    wallet_id=1,
                    block_time="2023-01-01T00:00:00Z",
                ),
            ]
        )
        persist_ledger(state.db, ledger)
        r = client.post("/api/enrich")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["enrichment"]["internal_count"] >= 1
        assert body["enrichment"]["labels_applied"] >= 1

        labs = client.get("/api/labels").json()
        assert labs["count"] >= 1
        assert any(x["label"] == "FTX" for x in labs["labels"])
        assert all(
            "Kaufnachweis fehlt" in (x.get("status") or "")
            for x in labs["labels"]
            if x["label"] == "FTX"
        )


def test_trace_endpoint_with_graph() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/trace",
            json={
                "txid": "root",
                "max_depth": 3,
                "graph": {"root": ["a", "b"], "a": [], "b": []},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["root_txid"] == "root"
        assert body["ambiguous"] is True
        assert body["ephemeral"] is True
        assert len(body["steps"]) >= 1


def test_report_csv_headers_disk_written_false() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/report/csv",
            json={
                "rows": [
                    {
                        "txid": "r1",
                        "direction": "in",
                        "amount_sats": 10,
                        "inflow_date": "2024-01-15",
                        "evidence_gap_status": "Kaufnachweis fehlt, Quelle nicht erreichbar",
                    }
                ]
            },
        )
        assert r.status_code == 200
        assert r.headers.get("X-BTC-Herkunft-Disk-Written") == "false"
        assert b"anschaffungs_referenz" in r.content
        assert b"haltefrist" in r.content


def test_report_pdf_magic_and_disk_header() -> None:
    with TestClient(app) as client:
        r = client.post("/api/report/pdf", json={"rows": []})
        assert r.status_code == 200
        assert r.headers.get("X-BTC-Herkunft-Disk-Written") == "false"
        assert r.content.startswith(b"%PDF")


def test_session_enricher_unit() -> None:
    from btc_origin.db import connect, init_schema, list_flows

    db = connect()
    init_schema(db)
    db.execute(
        "INSERT INTO wallets (id, name, kind, created_at) VALUES (1,'w','address','t')"
    )
    db.execute(
        "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,'a1',0)"
    )
    db.execute(
        "INSERT INTO addresses (wallet_id, address, is_change) VALUES (1,'a2',0)"
    )
    # External buy then pure consolidation — Haltefrist only on remaining lot.
    persist_ledger(
        db,
        Ledger(
            flows=[
                Flow(
                    "buy",
                    "a1",
                    "in",
                    5,
                    wallet_id=1,
                    block_time="2022-01-01T00:00:00Z",
                    vout=0,
                    block_height=700000,
                ),
                Flow(
                    "tx",
                    "a1",
                    "out",
                    5,
                    wallet_id=1,
                    block_time="2022-06-01T00:00:00Z",
                    tx_total_output_sats=5,
                    vin_index=0,
                ),
                Flow(
                    "tx",
                    "a2",
                    "in",
                    5,
                    wallet_id=1,
                    block_time="2022-06-01T00:00:00Z",
                    tx_total_output_sats=5,
                    vout=0,
                ),
            ]
        ),
    )
    enr = SessionEnricher().enrich(db, as_of="2026-09-27")
    assert enr.internal_count == 2  # consolidation legs
    assert enr.haltefrist_qualified >= 1  # remaining lot from buy
    # DB roundtrip restored vout / block_height and FIFO annotations
    rows = list_flows(db)
    buy = next(r for r in rows if r["txid"] == "buy")
    assert buy["vout"] == 0
    assert buy["block_height"] == 700000
    assert buy["lot_date"] == "2022-01-01"
    db.close()


def test_clear_session_endpoint() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/wallets",
            json={"name": "tmp", "address": "bc1qcleartest000000000000000000000001"},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
        cleared = client.delete("/api/session")
        assert cleared.status_code == 200
        body = cleared.json()
        assert body["ok"] is True
        assert body.get("ephemeral") is True
        wallets = client.get("/api/wallets").json()
        assert wallets["count"] == 0
