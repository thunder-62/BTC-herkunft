"""Name\\tXPub paste lines — TAB separator only."""

from __future__ import annotations

from fastapi.testclient import TestClient

from btc_origin.api.app import app
from btc_origin.wallet_registry import (
    classify_public_input,
    parse_paste_line,
    parse_paste_lines,
    WalletRegistry,
)

BIP84_ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
BIP49_YPUB = (
    "ypub6Ww3ibxVfGzLrAH1PNcjyAWenMTbbAosGNB6VvmSEgytSER9azLDWCxoJwW7K"
    "e7icmizBMXrzBx9979FfaHxHcrArf3zbeJJJUZPf663zsP"
)
BIP44_XPUB = (
    "xpub6BosfCnifzxcFwrSzQiqu2DBVTshkCXacvNsWGYJVVhhawA7d4R5WSWGFNbi8"
    "Aw6ZRc1brxMyWMzG3DSSSSoekkudhUd9yLb6qx39T9nMdj"
)
ADDR = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def test_parse_line_with_tab_sets_name() -> None:
    name, key = parse_paste_line(f"Sparkonto\t{BIP84_ZPUB}")
    assert name == "Sparkonto"
    assert key == BIP84_ZPUB


def test_parse_line_without_tab_unchanged() -> None:
    name, key = parse_paste_line(BIP84_ZPUB)
    assert name is None
    assert key == BIP84_ZPUB


def test_parse_multiline_mix() -> None:
    text = f"A\t{BIP84_ZPUB}\n{BIP44_XPUB}\nB\t{ADDR}"
    parsed = parse_paste_lines(text)
    assert parsed == [
        ("A", BIP84_ZPUB),
        (None, BIP44_XPUB),
        ("B", ADDR),
    ]


def test_parse_whitespace_and_empty_name() -> None:
    name, key = parse_paste_line(f"  Hot  \t  {BIP84_ZPUB}  ")
    assert name == "Hot"
    assert key == BIP84_ZPUB
    name2, key2 = parse_paste_line(f"\t{BIP84_ZPUB}")
    assert name2 is None
    assert key2 == BIP84_ZPUB


def test_parse_multiple_tabs_first_separates() -> None:
    name, key = parse_paste_line("Name\tkey\twith\ttabs")
    assert name == "Name"
    assert key == "key\twith\ttabs"


def test_comma_in_name_without_tab_is_not_separator() -> None:
    line = f"Wallet, Main,{BIP84_ZPUB}"
    name, key = parse_paste_line(line)
    assert name is None
    assert key == line


def test_zpub_ypub_still_classify() -> None:
    assert classify_public_input(BIP84_ZPUB) == "xpub"
    assert classify_public_input(BIP49_YPUB) == "xpub"
    assert classify_public_input(BIP44_XPUB) == "xpub"
    assert classify_public_input(ADDR) == "address"


def test_register_paste_names_and_defaults() -> None:
    reg = WalletRegistry()
    results = reg.register_paste(
        f"Cold\t{BIP84_ZPUB}\n{BIP49_YPUB}\n\t{ADDR}",
        default_name="Default",
    )
    assert len(results) == 3
    assert all("error" not in r for r in results)
    wallets = reg.list_wallets()
    assert wallets[0].name == "Cold"
    assert wallets[1].name == "Default"
    assert wallets[2].name == "Default (2)"  # Namen eindeutig: der Bericht gruppiert nach Name


def test_api_batch_paste_tab_names() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/wallets/batch",
            json={
                "paste": f"Alpha\t{BIP84_ZPUB}\n{ADDR}",
                "default_name": "Fallback",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["imported"] == 2
        names = [x["name"] for x in body["results"]]
        assert names[0] == "Alpha"
        assert names[1] == "Fallback"
        listed = client.get("/api/wallets").json()["wallets"]
        assert listed[0]["name"] == "Alpha"
        assert listed[0]["kind"] == "xpub"
        assert listed[1]["kind"] == "address"


def test_duplicate_wallet_name_gets_suffix_and_warning() -> None:
    reg = WalletRegistry()
    reg.register_address("Lightning", ADDR)
    res = reg.register_xpub("lightning", BIP84_ZPUB)
    assert res.entry.name == "lightning (2)"
    assert res.warning and "schon vergeben" in res.warning
