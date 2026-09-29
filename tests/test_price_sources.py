"""Price sources: Binance daily EUR bulk, retry of failures, status (no network)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import btc_origin.price_oracle as po
from btc_origin.price_oracle import (
    HISTORICAL_UNAVAILABLE,
    PRICE_LOADING,
    PriceOracle,
    _binance_daily_eur,
    _nearest,
)


def _kline(day: date, close: float) -> list:
    open_ms = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    # Binance kline: [openTime, open, high, low, close, volume, closeTime, …]
    return [open_ms, "1", "1", "1", f"{close:.2f}", "0", open_ms + 86_399_999]  # privacy: ok (Tag in ms − 1)


def test_binance_daily_eur_pages_and_parses(monkeypatch) -> None:
    calls: list[str] = []
    days = [date(2021, 1, 1).fromordinal(date(2021, 1, 1).toordinal() + i) for i in range(1500)]

    def fake_get(path: str):
        calls.append(path)
        start_ms = int(path.split("startTime=")[1].split("&")[0])
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
        page = [d for d in days if d >= start][:1000]
        return [_kline(d, 30000 + i) for i, d in enumerate(page)]

    monkeypatch.setattr(po, "_binance_get", fake_get)
    series = _binance_daily_eur(days[0], days[-1])
    assert len(calls) == 2  # 1500 days → 2 requests à 1000
    assert len(series) == 1500
    assert _nearest(series, days[0]) == (None, 30000.0)
    assert _nearest(series, date(2019, 1, 1)) is None  # not covered → fallback


def test_bulk_fills_days_and_per_day_only_for_rest() -> None:
    per_day: list[date] = []

    def bulk(days):
        return [
            (datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp(), None, 20000.0)
            for d in days
            if d.year >= 2020
        ]

    def one(on):
        per_day.append(on)
        return 5000.0, 4000.0

    oracle = PriceOracle(historical_fetcher=one, bulk_fetcher=bulk, spot_fetcher=lambda: (None, None))
    wanted = [date(2019, 5, 1), date(2021, 3, 1), date(2022, 3, 1), date(2023, 3, 1)]
    oracle.prefetch_historical(wanted)
    assert per_day == [date(2019, 5, 1)]
    assert oracle.get_acquisition_reference(date(2022, 3, 1)).eur == 20000.0
    assert oracle.get_acquisition_reference(date(2019, 5, 1)).eur == 4000.0
    assert oracle.price_status()["days_loaded"] == 4


def test_failed_days_are_retried_not_cached_forever(monkeypatch) -> None:
    ok = {"now": False}

    def flaky(on):
        return (1.0, 2.0) if ok["now"] else (None, None)

    oracle = PriceOracle(historical_fetcher=flaky, spot_fetcher=lambda: (None, None))
    d = date(2024, 1, 1)
    oracle.prefetch_historical([d])
    with oracle.cached_only():
        assert oracle.get_acquisition_reference(d).message == HISTORICAL_UNAVAILABLE
    assert oracle.price_status()["days_failed"] == 1
    assert oracle.missing_days([d]) == []  # not retried immediately

    monkeypatch.setattr(po, "RETRY_FAILED_AFTER_S", 0.0)
    ok["now"] = True
    assert oracle.missing_days([d]) == [d]
    oracle.prefetch_historical([d])
    assert oracle.get_acquisition_reference(d).eur == 2.0
    assert oracle.price_status()["days_failed"] == 0


def test_cached_only_unknown_day_says_loading() -> None:
    oracle = PriceOracle(historical_fetcher=lambda on: (1.0, 1.0), spot_fetcher=lambda: (None, None))
    with oracle.cached_only():
        assert oracle.get_acquisition_reference(date(2024, 2, 2)).message == PRICE_LOADING


def test_spot_failure_not_cached() -> None:
    state = {"v": (None, None)}
    oracle = PriceOracle(historical_fetcher=lambda on: (None, None), spot_fetcher=lambda: state["v"])
    assert oracle.get_spot().available is False
    state["v"] = (None, 50000.0)
    po_retry = po.RETRY_FAILED_AFTER_S
    try:
        po.RETRY_FAILED_AFTER_S = 0.0
        assert oracle.get_spot().eur == 50000.0
    finally:
        po.RETRY_FAILED_AFTER_S = po_retry


def test_bulk_rule_binance_eur_then_usdt_ecb_then_mempool(monkeypatch) -> None:
    """Rz. 91: one documented rule — BTC/EUR, before 2020 BTC/USDT ÷ ECB, before 2017-08 mempool."""
    po.DAY_SOURCES.clear()

    def fake_get(path: str):
        symbol = path.split("symbol=")[1].split("&")[0]
        start_ms = int(path.split("startTime=")[1].split("&")[0])
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
        first = po.BINANCE_EUR_SINCE if symbol == "BTCEUR" else po.BINANCE_USDT_SINCE
        close = 30000.0 if symbol == "BTCEUR" else 8800.0
        d0 = max(start, first)
        return [_kline(date.fromordinal(d0.toordinal() + i), close) for i in range(5)]

    monkeypatch.setattr(po, "_binance_get", fake_get)
    # ECB: Friday 2019-06-07 published, weekend not → Saturday uses Friday.
    monkeypatch.setattr(po, "_ecb_usd_per_eur", lambda a, b: {date(2019, 6, 7): 1.10, date(2017, 8, 17): 1.17})
    monkeypatch.setattr(
        po, "_mempool_bulk_history_http",
        lambda: [(po._noon(date(2016, 1, 1)), 430.0, 400.0)],
    )
    days = [date(2021, 1, 1), date(2019, 6, 8), date(2016, 1, 1)]
    series = po._default_bulk_history(days)
    usd, eur = _nearest(series, date(2019, 6, 8))
    assert usd == 8800.0 and abs(eur - 8800.0 / 1.10) < 1e-6  # Saturday → Friday's ECB rate
    assert _nearest(series, date(2021, 1, 1)) == (None, 30000.0)
    assert _nearest(series, date(2016, 1, 1)) == (430.0, 400.0)
    src = po.price_sources_for(days)
    assert src[po.PRICE_SOURCE_BINANCE_USDT_ECB] == 1
    assert src[po.PRICE_SOURCE_MEMPOOL] == 1
    assert src[po.PRICE_SOURCE_BINANCE_EUR] == 1


def test_ecb_rate_weekend_uses_last_published() -> None:
    rates = {date(2019, 6, 7): 1.1}
    days = sorted(rates)
    assert po._fx_on(rates, days, date(2019, 6, 9)) == 1.1
    assert po._fx_on(rates, days, date(2019, 6, 30)) is None
    assert po._fx_on(rates, days, date(2019, 6, 1)) is None
