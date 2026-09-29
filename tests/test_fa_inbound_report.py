"""FA-Inbound PDF: two tables vs Stichtag + German columns."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.fa_inbound_report import (
    COL_ANZAHL,
    COL_EINTRITT,
    COL_WALLET,
    COL_WERT,
    EMPTY_TABLE_NOTE,
    HALTEFRIST_CHECK,
    TABLE_HALTEFRIST,
    TABLE_NACHWEIS,
    build_fa_inbound_pdf,
    build_fa_inbound_payload,
    build_fa_inbound_rows,
    default_stichtag,
    format_eur_value,
    mask_xpub,
    parse_stichtag,
    sats_to_btc_str,
)
from btc_origin.holding_clock import HoldingClock, Lot, LotMove, lot_to_genealogy_dict
from btc_origin.price_oracle import HISTORICAL_UNAVAILABLE, PriceOracle
from btc_origin.wallet_registry import WalletEntry


AS_OF = date(2026, 9, 27)
STICHTAG = date(2026, 12, 31)


def _oracle_ok() -> PriceOracle:
    return PriceOracle(
        spot_fetcher=lambda: (90_000.0, 82_000.0),
        historical_fetcher=lambda _d: (40_000.0, 36_000.0),
    )


def _oracle_fail() -> PriceOracle:
    return PriceOracle(
        spot_fetcher=lambda: (None, None),
        historical_fetcher=lambda _d: (None, None),
    )


def _lot(
    *,
    lot_date: date,
    remaining: int,
    address: str,
    path_addr: str | None = None,
    lot_id: str = "lot1",
    wallet_id: int = 1,
) -> Lot:
    path = [
        LotMove(wallet_id=1, txid="in1", date=lot_date, kind="inflow", address=address),
    ]
    if path_addr and path_addr != address:
        path.append(
            LotMove(
                wallet_id=2,
                txid="mv1",
                date=lot_date + timedelta(days=10),
                kind="transfer",
                address=path_addr,
            )
        )
        cur = path_addr
        wid = 2
    else:
        cur = address
        wid = wallet_id
    return Lot(
        lot_id=lot_id,
        wallet_id=wid,
        acquisition_date=lot_date,
        amount_sats=remaining,
        remaining_sats=remaining,
        txid="in1",
        address=cur,
        origin_wallet_id=1,
        origin_address=address,
        original_amount_sats=remaining,
        path=path,
    )


def test_mask_xpub_fingerprint() -> None:
    xp = "xpub" + ("A" * 100)
    m = mask_xpub(xp)
    assert m.startswith("xpubAAAA")
    assert "…" in m
    assert m.endswith(xp[-6:])


def test_default_stichtag_is_dec_31_current_year() -> None:
    assert default_stichtag(date(2026, 9, 27)) == date(2026, 12, 31)
    assert parse_stichtag(None) == default_stichtag()
    assert parse_stichtag("2025-06-15") == date(2025, 6, 15)


def test_sats_and_eur_value_helpers() -> None:
    assert sats_to_btc_str(100_000_000) == "1.00000000"
    assert sats_to_btc_str(10_000) == "0.00010000"
    # 10000 sats * 36000 EUR/BTC = 3.60 EUR
    assert format_eur_value(36_000.0, 10_000) == "3.60"


def test_lot_serialization_includes_current_address() -> None:
    lot = _lot(
        lot_date=AS_OF - timedelta(days=400),
        remaining=50_000,
        address="bc1qoriginaddr0001",
        path_addr="bc1qcurrentdest999",
    )
    payload = lot_to_genealogy_dict(lot, as_of=AS_OF, wallet_names={1: "A", 2: "B"})
    assert payload["current_address"] == "bc1qcurrentdest999"
    assert payload["current_wallet_name"] == "B"
    assert payload["qualifies_haltefrist"] is True


def test_rows_split_haltefrist_vs_nachweis_and_columns() -> None:
    old = STICHTAG - timedelta(days=400)
    young = STICHTAG - timedelta(days=30)
    lots = [
        _lot(lot_date=old, remaining=10_000, address="bc1qoldaddrxxxx", lot_id="old"),
        _lot(
            lot_date=young,
            remaining=20_000,
            address="bc1qyoungaddrxx",
            path_addr="bc1qyoungdest01",
            lot_id="young",
        ),
    ]
    names = {1: "Cold-Demo", 2: "Hot-Demo"}
    rows = build_fa_inbound_rows(
        lots,
        _oracle_ok(),
        stichtag=STICHTAG,
        holding_clock=HoldingClock(),
        wallet_names=names,
    )
    assert len(rows) == 2
    by_id = {r.lot_id: r for r in rows}

    old_row = by_id["old"]
    assert old_row.eintrittszeitpunkt == old.isoformat()
    assert old_row.anzahl_btc == "0.00010000"
    assert old_row.wert_zu_der_zeit == "3.60"  # 10000 sats * 36000
    assert old_row.wallet_aktuell == "Cold-Demo"
    assert old_row.qualifies_haltefrist is True
    assert old_row.haltefrist_mark == HALTEFRIST_CHECK
    assert old_row.price_missing is False

    young_row = by_id["young"]
    assert young_row.eintrittszeitpunkt == young.isoformat()
    assert young_row.wallet_aktuell == "Hot-Demo"
    assert young_row.wallet_address == "bc1qyoungdest01"
    assert young_row.qualifies_haltefrist is False
    assert young_row.haltefrist_mark == ""

    payload = build_fa_inbound_payload(
        lots=lots,
        wallets=[],
        oracle=_oracle_ok(),
        stichtag=STICHTAG,
        holding_clock=HoldingClock(),
        wallet_names=names,
    )
    assert payload.stichtag == STICHTAG.isoformat()
    assert len(payload.rows_haltefrist) == 1
    assert payload.rows_haltefrist[0].lot_id == "old"
    assert len(payload.rows_nachweis) == 1
    assert payload.rows_nachweis[0].lot_id == "young"
    d = payload.as_dict()
    assert d["columns"] == [COL_EINTRITT, COL_ANZAHL, COL_WERT, COL_WALLET]
    assert d["table_haltefrist"] == TABLE_HALTEFRIST
    assert d["table_nachweis"] == TABLE_NACHWEIS


def test_haltefrist_evaluated_against_stichtag_not_today() -> None:
    """Lot that is young today can still qualify if Stichtag is far enough ahead."""
    # Acquired 200 days before AS_OF → not yet 365 as of AS_OF
    acq = AS_OF - timedelta(days=200)
    lot = _lot(lot_date=acq, remaining=1_000, address="bc1qmid", lot_id="mid")
    # Against AS_OF: still within Haltefrist
    rows_today = build_fa_inbound_rows([lot], _oracle_ok(), stichtag=AS_OF)
    assert rows_today[0].qualifies_haltefrist is False
    # Against STICHTAG (Dec 31): 200 + (Dec31-Sep27)=200+95=295 — still < 365
    rows_year = build_fa_inbound_rows([lot], _oracle_ok(), stichtag=STICHTAG)
    assert rows_year[0].days_held == (STICHTAG - acq).days
    assert rows_year[0].qualifies_haltefrist is ((STICHTAG - acq).days >= 365)

    # Far-future Stichtag → qualifies
    far = acq + timedelta(days=400)
    rows_far = build_fa_inbound_rows([lot], _oracle_ok(), stichtag=far)
    assert rows_far[0].qualifies_haltefrist is True


def test_fail_open_price_missing_note() -> None:
    old = STICHTAG - timedelta(days=400)
    lots = [_lot(lot_date=old, remaining=1, address="bc1qnoprice0001")]
    rows = build_fa_inbound_rows(lots, _oracle_fail(), stichtag=STICHTAG)
    assert len(rows) == 1
    assert rows[0].wert_zu_der_zeit == "Kurs nicht ermittelbar"
    assert rows[0].price_missing is True
    assert "Kurs nicht ermittelbar" in rows[0].note
    assert rows[0].btc_preis_eur == HISTORICAL_UNAVAILABLE


def test_zero_remaining_excluded() -> None:
    old = STICHTAG - timedelta(days=400)
    lots = [_lot(lot_date=old, remaining=0, address="bc1qzero")]
    rows = build_fa_inbound_rows(lots, _oracle_ok(), stichtag=STICHTAG)
    assert rows == []


def test_pdf_bytes_two_tables_and_wallet_name() -> None:
    old = STICHTAG - timedelta(days=500)
    young = STICHTAG - timedelta(days=10)
    lots = [
        _lot(
            lot_date=old,
            remaining=12_345,
            address="bc1qorigin",
            path_addr="bc1qdestaddrFAKEEXAMPLE01",
            lot_id="old",
        ),
        _lot(lot_date=young, remaining=50_000, address="bc1qyoung", lot_id="young"),
    ]
    wallets = [
        WalletEntry(
            name="Cold-Demo",
            kind="xpub",
            xpub="xpub" + ("B" * 80),
            id=1,
        ),
        WalletEntry(
            name="Hot-Demo",
            kind="address",
            address="bc1qyoung",
            id=2,
        ),
    ]
    artifact = build_fa_inbound_pdf(
        lots=lots,
        wallets=wallets,
        oracle=_oracle_ok(),
        stichtag=STICHTAG,
        holding_clock=HoldingClock(),
        wallet_names={1: "Cold-Demo", 2: "Hot-Demo"},
    )
    assert artifact.path is None
    assert artifact.meta.get("disk_written") is False
    assert artifact.meta.get("kind") == "fa-inbound"
    assert artifact.meta.get("stichtag") == STICHTAG.isoformat()
    data = bytes(artifact.content or b"")
    assert data.startswith(b"%PDF")
    payload = artifact.meta["payload"]
    assert payload["stichtag"] == STICHTAG.isoformat()
    assert len(payload["rows_haltefrist"]) == 1
    assert len(payload["rows_nachweis"]) == 1
    old_row = payload["rows_haltefrist"][0]
    assert old_row["eintrittszeitpunkt"] == old.isoformat()
    assert old_row["anzahl_btc"] == sats_to_btc_str(12_345)
    assert old_row["wallet_aktuell"] == "Hot-Demo"  # transferred to wallet 2
    assert payload["columns"] == [COL_EINTRITT, COL_ANZAHL, COL_WERT, COL_WALLET]
    assert any("Cold-Demo" in ln for ln in payload["cloud_lines"])
    assert "fallback" not in artifact.message


def test_empty_session_pdf_valid_with_disclaimer() -> None:
    artifact = build_fa_inbound_pdf(
        lots=[],
        wallets=[],
        oracle=_oracle_ok(),
        stichtag=STICHTAG,
    )
    data = bytes(artifact.content or b"")
    assert data.startswith(b"%PDF")
    assert artifact.meta.get("disk_written") is False
    assert artifact.meta["payload"]["cloud_lines"] == []
    assert artifact.meta["payload"]["rows_haltefrist"] == []
    assert artifact.meta["payload"]["rows_nachweis"] == []
    assert artifact.meta["payload"]["rows"] == []
    assert "Keine Steuerberatung" in artifact.message or "Keine Steuerberatung" in str(
        artifact.meta
    )
    # Empty-table note present in meta columns / legend path
    assert EMPTY_TABLE_NOTE == "keine Positionen"


def test_api_fa_inbound_empty_session() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/report/pdf/fa-inbound",
            json={"stichtag": STICHTAG.isoformat()},
        )
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.headers.get("X-BTC-Herkunft-Disk-Written") == "false"
        assert r.headers.get("X-BTC-Herkunft-Report-Kind") == "fa-inbound"
        assert r.headers.get("X-BTC-Herkunft-Stichtag") == STICHTAG.isoformat()
        assert r.content.startswith(b"%PDF")


def test_api_fa_inbound_rejects_bad_stichtag() -> None:
    with TestClient(app) as client:
        r = client.post("/api/report/pdf/fa-inbound", json={"stichtag": "nicht-datum"})
        assert r.status_code == 400


def test_api_fa_inbound_defaults_stichtag_when_omitted() -> None:
    with TestClient(app) as client:
        r = client.post("/api/report/pdf/fa-inbound", json={})
        assert r.status_code == 200
        assert r.headers.get("X-BTC-Herkunft-Stichtag") == default_stichtag().isoformat()


def test_api_fa_inbound_with_mocked_session_lot() -> None:
    """Register address wallet + persist one external inflow → PDF has wallet/date."""
    from btc_origin.api.app import _session_lots_for_fa
    from btc_origin.db import persist_ledger
    from btc_origin.merger import Ledger
    from btc_origin.tx_ingestor import Flow

    buy = (STICHTAG - timedelta(days=400)).isoformat()
    with TestClient(app) as client:
        state = app.state.session
        state.oracle = _oracle_ok()
        state.registry.register_address("Sitzung-Wallet", "bc1qsessiondest01")
        flows = [
            Flow(
                "fa_buy_txid",
                "bc1qsessiondest01",
                "in",
                77_000,
                wallet_id=1,
                block_time=f"{buy}T12:00:00Z",
                vout=0,
            )
        ]
        persist_ledger(state.db, Ledger(flows=flows))

        r = client.post(
            "/api/report/pdf/fa-inbound",
            json={"stichtag": STICHTAG.isoformat()},
        )
        assert r.status_code == 200
        assert r.content.startswith(b"%PDF")
        assert r.headers.get("X-BTC-Herkunft-Stichtag") == STICHTAG.isoformat()

        lots, by_id = _session_lots_for_fa(state, as_of=STICHTAG)
        assert lots
        assert lots[0]["current_address"] == "bc1qsessiondest01"
        assert lots[0]["lot_date"] == buy
        assert lots[0]["qualifies_haltefrist"] is True
        art = build_fa_inbound_pdf(
            lots=lots,
            wallets=state.registry.list_wallets(),
            oracle=state.oracle,
            stichtag=STICHTAG,
            holding_clock=state.clock,
            wallet_names=by_id,
        )
        assert len(art.meta["payload"]["rows_haltefrist"]) == 1
        row = art.meta["payload"]["rows_haltefrist"][0]
        assert row["eintrittszeitpunkt"] == buy
        assert row["wallet_aktuell"] == "Sitzung-Wallet"
        assert row["anzahl_btc"] == sats_to_btc_str(77_000)
        # 77000 sats * 36000 EUR = 27.72
        assert row["wert_zu_der_zeit"] == "27.72"


def test_eintritt_prefers_path0_inflow_date_matches_acquisition() -> None:
    """FA eintrittszeitpunkt == path[0].date when kind=inflow (== acquisition_date)."""
    from btc_origin.fa_inbound_report import cloud_entry_date

    acq = date(2024, 3, 1)
    lot = _lot(
        lot_date=acq,
        remaining=12_000,
        address="bc1qorigincloud01",
        path_addr="bc1qcurrentcloud99",
        lot_id="cloud1",
    )
    payload = lot_to_genealogy_dict(lot, as_of=STICHTAG, wallet_names={1: "A", 2: "B"})
    assert payload["path"][0]["kind"] == "inflow"
    assert payload["path"][0]["date"] == acq.isoformat()
    assert payload["lot_date"] == acq.isoformat()
    assert cloud_entry_date(payload) == acq
    assert payload["path"][1]["kind"] == "transfer"
    assert payload["path"][1]["date"] != acq.isoformat()
    rows = build_fa_inbound_rows(
        [lot], _oracle_ok(), stichtag=STICHTAG, wallet_names={1: "A", 2: "B"}
    )
    assert rows[0].eintrittszeitpunkt == acq.isoformat()
    assert rows[0].wallet_aktuell == "B"


def test_fa_privacy_masks_names_amounts_and_no_bc1_address() -> None:
    """Privacy PDF/payload: no bc1/1/3/tb1 prefixes; names+amounts masked."""
    from btc_origin.fa_inbound_report import MASK_ADDR_PDF, MASK_BTC_PDF, MASK_NAME_PDF

    old = STICHTAG - timedelta(days=400)
    lots = [
        _lot(
            lot_date=old,
            remaining=99_000,
            address="bc1qsecretorigin0001",
            path_addr="bc1qsecretdest999999",
            lot_id="priv1",
        )
    ]
    wallets = [
        WalletEntry(
            name="Ledger-Geheim",
            kind="xpub",
            xpub="xpub" + ("Z" * 80),
            id=1,
        ),
        WalletEntry(
            name="Bitbox-Geheim",
            kind="address",
            address="bc1qsecretdest999999",
            id=2,
        ),
    ]
    art = build_fa_inbound_pdf(
        lots=lots,
        wallets=wallets,
        oracle=_oracle_ok(),
        stichtag=STICHTAG,
        wallet_names={1: "Ledger-Geheim", 2: "Bitbox-Geheim"},
        privacy=True,
    )
    payload = art.meta["payload"]
    assert art.meta.get("privacy") is True
    blob = (bytes(art.content or b"") + str(payload).encode()).lower()
    assert b"bc1q" not in blob
    assert b"ledger-geheim" not in blob
    assert b"bitbox-geheim" not in blob
    row = payload["rows"][0]
    assert row["wallet_aktuell"] == MASK_NAME_PDF
    assert row["anzahl_btc"] == MASK_BTC_PDF
    assert not row.get("wallet_address") or row["wallet_address"] == MASK_ADDR_PDF
    assert "bc1" not in row["wallet_aktuell"].lower()
    for line in payload["cloud_lines"]:
        assert "bc1" not in line.lower()
        assert "ledger" not in line.lower()


def test_fa_pdf_paginates_with_many_lots() -> None:
    """Many lots → multi-page PDF (/Page count > 1)."""
    lots = []
    for i in range(60):
        d = STICHTAG - timedelta(days=400 + i)
        lots.append(
            _lot(
                lot_date=d,
                remaining=1_000 + i,
                address=f"bc1qpage{i:04d}addrxxxxxxxx",
                lot_id=f"p{i}",
                wallet_id=1,
            )
        )
    art = build_fa_inbound_pdf(
        lots=lots,
        wallets=[WalletEntry(name="W", kind="address", address="bc1qpage", id=1)],
        oracle=_oracle_ok(),
        stichtag=STICHTAG,
        wallet_names={1: "W"},
    )
    data = bytes(art.content or b"")
    assert data.startswith(b"%PDF")
    # Count page objects loosely
    page_objs = data.count(b"/Type /Page") + data.count(b"/Type/Page")
    assert page_objs >= 2, f"expected multi-page PDF, got page markers={page_objs}"


def test_api_fa_inbound_privacy_flag_header() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/report/pdf/fa-inbound",
            json={"stichtag": STICHTAG.isoformat(), "privacy": True},
        )
        assert r.status_code == 200
        assert r.headers.get("X-BTC-Herkunft-Privacy") == "true"
