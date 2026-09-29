"""Label packs for insolvency / exchange-failure clusters + evidence-gap status.

Hard rules
----------
* NEVER silently assign cost basis 0. Missing purchase proof → status flag only.
* **Public** cluster address packs may live in Git under ``data/label_packs/``.
* User xpubs, tx history, personal labels, and session data stay RAM /
  SQLite ``:memory:`` only — never written into label packs or LocalStorage.
* Status strings are documentation hints — not tax or investment advice.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from btc_origin.tx_ingestor import Flow

logger = logging.getLogger(__name__)

# Primary evidence-gap status (DE Anlage-SO Zuarbeit wording).
STATUS_PROOF_MISSING = "Kaufnachweis fehlt"
STATUS_PROOF_MISSING_SOURCE_UNREACHABLE = (
    "Kaufnachweis fehlt, Quelle nicht erreichbar"
)

# Built-in entity display names (always available even if JSON packs missing).
DEFAULT_ENTITY_PACK: dict[str, str] = {
    "ftx": "FTX",
    "mtgox": "Mt.Gox",
    "mt.gox": "Mt.Gox",
    "mt_gox": "Mt.Gox",
    "celsius": "Celsius",
    "blockfi": "BlockFi",
    "voyager": "Voyager",
    "binance": "Binance",
    "coinbase": "Coinbase",
}

# Empty by default — real addresses load from ``data/label_packs/*.json``.
# Unit tests may inject fixtures via ``load_packs_from_dir`` / ``load_pack_dict``.
DEFAULT_ADDRESS_PACK: dict[str, str] = {}


def default_pack_json() -> str:
    """Serialize the built-in entity pack + current address map as JSON (RAM)."""
    return json.dumps(
        {"entities": DEFAULT_ENTITY_PACK, "addresses": DEFAULT_ADDRESS_PACK},
        indent=2,
        ensure_ascii=False,
    )


def resolve_label_packs_dir(
    explicit: str | Path | None = None,
) -> Path | None:
    """Locate ``data/label_packs`` for editable checkout or installed layout.

    Search order:
    1. ``explicit`` argument
    2. ``BTC_ORIGIN_LABEL_PACKS`` env
    3. Walk parents of this file for ``data/label_packs`` (repo root)
    4. ``<package>/data/label_packs`` (bundled next to the module)
    """
    if explicit is not None:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_dir() else None

    env = os.environ.get("BTC_ORIGIN_LABEL_PACKS", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        return p if p.is_dir() else None

    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "data" / "label_packs"
        if candidate.is_dir():
            return candidate

    bundled = here.parent / "data" / "label_packs"
    if bundled.is_dir():
        return bundled
    return None


def load_cluster_pack_file(path: Path) -> dict[str, Any] | None:
    """Parse one cluster JSON file; return None on error / wrong shape."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("label pack unreadable %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("label pack not an object: %s", path)
        return None
    return raw


def cluster_pack_to_maps(
    payload: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], str | None]:
    """Extract (entities, addresses, default_status) from a cluster pack.

    Schema (``btc-origin.label_pack.v1`` cluster file)::

        {
          "id": "ftx",
          "name": "FTX",
          "status": "Kaufnachweis fehlt, Quelle nicht erreichbar",
          "note": "...",
          "source": "https://…",
          "updated": "YYYY-MM-DD",
          "addresses": ["bc1…", "1…", "3…"]
        }
    """
    entities: dict[str, str] = {}
    addresses: dict[str, str] = {}
    entity_id = str(payload.get("id") or "").strip().lower()
    name = str(payload.get("name") or entity_id or "").strip()
    status = payload.get("status")
    status_s = str(status).strip() if status else None

    if entity_id and name:
        entities[entity_id] = name
        # Common aliases for Mt.Gox
        if entity_id in {"mtgox", "mt_gox", "mt.gox"}:
            entities["mtgox"] = name
            entities["mt_gox"] = name
            entities["mt.gox"] = name

    addrs = payload.get("addresses") or []
    if isinstance(addrs, list) and entity_id:
        for a in addrs:
            s = str(a).strip()
            if s:
                addresses[s] = entity_id

    # Legacy combined pack shape: {"entities": {...}, "addresses": {...}}
    legacy_entities = payload.get("entities") or payload.get("entity_pack")
    legacy_addresses = payload.get("addresses")
    if isinstance(legacy_entities, dict):
        for k, v in legacy_entities.items():
            entities[str(k).strip().lower()] = str(v)
    if isinstance(legacy_addresses, dict):
        for k, v in legacy_addresses.items():
            addresses[str(k).strip()] = str(v).strip().lower()

    return entities, addresses, status_s


@dataclass
class LabelAssignment:
    target_type: str  # address | txid | flow | counterparty
    target_id: str
    label: str
    status: str
    source: str = "label_pack"
    entity_key: str | None = None
    # ALWAYS None unless an explicit non-zero basis is supplied later by the user.
    # NEVER invent 0.
    cost_basis: float | None = None


