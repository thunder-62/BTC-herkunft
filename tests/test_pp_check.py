from datetime import date

from btc_origin.pp_check import chain_movements, compare, parse_pp_export

PP_DE = """Datum;Uhrzeit;Typ;Wert;Buchungswährung;Gebühren;Steuern;Stück;ISIN;WKN;Ticker-Symbol;Wertpapiername;Notiz
2021-03-05;00:00;Kauf;-1.000,00;EUR;2,00;0,00;0,02;;;BTC-EUR;Bitcoin;Relai
2021-03-09;00:00;Einlieferung;500,00;EUR;0,00;0,00;0,01;;;BTC-EUR;Bitcoin;
2021-04-05;00:00;Umbuchung (Ausgang);500,00;EUR;0,00;0,00;0,01;;;BTC-EUR;Bitcoin;
2022-06-05;00:00;Verkauf;300,00;EUR;1,00;0,00;0,015;;;BTC-EUR;Bitcoin;
2022-06-05;00:00;Kauf;100,00;EUR;0,00;0,00;3;;;;Apple;
"""


def test_parse_pp_german():
    rows, errors = parse_pp_export(PP_DE)
    assert errors == []
    assert [(r.day, r.direction, r.sats) for r in rows] == [
        (date(2021, 3, 5), "in", 2_000_000),
        (date(2021, 3, 9), "in", 1_000_000),
        (date(2022, 6, 5), "out", 1_500_000),
    ]
    assert rows[0].eur == 1000.0 and rows[0].fee_eur == 2.0


def test_parse_pp_english_and_bad_header():
    text = "Date,Type,Shares,Value,Security Name\n2023-01-06,Delivery (Outbound),0.5,100,Bitcoin\n"
    rows, _ = parse_pp_export(text)
    assert rows[0].direction == "out" and rows[0].sats == 50_000_000
    rows, errors = parse_pp_export("a;b\n1;2\n")
    assert rows == [] and "Spalten nicht erkannt" in errors[0]


def _mv(txid, day, direction, sats, fee=0, cp=""):
    return {"txid": txid, "day": day, "direction": direction, "sats": sats,
            "fee_sats": fee, "wallet": "W", "counterparty": cp}


def test_chain_movements_per_tx():
    shaped = [
        {"direction": "out", "txid": "b", "time": "2022-06-05", "amount_sats": 1_000_000, "external_name": "Bison"},
        {"direction": "out", "txid": "b", "time": "2022-06-05", "amount_sats": 500_000},
        {"direction": "out", "txid": "b", "time": "2022-06-05", "amount_sats": 2_000, "kind": "fee"},
    ]
    assert [(c["sats"], c["fee_sats"], c["counterparty"]) for c in chain_movements(shaped)] == [
        (1_500_000, 2_000, "Bison")
    ]


def test_compare_ok_cases_are_not_listed():
    text = """Datum;Typ;Stück
2024-06-08;Einlieferung;0,01
2022-02-23;Auslieferung;0,01002
2025-08-05;Kauf;0,003
2025-08-14;Kauf;0,002
"""
    pp, _ = parse_pp_export(text)
    chain = [
        _mv("a", "2024-06-09", "in", 1_000_000),  # Datum +1 → ok
        _mv("b", "2022-02-23", "out", 1_000_000, fee=2_000),  # PP inkl. Gebühr → ok
        _mv("c", "2025-09-02", "in", 500_000, cp="Bitvavo"),  # 2 Käufe = 1 Auszahlung
    ]
    res = compare(pp, chain)
    assert res["actions"] == []
    assert res["ok"] == {"exact": 1, "date": 1, "grouped": 1, "fees": 0, "corrected": 0, "total": 3}
    assert res["bestand"]["delta_sats"] == 0


