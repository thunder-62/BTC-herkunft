"""CLI-Vergleich PP vorher / nachher / Ist-Stand (erfundene Werte)."""

from btc_origin.pp_check import build_import_csv, compare, parse_pp_file
from btc_origin.pp_diff import ist_csv, main, read_ist

HEAD = "Datum;Typ;Wertpapier;Stück;Betrag;Notiz\n"
VORHER = HEAD + "2024-01-05;Einlieferung;Bitcoin;0,01;500,00;\n2024-06-01;Einlieferung;Bitcoin;0,02;900,00;alt\n"
CHAIN = [
    {"txid": "t1", "day": "2024-01-05", "direction": "in", "sats": 1_000_000, "fee_sats": 0,
     "wallet": "W", "counterparty": "Wallet A"},
    {"txid": "t2", "day": "2024-03-01", "direction": "in", "sats": 1_000_000, "fee_sats": 0,
     "wallet": "W", "counterparty": "Wallet B"},
]


def test_ist_csv_round_trip():
    fees = [{"day": "2024-04-01", "sats": 1_000, "txid": "t3"}]
    chain, got_fees, bestand = read_ist(ist_csv(CHAIN, fees, 1_999_000))
    assert [(c["day"], c["direction"], c["sats"]) for c in chain] == [
        ("2024-01-05", "in", 1_000_000), ("2024-03-01", "in", 1_000_000)]
    assert got_fees[0]["sats"] == 1_000 and bestand == 1_999_000


def test_cli_shows_changes_and_checks_the_delta(tmp_path, capsys):
    before = parse_pp_file(VORHER)
    delta, _ = build_import_csv(before, compare(before.rows, CHAIN), lambda d: 50_000.0)
    # Delta importiert, „alt“ gelöscht — aber die Delta-Zeile versehentlich zweimal
    delta_rows = delta.splitlines()[1:]
    nachher = HEAD + "2024-01-05;Einlieferung;Bitcoin;0,01;500,00;\n" + "\n".join(delta_rows * 2) + "\n"
    for name, text in (("vorher", VORHER), ("nachher", nachher), ("delta", delta),
                       ("ist", ist_csv(CHAIN, [], 2_000_000))):
        (tmp_path / f"{name}.csv").write_text(text, encoding="utf-8")
    args = [f"--{n}={tmp_path / f'{n}.csv'}" for n in ("vorher", "nachher", "ist", "delta")]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Δ nachher − Ist:     0,01000000 BTC  ✗" in out
    assert "Aus PP entfernt (1" in out and "alt" in out
    assert "zusätzlich neu (nicht aus der Delta-Datei): 2024-03-01 +0,01000000 BTC" in out
    assert main(args + ["--teilen"]) == 0
    shared = capsys.readouterr().out
    assert "+50.0 %" in shared and "0,01" not in shared and "2024-03-01" not in shared
