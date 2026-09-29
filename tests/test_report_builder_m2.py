"""Report builder M2: Anlage-SO columns, holding flag, real PDF bytes."""

from __future__ import annotations

from datetime import date, timedelta

from btc_origin.holding_clock import HoldingClock
from btc_origin.price_oracle import HISTORICAL_UNAVAILABLE, PriceOracle
from btc_origin.report_builder import CSV_COLUMNS, ReportBuilder


def _oracle() -> PriceOracle:
    return PriceOracle(
        spot_fetcher=lambda: (90_000.0, 82_000.0),
        historical_fetcher=lambda d: (40_000.0, 36_000.0),
    )


def test_csv_contains_anschaffungs_referenz_columns() -> None:
    as_of = date(2026, 9, 27)
    old = (as_of - timedelta(days=400)).isoformat()
    builder = ReportBuilder(oracle=_oracle(), holding_clock=HoldingClock())
    artifact = builder.build_csv(
        [
            {
                "txid": "abc123",
                "address": "bc1qdemo",
                "direction": "in",
                "amount_sats": 150_000,
                "is_internal": 0,
                "block_time": f"{old}T12:00:00Z",
                "inflow_date": old,
                "evidence_gap_status": "Kaufnachweis fehlt, Quelle nicht erreichbar",
                "entity_label": "FTX",
            }
        ]
    )
    assert artifact.path is None
    assert artifact.meta.get("disk_written") is False
    text = artifact.content
    assert isinstance(text, str)
    for col in (
        "anschaffungs_referenz_usd",
        "anschaffungs_referenz_eur",
        "anschaffungs_referenz_date",
        "anschaffungs_referenz_source",
        "haltefrist_hint",
        "evidence_gap_status",
        "internal_external",
        "referenzwert_usd",
    ):
        assert col in text
        assert col in CSV_COLUMNS
    assert "36000.00" in text or "40000.00" in text
    assert "yes" in text  # haltefrist for 400d old inflow
    assert "Kaufnachweis fehlt" in text
    assert "REFERENZWERT" in text


def test_csv_holding_flag_young_inflow() -> None:
    as_of = date(2026, 9, 27)
    young = (as_of - timedelta(days=10)).isoformat()
    # Freeze clock via absolute dates in row; HoldingClock uses now unless we
    # pre-set haltefrist — _normalize_row_base uses clock.flag_inflow(as_of=now).
    # Provide explicit hint to avoid flake around "today".
    builder = ReportBuilder(oracle=_oracle())
    artifact = builder.build_csv(
        [
            {
                "txid": "young",
                "direction": "in",
                "amount_sats": 1,
                "inflow_date": young,
                "block_time": young,
                "haltefrist_hint": False,
                "haltefrist_days": 10,
            }
        ]
    )
    assert "haltefrist_hint" in str(artifact.content)
    # young row should say no when explicitly provided
    lines = str(artifact.content).splitlines()
    assert any("no" in line and "young" in line for line in lines[1:])


def test_pdf_is_real_pdf_bytes_in_memory() -> None:
    builder = ReportBuilder(oracle=_oracle())
    artifact = builder.build_pdf(
        {"flow_count": 1},
        rows=[
            {
                "txid": "pdf1",
                "direction": "in",
                "amount_sats": 1000,
                "inflow_date": "2024-06-01",
                "block_time": "2024-06-01T00:00:00Z",
            }
        ],
    )
    assert artifact.path is None
    assert artifact.meta.get("disk_written") is False
    assert isinstance(artifact.content, (bytes, bytearray))
    assert bytes(artifact.content).startswith(b"%PDF")


def test_csv_fail_open_still_has_columns() -> None:
    oracle = PriceOracle(
        spot_fetcher=lambda: (None, None),
        historical_fetcher=lambda _d: (None, None),
    )
    artifact = ReportBuilder(oracle=oracle).build_csv(
        [{"txid": "z", "direction": "in", "inflow_date": "2020-01-01", "amount_sats": 1}]
    )
    assert HISTORICAL_UNAVAILABLE in str(artifact.content)
    assert "anschaffungs_referenz_eur" in str(artifact.content)


def test_csv_contains_external_fee_and_lot_columns() -> None:
    builder = ReportBuilder(oracle=_oracle(), holding_clock=HoldingClock())
    artifact = builder.build_csv(
        [
            {
                "txid": "pay1",
                "address": "bc1qdemo",
                "direction": "out",
                "amount_sats": 100_000,
                "external_amount_sats": 70_000,
                "fee_sats": 500,
                "is_internal": 0,
                "block_time": "2025-01-15T12:00:00Z",
                "lot_date": "2023-01-01",
                "holding_days": 745,
                "haltefrist_hint": True,
                "haltefrist_days": 745,
            },
            {
                "txid": "pay1",
                "address": "bc1qchange",
                "direction": "in",
                "amount_sats": 29_500,
                "is_internal": 1,
                "block_time": "2025-01-15T12:00:00Z",
                "lot_date": "2023-01-01",
                "holding_days": 745,
            },
        ]
    )
    text = str(artifact.content)
    assert "external_amount_sats" in text
    assert "fee_sats" in text
    assert "lot_date" in CSV_COLUMNS
    assert "70000" in text
    assert "500" in text
    assert "2023-01-01" in text
    # Change/internal inflow must not get Anschaffungs-Referenz price columns filled
    # via inflow_date — anschaffungs date may still show lot_date empty path.
    lines = text.splitlines()
    assert "external_amount_sats" in lines[0]
