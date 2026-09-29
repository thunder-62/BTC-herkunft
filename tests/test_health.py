"""FastAPI health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from btc_origin.api.app import app


def test_health_ok() -> None:
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["package"] == "btc_origin"
        assert "version" in body
        assert body.get("persistence") == "memory_only"
        assert body.get("sqlite_memory") is True


def test_report_csv_on_demand_no_disk() -> None:
    with TestClient(app) as client:
        r = client.post("/api/report/csv", json={"rows": []})
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")
        assert r.headers.get("X-BTC-Herkunft-Disk-Written") == "false"
        assert b"REFERENZWERT" in r.content or b"referenzwert" in r.content.lower()
