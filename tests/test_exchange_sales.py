"""Real sale date/proceeds from exchange exports (BMF 06.03.2025 Rz. 20, 55, 61)."""

from __future__ import annotations

from datetime import date

from btc_origin.exchange_sales import resolve_exchange_sales
from btc_origin.local_files import ExchangeTrade
from btc_origin.year_summary import yearly_summary

PRICES = {date(2023, 5, 6): 25_000.0, date(2021, 3, 5): 40_000.0, date(2024, 3, 5): 60_000.0,
          date(2023, 1, 14): 20_000.0, date(2023, 6, 5): 26_000.0}


def _deposit(sats: int = 10_000_000, fee: int = 10_000) -> list[dict]:
    return [
        {"direction": "out", "kind": "outflow", "txid": "dep", "time": "2023-05-06", "lot_date": "2021-03-05",
         "amount_sats": sats, "external_name": "Bison", "address": "bc1qexchange"},
        {"direction": "out", "kind": "fee", "txid": "dep", "time": "2023-05-06", "lot_date": "2021-03-05",
         "amount_sats": fee},
    ]


def test_deposit_sold_later_uses_export_date_and_proceeds() -> None:
    trades = [ExchangeTrade("Bison", date(2024, 3, 5), "sell", 4_000_000, eur=2_400.0, fee_eur=6.0)]
    rows = resolve_exchange_sales(_deposit(), trades, PRICES.get)
    sold = [r for r in rows if not r.get("not_disposal")]
    held = [r for r in rows if r.get("not_disposal")]
    assert [(r["time"], r["amount_sats"], r["proceeds_eur"]) for r in sold] == [("2024-03-05", 4_000_000, 2_400.0)]
    assert [(r["amount_sats"], "nicht verkauft" in r["status"]) for r in held] == [(6_000_000, True)]
    # deposit network fee split exactly across the pieces (Mengenabstimmung)
    assert sum(r["fee_sats"] for r in rows) == 10_000
    # Werbungskosten = exchange fee + deposit fee share at deposit-day price
    assert abs(sold[0]["fee_eur"] - (6.0 + 4_000 / 1e8 * 25_000)) < 1e-9
    years = {y.year: y.as_dict() for y in yearly_summary(rows, PRICES.get)}
    assert years[2023]["disposals"] == 0  # deposit year: nothing sold
    y = years[2024]
    assert y["disposals"] == 1
    assert y["long"]["proceeds_eur"] == 2_400.0  # held since 2021 → ≥ 1 Jahr
    assert y["long"]["cost_eur"] == 0.04 * 40_000


def test_fifo_on_exchange_prefers_older_coins_and_bought_coins_count() -> None:
    trades = [
        ExchangeTrade("Bison", date(2023, 1, 14), "buy", 1_000_000, eur=200.0, fee_eur=1.0),
        ExchangeTrade("Bison", date(2024, 3, 5), "sell", 10_500_000, eur=6_300.0),
    ]
    rows = resolve_exchange_sales(_deposit(), trades, PRICES.get)
    pieces = [(r["origin"], r["lot_date"], r["amount_sats"]) for r in rows]
    # 2021 wallet coins first (older), then the coins bought on Bison in 2023
    assert pieces == [("wallet", "2021-03-05", 10_000_000), ("exchange", "2023-01-14", 500_000)]
    # purchase price incl. fee per BTC: (200 + 1) / 0.01
    assert abs(rows[1]["acq_price_eur"] - 20_100.0) < 1e-6


def test_sell_beyond_known_coins_is_flagged_unknown() -> None:
    trades = [ExchangeTrade("Bison", date(2024, 3, 5), "sell", 12_000_000, eur=7_200.0)]
    rows = resolve_exchange_sales(_deposit(), trades, PRICES.get)
    unknown = [r for r in rows if r["origin"] == "unknown"]
    assert [(r["amount_sats"], r["lot_date"]) for r in unknown] == [(2_000_000, None)]
    y = {y.year: y for y in yearly_summary(rows, PRICES.get)}[2024]
    assert y.short.missing_price == 1  # no acquisition → no gain computed


def test_exchange_without_sells_keeps_assumption() -> None:
    trades = [ExchangeTrade("Relai", date(2023, 5, 6), "buy", 100_000, eur=25.0, destination=True)]
    rows = resolve_exchange_sales(_deposit(), trades, PRICES.get)
    assert [(r["time"], r["amount_sats"], r["fee_sats"]) for r in rows] == [("2023-05-06", 10_000_000, 10_000)]


def test_withdrawal_back_to_wallet_is_no_disposal() -> None:
    trades = [
        ExchangeTrade("Bison", date(2023, 6, 5), "withdraw", 10_000_000),
        ExchangeTrade("Bison", date(2024, 3, 5), "sell", 1, eur=1.0),
    ]
    rows = resolve_exchange_sales(_deposit(), trades, PRICES.get)
    back = [r for r in rows if r["origin"] == "wallet"]
    assert [r["not_disposal"] for r in back] == [True] and "wieder ausgezahlt" in back[0]["status"]