def test_compare_actions_with_effect():
    text = """Datum;Typ;Stück;Notiz
2024-02-12;Einlieferung;0,0101;
2022-03-12;Auslieferung;0,01;
2023-03-06;Einlieferung;0,02;BTC 2023
"""
    pp, _ = parse_pp_export(text)
    chain = [
        _mv("a", "2024-02-12", "in", 1_000_000, cp="Bitvavo"),  # PP +0,0001 zu viel
        _mv("b", "2022-03-12", "out", 1_000_000, fee=3_000),  # Gebühr fehlt in PP
        _mv("f1", "2021-09-17", "in", 500_000, cp="FTX"),
        _mv("f2", "2021-09-24", "out", 200_000, fee=1_000, cp="FTX"),
    ]
    res = compare(pp, chain, bestand_sats=296_000)
    by = {a["kind"] + a["day"]: a for a in res["actions"]}
    ftx = by["missing2021-09-17"]
    assert ftx["title"] == "FTX 2021: fehlt in PP" and ftx["effect_sats"] == 299_000
    assert ftx["suggest"]["text"] == "1 Einlieferung und 1 Auslieferung"
    assert by["amount2022-03-12"]["suggest"] == {"code": "out_fee", "sats": 3_000, "target_sats": 1_003_000}
    assert by["amount2024-02-12"]["suggest"]["code"] == "in_fee"
    assert by["amount2024-02-12"]["effect_sats"] == -10_000
    only = by["only_pp2023-03-06"]
    assert only["effect_sats"] == -2_000_000 and only["pp"][0]["note"] == "BTC 2023"
    b = res["bestand"]
    assert b["after_sats"] == b["chain_sats"] and b["rest_sats"] == 0
    assert [y["open"] for y in res["years"]] == [1, 1, 1, 1]


def test_tiny_pp_fee_row_is_not_matched_to_a_payout():
    pp, _ = parse_pp_export("Datum;Typ;Stück\n2025-10-24;Auslieferung;0,00001\n")
    res = compare(pp, [_mv("x", "2025-10-24", "out", 50_000, fee=1_000)])
    assert {a["kind"] for a in res["actions"]} == {"missing", "only_pp"}


PP_FULL = """Datum;Uhrzeit;Typ;Wert;Buchungswährung;Gebühren;Steuern;Stück;ISIN;Wertpapiername;Notiz
2024-02-12;10:00;Kauf;-500,00;EUR;1,00;0,00;0,0101;XF000BTC0017;Bitcoin;Bitvavo
2022-03-12;00:00;Auslieferung;400,00;EUR;0,00;0,00;0,01;XF000BTC0017;Bitcoin;
2023-03-06;00:00;Einlieferung;500,00;EUR;0,00;0,00;0,02;XF000BTC0017;Bitcoin;BTC 2023
2023-05-05;00:00;Umbuchung (Eingang);1,00;EUR;0,00;0,00;0,5;XF000BTC0017;Bitcoin;
2025-10-24;00:00;Auslieferung;1,00;EUR;0,00;0,00;0,00002;XF000BTC0017;Bitcoin;Netzwerkgebühren
"""


def test_separately_booked_fees_are_matched():
    from btc_origin.pp_check import internal_fees

    pp, _ = parse_pp_export(PP_FULL)
    raw = [
        {"direction": "out", "is_internal": 1, "fee_sats": 2_000, "txid": "i1", "block_time": "2025-10-25T08:00:00"},
        {"direction": "out", "is_internal": 1, "fee_sats": 2_000, "txid": "i1", "block_time": "2025-10-25T08:00:00"},
        {"direction": "out", "is_internal": 1, "fee_sats": 700, "txid": "i2", "block_time": "2021-01-06"},
    ]
    fees = internal_fees(raw)
    assert [(f["txid"], f["sats"]) for f in fees] == [("i2", 700), ("i1", 2_000)]
    res = compare(pp, [], fees=fees)
    assert res["ok"]["fees"] == 1  # Zeile 6 = Gebühr von i1
    fee_actions = [a for a in res["actions"] if a["kind"] == "fees"]
    assert len(fee_actions) == 1 and fee_actions[0]["effect_sats"] == -700