@dataclass
class LabelService:
    """Apply known entity labels; default status is evidence-gap (Kaufnachweis fehlt)."""

    entity_pack: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_ENTITY_PACK)
    )
    address_pack: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_ADDRESS_PACK)
    )
    # Per-entity status override from JSON packs (falls back to global constant).
    entity_status: dict[str, str] = field(default_factory=dict)
    pack_dir: Path | None = field(default=None, repr=False)
    packs_loaded: list[str] = field(default_factory=list)
    # When True (default), load ``data/label_packs`` at construction.
    # Tests that need an empty/minimal service set this False.
    autoload_packs: bool = True

    def __post_init__(self) -> None:
        """Load public packs from disk once at construction (in-memory match after)."""
        if not self.autoload_packs:
            return
        if self.packs_loaded:
            return
        auto = self.pack_dir or resolve_label_packs_dir()
        if auto is not None:
            self.load_packs_from_dir(auto)

    def load_packs_from_dir(self, directory: str | Path) -> int:
        """Load all ``*.json`` cluster packs (skips ``index.json``). Returns count."""
        root = Path(directory)
        if not root.is_dir():
            logger.warning("label packs dir missing: %s — using empty/minimal", root)
            return 0
        self.pack_dir = root.resolve()
        count = 0
        for path in sorted(root.glob("*.json")):
            if path.name == "index.json":
                continue
            payload = load_cluster_pack_file(path)
            if payload is None:
                continue
            entities, addresses, status = cluster_pack_to_maps(payload)
            for k, v in entities.items():
                self.entity_pack[k] = v
            for addr, key in addresses.items():
                self.address_pack[addr] = key
                # case-insensitive mirror for bech32 / mixed legacy
                low = addr.lower()
                if low != addr:
                    self.address_pack[low] = key
            if status and payload.get("id"):
                self.entity_status[str(payload["id"]).strip().lower()] = status
            self.packs_loaded.append(path.name)
            count += 1
        return count

    def load_pack_dict(self, payload: dict[str, Any]) -> None:
        """Merge an in-memory JSON-like pack (entities / addresses or cluster)."""
        entities, addresses, status = cluster_pack_to_maps(payload)
        # Also support explicit entity_pack / address_pack keys (tests).
        more_e = payload.get("entities") or payload.get("entity_pack") or {}
        more_a = payload.get("addresses") or payload.get("address_pack") or {}
        if isinstance(more_e, dict):
            for k, v in more_e.items():
                self.entity_pack[str(k).strip().lower()] = str(v)
        if isinstance(more_a, dict):
            for k, v in more_a.items():
                self.address_pack[str(k).strip()] = str(v).strip().lower()
        for k, v in entities.items():
            self.entity_pack[k] = v
        for addr, key in addresses.items():
            self.address_pack[addr] = key
        if status and payload.get("id"):
            self.entity_status[str(payload["id"]).strip().lower()] = status

    def load_pack_json(self, text: str) -> None:
        self.load_pack_dict(json.loads(text))

    def lookup_entity(self, key: str) -> str | None:
        return self.entity_pack.get(key.strip().lower())

    def lookup_address(self, address: str) -> tuple[str, str] | None:
        """Return (entity_key, display_label) if address is in the pack."""
        key = self.address_pack.get(address.strip())
        if not key:
            # case-insensitive fallback
            key = self.address_pack.get(address.strip().lower())
        if not key:
            return None
        label = self.lookup_entity(key) or key
        return key, label

    def status_for_entity(self, entity_key: str | None) -> str:
        if entity_key:
            hit = self.entity_status.get(entity_key.strip().lower())
            if hit:
                return hit
        return STATUS_PROOF_MISSING_SOURCE_UNREACHABLE

    def assign(
        self,
        target_type: str,
        target_id: str,
        label: str | None = None,
        *,
        entity_key: str | None = None,
        status: str | None = None,
        cost_basis: float | None = None,
    ) -> LabelAssignment:
        resolved = label
        key = entity_key
        if resolved is None and key:
            resolved = self.lookup_entity(key)
        if not resolved:
            resolved = "Unbekannt"
        # Critical: never invent cost basis 0 — only accept explicit non-None
        # values supplied by the caller. Silent 0 is forbidden.
        if cost_basis is not None and cost_basis == 0:
            # Treat accidental 0 as missing — force None + evidence-gap status.
            cost_basis = None
            status = status or STATUS_PROOF_MISSING_SOURCE_UNREACHABLE
        return LabelAssignment(
            target_type=target_type,
            target_id=target_id,
            label=resolved,
            status=status or self.status_for_entity(key),
            entity_key=key,
            cost_basis=cost_basis,  # None unless explicitly provided (>0 path)
        )

    def label_address(self, address: str) -> LabelAssignment | None:
        hit = self.lookup_address(address)
        if not hit:
            return None
        key, display = hit
        return self.assign(
            "address",
            address,
            label=display,
            entity_key=key,
            status=self.status_for_entity(key),
        )

    def apply_to_addresses(
        self, addresses: Iterable[str]
    ) -> list[LabelAssignment]:
        out: list[LabelAssignment] = []
        for addr in addresses:
            assignment = self.label_address(addr)
            if assignment is not None:
                out.append(assignment)
        return out

    def apply_to_flows(self, flows: Iterable[Flow]) -> list[LabelAssignment]:
        """Label flow addresses / counterparties when they match the pack.

        Does **not** mutate cost basis. Attaches evidence-gap status only.
        """
        seen: set[str] = set()
        out: list[LabelAssignment] = []
        for f in flows:
            if f.address in seen:
                continue
            assignment = self.label_address(f.address)
            if assignment is None:
                continue
            seen.add(f.address)
            # Also record a flow-scoped assignment for report rows.
            out.append(assignment)
            out.append(
                self.assign(
                    "flow",
                    f"{f.txid}:{f.address}:{f.direction}",
                    label=assignment.label,
                    entity_key=assignment.entity_key,
                    status=assignment.status,
                )
            )
        return out

    def apply_pack_stub(self, targets: list[tuple[str, str]]) -> list[LabelAssignment]:
        """Backward-compatible helper: assign Unbekannt / evidence-gap to pairs."""
        return [self.assign(t, i) for t, i in targets]
