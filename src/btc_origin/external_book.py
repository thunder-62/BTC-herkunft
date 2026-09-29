"""External (foreign) destination addresses: short names + CSV import/export.

Every address outside the user's pasted xpubs that received sats from the
cloud gets a stable short name ``ext-001``, ``ext-002`` … (ordered by first
Cloud-Austritt). The user can replace these with own names (e.g. "Kraken
Einzahlung") via CSV ``name,adresse`` — imported by paste/upload in the
browser, exported only on explicit click.

Hard Boundaries: RAM only (no disk, no LocalStorage); public addresses only —
anything that is not a valid Bitcoin address is rejected (never keys/seeds).
"""

from __future__ import annotations

import csv
import io
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from embit.script import address_to_scriptpubkey

EXT_PREFIX = "ext-"
# Exchange-behaviour thresholds (hints only, never a certainty claim).
BATCH_PAYOUT_MIN_OUTPUTS = 5
HOT_WALLET_MIN_PAYOUTS = 3
# Sender marker for newly mined coins (see tx_ingestor.COINBASE).
COINBASE_LABEL = "coinbase"
CSV_HEADER = ("name", "adresse")
MAX_NAME_LEN = 80
# eigener Name, der ausdrücklich keine Zuordnung ist („Dienst unbekannt 2024“) → ohne ³
_UNKNOWN_RE = re.compile(r"unbekannt|unknown", re.IGNORECASE)


def is_bitcoin_address(text: str) -> bool:
    try:
        address_to_scriptpubkey(text)
        return True
    except Exception:  # noqa: BLE001 — any parse error = not an address
        return False


@dataclass
class ExternalEntry:
    """One external counterparty: an address, or a bundle of addresses that
    appeared together as inputs of one tx (same sender)."""

    address: str  # primary (first seen) address
    name: str
    auto_named: bool
    out_count: int = 0
    total_sats: int = 0  # sats sent TO this counterparty (Cloud-Austritt)
    first_time: str | None = None
    last_time: str | None = None
    addresses: list[str] = field(default_factory=list)
    in_count: int = 0
    in_sats: int = 0  # sats received FROM this counterparty (Cloud-Eintritt)
    # Public label-pack hit (e.g. "Binance") and exchange-like behaviour.
    label: str | None = None
    exchange_hints: list[str] = field(default_factory=list)
    # Name from a date rule (local/zuordnung.csv): taxpayer's own statement.
    declared: str | None = None

    def _seen(self, t: str | None) -> None:
        if t and (self.first_time is None or t < self.first_time):
            self.first_time = t
        if t and (self.last_time is None or t > self.last_time):
            self.last_time = t

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "addresses": list(self.addresses or [self.address]),
            "name": self.name,
            "auto_named": self.auto_named,
            "in_count": self.in_count,
            "in_sats": self.in_sats,
            "out_count": self.out_count,
            "total_sats": self.total_sats,
            "out_sats": self.total_sats,
            "first_time": self.first_time,
            "last_time": self.last_time,
            "label": self.label,
            "exchange_hints": list(self.exchange_hints),
            "declared": self.declared,
        }


@dataclass
class ImportResult:
    imported: int = 0
    updated: int = 0
    skipped: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "updated": self.updated,
            "skipped": list(self.skipped or []),
        }