def test_build_import_csv_is_a_delta_only():
    """Nur das Delta: fehlende Bewegungen, Gebühren und Mengenkorrekturen als eigene
    Ein-/Auslieferungen; bestehende PP-Buchungen stehen nicht in der Datei (bleiben mit
    Verrechnungskonto in PP), PP-Zeilen ohne Gegenstück zählen als „von Hand löschen“."""
    import csv
    import io

    from btc_origin.pp_check import build_import_csv, parse_pp_file

    ppf = parse_pp_file(PP_FULL)
    assert ppf.skipped_transfers == 1
    chain = [
        _mv("a", "2024-02-12", "in", 1_000_000, cp="Bitvavo"),  # PP +0,0001 → Auszahlungsgebühr
        _mv("b", "2022-03-12", "out", 1_001_000, fee=3_000),  # PP zu wenig → Differenz buchen
        _mv("f1", "2021-09-17", "in", 500_000, cp="FTX"),
    ]
    res = compare(ppf.rows, chain)
    text, stats = build_import_csv(ppf, res, lambda d: 50_000.0)
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][0] == "Datum" and len(rows[0]) == 11
    body = rows[1:]
    assert len(body) == 3 and all(r[2] in ("Einlieferung", "Auslieferung") for r in body)
    assert [r[0] for r in body] == sorted(r[0] for r in body)
    ftx = next(r for r in body if "FTX" in r[10])
    assert ftx[2] == "Einlieferung" and ftx[7] == "0,00500000" and ftx[3] == "250,00"
    assert ftx[4] == "" and ftx[8] == "XF000BTC0017" and ftx[9] == "Bitcoin"  # keine Währung/Konto
    fee = next(r for r in body if "Auszahlungsgebühr zu Zeile 2" in r[10])
    assert fee[2] == "Auslieferung" and fee[7] == "0,00010000"
    fix = next(r for r in body if "Korrektur Stück zu Zeile 3" in r[10])
    assert fix[2] == "Auslieferung" and fix[7] == "0,00004000" and fix[0] == "2022-03-12"
    assert not any("Bitvavo" == r[10] or "BTC 2023" in r[10] for r in body)  # Bestehendes fehlt
    assert stats["added"] == 3 and stats["corrections"] == 1
    assert stats["to_delete"] == 2 and stats["delete_sats"] == 2_000_000 - 2_000
    # PP-Bestand + Delta − gelöschte Zeilen = Blockchain
    pp = sum(r.sats if r.direction == "in" else -r.sats for r in ppf.rows)
    assert pp + stats["delta_sats"] - stats["delete_sats"] == 1_000_000 - 1_004_000 + 500_000
    _, kept = build_import_csv(ppf, res, lambda d: 50_000.0, keep_lines=[4])
    assert kept["to_delete"] == 1 and kept["delete_sats"] == -2_000


def test_loose_match_books_the_difference_on_the_blockchain_day():
    import csv
    import io

    from btc_origin.pp_check import build_import_csv, parse_pp_file

    ppf = parse_pp_file(
        "Datum;Typ;Stück;Wert;Notiz\n2023-03-06;Einlieferung;0,02;500,00;BTC 2023\n"
    )
    chain = [_mv("x", "2023-02-07", "in", 1_500_000, cp="Binance-Wallet")]
    res = compare(ppf.rows, chain)
    (a,) = res["actions"]
    assert a["suggest"]["code"] == "loose" and a["effect_sats"] == -500_000
    text, stats = build_import_csv(ppf, res, lambda d: 10_000.0)
    (row,) = list(csv.reader(io.StringIO(text), delimiter=";"))[1:]
    assert row[:4] == ["2023-02-07", "Auslieferung", "0,00500000", "50,00"]
    assert "Korrektur Stück zu Zeile 2" in row[4]
    assert stats["delta_sats"] == -500_000 and stats["to_delete"] == 0


def test_import_csv_from_all_bookings_export_uses_pp_import_names():
    """Export „Alle Buchungen“ (Betrag/Gesamtpreis/Wertpapier/Konto): die Delta-Datei nennt
    die Spalten so, wie der PP-Import sie erwartet („Wert“, „Wertpapiername“); neue Zeilen
    haben Kurs, Betrag und Gesamtpreis passend und übernehmen kein Verrechnungskonto
    (sonst „Buchungswährung passt nicht zu Kontowährung“)."""
    import csv
    import io

    from btc_origin.pp_check import build_import_csv, parse_pp_file

    head = "Datum;Typ;Wertpapier;Stück;Kurs;Betrag;Gebühren;Steuern;Gesamtpreis;Konto;Gegenkonto;Notiz\n"
    ppf = parse_pp_file(
        head + "2024-02-12;Kauf;Bitcoin;0,01;50.000,00;-500,00;1,00;0,00;-501,00;Konto USD;;\n"
    )
    chain = [
        _mv("a", "2024-02-12", "in", 1_000_000, cp="Wallet A"),
        _mv("f1", "2024-05-01", "in", 2_000_000, cp="Wallet B"),
    ]
    text, stats = build_import_csv(ppf, compare(ppf.rows, chain), lambda d: 60_000.0)
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0] == ["Datum", "Typ", "Wertpapiername", "Stück", "Kurs", "Betrag", "Gebühren", "Steuern",
                       "Wert", "Konto", "Gegenkonto", "Notiz"]
    (new,) = rows[1:]  # nur die fehlende Einlieferung, der Kauf bleibt in PP
    assert new[:4] == ["2024-05-01", "Einlieferung", "Bitcoin", "0,02000000"]
    assert new[4] == "60000,00" and new[5] == "1200,00" and new[8] == "1200,00"  # positiv, nicht wie der Kauf
    assert new[9] == "" and new[10] == ""  # kein Konto
    assert stats["delta_sats"] == 2_000_000