def test_withdrawal_gives_cloud_entry_the_purchase_dates() -> None:
    """Kauf-Seite (Rz. 20): coins bought on Bison on two days, withdrawn together →
    the Cloud-Eintritt gets both purchase dates and prices; fee excluded."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    trades = [
        ExchangeTrade("Bison", date(2023, 1, 14), "buy", 3_000_000, eur=600.0, fee_eur=3.0),
        ExchangeTrade("Bison", date(2023, 5, 6), "buy", 1_000_000, eur=250.0),
        ExchangeTrade("Bison", date(2023, 6, 5), "withdraw", 4_000_000),
    ]
    _rows, withdrawals, reports = replay_exchanges([], trades, PRICES.get)
    entry = {"direction": "in", "txid": "w1", "lot_id": "w1:bc1qown:0", "time": "2023-06-06",
             "amount_sats": 3_990_000, "source_name": "Bison", "address": "bc1qown"}
    other = {**entry, "txid": "w2", "lot_id": "w2:bc1qown:0", "time": "2023-06-24"}
    overrides = match_withdrawals([entry, other], withdrawals, reports)
    assert list(overrides) == ["w1:0"]
    pieces = overrides["w1:0"]
    assert [(p.acquisition_date, p.sats) for p in pieces] == [(date(2023, 1, 14), 3_000_000), (date(2023, 5, 6), 1_000_000)]
    assert abs(pieces[0].price_eur - 603.0 / 0.03) < 1e-6
    assert reports["Bison"].entries_total == 2 and len(reports["Bison"].matched) == 1


def test_lot_engine_splits_entry_by_purchase_pieces() -> None:
    from btc_origin.holding_clock import AcqPiece, HoldingClock
    from btc_origin.internal_transfer_tagger import InternalTransferTagger
    from btc_origin.tx_ingestor import Flow

    flows = [
        Flow("w1", "a1", "in", 3_990_000, wallet_id=1, block_time="2023-06-06T00:00:00Z", vout=0, tx_total_output_sats=3_990_000),
        Flow("sell", "a1", "out", 3_990_000, wallet_id=1, block_time="2024-03-05T00:00:00Z", vin_index=0,
             tx_total_output_sats=3_980_000, prev_txid="w1", prev_vout=0),
    ]
    InternalTransferTagger().tag_inplace(flows, {"a1"})
    clock = HoldingClock()
    clock.acquisition_overrides = {"w1:0": [
        AcqPiece(3_000_000, date(2023, 1, 14), 20_100.0, "Kauf auf Bison lt. Export"),
        AcqPiece(1_000_000, date(2023, 5, 6), 25_000.0, "Kauf auf Bison lt. Export"),
    ]}
    res = clock.apply_fifo_lots(flows, as_of="2026-09-27")
    assert sorted((a.acquisition_date, a.amount_sats) for a in res.acquisitions) == [
        (date(2023, 1, 14), 2_992_500), (date(2023, 5, 6), 997_500)]  # privacy: ok (3 Mio./1 Mio. − 0,25 %)
    outs = [c for c in res.consumptions if c.kind == "outflow"]
    # oldest purchase leaves first; 2023-01-14 → 2024-03-05 = held > 1 year
    assert outs[0].acquisition_date == date(2023, 1, 14) and outs[0].qualifies_haltefrist_hint
    assert outs[0].price_eur == 20_100.0


BITVAVO = """Timezone,Date,Time,Type,Currency,Amount,Quote Currency,Quote Price,Received / Paid Currency,Received / Paid Amount,Fee currency,Fee amount,Status,Transaction ID,Address
Europe/Amsterdam,2024-01-19,10:00:00,deposit,EUR,500,,,,,,,Completed,x1,
Europe/Amsterdam,2024-01-19,10:05:00,buy,BTC,0.01,EUR,42000,EUR,420.00,EUR,1.05,Completed,x2,
Europe/Amsterdam,2024-06-24,12:00:00,withdrawal,BTC,-0.01,,,,,BTC,0.0001,Completed,x4,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"""

STRIKE = """ReferenceId,Timestamp,TransactionType,FiatAmount,FiatFee,BitcoinAmount,BitcoinFee,BitcoinPrice,CostBasisUsd,Destination,Description,TransactionHash,Note
r1,Jan 19 2024 10:00:00,Purchase,-500.00,-2.50,0.01200000,,41500.00,,,,,
r3,Jun 24 2024 12:00:00,Withdrawal,,,-0.01200000,0.00010000,,,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu,,,"""


def test_bitvavo_and_strike_exports_parse() -> None:
    from btc_origin.local_files import parse_trades

    bv = parse_trades(BITVAVO, "Bitvavo")
    assert [(t.kind, t.sats, t.eur, t.fee_sats) for t in bv] == [
        ("buy", 1_000_000, 420.0, 0), ("withdraw", 1_000_000, None, 10_000)]  # EUR deposit skipped
    st = parse_trades(STRIKE, "Strike")
    assert [(t.kind, t.day.isoformat(), t.sats, t.eur, t.fee_sats) for t in st] == [
        ("buy", "2024-01-19", 1_200_000, 500.0, 0), ("withdraw", "2024-06-24", 1_200_000, None, 10_000)]


BITVAVO_OLD = """Timezone,Date,Time,Type,Currency,Amount,Price (EUR),EUR received / paid,Fee currency,Fee amount,Status,Transaction ID,Address
Europe/Amsterdam,2023-01-19,10:00:00,deposit,EUR,500,,,,,Completed,x1,
Europe/Amsterdam,2023-01-19,10:05:00,buy,BTC,0.01,42000,-420.00,EUR,1.05,Completed,x2,
Europe/Amsterdam,2023-03-01,10:05:00,deposit,BTC,0.02,,,,,Completed,x3,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu
Europe/Amsterdam,2023-04-01,10:05:00,sell,BTC,-0.02,30000,600.00,EUR,1.50,Completed,x5,
Europe/Amsterdam,2023-06-24,12:00:00,withdrawal,BTC,-0.01,,,BTC,0.0001,Completed,x4,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu
Europe/Amsterdam,2023-06-25,12:00:00,withdrawal,BTC,-0.01,,,BTC,0.0001,Canceled,x6,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"""


def test_older_bitvavo_export_parses_eur_and_skips_canceled() -> None:
    """Älterer Bitvavo-Export: Euro-Betrag in „EUR received / paid“ (Vorzeichen egal),
    abgebrochene Zeilen (Status „Canceled“) sind kein Vorgang."""
    from btc_origin.local_files import parse_trades

    bv = parse_trades(BITVAVO_OLD, "Bitvavo")
    assert [(t.kind, t.day.isoformat(), t.sats, t.eur, t.fee_eur, t.fee_sats) for t in bv] == [
        ("buy", "2023-01-19", 1_000_000, 420.0, 1.05, 0),
        ("deposit", "2023-03-01", 2_000_000, None, None, 0),
        ("sell", "2023-04-01", 2_000_000, 600.0, 1.5, 0),
        ("withdraw", "2023-06-24", 1_000_000, None, None, 10_000),
    ]
    assert bv[1].addresses and bv[3].addresses  # Einzahlungs-/Zieladresse für die Zuordnung


def test_bitvavo_cancelled_withdrawal_row_is_skipped() -> None:
    """„withdrawal_cancelled“ (Status Completed, Betrag positiv) steht für den abgebrochenen
    Versuch selbst: überspringen — die echte Auszahlung desselben Tages bleibt."""
    from btc_origin.local_files import parse_trades

    rows = BITVAVO.splitlines()[0] + """
Europe/Berlin,2025-12-27,11:05:00,withdrawal_cancelled,BTC,0.01,,,,,,,Completed,x8,
Europe/Berlin,2025-12-27,12:05:00,withdrawal,BTC,-0.01,,,,,BTC,0.0001,Completed,x9,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"""
    bv = parse_trades(rows, "Bitvavo")
    assert [(t.kind, t.day.isoformat(), t.sats) for t in bv] == [("withdraw", "2025-12-27", 1_000_000)]


def test_withdrawal_recon_is_exact_gross_and_net() -> None:
    """Käufe − Auszahlungsgebühr − Transaktionskosten = Eingang (both export conventions)."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    buy = ExchangeTrade("Bitvavo", date(2024, 1, 19), "buy", 1_000_000, eur=420.0)
    entry = {"direction": "in", "txid": "w", "lot_id": "w:bc1qown:0", "time": "2024-06-24",
             "source_name": "Bitvavo", "address": "bc1qown"}
    # gross: exported 0.01 incl. 0.0001 fee → 0.0099 arrives (and 1.000 sats network cost)
    wd = ExchangeTrade("Bitvavo", date(2024, 6, 24), "withdraw", 1_000_000, fee_sats=10_000)
    _r, ws, reps = replay_exchanges([], [buy, wd], PRICES.get)
    match_withdrawals([{**entry, "amount_sats": 989_000}], ws, reps)
    rc = reps["Bitvavo"].matched[0].recon()
    assert (rc["bought_sats"], rc["fee_sats"], rc["rest_sats"], rc["received_sats"], rc["ok"]) == (
        1_000_000, 10_000, 1_000, 989_000, True)
    # net: exported 0.005 arrives in full, fee charged on top
    wd2 = ExchangeTrade("Bitvavo", date(2024, 6, 24), "withdraw", 500_000, fee_sats=10_000)
    _r, ws, reps = replay_exchanges([], [buy, wd2], PRICES.get)
    match_withdrawals([{**entry, "amount_sats": 500_000}], ws, reps)
    w = reps["Bitvavo"].matched[0]
    assert w.net_fee and w.recon()["rest_sats"] == 0 and w.recon()["ok"]
    # second replay: the fee coins leave the pool too
    _r, ws2, _ = replay_exchanges([], [buy, wd2], PRICES.get, {("Bitvavo", date(2024, 6, 24), 500_000)})
    assert sum(p.sats for p in ws2[0].pieces) == 510_000
    w2 = ws2[0]
    w2.matched_entry = {"amount_sats": 500_000}
    assert w2.recon()["rest_sats"] == 0 and w2.recon()["fee_sats"] == 10_000


