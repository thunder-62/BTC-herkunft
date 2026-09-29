"""Regelwerk (btc-regeln/): Regeldateien je Veranlagungsjahr und kategorien.yaml —
Validierung nach REGELWERK.md Abschnitt 5, Abbruch bei fehlender Datei."""

from __future__ import annotations

import os
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from btc_origin.origin_report import TxRef
from btc_origin.regelwerk import (
    RegelwerkFehler,
    check_manual,
    load_categories,
    methodik_vermerk,
    parse_categories,
    parse_rules,
    rules_for,
    validate_all,
)

# Testkopie (eingefroren); die echten Dateien prüft test_repository_rules_are_valid
REPO = Path(__file__).resolve().parent / "data" / "btc-regeln"
REAL = Path(__file__).resolve().parents[1] / "btc-regeln"


def test_repository_rules_are_valid() -> None:
    """Die echten Dateien in btc-regeln/ (von der steuerlichen Prüfung gepflegt) laden ohne
    Fehler; jedes Jahr ab 2022 bis zum Vorjahr hat eine Regeldatei."""
    from datetime import date as _date

    checked = validate_all(REAL, local_dir=Path("/nonexistent"))
    years = [int(v.split()[2].rstrip(",")) for v in checked]
    assert set(range(2022, _date.today().year)) <= set(years)


def _rules_text(year: int = 2025) -> str:
    return (REPO / "regeln" / f"{year}.yaml").read_text(encoding="utf-8")


def _categories_text() -> str:
    return (REPO / "kategorien.yaml").read_text(encoding="utf-8")


def test_repository_files_are_valid_and_match_documented_values() -> None:
    checked = validate_all(REPO, local_dir=Path("/nonexistent"))
    assert checked == [f"Regelwerk VZ {y}, Stand 29.09.2026" for y in range(2022, 2027)]
    values = {y: (r.haltefrist_monate, r.freigrenze_23_eur, r.freigrenze_22_3_eur, r.reform_beschlossen)
              for y in range(2022, 2027) for r in [rules_for(y, REPO)]}
    assert values == {2022: (12, 600, 256, False), 2023: (12, 600, 256, False), 2024: (12, 1000, 256, False),
                      2025: (12, 1000, 256, False), 2026: (12, 1000, 256, False)}
    assert rules_for(2026, REPO).altbestand_stichtag() == date(2026, 12, 31)
    assert "06.03.2025" in rules_for(2025, REPO).bmf_quelle


def test_repository_categories_hold_no_personal_assignments() -> None:
    kat = load_categories(REPO, local_dir=Path("/nonexistent"))
    assert kat.manuell == {}  # persönliche Zuordnungen nur in local/kategorien.yaml
    assert {k.name for k in kat.unterstuetzt} == {"kauf", "empfehlung", "cashback"}
    assert "mining" in {k.name for k in kat.nicht_unterstuetzt}
    assert kat.export_kategorie("Relai", "Buy") == "kauf"
    assert kat.export_kategorie("relai", "Reward") is None  # unbekannt → nicht unterstützt


def test_missing_year_aborts_with_clear_message() -> None:
    with pytest.raises(RegelwerkFehler, match=r"Veranlagungsjahr 2021 fehlt \(regeln/2021\.yaml\)"):
        rules_for(2021, REPO)
    with pytest.raises(RegelwerkFehler, match="2008"):
        methodik_vermerk([2008, 2025])