class ExternalBook:
    """User-given names for external addresses (session RAM)."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}  # imported in this session (UI)
        self._local: dict[str, str] = {}  # from local/externe-adressen.csv
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget session imports (local file names stay — re-read from disk)."""
        with self._lock:
            self._names.clear()

    def set_local_names(self, pairs: Iterable[tuple[str, str]]) -> None:
        with self._lock:
            self._local = {address: name for name, address in pairs}

    def user_names(self) -> dict[str, str]:
        """Local file names, overridden by names imported in this session."""
        with self._lock:
            return {**self._local, **self._names}

    def set_names(
        self, pairs: Iterable[tuple[str, str]], *, own_addresses: set[str] | None = None
    ) -> ImportResult:
        res = ImportResult(skipped=[])
        own = own_addresses or set()
        with self._lock:
            for name, address in pairs:
                if address in own:
                    res.skipped.append(
                        f"{address}: gehört zu einer eingefügten Wallet (nicht extern)"
                    )
                    continue
                if address in self._names:
                    if self._names[address] != name:
                        res.updated += 1
                else:
                    res.imported += 1
                self._names[address] = name
        return res

    def build(
        self,
        out_rows: Iterable[dict[str, Any]],
        in_rows: Iterable[dict[str, Any]] = (),
        *,
        links: Iterable[Iterable[str]] = (),
        label_lookup: Callable[[str], str | None] | None = None,
        txid_names: dict[str, str] | None = None,
        date_rules: Iterable[tuple[str, Any]] = (),
    ) -> list[ExternalEntry]:
        """Group external counterparties and assign ext-NNN names.

        * OUT rows: destination ``address``.
        * IN rows: ``source_addresses`` — all sender addresses of the entry tx.
          Addresses that appear together as inputs of one tx are bundled into
          one counterparty (common-input heuristic: same sender).
        * ``links``: extra address groups of one owner (e.g. deposit addresses
          swept together) — they only connect counterparties; addresses that
          never were a counterparty of the cloud are not listed.
        One numbering for senders and destinations, ordered by first
        appearance (time, then address); imported names replace the auto name
        without shifting other numbers.
        """
        parent: dict[str, str] = {}

        def find(a: str) -> str:
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        events: list[tuple[str, str, int, str | None]] = []  # role, addr, sats, time
        batch_roots: list[str] = []  # senders of batched payouts (≥N outputs)
        txid_named: list[tuple[str, str]] = []  # (addr, name) from exchange exports
        in_per_addr: dict[str, int] = {}
        order: dict[str, tuple[str, str]] = {}  # addr → (time, addr) first seen
        for r in in_rows:
            srcs = [
                a for a in (r.get("source_addresses") or []) if a and a != COINBASE_LABEL
            ]
            if not srcs:
                continue
            t = str(r.get("time") or "") or None
            for a in srcs:
                find(a)
                union(srcs[0], a)
                order.setdefault(a, (t or "9999", a))
            events.append(("in", srcs[0], int(r.get("amount_sats") or 0), t))
            tx_named = (txid_names or {}).get(str(r.get("txid") or "").lower())
            if tx_named:
                txid_named.append((srcs[0], tx_named))
            if int(r.get("entry_vout_count") or 0) >= BATCH_PAYOUT_MIN_OUTPUTS:
                batch_roots.append(srcs[0])
        for r in out_rows:
            addr = str(r.get("address") or "")
            if not addr:
                continue  # „außerhalb Cloud“ — destination unknown
            t = str(r.get("time") or "") or None
            find(addr)
            order.setdefault(addr, (t or "9999", addr))
            events.append(("out", addr, int(r.get("amount_sats") or 0), t))
            tx_named = (txid_names or {}).get(str(r.get("txid") or "").lower())
            if tx_named:
                txid_named.append((addr, tx_named))

        groups: dict[str, ExternalEntry] = {}
        for role, addr, sats, t in events:
            if role == "in":
                in_per_addr[addr] = in_per_addr.get(addr, 0) + 1
            root = find(addr)
            e = groups.get(root)
            if e is None:
                e = groups[root] = ExternalEntry(address="", name="", auto_named=True)
            if role == "in":
                e.in_count += 1
                e.in_sats += sats
            else:
                e.out_count += 1
                e.total_sats += sats
            e._seen(t)
        for group in links:
            members = [a for a in group if a and a != COINBASE_LABEL]
            for a in members[1:]:
                union(members[0], a)
        # Re-key clusters after linking (unions may merge earlier groups).
        merged: dict[str, ExternalEntry] = {}
        for root, e in groups.items():
            r = find(root)
            m = merged.get(r)
            if m is None:
                merged[r] = e
                continue
            m.in_count += e.in_count
            m.in_sats += e.in_sats
            m.out_count += e.out_count
            m.total_sats += e.total_sats
            m._seen(e.first_time)
            m._seen(e.last_time)
        groups = merged
        for addr in sorted(order, key=lambda a: order[a]):
            e = groups.get(find(addr))
            if e is not None:
                e.addresses.append(addr)
        for e in groups.values():
            e.address = e.addresses[0] if e.addresses else ""

        # Exchange evidence: public label pack hit, batched payouts, a sender
        # address reused for many payouts (hot wallet).
        for addr in batch_roots:
            e = groups.get(find(addr))
            if e is not None and "Sammelauszahlung" not in e.exchange_hints:
                e.exchange_hints.append("Sammelauszahlung")
        for addr, n in in_per_addr.items():
            e = groups.get(find(addr))
            if e is not None and n >= HOT_WALLET_MIN_PAYOUTS and "Hot-Wallet" not in e.exchange_hints:
                e.exchange_hints.append("Hot-Wallet")
        export_names: dict[int, str] = {}
        for addr, name in txid_named:
            e = groups.get(find(addr))
            if e is not None:
                export_names.setdefault(id(e), name)
        if label_lookup is not None:
            for e in groups.values():
                for a in e.addresses:
                    lab = label_lookup(a)
                    if lab:
                        e.label = lab
                        break

        names = self.user_names()
        rules = sorted(date_rules, key=lambda r: r[1])
        ordered = sorted(groups.values(), key=lambda e: (e.first_time or "9999", e.address))
        width = max(3, len(str(len(ordered))))
        for i, e in enumerate(ordered, start=1):
            named = next((names[a] for a in e.addresses if a in names), None)
            if named is not None:
                e.name = named
                e.auto_named = False
                if (not e.label or named.lower() != e.label.lower()) and not _UNKNOWN_RE.search(named):
                    # eigener Name (externe-adressen.csv / Oberfläche): Angabe, kein Beleg → ³;
                    # „unbekannt“ ist keine Zuordnung, also auch keine Angabe zur Gegenstelle
                    e.declared = f"Angabe: Gegenstelle benannt als {named}"
            elif id(e) in export_names:
                e.name = export_names[id(e)]
                e.auto_named = False
            elif e.label:
                e.name = e.label
                e.auto_named = False
            else:
                e.name = f"{EXT_PREFIX}{i:0{width}d}"
                last = (e.last_time or "")[:10]
                for rule_name, until in rules:
                    if last and last <= until.isoformat():
                        e.name = rule_name
                        e.auto_named = False
                        e.declared = (
                            f"Angabe: unbenannte Gegenstelle bis {until.strftime('%d.%m.%Y')} "
                            f"= {rule_name}"
                        )
                        break
        # Imported names for addresses not (yet) seen in this session.
        for addr, name in sorted(names.items()):
            if addr not in order:
                ordered.append(
                    ExternalEntry(address=addr, name=name, auto_named=False, addresses=[addr])
                )
        return ordered