def test_receipt_beats_declared_name() -> None:
    """A rule-named sender (FTX³ by date) still matches a Bitvavo withdrawal."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    trades = [
        ExchangeTrade("Bitvavo", date(2022, 3, 5), "buy", 1_000_000, eur=400.0),
        ExchangeTrade("Bitvavo", date(2022, 3, 9), "withdraw", 1_000_000),
    ]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    entry = {"direction": "in", "txid": "w", "lot_id": "w:bc1qown:0", "time": "2022-03-09",
             "amount_sats": 1_000_000, "source_name": "FTX", "source_declared": True}
    assert list(match_withdrawals([entry], ws, reps)) == ["w:0"]
    # a sender with a real, different name is not taken
    named = {**entry, "source_name": "Kraken", "source_declared": False}
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    assert match_withdrawals([named], ws, reps) == {}


# BitGo „Go Account“: synthetische Zeilen im Format des Exports (USD).
BITGO = """WALLET,WALLET_LABEL,COIN,STATUS,CONFIRMED_DATE,CREATE_DATE,TXID,AMOUNT,FEE,TOTAL,BALANCE_IN_COIN,BALANCE_IN_USD,TO_ADDRESS,DESCRIPTION,COMMENT,USD_PRICE,USD_AMOUNT,FROM_ADDRESS,FEE_USD,TX_TYPE
w1,Go Account,USD,Confirmed,2025-04-18T10:00:00.000Z,2025-04-20T09:00:00.000Z,aaaa-ofcusd,1000.00,0,1000.00,1000.00,1000.00,Received,Received on w1: 1000.00,NULL,1,1000.00,bank1,0,Deposit
w1,Go Account,USD,Confirmed,2025-04-20T10:00:00.000Z,2025-04-20T10:01:00.000Z,bbbb,0,0,-1000.00,0,0,Fee,Sent,NULL,1,0,w1,NULL,Withdrawal
w1,Go Account,USD,Confirmed,2025-04-20T10:00:00.000Z,2025-04-20T10:01:00.000Z,bbbb,-1000.00,0,0,0,0,settle1,Transfer to settle1,NULL,1,-1000.00,Outgoing,0,Withdrawal
w1,Go Account,BTC,Confirmed,2025-04-20T12:00:00.000Z,2025-04-20T12:01:00.000Z,primesettlementbalance_x,0.01000000,0,0.01,0.01,985.00,Received,Received on w1: 0.01,NULL,98500,985.00,settle1,0,Deposit
w1,Go Account,BTC,Confirmed,2025-04-22T12:00:00.000Z,2025-04-22T11:00:00.000Z,""" + "c" * 64 + """,0,0,-0.01,0,0,Fee,Sent,NULL,98000,0,w1,NULL,Withdrawal
w1,Go Account,BTC,Confirmed,2025-04-22T12:00:00.000Z,2025-04-22T11:00:00.000Z,""" + "c" * 64 + """,-0.01000000,0.00001,0,0,0,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu,Transfer to x,NULL,98000,-980.00,Outgoing,0,Withdrawal
"""


def test_bitgo_settlement_buy_and_withdrawal() -> None:
    from btc_origin.local_files import _structured_rows, parse_trades

    trades = parse_trades(BITGO, "Bitgo")
    assert [(t.kind, t.day.isoformat(), t.sats, t.usd, t.fee_sats) for t in trades] == [
        ("buy", "2025-04-20", 1_000_000, 1000.0, 0),  # gezahlte USD, nicht der Marktwert 985
        ("withdraw", "2025-04-22", 1_000_000, None, 1_000),
    ]
    assert trades[1].addresses == ("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",)
    rows = _structured_rows(BITGO, "Bitgo")
    assert rows == [("Bitgo", [trades[1].day], [1_000_000], list(trades[1].addresses), "in")]


def test_usd_trades_converted_with_ecb_rate(monkeypatch) -> None:
    from btc_origin.api import core as app_mod
    from btc_origin.local_files import LocalData, parse_trades

    local = LocalData(trades=parse_trades(BITGO, "Bitgo"))
    monkeypatch.setattr(app_mod, "usd_per_eur_on", lambda days: {d: 1.25 for d in days})
    app_mod._convert_usd_trades(local)
    assert local.trades[0].eur == 800.0 and local.trades[1].eur is None


# 21bitcoin (Buy/Sell-Spaltenformat), synthetische Zeilen.
BUY_SELL = """id,exchange_name,depot_name,transaction_date,buy_asset,buy_amount,sell_asset,sell_amount,fee_asset,fee_amount,transaction_type,note,linked_transaction,btc_price
1,21bitcoin,Depot,2024-03-05 10:00:00,BTC,0.00200000,EUR,120.00,EUR,1.20,trade,,,60000
2,21bitcoin,Depot,2024-03-09 12:00:00,,,BTC,0.00200000,BTC,0.00001000,withdrawal,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu,,
3,21bitcoin,Depot,2024-04-05 09:00:00,EUR,70.00,BTC,0.00100000,EUR,0.70,trade,,,70000
4,21bitcoin,Depot,2024-04-06 09:00:00,BTC,0.00100000,,,,,deposit,,,
5,21bitcoin,Depot,2024-04-07 09:00:00,ETH,1.0,EUR,3000,EUR,3,trade,,,
"""


def test_buy_sell_format_21bitcoin() -> None:
    from btc_origin.local_files import _structured_rows, parse_trades

    trades = parse_trades(BUY_SELL, "21bitcoin")
    assert [(t.kind, t.day.isoformat(), t.sats, t.eur, t.fee_eur, t.fee_sats) for t in trades] == [
        ("buy", "2024-03-05", 200_000, 120.0, 1.2, 0),
        ("withdraw", "2024-03-09", 200_000, None, None, 1_000),
        ("sell", "2024-04-05", 100_000, 70.0, 0.7, 0),
        ("deposit", "2024-04-06", 100_000, None, None, 0),
    ]
    assert trades[1].addresses == ("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",)
    rows = _structured_rows(BUY_SELL, "21bitcoin")
    assert [(r[2], r[4]) for r in rows] == [([200_000], "in"), ([100_000], "out")]


# Coinbase-Transaktionsexport (synthetisch; Vorspann wie im Original).
COINBASE = """Transactions
User,Erika Muster,00000000-0000-0000-0000-000000000000
ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes,Sender Address,Recipient Address
a1,2025-01-14 09:00:00 UTC,Buy,BTC,0.01000000,EUR,€90000.00,€900.00,€914.90,€14.90,Bought 0.01 BTC for € 914.90 EUR,,
a2,2025-02-05 10:00:00 UTC,Advanced Trade Sell,BTC,-0.00200000,EUR,"€95,000.00",€190.00,€189.24,€0.76,,,
a3,2025-03-05 11:00:00 UTC,Convert,ETH,-1.00000000,EUR,€3000.00,€3000.00,€3000.00,€0.00,Converted 1 ETH to 0.03000000 BTC,,
a4,2025-11-17 12:00:00 UTC,Send,BTC,-0.02500000,EUR,€88000.00000000,-€2200.00000,-€2200.00000,€0.00,Sent 0.025 BTC to bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu,,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu
a5,2025-11-18 08:00:00 UTC,Receive,BTC,0.00500000,EUR,€85000.00,€425.00,€425.00,€0.00,,,
a6,2025-11-19 08:00:00 UTC,Buy,ETH,1.0,EUR,€3000.00,€3000.00,€3010.00,€10.00,,,
"""


def test_coinbase_export() -> None:
    from btc_origin.local_files import _structured_rows, parse_trades

    trades = parse_trades(COINBASE, "Coinbase")
    assert [(t.kind, t.day.isoformat(), t.sats, t.eur, t.fee_eur) for t in trades] == [
        ("buy", "2025-01-14", 1_000_000, 900.0, 14.9),
        ("sell", "2025-02-05", 200_000, 190.0, 0.76),
        ("buy", "2025-03-05", 3_000_000, 3000.0, None),
        ("withdraw", "2025-11-17", 2_500_000, None, None),
        ("deposit", "2025-11-18", 500_000, None, None),
    ]
    assert trades[3].addresses == ("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",)
    rows = _structured_rows(COINBASE, "Coinbase")
    assert [(r[2], r[4]) for r in rows] == [([2_500_000], "in"), ([500_000], "out")]


# Binance Order-Historie (synthetisch; Spaltennamen wie im Export).
BINANCE = """Time,OrderNo,Pair,Type¹,Side,Order Price,Order Amount,Time,Executed²,Average Price,Trading total³,Status
2024-01-09 10:00:00,1001,BTCEUR,MARKET,BUY,0,0.01BTC,2024-01-09 10:01:00,0.01000000BTC,40000,400.00EUR,FILLED
2024-02-09 10:00:00,1002,BTCUSDT,LIMIT,SELL,50000,0.002BTC,2024-02-10 09:00:00,0.00200000BTC,50000,100.00USDT,FILLED
2024-03-09 10:00:00,1003,BTCEUR,LIMIT,BUY,30000,0.01BTC,2024-03-09 10:00:00,0.00000000BTC,0,0.00EUR,CANCELED
2024-04-09 10:00:00,1004,ETHBTC,LIMIT,BUY,0.05,1ETH,2024-04-09 10:05:00,1.00000000ETH,0.05,0.05000000BTC,FILLED
"""


def test_binance_order_history() -> None:
    from btc_origin.local_files import parse_trades

    trades = parse_trades(BINANCE, "Binance")
    assert [(t.kind, t.day.isoformat(), t.sats, t.eur, t.usd) for t in trades] == [
        ("buy", "2024-01-09", 1_000_000, 400.0, None),
        ("sell", "2024-02-10", 200_000, None, 100.0),
        ("sell", "2024-04-09", 5_000_000, None, None),  # ETH mit BTC gekauft = BTC hinaus
    ]


def test_binance_orders_are_no_transfers() -> None:
    from btc_origin.local_files import _structured_rows

    assert _structured_rows(BINANCE, "Binance") == []


# Binance-Transaktionshistorie (Kontoauszug), synthetische Zeilen.
BINANCE_STATEMENT = """User ID,Time,Account,Operation,Coin,Change,Remark
1,2021-09-17 10:00:00,Spot,Buy Crypto With Card,BTC,0.00250000,Ref - X
1,2021-09-17 11:00:00,Spot,Withdraw,BTC,-0.00250000,Withdraw fee is included
1,2022-01-14 09:00:00,Spot,Transaction Buy,BTC,0.01000000,
1,2022-01-14 09:00:00,Spot,Transaction Spend,EUR,-400.00,
1,2022-01-14 09:00:00,Spot,Transaction Fee,EUR,-0.40,
1,2022-02-14 09:00:00,Spot,Transaction Sold,BTC,-0.00500000,
1,2022-02-14 09:00:00,Spot,Transaction Revenue,EUR,210.00,
1,2022-02-14 09:00:00,Spot,Transaction Fee,EUR,-0.21,
1,2022-03-05 00:00:00,Spot,Simple Earn Flexible Subscription,BTC,-0.00100000,
1,2022-03-06 00:00:00,Earn,Simple Earn Flexible Interest,BTC,0.00000100,
1,2022-04-05 12:00:00,Spot,Deposit,BTC,0.00300000,
"""


def test_binance_statement() -> None:
    from btc_origin.local_files import _structured_rows, parse_trades

    trades = parse_trades(BINANCE_STATEMENT, "Binance")
    assert [(t.kind, t.day.isoformat(), t.sats, t.eur, t.fee_eur) for t in trades] == [
        ("buy", "2021-09-17", 250_000, None, None),  # Kartenkauf: Betrag nicht im Auszug
        ("withdraw", "2021-09-17", 250_000, None, None),
        ("buy", "2022-01-14", 1_000_000, 400.0, 0.4),
        ("sell", "2022-02-14", 500_000, 210.0, 0.21),
        ("buy", "2022-03-06", 100, None, None),  # Zinsen
        ("deposit", "2022-04-05", 300_000, None, None),
    ]
    rows = _structured_rows(BINANCE_STATEMENT, "Binance")
    assert [(r[2], r[4]) for r in rows] == [([250_000], "in"), ([300_000], "out")]


def test_round_trip_via_exchange_without_sells_is_no_disposal() -> None:
    """Abfluss an Coinbase (Einzahlung im Export, kein Verkauf) → Kauf → Auszahlung zurück:
    Einzahlung = Umbuchung aufs eigene Börsenkonto (Rz. 54); die Auszahlung besteht aus den
    alten Teilbeständen (Anschaffungsdatum bleibt) + den neu gekauften (FiFo, Rz. 61)."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    PRICES.update({date(2025, 8, 30): 95_000.0, date(2021, 3, 5): 40_000.0})
    out = [
        {"direction": "out", "kind": "outflow", "txid": "dep", "time": "2025-08-30", "lot_date": "2021-03-05",
         "amount_sats": 1_000_000, "external_name": "Coinbase³", "address": "bc1qexchange"},
    ]
    trades = [
        ExchangeTrade("Coinbase", date(2025, 8, 30), "deposit", 1_000_000),
        ExchangeTrade("Coinbase", date(2025, 8, 30), "buy", 500_000, eur=475.0),
        ExchangeTrade("Coinbase", date(2025, 8, 31), "withdraw", 1_500_000),
    ]
    rows, ws, reps = replay_exchanges(out, trades, PRICES.get)
    assert [(r["amount_sats"], r["not_disposal"], "wieder ausgezahlt" in r["status"]) for r in rows] == [
        (1_000_000, True, True)
    ]
    assert reps["Coinbase"].deposits_matched == 1
    entry = {"direction": "in", "txid": "back", "lot_id": "back:bc1qown:0", "time": "2025-08-31",
             "amount_sats": 1_500_000, "source_name": "Coinbase", "address": "bc1qown"}
    overrides = match_withdrawals([entry], ws, reps)
    pieces = overrides["back:0"]
    assert [(p.acquisition_date, p.sats) for p in pieces] == [(date(2021, 3, 5), 1_000_000), (date(2025, 8, 30), 500_000)]
    assert pieces[0].price_eur == 40_000.0  # alter Teilbestand: Kurs am Anschaffungstag
    assert ws[0].recon()["ok"] and ws[0].recon()["rest_sats"] == 0
    years = {y.year: y.as_dict() for y in yearly_summary(rows, PRICES.get)}
    assert all(y["disposals"] == 0 for y in years.values())


