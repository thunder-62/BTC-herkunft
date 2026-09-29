"""Oberfläche Regelwerk: Übersicht der Export-Arten, Vorschau und Speichern von
local/kategorien.yaml (nur nach Bestätigung), PDF-Vergleich."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

EXPORT = (
    "Date,Type,Asset,Amount,Fiat Amount\n"
    "2025-03-01 10:05:00,Buy,BTC,0.01,600.00\n"
    "2025-03-02 10:05:00,Referral,BTC,0.001,\n"
)


@pytest.fixture()
def local(tmp_path, monkeypatch):
    d = tmp_path / "local"
    (d / "boersen").mkdir(parents=True)
    (d / "boersen" / "kraken.csv").write_text(EXPORT, encoding="utf-8")
    monkeypatch.setenv("BTC_ORIGIN_LOCAL_DIR", str(d))
    return d


def test_overview_preview_and_confirmed_save(local) -> None:
    from btc_origin.api.app import app

    with TestClient(app) as client:
        client.post("/api/local/reload")
        data = client.get("/api/regelwerk/kategorien").json()
        assert {(a["art"], a["status"]) for a in data["arten"]} == {
            ("Buy", "nicht_unterstuetzt"), ("Referral", "nicht_unterstuetzt")}
        assert "empfehlung" in {k["name"] for k in data["kategorien"]} and not data["local_exists"]
        change = {"export": {"Kraken": {"Buy": "kauf", "Referral": "empfehlung"}}}
        prev = client.post("/api/regelwerk/kategorien/vorschau", json=change).json()
        assert '"Referral": empfehlung' in prev["yaml"] or "Referral: empfehlung" in prev["yaml"]
        assert not (local / "kategorien.yaml").exists()  # Vorschau schreibt nichts
        # ohne Bestätigung wird nichts geschrieben
        r = client.post("/api/regelwerk/kategorien/speichern", json={"yaml": prev["yaml"]})
        assert r.status_code == 400 and not (local / "kategorien.yaml").exists()
        r = client.post("/api/regelwerk/kategorien/speichern", json={"yaml": prev["yaml"], "bestaetigt": True})
        assert r.status_code == 200 and (local / "kategorien.yaml").is_file()
        state = app.state.session
        assert [(t.art, t.kategorie, t.einkunft) for t in state.local.trades] == [
            ("Buy", "kauf", False), ("Referral", "empfehlung", True)]
        # zweites Speichern sichert die vorherige Fassung
        prev2 = client.post("/api/regelwerk/kategorien/vorschau",
                            json={"export": {"Kraken": {"Referral": ""}}}).json()
        r = client.post("/api/regelwerk/kategorien/speichern", json={"yaml": prev2["yaml"], "bestaetigt": True})
        assert (local / "kategorien.yaml.bak").read_text(encoding="utf-8") == prev["yaml"]
        assert "Referral" not in (local / "kategorien.yaml").read_text(encoding="utf-8")


def test_changes_apply_in_ram_without_saving(local) -> None:
    """Zuordnungen gelten sofort für die Sitzung (Arbeitsspeicher); Speichern ist optional."""
    from btc_origin.api.app import app
    from btc_origin.regelwerk import load_categories

    with TestClient(app) as client:
        client.post("/api/local/reload")
        r = client.post("/api/regelwerk/kategorien/anwenden", json={"export": {"Kraken": {"Buy": "kauf"}}})
        assert r.status_code == 200 and r.json()["disk_written"] is False
        assert not (local / "kategorien.yaml").exists()  # nichts geschrieben
        state = app.state.session
        assert [t.art for t in state.local.trades] == ["Buy"]  # sofort wirksam
        data = client.get("/api/regelwerk/kategorien").json()
        buy = next(a for a in data["arten"] if a["art"] == "Buy")
        assert buy["status"] == "ok" and buy["ungespeichert"] is True
        assert data["sitzung"] == {"aktiv": True, "ungespeichert": True}
        assert any("Sitzung, nicht gespeichert" in n for n, _h in load_categories().hashes)  # Prüfprotokoll
        # weitere Änderung baut auf der Sitzung auf
        client.post("/api/regelwerk/kategorien/anwenden", json={"export": {"Kraken": {"Referral": "empfehlung"}}})
        assert [t.art for t in state.local.trades] == ["Buy", "Referral"]
        # speichern (optional): Datei = Stand der Sitzung
        prev = client.post("/api/regelwerk/kategorien/vorschau", json={}).json()
        client.post("/api/regelwerk/kategorien/speichern", json={"yaml": prev["yaml"], "bestaetigt": True})
        data = client.get("/api/regelwerk/kategorien").json()
        assert data["sitzung"]["aktiv"] is False and not any(a["ungespeichert"] for a in data["arten"])
        # verwerfen: zurück zur Datei
        client.post("/api/regelwerk/kategorien/anwenden", json={"export": {"Kraken": {"Referral": ""}}})
        assert [t.art for t in state.local.trades] == ["Buy"]
        client.post("/api/regelwerk/kategorien/verwerfen")
        assert [t.art for t in state.local.trades] == ["Buy", "Referral"]


def test_invalid_assignment_is_rejected_before_writing(local) -> None:
    from btc_origin.api.app import app

    with TestClient(app) as client:
        r = client.post("/api/regelwerk/kategorien/vorschau", json={"export": {"Kraken": {"Buy": "geschenk"}}})
        assert r.status_code == 422 and "geschenk" in r.json()["detail"]
        r = client.post("/api/regelwerk/kategorien/vorschau",
                        json={"manuell": {"T-001": {"kategorie": "empfehlung", "datum": "2025-03-01"}}})
        assert r.status_code == 200 and "T-001" in r.json()["yaml"]
    assert not (local / "kategorien.yaml").exists()


def test_pdf_compare_endpoint(client) -> None:
    pytest.importorskip("pymupdf")
    from fpdf import FPDF

    def pdf(value: str) -> str:
        doc = FPDF()
        doc.add_page()
        doc.set_font("helvetica", size=10)
        for line in ("3  Werte", value):
            doc.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
        return base64.b64encode(bytes(doc.output())).decode()

    same = client.post("/api/pdf-vergleich", json={"alt": pdf("Wert 1"), "neu": pdf("Wert 1")}).json()
    assert same["gleich"] is True
    diff = client.post("/api/pdf-vergleich", json={"alt": pdf("Wert 1"), "neu": pdf("Wert 2")}).json()
    assert diff == {"gleich": False, "zeilen": 2, "abschnitte": [{"abschnitt": "3  Werte", "zeilen": 2}]}


def test_change_list_for_the_review_before_merge(local) -> None:
    from btc_origin.api.app import app

    with TestClient(app) as client:
        client.post("/api/local/reload")
        info = client.get("/api/regelwerk/kategorien").json()["aenderungen"]
        r = client.get("/api/regelwerk/aenderungen.csv")
    assert info == {"zeilen": 2, "wirkt_auf_werte": 1}
    assert r.headers["content-disposition"].startswith("attachment;")
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "Börse;Art laut Export;Datum;Menge BTC;Datei;bisher;neu;wirkt auf Werte"
    assert lines[1].startswith("Kraken;Buy;2025-03-01;0,01000000;kraken.csv;Kauf (bewertet);nicht unterstützt:")
    assert lines[1].endswith(";ja")
    assert lines[2].startswith("Kraken;Referral;2025-03-02;0,00100000;kraken.csv;verworfen (Art unbekannt);")
    assert lines[2].endswith(";nein")
