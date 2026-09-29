"""Verbleib einer Zieladresse: tiefe Ableitung und Blockchain-Spur (synthetische Daten)."""

from __future__ import annotations

from datetime import date

from btc_origin.destination_trace import chain_fate, deep_owner, script_type_of
from btc_origin.trail import TxInfo

ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
DEST = "bc1qzs4lwwnll38kxtkvd8997pggr9fs78480dhhu0"
FOREIGN = "36YG9HzwdkWf3GFfFLbqpyBxwPqkimih3b"
OWN = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


class FakeChain:
    def __init__(self, txs: dict[str, TxInfo], hist: dict[str, list[str]], heights: dict[str, int]) -> None:
        self.txs, self.hist, self.heights = txs, hist, heights

    def history(self, address: str) -> list[tuple[str, int | None]]:
        return [(t, self.heights.get(t)) for t in self.hist.get(address, [])]

    def tx_hex(self, txid: str) -> str:
        return txid

    def block_time(self, height: int | None) -> str | None:
        return {1: "2024-03-05T10:00:00Z", 2: "2024-03-20T10:00:00Z"}.get(height or 0)


def _patch(monkeypatch, chain: FakeChain) -> None:
    import btc_origin.destination_trace as dt

    monkeypatch.setattr(dt, "parse_tx", lambda txid, _hex: chain.txs[txid])


def test_script_type_and_deep_owner() -> None:
    assert script_type_of(OWN) == "p2wpkh" and script_type_of(FOREIGN) == "p2sh-p2wpkh"
    from btc_origin.hd_deriver import HdDeriver

    far = HdDeriver().derive_receive(ZPUB, start=300, count=1)[0].address
    assert deep_owner(far, [("Ledger", ZPUB)]) == ("Ledger", "m/0/300")
    assert deep_owner(DEST, [("Ledger", ZPUB)], max_index=50) is None


def test_chain_fate_forwarded_to_own_wallet(monkeypatch) -> None:
    chain = FakeChain(
        txs={"buy": TxInfo("buy", [("x" * 64, 0)], [(DEST, 100_000)]),
             "fwd": TxInfo("fwd", [("buy", 0)], [(OWN, 99_000)])},
        hist={DEST: ["buy", "fwd"]}, heights={"buy": 1, "fwd": 2},
    )
    _patch(monkeypatch, chain)
    text = chain_fate(chain, DEST, date(2024, 3, 4), 100_000, {OWN: "Ledger 2024"}, lambda a: None)
    assert text == ("Blockchain: Eingang am 05.03.2024 (+1 Tage, +0.00 % zur Menge laut Export); "
                    "Schritt 1: am 20.03.2024 weitergeleitet an eigene Wallet „Ledger 2024“")
    assert DEST not in text and OWN not in text


def test_chain_fate_unspent_and_foreign_hops(monkeypatch) -> None:
    chain = FakeChain(
        txs={"buy": TxInfo("buy", [("x" * 64, 0)], [(DEST, 100_000)]),
             "fwd": TxInfo("fwd", [("buy", 0)], [(FOREIGN, 99_000)])},
        hist={DEST: ["buy", "fwd"], FOREIGN: ["fwd"]}, heights={"buy": 1, "fwd": 2},
    )
    _patch(monkeypatch, chain)
    text = chain_fate(chain, DEST, date(2024, 3, 4), 100_000, {}, lambda a: None)
    assert "Schritt 1: am 20.03.2024 weitergeleitet an eine fremde Adresse" in text
    assert text.endswith("liegt nach 1 Schritt unbewegt auf einer fremden Adresse")
    assert FOREIGN not in text
    labelled = chain_fate(chain, DEST, date(2024, 3, 4), 100_000, {},
                          lambda a: "Binance" if a == FOREIGN else None)
    assert labelled.endswith("weitergeleitet an Binance (Label-Pack)")
    still = FakeChain(txs={"buy": chain.txs["buy"]}, hist={DEST: ["buy"]}, heights={"buy": 1})
    _patch(monkeypatch, still)
    assert chain_fate(still, DEST, date(2024, 3, 4), 100_000, {}, lambda a: None).endswith(
        "liegt noch dort (nicht weitergeleitet)")
    assert chain_fate(FakeChain({}, {}, {}), DEST, date(2024, 3, 4), 1, {}, lambda a: None).startswith(
        "Blockchain: Adresse nie benutzt")