def test_outflow_without_deposit_in_export_stays_assumed_sale() -> None:
    from btc_origin.exchange_sales import replay_exchanges

    out = [{"direction": "out", "kind": "outflow", "txid": "x", "time": "2025-08-30", "lot_date": "2021-03-05",
            "amount_sats": 1_000_000, "external_name": "Coinbase"}]
    trades = [ExchangeTrade("Coinbase", date(2025, 8, 30), "buy", 500_000, eur=475.0)]
    rows, _ws, reps = replay_exchanges(out, trades, PRICES.get)
    assert [r.get("not_disposal") for r in rows] == [None] and reps["Coinbase"].deposits_matched == 0


def test_buy_without_amount_uses_day_price_and_swap_is_marked() -> None:
    from btc_origin.exchange_sales import replay_exchanges, trade_ledger

    PRICES[date(2021, 9, 17)] = 38_000.0
    card = ExchangeTrade("Binance", date(2021, 9, 17), "buy", 200_000, quote="Karte")
    swap = ExchangeTrade("Binance", date(2021, 9, 17), "buy", 100_000, quote="ETH")
    wd = ExchangeTrade("Binance", date(2021, 9, 17), "withdraw", 300_000)
    _rows, ws, reps = replay_exchanges([], [card, swap, wd], PRICES.get)
    pieces = {p.sats: p for p in ws[0].pieces}
    assert pieces[200_000].price_eur == 38_000.0 and "Tageskurs" in pieces[200_000].source
    assert pieces[100_000].price_eur is None and "Tausch gegen ETH" in pieces[100_000].source
    notes = {r["sats"]: r["note"] for r in trade_ledger([card, swap], reps)}
    assert "Kartenkauf" in notes[200_000] and "Tageskurs" in notes[200_000]
    assert "Tausch gegen ETH (Rz. 54)" in notes[100_000]