def parse_csv(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse ``name,adresse`` CSV (also ``;`` / Tab, optional header, BOM).

    Returns ``(pairs, errors)``. Rows whose address is not a valid Bitcoin
    address are skipped with an error — secrets can never get in this way.
    """
    text = (text or "").lstrip("﻿")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], ["Leere Datei"]
    first = lines[0]
    delim = max((";", ",", "\t"), key=first.count)
    if first.count(delim) == 0:
        delim = ","
    pairs: list[tuple[str, str]] = []
    errors: list[str] = []
    for n, row in enumerate(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim), 1):
        cells = [c.strip() for c in row]
        if len(cells) < 2:
            errors.append(f"Zeile {n}: erwartet name{delim}adresse")
            continue
        name, address = cells[0], cells[1]
        if n == 1 and not is_bitcoin_address(address):
            # Header row (e.g. name,adresse / Name;Address)
            if address.lower() in ("adresse", "address", "addr") or name.lower() == "name":
                continue
        if not is_bitcoin_address(address):
            errors.append(f"Zeile {n}: keine gültige Bitcoin-Adresse")
            continue
        if not name:
            errors.append(f"Zeile {n}: Name fehlt")
            continue
        if re.fullmatch(r"ext-\d+", name, re.IGNORECASE):
            # automatischer Name aus dem Export — keine eigene Angabe, nicht übernehmen
            continue
        pairs.append((name[:MAX_NAME_LEN], address))
    return pairs, errors


def to_csv(entries: Iterable[ExternalEntry]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    for e in entries:
        for addr in e.addresses or [e.address]:
            w.writerow([e.name, addr])
    return buf.getvalue()