def test_report_without_rules_for_its_year_is_refused(tmp_path, monkeypatch) -> None:
    root = tmp_path / "btc-regeln"
    shutil.copytree(os.environ["BTC_ORIGIN_RULES_DIR"], root)
    (root / "regeln" / "2025.yaml").unlink()
    monkeypatch.setenv("BTC_ORIGIN_RULES_DIR", str(root))
    from btc_origin.api.app import app

    with TestClient(app) as client:
        r = client.get("/api/report/herkunft.pdf", params={"stichtag": "2025-12-31"})
    assert r.status_code == 422 and "regeln/2025.yaml" in r.json()["detail"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("veranlagungsjahr: 2025", "veranlagungsjahr: 2024", "passt nicht zum Dateinamen"),
        ('geprueft_von: "Recherche (keine Steuerberatung)"\n', "", "Pflichtfeld „geprueft_von“ fehlt"),
        ("freigrenze_eur: 1000", 'freigrenze_eur: "1000"', "falschen Typ (erwartet Ganzzahl)"),
        ("stand: 2026-09-29", 'stand: "29.09.2026"', "„stand“ hat den falschen Typ (erwartet Datum"),
        ("beschlossen: false", "beschlossen: nein", "falschen Typ (erwartet Boolesch)"),
        ("beschlossen: false", "beschlossen: true", "beschlossene Reform"),
        ("haltefrist_monate: 12", "haltefrist_monate: 6", "nicht unterstützt"),
        ("weniger als 1000 € beträgt", "weniger als 600 € beträgt", "nennt 600 €"),
        ("weniger als 256 € betragen", "weniger als 265 € betragen", "nennt 265 €"),
        ("- id: rz_63", "- id: rz_56", "id „rz_56“ mehrfach"),
        ("- id: rz_63", "- id: Rz-63", "nur a–z"),
        ("  primaer:", "  primär_alt:", "Pflichtfeld „kursregel.primaer“ fehlt"),
        ("stand: 2026-09-29", "stand: 2026-09-29\nstand: 2026-09-30", "Schlüssel „stand“ doppelt"),
        ("id: binance_tagesschluss_utc", "id: coingecko_mittel", "kursregel.id „coingecko_mittel“ ist im Programm nicht umgesetzt"),
        ("  ausblick: >-", "  ausblick_alt: >-", "Pflichtfeld „reform.ausblick“ fehlt"),
    ],
)
def test_rules_validation(old: str, new: str, message: str) -> None:
    text = _rules_text()
    assert old in text
    with pytest.raises(RegelwerkFehler, match=re.escape(message)):
        parse_rules(text.replace(old, new, 1), 2025)


def test_optional_stichtag_altbestand() -> None:
    text = _rules_text().replace("  stichtag_altbestand: 2026-12-31", "")
    rw = parse_rules(text, 2025)
    with pytest.raises(RegelwerkFehler, match="stichtag_altbestand fehlt"):
        rw.altbestand_stichtag()


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('"Buy": kauf', '"Buy": geschenk', "Kategorie „geschenk“ ist in kategorien nicht definiert"),
        ('"Buy": kauf', '"Buy": kauf\n      "Buy": kauf', "Schlüssel „Buy“ doppelt"),
        ("behandlung: nicht_unterstuetzt\n    beschreibung: \"Block",
         "behandlung: ignorieren\n    beschreibung: \"Block", "behandlung „ignorieren“ unbekannt"),
        ('    spalte_art: "Transaction Type"\n', "", "Pflichtfeld „spalte_art“ fehlt"),
    ],
)
def test_categories_validation(old: str, new: str, message: str) -> None:
    text = _categories_text()
    assert old in text
    with pytest.raises(RegelwerkFehler, match=re.escape(message)):
        parse_categories(text.replace(old, new, 1))


def test_local_categories_add_assignments_only() -> None:
    local = (
        "zuordnung_manuell:\n"
        "  T-003:\n"
        "    kategorie: empfehlung\n"
        "    datum: 2025-03-01\n"
        '    erlaeuterung: "Empfehlungsprämie"\n'
        "zuordnung_export:\n"
        "  relai:\n"
        '    spalte_art: "Transaction Type"\n'
        "    arten:\n"
        '      "Referral": empfehlung\n'
    )
    kat = parse_categories(_categories_text(), local_text=local)
    assert kat.manuell["T-003"].kategorie == "empfehlung"
    assert kat.export_kategorie("Relai", "Referral") == "empfehlung" and kat.export_kategorie("Relai", "Buy") == "kauf"
    assert [name for name, _ in kat.hashes] == ["kategorien.yaml", "local/kategorien.yaml"]
    with pytest.raises(RegelwerkFehler, match="„kategorien“ nur in"):
        parse_categories(_categories_text(), local_text="kategorien:\n  bonus:\n    behandlung: anschaffung\n")
    with pytest.raises(RegelwerkFehler, match="Art „Buy“ für relai steht schon"):
        parse_categories(_categories_text(), local_text=local.replace('"Referral"', '"Buy"'))
    with pytest.raises(RegelwerkFehler, match="Kategorie „bonus“ ist in kategorien nicht definiert"):
        parse_categories(_categories_text(), local_text=local.replace("kategorie: empfehlung", "kategorie: bonus"))
    with pytest.raises(RegelwerkFehler, match="erwartet einen Transaktions-Hash"):
        parse_categories(_categories_text(), local_text=local.replace("T-003", "Relai"))