def test_sale_of_coins_not_from_wallets_is_marked() -> None:
    from btc_origin.exchange_sales import replay_exchanges, trade_ledger

    trades = [
        ExchangeTrade("Binance", date(2022, 3, 23), "deposit", 400_000),
        ExchangeTrade("Binance", date(2022, 3, 23), "sell", 400_000, eur=15_000.0),
    ]
    _rows, _ws, reps = replay_exchanges([], trades, PRICES.get)
    assert reps["Binance"].deposits_export == 1 and reps["Binance"].deposits_matched == 0
    note = [r["note"] for r in trade_ledger(trades, reps) if r["kind"] == "Verkauf"][0]
    assert "stammen nicht aus den betrachteten Wallets" in note


def test_binance_statement_quote_of_card_and_swap() -> None:
    from btc_origin.local_files import parse_trades

    text = (
        "User_ID,UTC_Time,Account,Operation,Coin,Change,Remark\n"
        "1,2021-09-17 10:00:00,Spot,Buy Crypto With Card,BTC,0.002,\n"
        "1,2022-05-05 10:00:00,Spot,Transaction Buy,BTC,0.001,\n"
        "1,2022-05-05 10:00:00,Spot,Transaction Spend,ETH,-0.015,\n"
    ).replace("UTC_Time", "Time")
    trades = parse_trades(text, "Binance")
    assert [(t.kind, t.quote, t.eur) for t in trades] == [("buy", "Karte", None), ("buy", "ETH", None)]


def test_matching_diagnosis_names_best_candidate_and_reason() -> None:
    from btc_origin.exchange_sales import match_withdrawals, matching_diagnosis, replay_exchanges

    trades = [
        ExchangeTrade("Strike", date(2024, 5, 10), "buy", 1_000_000, eur=600.0),
        ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 1_000_000),
        ExchangeTrade("Relai", date(2022, 8, 9), "buy", 200_000, eur=100.0, destination=True),
    ]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    strike_in = {"direction": "in", "txid": "s1", "lot_id": "s1:a:0", "time": "2024-05-11",
                 "amount_sats": 800_000, "source_name": "Strike", "wallet_name": "Bitcoin 2024"}
    relai_in = {"direction": "in", "txid": "r1", "lot_id": "r1:a:0", "time": "2022-08-18",
                "amount_sats": 200_000, "source_name": "ext-012", "wallet_name": "Bitcoin 2022"}
    overrides = match_withdrawals([strike_in, relai_in], ws, reps)
    assert overrides == {}
    diag = matching_diagnosis([strike_in, relai_in], reps, ws, set(overrides))
    strike = next(d for d in diag if d["art"] == "Auszahlung ohne Zufluss" and d["boerse"] == "Strike")
    assert strike["abstand_tage"] == 1 and abs(strike["diff_btc"] - 0.002) < 1e-12
    assert "Mengendifferenz" in strike["grund"]
    relai = next(d for d in diag if d["art"] == "Zufluss ohne Kaufdaten" and d["zufluss_absender"] == "ext-012")
    assert relai["boerse"] == "Relai" and relai["abstand_tage"] == 9 and "9 Tage Abstand (erlaubt ±7)" in relai["grund"]


def test_matching_diagnose_endpoint_empty_session() -> None:
    from fastapi.testclient import TestClient

    from btc_origin.api.app import app

    with TestClient(app) as client:
        r = client.get("/api/report/matching-diagnose.md")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
        body = r.content.decode("utf-8")
        assert body.startswith("# Matching-Diagnose") and "## 2 Beinahe-Treffer" in body
        assert "attachment" in r.headers["content-disposition"]
        r = client.get("/api/report/matching-diagnose.md", params={"privacy": "true"})
        assert "Maskierte Fassung" in r.content.decode("utf-8") and "maskiert" in r.headers["content-disposition"]


