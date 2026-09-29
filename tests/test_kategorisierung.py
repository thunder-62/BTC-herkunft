"""Einordnung der Export-Zeilen nach kategorien.yaml und Schutz vor nicht unterstützten
Vorgängen (REGELWERK.md 4.1): kein Wert, keine Summe, Abschnitt 8.3, Kontrolldatei mit Fehler."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from btc_origin.exchanges.common import ExchangeTrade
from btc_origin.kategorisierung import art_overview, categorize_trades, passende_kategorien
from btc_origin.local_files import parse_trades
from btc_origin.regelwerk import parse_categories

REPO = Path(__file__).resolve().parent / "data" / "btc-regeln"  # Testkopie (eingefroren)
LOCAL = (
    "zuordnung_export:\n"
    "  kraken:\n"
    '    spalte_art: "Type"\n'
    "    arten:\n"
    '      "Mining Reward": mining\n'
    '      "Buy": kauf\n'
    "  bitvavo:\n"
    '    spalte_art: "Type"\n'
    "    arten:\n"
    '      "referral": empfehlung\n'
)
D = date(2025, 3, 1)


def _kat(local: str | None = LOCAL):
    return parse_categories((REPO / "kategorien.yaml").read_text(encoding="utf-8"), local_text=local)


def test_supported_rows_get_their_category() -> None:
    ok, bad = categorize_trades([
        ExchangeTrade("Relai", D, "buy", 100_000, eur=60.0, art="Buy"),
        ExchangeTrade("Bitvavo", D, "sell", 100_000, eur=60.0, art="sell"),
        ExchangeTrade("Bitvavo", D, "withdraw", 100_000, art="withdrawal"),
    ], _kat())
    assert bad == [] and [t.kategorie for t in ok] == ["kauf", "verkauf", "auszahlung"]


def test_income_category_becomes_purchase_at_daily_rate() -> None:
    (t,), bad = categorize_trades([ExchangeTrade("Bitvavo", D, "deposit", 10_000, eur=5.0, art="referral")], _kat())
    assert bad == []
    assert (t.kind, t.eur, t.kategorie, t.einkunft) == ("buy", None, "empfehlung", True)


@pytest.mark.parametrize(
    ("trade", "grund"),
    [
        (ExchangeTrade("Kraken", D, "buy", 1, art="Mining Reward"), "Kategorie „Mining“"),
        (ExchangeTrade("Kraken", D, "unknown", 1, art="Mining Reward"), "Kategorie „Mining“"),
        (ExchangeTrade("Kraken", D, "unknown", 1, art="Foo"), "vom Parser nicht erkannt"),
        (ExchangeTrade("Kraken", D, "withdraw", 1, art="Withdrawal"), "nicht in kategorien.yaml zugeordnet"),
        (ExchangeTrade("Kraken", D, "sell", 1, eur=1.0, art="Buy"), "passt nicht zur Richtung laut Export (Verkauf)"),
        (ExchangeTrade("Coinbase", D, "buy", 1, quote="ETH", art="Convert"), "Tausch gegen ETH"),
        (ExchangeTrade("Binance", D, "buy", 1, art="Simple Earn Flexible Interest"), "Kategorie „Lending“"),
    ],
)
def test_unsupported_rows_are_not_valued(trade: ExchangeTrade, grund: str) -> None:
    ok, (u,) = categorize_trades([trade], _kat())
    assert ok == [] and grund in u.grund and u.status == "nicht unterstützt – manuell prüfen"


ALLE_NU = ["mining", "staking", "lending", "airdrop", "fork", "tausch_krypto", "andere_kryptowerte", "defi",
           "schenkung_erbschaft", "betriebsvermoegen", "lightning_zahlung"]
ZUGANG_NU = ALLE_NU
ABGANG_NU = ["lending", *ALLE_NU[5:]]


def test_art_overview_without_amounts_or_dates() -> None:
    rows = art_overview([
        ExchangeTrade("Kraken", D, "buy", 100_000, eur=60.0, art="Buy"),
        ExchangeTrade("Kraken", date(2025, 4, 1), "buy", 200_000, eur=60.0, art="Buy"),
        ExchangeTrade("Kraken", D, "unknown", 1, art="Foo"),
    ], _kat())
    assert rows == [
        {"exchange": "Kraken", "art": "Buy", "count": 2, "richtung": "Kauf", "zuordnung": "kauf",
         "status": "ok", "grund": "", "jahre": {"2025": 2}, "erste_je_jahr": {"2025": "2025-03-01"},
         "passend": ["kauf", "empfehlung", "cashback", *ZUGANG_NU], "erste": "2025-03-01"},
        {"exchange": "Kraken", "art": "Foo", "count": 1, "richtung": "unbekannt", "zuordnung": "",
         "status": "nicht_unterstuetzt", "grund": "Art „Foo“ vom Parser nicht erkannt (Richtung unbekannt)",
         "jahre": {"2025": 1}, "erste_je_jahr": {"2025": "2025-03-01"}, "passend": ["empfehlung", "cashback", *ALLE_NU],
         "erste": "2025-03-01"},
    ]


def test_suitable_categories_follow_the_direction() -> None:
    """Auswahl in der Oberfläche: nur Zuordnungen, die categorize_trades bei dieser Richtung
    annimmt — Kauf bzw. Verkauf zuerst."""
    kat = _kat()
    assert passende_kategorien({"Kauf"}, kat)[0] == "kauf"
    assert passende_kategorien({"Verkauf"}, kat) == ["verkauf", *ABGANG_NU]
    assert passende_kategorien({"Auszahlung"}, kat) == ["auszahlung", *ABGANG_NU]
    assert passende_kategorien({"Einzahlung"}, kat) == ["empfehlung", "cashback", "einzahlung", *ZUGANG_NU]
    assert "mining" not in ABGANG_NU and "kauf" not in passende_kategorien({"Auszahlung"}, kat)
    for richtung, kind in (("Kauf", "buy"), ("Verkauf", "sell"), ("Auszahlung", "withdraw"),
                           ("Einzahlung", "deposit")):
        for name in passende_kategorien({richtung}, kat):
            if name in ALLE_NU:
                continue
            local = f'zuordnung_export:\n  kraken:\n    spalte_art: "Type"\n    arten:\n      "X": {name}\n'
            k = parse_categories((REPO / "kategorien.yaml").read_text(encoding="utf-8"), local_text=local)
            ok, bad = categorize_trades([ExchangeTrade("Kraken", D, kind, 1, eur=1.0, art="X")], k)
            assert ok and not bad, (richtung, name, bad)


def test_parsers_keep_the_export_art() -> None:
    text = (
        "Date,Type,Asset,Amount,Fiat Amount\n"
        "2025-03-01 10:05:00,Buy,BTC,0.01,600.00\n"
        "2025-03-02 10:05:00,Mining Reward,BTC,0.001,\n"
        "2025-03-03 10:05:00,Foo,BTC,0.002,\n"
        "2025-03-04 10:05:00,Buy,ETH,1.0,3000.00\n"
    )
    trades = parse_trades(text, "Kraken")
    assert [(t.kind, t.art, t.sats) for t in trades] == [
        ("buy", "Buy", 1_000_000), ("unknown", "Mining Reward", 100_000), ("unknown", "Foo", 200_000)]


# ---------------------------------------------------------------------------
# Abnahme (Vorgabe Punkt 4): „Mining Reward“ und eine unbekannte Art in einer Testkopie der
# Daten → beide nicht unterstützt, Bericht weist sie aus, Prüfprotokoll endet mit Fehler
# ---------------------------------------------------------------------------


def test_mining_reward_and_unknown_art_end_with_error(tmp_path, monkeypatch) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    local = tmp_path / "local"
    (local / "boersen").mkdir(parents=True)
    (local / "boersen" / "kraken.csv").write_text(
        "Date,Type,Asset,Amount,Fiat Amount\n"
        "2025-03-01 10:05:00,Buy,BTC,0.01,600.00\n"
        "2025-03-02 10:05:00,Mining Reward,BTC,0.001,\n"
        "2025-03-03 10:05:00,Foo,BTC,0.002,\n",
        encoding="utf-8",
    )
    (local / "kategorien.yaml").write_text(LOCAL, encoding="utf-8")
    monkeypatch.setenv("BTC_ORIGIN_LOCAL_DIR", str(local))
    from btc_origin.api.app import app

    with TestClient(app) as client:
        assert client.post("/api/local/reload").status_code == 200
        state = app.state.session
        assert [t.art for t in state.local.trades] == ["Buy"]  # nur die unterstützte Zeile wird bewertet
        params = {"stichtag": "2025-12-31", "bis": "2025-12-31"}
        control = client.get("/api/report/herkunft-kontrolle.txt", params=params)
        pdf = client.get("/api/report/herkunft.pdf", params=params)
    body = control.text
    assert control.headers["x-btc-herkunft-pruefergebnis"] == "fehler"
    assert "[FEHLER] 5 Keine nicht unterstützten Vorgänge" in body
    assert "Kraken „Mining Reward“" in body and "Kraken „Foo“" in body
    assert body.rstrip().splitlines()[-3].startswith("ERGEBNIS: FEHLER")
    text = " ".join("".join(p.get_text() for p in pymupdf.open(stream=io.BytesIO(pdf.content))).split())
    assert "NICHT UNTERSTÜTZTE VORGÄNGE — nicht bewertet, manuell prüfen 2 ✗" in text
    assert "8.3 Nicht unterstützte Vorgänge" in text
    assert "Kategorie „Mining“" in text and "vom Parser nicht erkannt" in text
    assert "Geltungsbereich — unterstützt: Bitcoin on-chain im Privatvermögen" in text and "mining" not in text


def test_control_file_passes_without_unsupported_rows(client) -> None:
    r = client.get("/api/report/herkunft-kontrolle.txt", params={"stichtag": "2025-12-31"})
    assert r.headers["x-btc-herkunft-pruefergebnis"] == "bestanden"
    assert "ERGEBNIS: BESTANDEN" in r.text and "[OK] 5 Keine nicht unterstützten Vorgänge" in r.text


def test_income_category_classifies_unknown_art_as_inflow() -> None:
    kat = _kat(LOCAL.replace('      "referral": empfehlung\n', '      "Referral": empfehlung\n'))
    (t,), bad = categorize_trades([ExchangeTrade("Bitvavo", D, "unknown", 10_000, art="Referral")], kat)
    assert bad == [] and (t.kind, t.einkunft, t.kategorie) == ("buy", True, "empfehlung")
    # Kauf/Verkauf brauchen die erkannte Richtung
    _ok, (u,) = categorize_trades([ExchangeTrade("Kraken", D, "unknown", 1, art="Buy")], _kat())
    assert "Richtung unbekannt" in u.grund


def test_acknowledged_unsupported_rows_only_warn(tmp_path, monkeypatch) -> None:
    """Quittierte Vorgänge (geprueft_nicht_unterstuetzt) bleiben in 8.3 mit Erläuterung, die
    Kontrolldatei meldet nur eine Warnung; eine nicht quittierte Zeile bleibt ein Fehler."""
    local = tmp_path / "local"
    (local / "boersen").mkdir(parents=True)
    (local / "boersen" / "kraken.csv").write_text(
        "Date,Type,Asset,Amount,Operation ID\n"
        "2025-03-02 10:05:00,Mining Reward,BTC,0.001,op-1\n"
        "2025-03-03 10:05:00,Foo,BTC,0.002,op-2\n",
        encoding="utf-8",
    )
    ack = (
        "geprueft_nicht_unterstuetzt:\n"
        "  - boerse: Kraken\n"
        "    id: op-1\n"
        "    datum: 2025-03-02\n"
        '    erlaeuterung: "Mining-Ertrag, mit Steuerberater geklärt"\n'
    )
    (local / "kategorien.yaml").write_text(LOCAL + ack, encoding="utf-8")
    monkeypatch.setenv("BTC_ORIGIN_LOCAL_DIR", str(local))
    from btc_origin.api.app import app

    params = {"stichtag": "2025-12-31", "bis": "2025-12-31"}
    with TestClient(app) as client:
        client.post("/api/local/reload")
        one_open = client.get("/api/report/herkunft-kontrolle.txt", params=params)
        (local / "kategorien.yaml").write_text(
            LOCAL + ack + "  - boerse: kraken\n    art: Foo\n    datum: 2025-03-03\n    erlaeuterung: geprüft\n"
            "  - tnr: T-999\n    datum: 2025-05-01\n    erlaeuterung: veraltet\n",
            encoding="utf-8",
        )
        client.post("/api/local/reload")
        all_acked = client.get("/api/report/herkunft-kontrolle.txt", params=params)
    assert one_open.headers["x-btc-herkunft-pruefergebnis"] == "fehler"
    assert "[WARNUNG] Quittiert (geprüft, nicht bewertet): 02.03.2025 Kraken „Mining Reward“" in one_open.text
    assert all_acked.headers["x-btc-herkunft-pruefergebnis"] == "warnung"
    assert "[OK] 5 Keine nicht unterstützten Vorgänge (Geltungsbereich): keine offenen; 2 geprüft" in all_acked.text
    assert "ERGEBNIS: BESTANDEN mit 3 Warnung(en)" in all_acked.text
    assert "Quittung ohne passenden Vorgang: T-999 am 01.05.2025" in all_acked.text


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("  - boerse: kraken\n    art: Foo\n    datum: 2025-03-03\n", "Pflichtfeld „erlaeuterung“ fehlt"),
        ("  - boerse: kraken\n    datum: 2025-03-03\n    erlaeuterung: x\n", r"txid \(Wallet-Vorgang\) oder boerse"),
        ("  - tnr: Relai\n    datum: 2025-03-03\n    erlaeuterung: x\n", "erwartet eine T-Nummer"),
    ],
)
def test_acknowledgement_validation(entry: str, message: str) -> None:
    from btc_origin.regelwerk import RegelwerkFehler

    with pytest.raises(RegelwerkFehler, match=message):
        _kat(LOCAL + "geprueft_nicht_unterstuetzt:\n" + entry)
    with pytest.raises(RegelwerkFehler, match="nur in local/kategorien.yaml"):
        parse_categories((REPO / "kategorien.yaml").read_text(encoding="utf-8")
                         + "\ngeprueft_nicht_unterstuetzt:\n  - tnr: T-001\n    datum: 2025-01-01\n    erlaeuterung: x\n")


def test_binance_convert_gets_the_direction_appended() -> None:
    """„Binance Convert“ steht für Kauf und Verkauf — die Art trägt die Lesart des Parsers."""
    text = (
        "User ID,Time,Account,Operation,Coin,Change,Remark\n"
        "1,2025-03-01 10:05:00,Spot,Binance Convert,BTC,0.01000000,\n"
        "1,2025-03-01 10:05:00,Spot,Binance Convert,EUR,-600.00,\n"
        "1,2025-04-01 10:05:00,Spot,Binance Convert,BTC,-0.00500000,\n"
        "1,2025-04-01 10:05:00,Spot,Binance Convert,EUR,300.00,\n"
        "1,2025-05-01 10:05:00,Spot,Transaction Buy,BTC,0.01000000,\n"
        "1,2025-05-01 10:05:00,Spot,Transaction Spend,EUR,-600.00,\n"
    )
    trades = parse_trades(text, "Binance")
    assert [(t.kind, t.art, t.eur) for t in trades] == [
        ("buy", "Binance Convert – Kauf", 600.0),
        ("sell", "Binance Convert – Verkauf", 300.0),
        ("buy", "Transaction Buy", 600.0),
    ]


def test_art_overview_counts_per_year_and_sorts_by_first_occurrence() -> None:
    rows = art_overview([
        ExchangeTrade("Relai", date(2024, 5, 1), "buy", 1, eur=1.0, art="Buy"),
        ExchangeTrade("Kraken", date(2023, 2, 1), "buy", 1, eur=1.0, art="Buy"),
        ExchangeTrade("Kraken", date(2025, 1, 1), "buy", 1, eur=1.0, art="Buy"),
    ], _kat())
    assert [(r["exchange"], r["erste"], r["jahre"]) for r in rows] == [
        ("Kraken", "2023-02-01", {"2023": 1, "2025": 1}),
        ("Relai", "2024-05-01", {"2024": 1}),
    ]
    assert rows[0]["erste_je_jahr"] == {"2023": "2023-02-01", "2025": "2025-01-01"}