def test_manual_assignment_needs_existing_inflow_with_same_date() -> None:
    local = "zuordnung_manuell:\n  T-002:\n    kategorie: empfehlung\n    datum: 2025-03-01\n"
    kat = parse_categories(_categories_text(), local_text=local)
    refs = [TxRef("T-001", "2025-01-01", "Wallet A", "Zufluss", "t1"),
            TxRef("T-002", "2025-03-01", "Wallet A", "Zufluss", "t2"),
            TxRef("T-003", "2025-04-01", "Wallet A", "Abfluss", "t3")]
    check_manual(kat, refs)
    with pytest.raises(RegelwerkFehler, match="passt nicht zur Transaktion .* verschoben"):
        check_manual(kat, [TxRef("T-002", "2025-01-01", "Wallet A", "Zufluss", "t1")])
    with pytest.raises(RegelwerkFehler, match="nicht im Transaktionsverzeichnis"):
        check_manual(kat, refs[:1])
    check_manual(kat, refs[:1], until=date(2025, 2, 1))  # Eintrag nach dem Berichtszeitraum
    kat = parse_categories(_categories_text(), local_text=local.replace("T-002", "T-003").replace("03-01", "04-01"))
    with pytest.raises(RegelwerkFehler, match="kein Zufluss"):
        check_manual(kat, refs)


def test_manual_assignment_by_transaction_hash_survives_shifted_numbers() -> None:
    """Schlüssel = Transaktions-Hash: stabil, auch wenn sich die T-Nummern verschieben."""
    tx = "c" * 64
    local = f"zuordnung_manuell:\n  {tx}:\n    kategorie: empfehlung\n    datum: 2025-03-01\n"
    kat = parse_categories(_categories_text(), local_text=local)
    check_manual(kat, [TxRef("T-002", "2025-03-01", "Wallet A", "Zufluss", tx)])
    # ältere Transaktion hinzugekommen → Nummer verschoben, Zuordnung bleibt gültig
    shifted = [TxRef("T-001", "2024-01-01", "Wallet A", "Zufluss", "t0"),
               TxRef("T-003", "2025-03-01", "Wallet A", "Zufluss", tx)]
    check_manual(kat, shifted)
    assert kat.manuell[tx].resolve(shifted).ref == "T-003"
    with pytest.raises(RegelwerkFehler, match="Transaktion nicht im Transaktionsverzeichnis"):
        check_manual(kat, shifted[:1])


def test_app_does_not_start_with_broken_rules(tmp_path, monkeypatch) -> None:
    root = tmp_path / "btc-regeln"
    shutil.copytree(os.environ["BTC_ORIGIN_RULES_DIR"], root)
    y = root / "regeln" / "2024.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("veranlagungsjahr: 2024", "veranlagungsjahr: 2023"),
                 encoding="utf-8")
    monkeypatch.setenv("BTC_ORIGIN_RULES_DIR", str(root))
    from btc_origin.api.app import app

    with pytest.raises(RegelwerkFehler, match="regeln/2024.yaml: veranlagungsjahr 2023"):
        with TestClient(app):
            pass


def test_pdf_vergleich_reports_sections_without_values() -> None:
    from btc_origin.pdf_vergleich import compare

    old = ["Titel", "3  Mögliche Gewinne und Verluste", "Gewinn 100,00 €", "6  Rechtsgrundlagen und Quellen", "alt"]
    new = ["Titel", "3  Mögliche Gewinne und Verluste", "Gewinn 100,00 €", "6  Rechtsgrundlagen und Quellen", "neu"]
    assert compare(old, old) == {}
    assert dict(compare(old, new)) == {"6  Rechtsgrundlagen und Quellen": 2}