def test_matching_diagnosis_masked_hides_amounts() -> None:
    from datetime import datetime as _dt

    from btc_origin.origin_report import HerkunftReport, render_matching_diagnosis

    rep = HerkunftReport(generated_at=_dt(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[], sources=[],
                         years=[], lots=[])
    diag = [{"art": "Auszahlung ohne Zufluss", "boerse": "Strike", "auszahlung_tag": "2024-05-10",
             "auszahlung_btc": 0.01, "zufluss_tag": "2024-05-11", "zufluss_btc": 0.008, "zufluss_wallet": "W",
             "zufluss_absender": "Strike", "abstand_tage": 1, "diff_btc": 0.002, "diff_prozent": 20.0,
             "grund": "Mengendifferenz 0.00200000 BTC über Toleranz 0.00100000"}]
    masked = render_matching_diagnosis(rep, diag, {}, privacy=True)
    assert "0,01000000" not in masked and "0.00200000" not in masked and "20,00" in masked and "•••" in masked
    assert "0,01000000" in render_matching_diagnosis(rep, diag, {})


def test_old_account_file_name_maps_to_exchange() -> None:
    from pathlib import Path

    from btc_origin.local_files import exchange_name_from_file

    assert exchange_name_from_file(Path("bitvavo-old.csv")) == "Bitvavo"
    assert exchange_name_from_file(Path("bitvavo_alt_2023.csv")) == "Bitvavo"


def test_diagnosis_compares_named_exchange_only_with_its_withdrawals() -> None:
    from btc_origin.exchange_sales import matching_diagnosis, replay_exchanges

    trades = [ExchangeTrade("Relai", date(2024, 3, 16), "buy", 100_000, eur=60.0, destination=True)]
    _r, ws, reps = replay_exchanges([], trades + [ExchangeTrade("Bitvavo", date(2025, 1, 1), "buy", 1, eur=1.0)],
                                    PRICES.get)
    entry = {"direction": "in", "txid": "b", "lot_id": "b:a:0", "time": "2024-02-19", "amount_sats": 500_000,
             "source_name": "Bitvavo"}
    d = matching_diagnosis([entry], reps, ws, set())
    assert d[0]["boerse"] == "" and "deckt der Export den Zeitraum ab" in d[0]["grund"]


def test_withdrawal_matches_entry_up_to_seven_days_later() -> None:
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    trades = [ExchangeTrade("Relai", date(2022, 8, 9), "buy", 200_000, eur=100.0, destination=True)]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    entry = {"direction": "in", "txid": "r1", "lot_id": "r1:a:0", "time": "2022-08-12",
             "amount_sats": 199_000, "source_name": "ext-012"}
    assert list(match_withdrawals([entry], ws, reps)) == ["r1:0"]
    late = {**entry, "time": "2022-08-17"}  # 8 Tage → keine Zuordnung
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    assert match_withdrawals([late], ws, reps) == {}


def test_open_exchange_items_with_explanations() -> None:
    from btc_origin.exchange_sales import match_withdrawals, open_exchange_items, replay_exchanges
    from btc_origin.local_files import parse_explanations

    trades = [
        ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 50_000),
        ExchangeTrade("Strike", date(2024, 5, 5), "deposit", 60_000),
        ExchangeTrade("Relai", date(2024, 4, 9), "buy", 70_000, eur=40.0, destination=True),
    ]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    match_withdrawals([], ws, reps)
    rules, errors = parse_explanations(
        "Börse;Datum;Art;Erläuterung\n"
        "Strike;;Auszahlung;Lightning-Test\n"
        "Strike;05.05.2024;Einzahlung;Lightning-Eingang\n"
        "Kraken;kein Datum;Kauf;x\n"
    )
    assert len(rules) == 2 and errors and "Datum" in errors[0]
    items = open_exchange_items(reps, rules)
    assert [(i["exchange"], i["kind"], i["note"]) for i in items] == [
        ("Relai", "Kauf mit Direktversand", "offen"),
        ("Strike", "Einzahlung", "Lightning-Eingang"),
        ("Strike", "Auszahlung", "Lightning-Test"),
    ]


def test_explanations_file_is_loaded(tmp_path) -> None:
    from btc_origin.local_files import load_local_data

    (tmp_path / "erlaeuterungen.csv").write_text("Bitvavo;;Auszahlung;altes Konto, Abrechnung angefordert\n",
                                                  encoding="utf-8")
    data = load_local_data(tmp_path)
    assert data.explanations == [("Bitvavo", None, "Auszahlung", "altes Konto, Abrechnung angefordert")]


def test_withdrawal_without_purchase_names_its_entry() -> None:
    """Strike-Auszahlung ohne Kauf im Export (per Lightning erhaltene BTC): passender
    Zufluss wird benannt, Kaufdaten gibt es nicht (Anschaffung = Zuflusstag)."""
    from btc_origin.exchange_sales import match_withdrawals, open_exchange_items, replay_exchanges, trade_ledger

    wd = ExchangeTrade("Strike", date(2024, 8, 9), "withdraw", 150_000)
    _r, ws, reps = replay_exchanges([], [wd], PRICES.get)
    entry = {"direction": "in", "txid": "s", "lot_id": "s:a:0", "time": "2024-08-09", "amount_sats": 150_000,
             "source_name": "Strike", "wallet_name": "Ledger 2024"}
    assert match_withdrawals([entry], ws, reps) == {}
    assert reps["Strike"].matched == [] and reps["Strike"].unmatched[0].matched_entry is entry
    item = open_exchange_items(reps)[0]
    assert item["status"].startswith("Zufluss 09.08.2024 in Ledger 2024; kein zuordenbarer Kauf im Export davor")
    note = trade_ledger([wd], reps)[0]["note"]
    assert "→ Zufluss 09.08.2024 in Ledger 2024" in note and "kein zuordenbarer Kauf" in note


def test_explanation_rule_distinguishes_withdrawals_with_and_without_entry() -> None:
    from btc_origin.exchange_sales import match_withdrawals, open_exchange_items, replay_exchanges

    trades = [ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 150_000),
              ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 40_000)]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    entry = {"direction": "in", "txid": "s", "lot_id": "s:a:0", "time": "2024-05-11", "amount_sats": 150_000,
             "source_name": "Strike", "wallet_name": "Ledger 2024"}
    match_withdrawals([entry], ws, reps)
    rules = [("Strike", date(2024, 5, 10), "Auszahlung ohne Zufluss", "Lightning-Test"),
             ("Strike", None, "Auszahlung mit Zufluss", "per Lightning erhaltene BTC")]
    notes = {i["sats"]: i["note"] for i in open_exchange_items(reps, rules)}
    assert notes == {40_000: "Lightning-Test", 150_000: "per Lightning erhaltene BTC"}
    both = [("Strike", None, "Auszahlung", "alle")]
    assert {i["note"] for i in open_exchange_items(reps, both)} == {"alle"}


