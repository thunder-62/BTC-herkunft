"""Derive addresses from xpub/ypub/zpub (BIP32 / BIP49 / BIP84).

Hardware-wallet-agnostic: accepts standard BIP32/BIP84 public extended keys
from any manufacturer that can export them (Trezor, BitBox, Ledger, Coldcard,
Foundation Devices, software wallets, …). No USB/HID/device APIs.

Version-byte variants (SLIP-132):
- ``xpub`` — BIP32 / BIP44-style P2PKH
- ``ypub`` — BIP49 P2SH-P2WPKH
- ``zpub`` — BIP84 native P2WPKH

``normalize_extended_public_key`` rewrites SLIP-132 version bytes to the
canonical xpub payload form (same key material) while remembering the script
type for address generation.

Uses the pure-Python ``embit`` library (no system libs required).

Limitations
-----------
* Mainnet prefixes ``xpub`` / ``ypub`` / ``zpub`` are fully supported.
* Testnet ``tpub`` / ``upub`` / ``vpub`` are normalized and derived on the
  testnet network (addresses start with ``tb1`` / ``2`` / ``m``/``n``).
* Account-level keys are expected (depth 3, as exported by Sparrow / HW
  wallets for BIP84 ``m/84'/0'/0'``). Keys at other depths still derive
  ``/change/index`` children; paths are labeled ``m/{change}/{index}``.
* Never accepts or derives from seeds / xprv (rejected in wallet_registry).
"""

from __future__ import annotations

from dataclasses import dataclass

from embit import base58, bip32
from embit.networks import NETWORKS
from embit.script import p2pkh, p2sh, p2tr, p2wpkh

from btc_origin.config import get_settings

_XPUB_PREFIXES = ("xpub", "ypub", "zpub", "tpub", "upub", "vpub")

# SLIP-132 version bytes
_XPUB_MAIN = bytes.fromhex("0488b21e")
_YPUB_MAIN = bytes.fromhex("049d7cb2")
_ZPUB_MAIN = bytes.fromhex("04b24746")
_TPUB_TEST = bytes.fromhex("043587cf")
_UPUB_TEST = bytes.fromhex("044a5262")
_VPUB_TEST = bytes.fromhex("045f1cf6")


# Probe order for plain xpub/tpub (script type not encoded in the prefix).
AMBIGUOUS_XPUB_SCRIPT_TYPES = ("p2wpkh", "p2sh-p2wpkh", "p2tr", "p2pkh")

SCRIPT_TYPE_LABELS = {
    "p2wpkh": "Native SegWit (bc1q…)",
    "p2sh-p2wpkh": "SegWit kompatibel (3…)",
    "p2tr": "Taproot (bc1p…)",
    "p2pkh": "Legacy (1…)",
}


@dataclass
class DerivedAddress:
    address: str
    derivation_path: str
    is_change: bool
    index: int
    script_type: str = "p2wpkh"
    scripthash: str | None = None


@dataclass
class NormalizedXpub:
    """Result of SLIP-132 version-byte normalization."""

    original: str
    normalized: str  # always xpub/tpub form (same payload)
    script_type_hint: str  # p2pkh | p2sh-p2wpkh | p2wpkh | unknown
    network: str  # main | test
    note: str


def _script_type_and_network(key: str) -> tuple[str, str, str]:
    """Return (script_type_hint, network, note) from prefix."""
    lower = key.strip().lower()
    if lower.startswith("zpub"):
        return (
            "p2wpkh",
            "main",
            "zpub (BIP84): version bytes → xpub payload; native segwit addresses.",
        )
    if lower.startswith("vpub"):
        return (
            "p2wpkh",
            "test",
            "vpub (BIP84 testnet): version bytes → tpub payload; native segwit.",
        )
    if lower.startswith("ypub"):
        return (
            "p2sh-p2wpkh",
            "main",
            "ypub (BIP49): version bytes → xpub payload; P2SH-P2WPKH addresses.",
        )
    if lower.startswith("upub"):
        return (
            "p2sh-p2wpkh",
            "test",
            "upub (BIP49 testnet): version bytes → tpub payload; P2SH-P2WPKH.",
        )
    if lower.startswith("xpub"):
        return ("p2pkh", "main", "Already standard xpub form (P2PKH).")
    if lower.startswith("tpub"):
        return ("p2pkh", "test", "Already standard tpub form (testnet P2PKH).")
    return ("unknown", "main", "Unrecognized prefix — expected xpub/ypub/zpub.")


def normalize_extended_public_key(key: str) -> NormalizedXpub:
    """Normalize zpub/ypub/vpub/upub toward standard xpub/tpub form.

    Rewrites SLIP-132 version bytes while keeping the BIP32 payload intact.
    Manufacturer is irrelevant — only the BIP32/BIP84 wire format matters.
    """
    k = key.strip()
    hint, network, note = _script_type_and_network(k)
    if hint == "unknown":
        return NormalizedXpub(
            original=k, normalized=k, script_type_hint=hint, network=network, note=note
        )

    try:
        raw = base58.decode_check(k)
    except Exception as exc:  # noqa: BLE001 — surface as ValueError
        raise ValueError(f"Invalid extended public key (base58/checksum): {exc}") from exc

    if len(raw) != 78:
        raise ValueError(f"Invalid extended public key length: {len(raw)} (expected 78)")

    target_version = _TPUB_TEST if network == "test" else _XPUB_MAIN
    normalized = base58.encode_check(target_version + raw[4:])
    return NormalizedXpub(
        original=k,
        normalized=normalized,
        script_type_hint=hint,
        network=network,
        note=note,
    )