def test_check_xpubs_file_accepts_only_public_keys(tmp_path) -> None:
    from btc_origin.local_files import CHECK_XPUBS_FILE, load_local_data, parse_check_xpubs

    rows, errors = parse_check_xpubs(f"# Kommentar\n{ZPUB}\nAltes Ledger;{ZPUB}\nxprv9s21ZrQH143K\n"
                                     + " ".join(["abandon"] * 12) + "\n")
    assert rows == [("Prüf-xpub 1", ZPUB), ("Altes Ledger", ZPUB)]
    assert len(errors) == 2 and all("kein öffentlicher Schlüssel" in e for e in errors)
    assert all("xprv" not in e and "abandon" not in e for e in errors)
    (tmp_path / CHECK_XPUBS_FILE).write_text(ZPUB + "\n", "utf-8")
    data = load_local_data(tmp_path)
    assert data.check_xpubs == [("Prüf-xpub 1", ZPUB)]
    assert ZPUB not in str(data.as_dict()) and data.as_dict()["check_xpubs_count"] == 1


def test_chain_fate_lists_days_of_all_receipts(monkeypatch) -> None:
    txs = {f"r{i}": TxInfo(f"r{i}", [("x" * 64, i)], [(DEST, 100_000)]) for i in range(3)}
    chain = FakeChain(txs=txs, hist={DEST: ["r0", "r1", "r2"]}, heights={"r0": 1, "r1": 2, "r2": 1})
    _patch(monkeypatch, chain)
    text = chain_fate(chain, DEST, date(2024, 3, 5), 100_000, {}, lambda a: None)
    assert "3 Eingänge insgesamt auf dieser Adresse (Tage: 05.03.2024, 20.03.2024)" in text
    assert "typisch für eine Einzahlungsadresse" in text


def test_diagnosis_lists_trace_of_assumed_disposals() -> None:
    from datetime import datetime

    from btc_origin.origin_report import HerkunftReport, render_matching_diagnosis

    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[], sources=[],
                         years=[], lots=[])
    disposals = [{"day": "2024-05-02", "wallet": "W", "counterparty": "ext-007", "sats": 150_000,
                  "trace": "Blockchain: Eingang am 02.05.2024; Schritt 1: weitergeleitet an eigene Wallet „W“"}]
    text = render_matching_diagnosis(rep, [], {}, privacy=True, disposals=disposals)
    assert "## 4 Spur der Abflüsse ohne Verkaufsbeleg" in text
    assert "| 02.05.2024 | W | ext-007 | ••• | Blockchain: Eingang am 02.05.2024;" in text


def test_diagnosis_lists_own_names_check() -> None:
    from datetime import datetime

    from btc_origin.origin_report import HerkunftReport, render_matching_diagnosis

    rep = HerkunftReport(generated_at=datetime(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[], sources=[],
                         years=[], lots=[])
    names = {"rows": [{"name": "Swap", "result": "greift (1 Abfluss/Abflüsse, 1 Zufluss/Zuflüsse)"}],
             "errors": ["Zeile 3: keine gültige Bitcoin-Adresse"]}
    text = render_matching_diagnosis(rep, [], {}, privacy=True, own_names=names)
    assert "## 5 Eigene Namen" in text and "| Swap | greift" in text
    assert "- Übersprungen: Zeile 3: keine gültige Bitcoin-Adresse" in text
