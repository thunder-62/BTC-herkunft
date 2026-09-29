"""Price oracle: REFERENZWERT / Anschaffungs-Referenz labels + fail-open."""

from __future__ import annotations

from datetime import date

from btc_origin.price_oracle import (
    HISTORICAL_UNAVAILABLE,
    SPOT_UNAVAILABLE,
    PriceOracle,
)
from btc_origin.report_builder import ReportBuilder


def test_spot_success_labeled_referenzwert_not_kaufpreis() -> None:
    oracle = PriceOracle(spot_fetcher=lambda: (100_000.0, 92_000.0))
    spot = oracle.get_spot(use_cache=False)
    assert spot.available is True
    assert spot.label == "REFERENZWERT"
    fields = spot.as_report_fields()
    assert fields["referenzwert_label"] == "REFERENZWERT"
    assert "100000.00" in fields["referenzwert_usd"]
    assert "92000.00" in fields["referenzwert_eur"]
    note = fields["referenzwert_note"].lower()
    assert "kaufpreis" in note or "anschaffungs" in note
    assert "kein" in note
    # Must not use forbidden framing as the label itself
    assert "Kaufpreis" not in fields["referenzwert_label"]
    assert "Kostenbasis" not in fields["referenzwert_label"]


def test_spot_fail_open() -> None:
    oracle = PriceOracle(spot_fetcher=lambda: (None, None))
    spot = oracle.get_spot(use_cache=False)
    assert spot.available is False
    fields = spot.as_report_fields()
    assert fields["referenzwert_usd"] == SPOT_UNAVAILABLE
    assert fields["referenzwert_eur"] == SPOT_UNAVAILABLE


def test_historical_anschaffungs_referenz_and_fail_open() -> None:
    def hist(on: date):
        if on == date(2024, 1, 15):
            return (42_000.0, 38_000.0)
        return None, None

    oracle = PriceOracle(
        spot_fetcher=lambda: (1.0, 1.0),
        historical_fetcher=hist,
    )
    ok = oracle.get_acquisition_reference(date(2024, 1, 15))
    assert ok.available is True
    assert ok.label == "Anschaffungs-Referenz"
    f = ok.as_report_fields()
    assert "Kostenbasis" not in f["anschaffungs_referenz_label"]
    assert "42000.00" in f["anschaffungs_referenz_usd"]

    missing = oracle.get_acquisition_reference(date(2020, 1, 1))
    assert missing.available is False
    mf = missing.as_report_fields()
    assert mf["anschaffungs_referenz_usd"] == HISTORICAL_UNAVAILABLE
    assert mf["anschaffungs_referenz_eur"] == HISTORICAL_UNAVAILABLE


def test_in_memory_hist_cache() -> None:
    calls: list[date] = []

    def hist(on: date):
        calls.append(on)
        return 10.0, 9.0

    oracle = PriceOracle(historical_fetcher=hist)
    d = date(2023, 5, 1)
    oracle.get_acquisition_reference(d)
    oracle.get_acquisition_reference(d)
    assert calls == [d]  # second hit from RAM cache
    oracle.clear_cache()
    oracle.get_acquisition_reference(d)
    assert calls == [d, d]


def test_report_csv_in_memory_no_path_and_fail_open_spot() -> None:
    oracle = PriceOracle(
        spot_fetcher=lambda: (None, None),
        historical_fetcher=lambda _d: (None, None),
    )
    builder = ReportBuilder(oracle=oracle)
    artifact = builder.build_csv(
        [{"txid": "abc", "inflow_date": "2024-06-01"}],
        fetch_spot=True,
    )
    assert artifact.path is None
    assert artifact.meta.get("disk_written") is False
    assert isinstance(artifact.content, str)
    assert SPOT_UNAVAILABLE in artifact.content
    assert HISTORICAL_UNAVAILABLE in artifact.content
    assert "REFERENZWERT" in artifact.content
    assert "Anschaffungs-Referenz" in artifact.content or "anschaffungs_referenz" in artifact.content


def test_default_historical_prefers_mempool_over_rate_limited_coingecko(
    monkeypatch,
) -> None:
    """CoinGecko free tier often 429'd — mempool must supply EUR/USD."""
    import btc_origin.price_oracle as po

    calls: list[str] = []

    def mempool_ok(on: date):
        calls.append("mempool")
        return 96_000.0, 88_000.0

    def gecko_fail(on: date):
        calls.append("gecko")
        return None, None

    monkeypatch.setattr(po, "_mempool_historical_http", mempool_ok)
    monkeypatch.setattr(po, "_coingecko_historical_http", gecko_fail)
    usd, eur = po._default_historical_http(date(2025, 1, 15))
    assert eur == 88_000.0
    assert usd == 96_000.0
    assert calls == ["mempool"]  # short-circuit; no need for gecko


def test_default_historical_falls_back_to_coingecko(monkeypatch) -> None:
    import btc_origin.price_oracle as po

    monkeypatch.setattr(po, "_mempool_historical_http", lambda on: (None, None))
    monkeypatch.setattr(
        po, "_coingecko_historical_http", lambda on: (42_000.0, 39_000.0)
    )
    usd, eur = po._default_historical_http(date(2024, 6, 1))
    assert usd == 42_000.0 and eur == 39_000.0


def test_historical_unavailable_marker_is_clear_german() -> None:
    assert "Kurs nicht ermittelbar" == HISTORICAL_UNAVAILABLE
    oracle = PriceOracle(historical_fetcher=lambda _d: (None, None))
    ref = oracle.get_acquisition_reference(date(2020, 1, 1))
    assert ref.message == HISTORICAL_UNAVAILABLE
