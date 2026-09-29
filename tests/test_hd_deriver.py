"""HD deriver: BIP84/BIP49/BIP44 vectors + zpub/ypub normalization."""

from __future__ import annotations

from btc_origin.hd_deriver import (
    HdDeriver,
    address_to_scripthash,
    normalize_extended_public_key,
)

# Account-level keys from mnemonic
# "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
# Generated via embit (BIP84 official doc zpub has a known bad checksum).
BIP84_ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
BIP84_RECV_0 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
BIP84_RECV_1 = "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"

BIP49_YPUB = (
    "ypub6Ww3ibxVfGzLrAH1PNcjyAWenMTbbAosGNB6VvmSEgytSER9azLDWCxoJwW7K"
    "e7icmizBMXrzBx9979FfaHxHcrArf3zbeJJJUZPf663zsP"
)
BIP49_ADDR_0 = "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf"

BIP44_XPUB = (
    "xpub6BosfCnifzxcFwrSzQiqu2DBVTshkCXacvNsWGYJVVhhawA7d4R5WSWGFNbi8"
    "Aw6ZRc1brxMyWMzG3DSSSSoekkudhUd9yLb6qx39T9nMdj"
)
BIP44_ADDR_0 = "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"


def test_normalize_zpub_to_xpub_payload() -> None:
    norm = normalize_extended_public_key(BIP84_ZPUB)
    assert norm.script_type_hint == "p2wpkh"
    assert norm.network == "main"
    assert norm.normalized.startswith("xpub")
    assert norm.original == BIP84_ZPUB


def test_normalize_ypub() -> None:
    norm = normalize_extended_public_key(BIP49_YPUB)
    assert norm.script_type_hint == "p2sh-p2wpkh"
    assert norm.normalized.startswith("xpub")


def test_bip84_receive_addresses() -> None:
    d = HdDeriver(gap_limit=5)
    addrs = d.derive_receive(BIP84_ZPUB, start=0, count=2)
    assert [a.address for a in addrs] == [BIP84_RECV_0, BIP84_RECV_1]
    assert addrs[0].derivation_path == "m/0/0"
    assert addrs[0].script_type == "p2wpkh"
    assert addrs[0].scripthash


def test_bip49_and_bip44_first_receive() -> None:
    d = HdDeriver(gap_limit=1)
    assert d.derive_receive(BIP49_YPUB, count=1)[0].address == BIP49_ADDR_0
    assert d.derive_receive(BIP44_XPUB, count=1)[0].address == BIP44_ADDR_0


def test_derive_until_gap_respects_limit() -> None:
    d = HdDeriver(gap_limit=3)
    addrs = d.derive_until_gap(BIP84_ZPUB)
    # 3 receive + 3 change when nothing is used
    assert len(addrs) == 6
    assert sum(1 for a in addrs if not a.is_change) == 3
    assert sum(1 for a in addrs if a.is_change) == 3


def test_derive_until_gap_extends_when_used() -> None:
    d = HdDeriver(gap_limit=2)
    used = {BIP84_RECV_0}  # index 0 used → need indices 0,1,2 (gap of 2 unused after)
    addrs = d.derive_until_gap(BIP84_ZPUB, used_addresses=used)
    recv = [a for a in addrs if not a.is_change]
    assert recv[-1].index == 2  # 0 used, then 1 and 2 unused (= gap 2)


def test_scripthash_stable() -> None:
    sh = address_to_scripthash(BIP84_RECV_0)
    assert len(sh) == 64
    assert sh == address_to_scripthash(BIP84_RECV_0)
