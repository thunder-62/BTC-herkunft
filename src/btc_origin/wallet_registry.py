"""RAM registry for public xpubs / addresses (never seeds). Unlimited multi-xpub.

Hardware-wallet-agnostic: any wallet that exports BIP32/BIP84 xpub/ypub/zpub
(or Sparrow CSV / single addresses). No USB/HID, no automatic device detection,
no manufacturer integrations. Input = cut-and-paste in the browser only.

Hard Boundary: in-memory only. Process / tab end = all entries gone. Nothing
is written to disk, LocalStorage, or IndexedDB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

from btc_origin.config import get_settings

# Reject seed-like inputs at the boundary (heuristic stub).
_SEED_HINTS = ("seed", "mnemonic", "private key", "wif", "xprv", "yprv", "zprv")


def parse_paste_line(line: str) -> tuple[str | None, str]:
    """Parse one paste line as optional ``Name\\tXPub`` (literal TAB only).

    - Text before the first TAB = wallet display name (trimmed).
    - Remainder after the first TAB = key/address (strip leading/trailing whitespace).
    - No TAB → key only; name is ``None`` (caller uses default/auto name).
    - Empty name after TAB → treat as no name (``None``).
    - Comma is *not* a separator.
    """
    raw = line.strip()
    if "\t" not in raw:
        return None, raw
    name_part, key_part = raw.split("\t", 1)
    name = name_part.strip() or None
    key = key_part.strip()
    return name, key


def parse_paste_lines(text: str) -> list[tuple[str | None, str]]:
    """Split multi-line paste; skip blank lines. One entry per non-empty line."""
    out: list[tuple[str | None, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        out.append(parse_paste_line(line))
    return out


def classify_public_input(raw: str) -> Literal["xpub", "address", "reject"]:
    """Classify a paste value as xpub, address, or reject (seeds/private keys)."""
    v = raw.strip()
    if not v:
        return "reject"
    lowered = v.lower()
    if (
        "seed" in lowered
        or lowered.startswith(("xprv", "yprv", "zprv", "tprv"))
        or any(h in lowered for h in ("mnemonic", "private key", "wif"))
    ):
        return "reject"
    words = v.split()
    if len(words) in (12, 15, 18, 21, 24) and all(w.isalpha() for w in words):
        return "reject"
    if re.match(r"^[xyzuvt]pub[1-9A-HJ-NP-Za-km-z]+$", v, re.I):
        return "xpub"
    if re.match(r"^(bc1|tb1|bcrt1|[13]|[mn2])[a-zA-HJ-NP-Z0-9]+$", v, re.I):
        return "address"
    if re.match(r"^[xyzuvt]pub", v, re.I):
        return "xpub"
    if len(v) >= 26:
        return "address"
    return "reject"


@dataclass
class WalletEntry:
    """A registered public wallet descriptor (session RAM only)."""

    name: str
    kind: str  # xpub | address | sparrow_csv
    xpub: str | None = None
    address: str | None = None
    id: int | None = None


@dataclass
class RegisterResult:
    entry: WalletEntry
    warning: str | None = None


class WalletRegistry:
    """Pure RAM registry. Unlimited xpubs; soft performance warning only.

    Durable persistence is **forbidden by design**. Closing the app/process
    discards every xpub, address, and related session state.
    """

    def __init__(self) -> None:
        self._wallets: list[WalletEntry] = []
        self._next_id = 1

    def _reject_secrets(self, value: str) -> None:
        lowered = value.strip().lower()
        for hint in _SEED_HINTS:
            if hint in lowered or lowered.startswith(("xprv", "yprv", "zprv")):
                raise ValueError(
                    "Seeds and private keys are not accepted. "
                    "Only public xpubs/addresses/Sparrow CSV are allowed."
                )
        # BIP39-ish: many space-separated words
        words = value.strip().split()
        if len(words) in (12, 15, 18, 21, 24) and all(w.isalpha() for w in words):
            raise ValueError(
                "Mnemonic-like input rejected. Provide an xpub or address instead."
            )

    def _unique_name(self, name: str) -> tuple[str, str | None]:
        """Der Bericht fasst Wallets über ihren Namen zusammen — Namen müssen eindeutig sein.
        Doppelter Name → „Name (2)“ usw., mit Hinweis."""
        taken = {w.name.strip().lower() for w in self._wallets}
        base = (name or "").strip()
        if base.lower() not in taken:
            return base, None
        n = 2
        while f"{base} ({n})".lower() in taken:
            n += 1
        unique = f"{base} ({n})"
        return unique, f"Name „{base}“ ist schon vergeben — gespeichert als „{unique}“."

    def _assign_id(self, entry: WalletEntry) -> WalletEntry:
        entry.id = self._next_id
        self._next_id += 1
        return entry

    def register_xpub(self, name: str, xpub: str) -> RegisterResult:
        self._reject_secrets(xpub)
        name, renamed = self._unique_name(name)
        entry = self._assign_id(WalletEntry(name=name, kind="xpub", xpub=xpub.strip()))
        self._wallets.append(entry)
        warning = self._maybe_large_count_warning()
        return RegisterResult(entry=entry, warning=" ".join(w for w in (renamed, warning) if w) or None)

    def register_address(self, name: str, address: str) -> RegisterResult:
        self._reject_secrets(address)
        name, renamed = self._unique_name(name)
        entry = self._assign_id(
            WalletEntry(name=name, kind="address", address=address.strip())
        )
        self._wallets.append(entry)
        warning = self._maybe_large_count_warning()
        return RegisterResult(entry=entry, warning=" ".join(w for w in (renamed, warning) if w) or None)

    def register_many_xpubs(
        self, items: Iterable[tuple[str, str]]
    ) -> list[RegisterResult]:
        """Register any number of xpubs — unlimited; soft warning only."""
        results: list[RegisterResult] = []
        for name, xpub in items:
            results.append(self.register_xpub(name, xpub))
        return results

    def register_paste(
        self,
        text: str,
        *,
        default_name: str | None = None,
        name_offset: int = 0,
    ) -> list[dict]:
        """Register wallets from multi-line paste (optional ``Name\\tXPub`` per line).

        Returns a list of result dicts (success or ``error``), matching the batch API.
        Lines classified as reject are recorded as errors and skipped.
        """
        base = (default_name or "").strip()
        parsed = parse_paste_lines(text)
        results: list[dict] = []
        for idx, (line_name, key) in enumerate(parsed):
            kind = classify_public_input(key)
            if kind == "reject":
                results.append(
                    {
                        "name": line_name or base or f"line {idx + 1}",
                        "error": (
                            "Seeds / private keys / invalid lines rejected. "
                            "Only public xpubs/addresses are allowed."
                        ),
                    }
                )
                continue
            label = (line_name or base or "").strip()
            if not label:
                # count already includes prior successful registers in this call
                label = f"Wallet {self.count() + 1 + name_offset}"
            try:
                if kind == "xpub":
                    r = self.register_xpub(label, key)
                else:
                    r = self.register_address(label, key)
            except ValueError as exc:
                results.append({"name": label, "error": str(exc)})
                continue
            results.append(
                {
                    "id": r.entry.id,
                    "name": r.entry.name,
                    "kind": r.entry.kind,
                    "warning": r.warning,
                }
            )
        return results

    def list_wallets(self) -> list[WalletEntry]:
        return list(self._wallets)

    def get(self, wallet_id: int) -> WalletEntry | None:
        for w in self._wallets:
            if w.id == wallet_id:
                return w
        return None

    def count(self) -> int:
        return len(self._wallets)

    def clear(self) -> None:
        """Discard all session wallets (explicit or process teardown)."""
        self._wallets.clear()
        self._next_id = 1

    def _maybe_large_count_warning(self) -> str | None:
        threshold = get_settings().large_wallet_warn_threshold
        n = self.count()
        if n >= threshold:
            return (
                f"You have {n} registered wallets/xpubs. "
                "Sync may take longer; there is no hard limit — processing continues."
            )
        return None