def test_pdf_vergleich_ignores_timestamps_and_page_headers(tmp_path) -> None:
    pytest.importorskip("pymupdf")
    from fpdf import FPDF

    from btc_origin.pdf_vergleich import main

    def pdf(stamp: str, value: str) -> Path:
        doc = FPDF()
        doc.add_page()
        doc.set_font("helvetica", size=10)
        for line in (f"Bericht - Stand {stamp}", "3  Werte", value, "Seite 1 von 1"):
            doc.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
        path = tmp_path / f"{len(list(tmp_path.iterdir()))}.pdf"
        path.write_bytes(bytes(doc.output()))
        return path

    a = pdf("2026-01-01 10:05:00", "Wert 1")
    assert main([str(a), str(pdf("2026-02-01 11:10:00", "Wert 1"))]) == 0
    assert main([str(a), str(pdf("2026-02-01 11:10:00", "Wert 2"))]) == 1


def test_freigrenze_text_names_all_years_of_the_report() -> None:
    from btc_origin.report_sections.grundlagen import with_other_years

    text = "Gewinne bleiben steuerfrei, wenn der Gesamtgewinn weniger als 1000 € beträgt."
    by_year = {2022: 600, 2023: 600, 2024: 1000, 2025: 1000}
    assert with_other_years(text, 1000, by_year) == (
        "Gewinne bleiben steuerfrei, wenn der Gesamtgewinn weniger als 1.000 € (VZ 2022–2023: 600 €) beträgt.")
    assert with_other_years(text, 1000, {2025: 1000}) == text


def test_txid_alone_only_with_exactly_one_inflow_otherwise_txid_vout() -> None:
    """Vorgabe der steuerlichen Prüfung: txid allein nur bei genau einem Zufluss in die
    betrachteten Wallets; sonst txid:vout. Datum bleibt Pflicht."""
    from btc_origin.regelwerk import Quittung, inflow_outputs

    tx = "d" * 64
    rows = [{"txid": tx, "direction": "in", "vout": 0, "amount_sats": 100_000},
            {"txid": tx, "direction": "in", "vout": 2, "amount_sats": 250_000},
            {"txid": tx, "direction": "out", "vout": None, "amount_sats": 1}]
    outputs = inflow_outputs(rows, {tx})
    assert outputs == {tx: [(0, 100_000), (2, 250_000)]}
    refs = [TxRef("T-001", "2025-03-01", "Wallet A", "Zufluss", tx)]

    def kat(key: str, day: str = "2025-03-01"):
        return parse_categories(_categories_text(),
                                local_text=f"zuordnung_manuell:\n  {key}:\n    kategorie: empfehlung\n    datum: {day}\n")

    with pytest.raises(RegelwerkFehler, match="2 Zuflüsse .* txid:vout"):
        check_manual(kat(tx), refs, outputs=outputs)
    check_manual(kat(f"{tx}:2"), refs, outputs=outputs)
    assert kat(f"{tx}:2").manuell[f"{tx}:2"].vout == 2
    with pytest.raises(RegelwerkFehler, match="Output 1 ist kein Zufluss"):
        check_manual(kat(f"{tx}:1"), refs, outputs=outputs)
    with pytest.raises(RegelwerkFehler, match="passt nicht zur Transaktion"):
        check_manual(kat(f"{tx}:2", "2025-03-02"), refs, outputs=outputs)
    with pytest.raises(RegelwerkFehler, match="Pflichtfeld „datum“"):
        parse_categories(_categories_text(), local_text=f"zuordnung_manuell:\n  {tx}:\n    kategorie: empfehlung\n")
    # Quittung eines Wallet-Vorgangs per txid bzw. txid:vout
    q = Quittung(date(2025, 3, 1), "geprüft", f"{tx}:2")
    assert q.matches({"day": "2025-03-01", "txid": tx, "vout": 2})
    assert not q.matches({"day": "2025-03-01", "txid": tx, "vout": 0})
    assert tx not in q.label  # nie vollständig in Meldungen