def test_same_day_withdrawals_buys_go_to_the_one_with_entry() -> None:
    """Export ohne Uhrzeit: FiFo gab die Käufe einer Lightning-Auszahlung desselben Tages;
    die Auszahlung mit Zufluss bekommt sie."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    trades = [
        ExchangeTrade("Strike", date(2024, 5, 6), "buy", 100_000, eur=55.0),
        ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 100_000),  # Lightning, ohne Zufluss
        ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 100_000),  # on-chain, mit Zufluss
    ]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    assert [bool(w.pieces) for w in ws] == [True, False]
    entry = {"direction": "in", "txid": "s", "lot_id": "s:a:0", "time": "2024-05-11", "amount_sats": 100_000,
             "source_name": "Strike", "wallet_name": "Ledger 2024"}
    overrides = match_withdrawals([entry], ws, reps)
    assert [(p.acquisition_date, p.sats) for p in overrides["s:0"]] == [(date(2024, 5, 6), 100_000)]
    assert len(reps["Strike"].matched) == 1 and reps["Strike"].unmatched[0].pieces == []


def test_duplicate_export_row_is_flagged() -> None:
    from btc_origin.exchange_sales import match_withdrawals, open_exchange_items, replay_exchanges, trade_ledger

    buy = ExchangeTrade("Bitvavo", date(2025, 12, 24), "buy", 200_000, eur=150.0)
    w1 = ExchangeTrade("Bitvavo", date(2025, 12, 31), "withdraw", 200_000)
    w2 = ExchangeTrade("Bitvavo", date(2025, 12, 31), "withdraw", 200_000)
    _r, ws, reps = replay_exchanges([], [buy, w1, w2], PRICES.get)
    entry = {"direction": "in", "txid": "b", "lot_id": "b:a:0", "time": "2025-12-31", "amount_sats": 199_900,
             "source_name": "Bitvavo", "wallet_name": "BitBox"}
    match_withdrawals([entry], ws, reps)
    assert "vermutlich doppelte Zeile" in open_exchange_items(reps)[0]["status"]
    notes = [r["note"] for r in trade_ledger([w1, w2], reps)]
    assert notes[0].startswith("→ Zufluss 31.12.2025 in BitBox") and "vermutlich doppelt" in notes[1]


def test_small_deposit_needs_near_exact_amount() -> None:
    from btc_origin.exchange_sales import replay_exchanges

    dep = ExchangeTrade("Coinbase", date(2023, 4, 15), "deposit", 21_000)
    exact = [{"direction": "out", "kind": "outflow", "txid": "d", "time": "2023-04-15", "lot_date": "2021-03-05",
              "amount_sats": 21_000, "external_name": "Coinbase"}]
    _r, _w, reps = replay_exchanges(exact, [dep], PRICES.get)
    assert reps["Coinbase"].deposits_matched == 1
    other = [{**exact[0], "amount_sats": 75_000}]  # früher innerhalb 0,001 BTC Toleranz
    _r, _w, reps = replay_exchanges(other, [dep], PRICES.get)
    assert reps["Coinbase"].deposits_matched == 0


def test_satoshi_test_is_recognized() -> None:
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    def run(dep_day: date, dep_sats: int, wd_sats: int):
        out = [{"direction": "out", "kind": "outflow", "txid": "st", "time": dep_day.isoformat(),
                "lot_date": "2023-02-05", "amount_sats": dep_sats, "external_name": "Kraken"}]
        trades = [
            ExchangeTrade("Kraken", dep_day, "deposit", dep_sats),
            ExchangeTrade("Kraken", dep_day, "buy", wd_sats - dep_sats, eur=900.0),
            ExchangeTrade("Kraken", date.fromordinal(dep_day.toordinal() + 1), "withdraw", wd_sats),
        ]
        rows, ws, reps = replay_exchanges(out, trades, lambda d: 60_000.0)
        entry = {"direction": "in", "txid": "back", "lot_id": "back:a:0",
                 "time": date.fromordinal(dep_day.toordinal() + 1).isoformat(),
                 "amount_sats": wd_sats, "source_name": "Kraken", "wallet_name": "W"}
        overrides = match_withdrawals([entry], ws, reps)
        return rows, overrides, reps["Kraken"]

    rows, overrides, rep = run(date(2025, 3, 14), 12_000, 2_012_000)
    assert rep.satoshi_tests == 1 and rows[0]["not_disposal"]
    assert rows[0]["status"].startswith("Satoshi-Test (Nachweis der Wallet-Inhaberschaft")
    assert [p.source for p in overrides["back:0"]][0] == "Satoshi-Test, zurück ausgezahlt"
    # vor der EU-Geldtransferverordnung bzw. zu groß: normale Rückzahlung
    assert run(date(2024, 6, 14), 12_000, 2_012_000)[2].satoshi_tests == 0
    assert run(date(2025, 3, 14), 500_000, 2_500_000)[2].satoshi_tests == 0


def test_insufficient_same_day_buys_stay_with_their_withdrawal() -> None:
    """Reichen die Käufe nicht für die Auszahlung mit Zufluss, bleiben sie, wo FiFo sie
    hingab (vorher gingen sie dabei verloren)."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges

    trades = [
        ExchangeTrade("Strike", date(2024, 5, 6), "buy", 60_000, eur=33.0),
        ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 60_000),   # Lightning
        ExchangeTrade("Strike", date(2024, 5, 10), "withdraw", 100_000),  # on-chain
    ]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    entry = {"direction": "in", "txid": "s", "lot_id": "s:a:0", "time": "2024-05-11", "amount_sats": 100_000,
             "source_name": "Strike", "wallet_name": "Ledger 2024"}
    assert match_withdrawals([entry], ws, reps) == {}
    lightning, onchain = ws
    assert [p.sats for p in lightning.pieces] == [60_000] and onchain.pieces == []
    assert onchain.matched_entry is entry


def test_buy_sold_on_exchange_points_to_sale_and_estimated_proceeds() -> None:
    from btc_origin.exchange_sales import replay_exchanges, trade_ledger

    PRICES[date(2022, 3, 25)] = 37_000.0
    buy = ExchangeTrade("Binance", date(2022, 3, 25), "buy", 300_000, quote="Karte")
    sell = ExchangeTrade("Binance", date(2022, 3, 25), "sell", 300_000)  # ohne Erlös im Export
    rows, _ws, reps = replay_exchanges([], [buy, sell], PRICES.get)
    assert rows[0]["proceeds_estimated"] and rows[0]["proceeds_eur"] == 0.003 * 37_000
    note = next(r["note"] for r in trade_ledger([buy, sell], reps) if r["kind"] == "Kauf")
    assert "→ Verkauf 25.03.2022 (Abschnitt 3.1)" in note


def test_strike_reversal_cancels_failed_send() -> None:
    """Strike: fehlgeschlagene Sends werden mit „Reversal“ (positive Menge) zurückgebucht —
    beides zusammen ist kein Vorgang; nur der letzte Send ist die echte Auszahlung."""
    from btc_origin.local_files import parse_trades

    text = (
        "ReferenceId,Timestamp,TransactionType,FiatAmount,FiatFee,BitcoinAmount,BitcoinFee,BitcoinPrice,"
        "CostBasisUsd,Destination,Description,TransactionHash,Note\n"
        "a,2023-07-07T08:00:00+00:00,Purchase,-100.00,1.00,0.00300000,,33000.00,,,,,\n"
        "b,2023-07-07T08:05:00+00:00,Send,,,-0.00290000,0.00000700,,,dest,,h1,\n"
        "c,2023-07-07T08:10:00+00:00,Send,,,0.00290000,,,,dest,Reversal,h1,\n"
        "d,2023-07-07T08:15:00+00:00,Send,,,-0.00290000,0.00000600,,,dest,,h2,\n"
        "e,2023-07-07T08:20:00+00:00,Send,,,0.00290000,,,,dest,Reversal,h2,\n"
        "f,2023-07-07T08:25:00+00:00,Send,,,-0.00300000,,,,dest,,h3,\n"
    )
    trades = parse_trades(text, "Strike")
    assert [(t.kind, t.sats) for t in trades] == [("buy", 300_000), ("withdraw", 300_000)]