def address_to_scripthash(address: str) -> str:
    """Electrum protocol scripthash: reverse(sha256(scriptPubKey))."""
    import hashlib

    from embit.script import address_to_scriptpubkey

    spk = address_to_scriptpubkey(address)
    digest = hashlib.sha256(spk.data).digest()
    return digest[::-1].hex()


def _address_for_pubkey(
    hd_key: bip32.HDKey, script_type: str, network: str
) -> str:
    net = NETWORKS["test"] if network == "test" else NETWORKS["main"]
    if script_type == "p2wpkh":
        return p2wpkh(hd_key).address(net)
    if script_type == "p2sh-p2wpkh":
        return p2sh(p2wpkh(hd_key)).address(net)
    if script_type == "p2tr":
        return p2tr(hd_key).address(net)
    # default / p2pkh
    return p2pkh(hd_key).address(net)


class HdDeriver:
    """HD address derivation from account-level xpub/ypub/zpub.

    Gap limit controls how far to scan unused addresses (default from
    ``GAP_LIMIT`` env / settings).
    """

    def __init__(self, gap_limit: int | None = None) -> None:
        self.gap_limit = gap_limit if gap_limit is not None else get_settings().gap_limit

    def prepare_xpub(self, key: str) -> NormalizedXpub:
        if not any(key.strip().lower().startswith(p) for p in _XPUB_PREFIXES):
            raise ValueError(
                "Expected a public extended key (xpub/ypub/zpub). "
                "Seeds and xprv are rejected elsewhere in wallet_registry."
            )
        return normalize_extended_public_key(key)

    def _root(self, xpub: str) -> tuple[bip32.HDKey, NormalizedXpub]:
        norm = self.prepare_xpub(xpub)
        hd = bip32.HDKey.from_string(norm.normalized)
        return hd, norm

    def _derive_range(
        self,
        xpub: str,
        *,
        is_change: bool,
        start: int,
        count: int,
        script_type: str | None = None,
    ) -> list[DerivedAddress]:
        hd, norm = self._root(xpub)
        change_idx = 1 if is_change else 0
        if script_type is None:
            script_type = (
                norm.script_type_hint if norm.script_type_hint != "unknown" else "p2pkh"
            )
        out: list[DerivedAddress] = []
        for i in range(start, start + count):
            child = hd.derive([change_idx, i])
            addr = _address_for_pubkey(child, script_type, norm.network)
            path = f"m/{change_idx}/{i}"
            out.append(
                DerivedAddress(
                    address=addr,
                    derivation_path=path,
                    is_change=is_change,
                    index=i,
                    script_type=script_type,
                    scripthash=address_to_scripthash(addr),
                )
            )
        return out

    def derive_receive(
        self,
        xpub: str,
        *,
        start: int = 0,
        count: int | None = None,
        script_type: str | None = None,
    ) -> list[DerivedAddress]:
        """Derive receive (change=0) addresses."""
        n = count if count is not None else self.gap_limit
        return self._derive_range(
            xpub, is_change=False, start=start, count=n, script_type=script_type
        )

    def derive_change(
        self,
        xpub: str,
        *,
        start: int = 0,
        count: int | None = None,
        script_type: str | None = None,
    ) -> list[DerivedAddress]:
        """Derive change (change=1) addresses."""
        n = count if count is not None else self.gap_limit
        return self._derive_range(
            xpub, is_change=True, start=start, count=n, script_type=script_type
        )

    def candidate_script_types(self, xpub: str) -> list[str]:
        """Script types worth probing for this key.

        ``ypub``/``zpub`` (SLIP-132) state their type. A plain ``xpub``/``tpub``
        does not: Trezor Suite, Ledger Live, BitBox, Sparrow & co. also show
        ``xpub`` for BIP84 native-segwit, BIP49 and BIP86 taproot accounts.
        Probe all of them, most common first.
        """
        hint = self.prepare_xpub(xpub).script_type_hint
        prefix = xpub.strip().lower()[:4]
        if prefix in ("xpub", "tpub") or hint == "unknown":
            return list(AMBIGUOUS_XPUB_SCRIPT_TYPES)
        return [hint]

    def derive_until_gap(
        self,
        xpub: str,
        *,
        used_addresses: set[str] | None = None,
        max_index: int | None = None,
        script_type: str | None = None,
    ) -> list[DerivedAddress]:
        """Scan receive+change until ``gap_limit`` consecutive unused addresses.

        If ``used_addresses`` is None, returns the first ``gap_limit`` receive
        and change addresses (initial scan window). Callers that know which
        addresses have history should pass them so the gap advances correctly.
        """
        used = used_addresses or set()
        hard_cap = max_index if max_index is not None else max(self.gap_limit * 50, 1000)
        found: list[DerivedAddress] = []

        for is_change in (False, True):
            gap = 0
            idx = 0
            while gap < self.gap_limit and idx <= hard_cap:
                batch = self._derive_range(
                    xpub,
                    is_change=is_change,
                    start=idx,
                    count=1,
                    script_type=script_type,
                )
                if not batch:
                    break
                addr = batch[0]
                found.append(addr)
                if addr.address in used:
                    gap = 0
                else:
                    gap += 1
                idx += 1

        return found
