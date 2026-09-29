"""Struktur-Prüfung eines Exports: keine Beträge, Adressen oder IDs in der Ausgabe."""

from __future__ import annotations

from datetime import date

from btc_origin.export_check import check

CSV = """ReferenceId,Timestamp,TransactionType,FiatAmount,FiatFee,BitcoinAmount,BitcoinFee,BitcoinPrice,CostBasisUsd,Destination,Description,TransactionHash,Note
3f2a9c1e-77aa-4b1b-9d0e-5c6b7a8d9e0f,Jan 15 2024 10:00:00,Purchase,-500.00,-2.50,0.01234567,,41500.00,,,,,
9a8b7c6d-1111-2222-3333-444455556666,Jun 20 2024 12:00:00,Withdrawal,,,-0.01200000,0.00010000,,,bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu,,aa11bb22cc33dd44ee55ff6677889900aa11bb22cc33dd44ee55ff6677889900,mail@example.org
"""


def test_check_masks_values_and_shows_ratios(tmp_path) -> None:
    f = tmp_path / "strike.csv"
    f.write_text(CSV, encoding="utf-8")
    out = check(f, date(2024, 6, 20))
    for secret in ("0.01234567", "0.012", "41500", "bc1qcr8te4", "aa11bb22", "3f2a9c1e", "mail@example.org", "500.00"):
        assert secret not in out
    assert "TransactionType=Withdrawal" in out and "BitcoinFee/BitcoinAmount = 0.83 %" in out
    assert "Zahl(8 NK)" in out and "Jun 20 2024" in out
    assert "So liest das Tool die Datei:" in out and "withdraw ×1" in out and "buy ×1" in out