def test_destination_check_reports_wallet_and_inflows_without_address() -> None:
    from btc_origin.exchange_sales import destination_check, match_withdrawals, replay_exchanges

    buy = ExchangeTrade("Relai", date(2023, 4, 9), "buy", 100_000, eur=40.0, destination=True,
                        addresses=("bc1qownaddress",))
    other = ExchangeTrade("Relai", date(2023, 6, 11), "buy", 50_000, eur=20.0, destination=True,
                          addresses=("bc1qforeign",))
    _r, ws, reps = replay_exchanges([], [buy, other], PRICES.get)
    match_withdrawals([], ws, reps)
    rows = destination_check(reps, {"bc1qownaddress": "Ledger 2023"},
                             {"bc1qownaddress": [(date(2023, 4, 24), 99_000)]})
    by_day = {r["day"]: r["result"] for r in rows}
    assert by_day[date(2023, 4, 9)] == "Adresse gehört zu „Ledger 2023“; Zuflüsse dort: 24.04.2023 (+15 Tage, -1.00 %)"
    assert by_day[date(2023, 6, 11)].startswith("keine Adresse der betrachteten Wallets")
    assert all("bc1q" not in r["result"] for r in rows)


def test_withdrawal_larger_than_buys_keeps_rest_without_receipt() -> None:
    """Auszahlung größer als die Käufe davor (z. B. per Lightning erhaltene BTC): die
    Käufe werden nicht hochgerechnet, der Rest ist „ohne Kaufbeleg“ (Zuflusstag, Tageskurs)."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges
    from btc_origin.holding_clock import HoldingClock
    from btc_origin.internal_transfer_tagger import InternalTransferTagger
    from btc_origin.tx_ingestor import Flow

    buy = ExchangeTrade("Strike", date(2024, 3, 4), "buy", 100_000, eur=60.0)
    wd = ExchangeTrade("Strike", date(2024, 3, 4), "withdraw", 150_000)
    _r, ws, reps = replay_exchanges([], [buy, wd], PRICES.get)
    entry = {"direction": "in", "txid": "t1", "lot_id": "t1:bc1qown:0", "time": "2024-03-05",
             "amount_sats": 150_000, "source_name": "Strike", "address": "bc1qown"}
    overrides = match_withdrawals([entry], ws, reps)
    pieces = overrides["t1:0"]
    assert [(p.sats, p.acquisition_date, p.source) for p in pieces] == [
        (100_000, date(2024, 3, 4), "Kauf auf Strike lt. Export"),
        (50_000, date(2024, 3, 5), ""),  # ohne Kaufbeleg, Anschaffung = Zuflusstag
    ]
    assert pieces[1].price_eur is None  # Tageskurs
    assert reps["Strike"].matched[0].recon()["ok"]

    clock = HoldingClock()
    clock.acquisition_overrides = overrides
    flows = [Flow("t1", "bc1qown", "in", 150_000, wallet_id=1, block_time="2024-03-05T12:00:00Z",
                  vout=0, tx_total_output_sats=150_000),
             Flow("s1", "bc1qown", "out", 150_000, wallet_id=1, block_time="2025-06-02T12:00:00Z",
                  vin_index=0, tx_total_output_sats=149_000, prev_txid="t1", prev_vout=0)]
    InternalTransferTagger().tag_inplace(flows, {"bc1qown"})
    res = clock.apply_fifo_lots(flows, as_of="2026-09-27")
    assert sorted((a.amount_sats, a.acquisition_date, a.acq_source) for a in res.acquisitions) == [
        (50_000, date(2024, 3, 5), ""), (100_000, date(2024, 3, 4), "Kauf auf Strike lt. Export")]


def test_withdrawal_that_comes_back_as_deposit_is_a_return() -> None:
    """Auszahlung, die binnen eines Tages als Einzahlung (Menge − Gebühr) zurückkommt: Rücklauf.
    Beides zählt nicht; die spätere Auszahlung bekommt die Käufe."""
    from btc_origin.exchange_sales import match_withdrawals, replay_exchanges, trade_ledger

    buy = ExchangeTrade("Binance", date(2022, 3, 2), "buy", 200_000, eur=80.0)
    out1 = ExchangeTrade("Binance", date(2022, 3, 2), "withdraw", 200_000)
    back = ExchangeTrade("Binance", date(2022, 3, 2), "deposit", 199_000)
    out2 = ExchangeTrade("Binance", date(2022, 3, 2), "withdraw", 199_000)
    trades = [buy, out2, back, out1]  # Reihenfolge im Export egal
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    rep = reps["Binance"]
    assert [w.sats for w in ws] == [199_000] and rep.unmatched_deposits == []
    entry = {"direction": "in", "txid": "t1", "lot_id": "t1:bc1qown:0", "time": "2022-03-02",
             "amount_sats": 198_000, "source_name": "Binance", "address": "bc1qown"}
    overrides = match_withdrawals([entry], ws, reps)
    assert [(p.sats, p.acquisition_date) for p in overrides["t1:0"]] == [(199_000, date(2022, 3, 2))]
    assert rep.unmatched == [] and rep.matched[0].recon()["ok"]
    from datetime import datetime as _dt

    from btc_origin.origin_report import HerkunftReport, render_matching_diagnosis

    hr = HerkunftReport(generated_at=_dt(2026, 9, 28), stichtag=date(2026, 12, 31), wallets=[], sources=[],
                        years=[], lots=[])
    line = next(ln for ln in render_matching_diagnosis(hr, [], reps).splitlines() if ln.startswith("| Binance"))
    assert line.split(" | ")[1:4] == ["1", "0", "0"]  # Einzahlung: nicht aus Wallets, nicht unbekannt
    notes = {(r["kind"], r["sats"]): r["note"] for r in trade_ledger(trades, reps)}
    assert notes[("Auszahlung", 200_000)].startswith("Rücklauf: am 02.03.2022 als Einzahlung zurück")
    assert notes[("Einzahlung", 199_000)].startswith("Rücklauf einer Auszahlung")


def test_deposit_far_from_withdrawal_is_no_return() -> None:
    from btc_origin.exchange_sales import replay_exchanges

    trades = [ExchangeTrade("Binance", date(2022, 3, 2), "withdraw", 200_000),
              ExchangeTrade("Binance", date(2022, 3, 5), "deposit", 199_000),
              ExchangeTrade("Binance", date(2022, 3, 6), "deposit", 150_000)]
    _r, ws, reps = replay_exchanges([], trades, PRICES.get)
    assert len(ws) == 1 and len(reps["Binance"].unmatched_deposits) == 2


def test_explanation_placeholders_are_ignored() -> None:
    from btc_origin.local_files import parse_explanations

    rows, errors = parse_explanations("Börse;Datum;Art;Erläuterung\next-018;;Zufluss;<Herkunft eintragen>\n"
                                      "Kraken;;Zufluss;zweites Konto\n")
    assert rows == [("Kraken", None, "Zufluss", "zweites Konto")]
    assert errors == ["Zeile 2: Platzhalter „<…>“ nicht ausgefüllt — Zeile ignoriert"]