def test_only_the_coin_itself_counts_not_bitcoin_funds():
    """Wertpapiere mit „Bitcoin“ im Namen (ETP, Fonds, Aktien) zählen nicht als BTC —
    ihre Stückzahl ist keine Bitcoin-Menge; Zeilen ohne Wertpapier (Konto) ebenfalls nicht."""
    from btc_origin.pp_check import is_bitcoin_security, parse_pp_file

    assert is_bitcoin_security("Bitcoin") and is_bitcoin_security("Bitcoin (BTC)")
    assert is_bitcoin_security("BTC-EUR")
    for name in ("WisdomTree Physical Bitcoin", "21Shares Bitcoin ETP", "iShares Bitcoin Trust ETF",
                 "Bitcoin Group SE AG", "Apple"):
        assert not is_bitcoin_security(name), name
    f = parse_pp_file(
        "Datum;Typ;Wertpapier;Stück;Betrag;Notiz\n"
        "2024-01-05;Kauf;Bitcoin;0,01;500,00;\n"
        "2024-01-06;Kauf;WisdomTree Physical Bitcoin;10;200,00;\n"
        "2024-01-07;Kauf;;5;100,00;Kontobuchung\n"
    )
    assert [r.sats for r in f.rows] == [1_000_000]
    assert f.counted == {"Bitcoin": 1} and f.ignored == {"WisdomTree Physical Bitcoin": 1}


def test_diagnose_shows_no_amounts_and_names_the_cause():
    """Diagnose zum Weitergeben: keine BTC-Mengen/Beträge/Daten, aber die Deutung —
    Abweichung ≈ Wirkung der letzten Delta-Datei → doppelt importiert/veralteter Export."""
    from btc_origin.pp_check import build_import_csv, parse_pp_file, pp_diagnose

    head = "Datum;Typ;Wertpapier;Stück;Betrag;Notiz\n"
    before = parse_pp_file(head + "2024-01-05;Einlieferung;Bitcoin;0,01;500,00;\n")
    chain = [_mv("a", "2024-01-05", "in", 1_000_000), _mv("b", "2024-03-01", "in", 1_000_000)]
    res = compare(before.rows, chain)
    _, stats = build_import_csv(before, res, lambda d: 50_000.0)
    # Delta zweimal importiert → PP zeigt 0,03 statt 0,02
    after = parse_pp_file(head + "2024-01-05;Einlieferung;Bitcoin;0,01;500,00;\n"
                          "2024-03-01;Einlieferung;Bitcoin;0,01;500,00;\n"
                          "2024-03-01;Einlieferung;Bitcoin;0,01;500,00;\n")
    text = pp_diagnose(after, compare(after.rows, chain), stats)
    assert "PP zu hoch (+50.0 % des Blockchain-Bestands)" in text
    assert "Datei doppelt importiert" in text
    assert "0,01" not in text and "2024-03-01" not in text and "500" not in text
    assert "Rechenprobe PP + Korrekturen = Blockchain: ja" in text


def test_delta_applied_once_is_recognized_and_not_repeated():
    """Nach dem Import der Delta-Datei erkennt der Abgleich die eigenen Korrekturzeilen
    („Korrektur Stück zu …“, „Auszahlungsgebühr zu …“) als Teil der Buchung: nichts mehr
    offen, und eine zweite Delta-Datei ist leer (kein doppeltes Korrigieren)."""
    import csv
    import io

    from btc_origin.pp_check import build_import_csv, parse_pp_file

    ppf = parse_pp_file(PP_FULL)
    chain = [
        _mv("a", "2024-02-12", "in", 1_000_000, cp="Bitvavo"),
        _mv("b", "2022-03-12", "out", 1_001_000, fee=3_000),
        _mv("f1", "2021-09-17", "in", 500_000, cp="FTX"),
    ]
    text, _ = build_import_csv(ppf, compare(ppf.rows, chain), lambda d: 50_000.0)
    # Delta in PP importiert, Zeilen ohne Gegenstück gelöscht, neu exportiert
    header, *delta = list(csv.reader(io.StringIO(text), delimiter=";"))
    kept = [ln for ln in PP_FULL.splitlines()[1:] if "BTC 2023" not in ln and "Netzwerkgebühren" not in ln]
    after = parse_pp_file(PP_FULL.splitlines()[0] + "\n" + "\n".join(kept + [";".join(r) for r in delta]) + "\n")
    res = compare(after.rows, chain)
    assert res["actions"] == [] and res["ok"]["corrected"] == 2
    assert res["bestand"]["pp_sats"] == res["bestand"]["chain_sats"]
    again, stats = build_import_csv(after, res, lambda d: 50_000.0)
    assert stats["added"] == 0 and stats["to_delete"] == 0
