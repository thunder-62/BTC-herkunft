"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile

import pytest

# Hermetic tests: never read a developer's real local/ folder (names, exports).
os.environ.setdefault(
    "BTC_ORIGIN_LOCAL_DIR", os.path.join(tempfile.gettempdir(), "btc-origin-test-local-none")
)


def _test_rules_dir() -> str:
    """Regelwerk für Tests: die eingefrorene Testkopie tests/data/btc-regeln (unabhängig von
    fachlichen Änderungen an btc-regeln/) plus synthetische Regeldateien für ältere Testjahre
    (Testdaten reichen bis 2009 zurück) — Kopien von 2022 mit angepasstem veranlagungsjahr."""
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parent / "data" / "btc-regeln"
    dst = Path(tempfile.mkdtemp(prefix="btc-origin-test-regeln-"))
    shutil.copytree(src, dst, dirs_exist_ok=True)
    base = (src / "regeln" / "2022.yaml").read_text(encoding="utf-8")
    for year in range(2009, 2022):
        text = base.replace("veranlagungsjahr: 2022", f"veranlagungsjahr: {year}")
        text = text.replace('geprueft_von: "Recherche (keine Steuerberatung)"', 'geprueft_von: "Testdaten"')
        (dst / "regeln" / f"{year}.yaml").write_text(text, encoding="utf-8")
    return str(dst)


os.environ.setdefault("BTC_ORIGIN_RULES_DIR", _test_rules_dir())
from fastapi.testclient import TestClient  # noqa: E402

from btc_origin.api.app import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_session_categories():
    """Zuordnungen der Sitzung (RAM) nicht zwischen Tests weitergeben."""
    from btc_origin.regelwerk import set_session_categories

    set_session_categories(None)
    yield
    set_session_categories(None)
