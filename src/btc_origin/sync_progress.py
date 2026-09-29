"""Thread-safe in-memory Electrum sync progress (session RAM only)."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class SyncProgressSnapshot:
    """Immutable-ish snapshot returned to API / callbacks."""

    running: bool = False
    phase: str = "idle"
    tx_done: int = 0
    tx_total: int = 0
    addresses_done: int = 0
    addresses_total: int = 0
    message: str = ""
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def german_line(self) -> str:
        """Primary user-facing progress line (German UI)."""
        if self.message:
            return self.message
        if self.tx_total > 0 or self.tx_done > 0:
            return f"{self.tx_done} / {self.tx_total} Tx verarbeitet"
        if self.addresses_total > 0:
            return f"Adressen {self.addresses_done}/{self.addresses_total} …"
        if self.phase == "connecting":
            return "Electrum …"
        if self.phase == "deriving":
            return "Adressen ableiten …"
        if self.phase == "enrich":
            return "Anreichern …"
        if self.phase == "done":
            return "Sync fertig"
        if self.phase == "error":
            return "Sync-Fehler"
        if self.running:
            return "Electrum …"
        return ""


class SyncProgressTracker:
    """Process-lifetime progress store with a lock for concurrent GET during POST."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = SyncProgressSnapshot()

    def reset_for_run(self) -> None:
        with self._lock:
            self._snap = SyncProgressSnapshot(
                running=True,
                phase="connecting",
                message="Electrum …",
                updated_at=_now_iso(),
            )

    def finish(self, *, phase: str = "done", message: str = "") -> None:
        with self._lock:
            self._snap.running = False
            self._snap.phase = phase
            if message:
                self._snap.message = message
            elif phase == "done" and (
                self._snap.tx_total > 0 or self._snap.tx_done > 0
            ):
                self._snap.message = (
                    f"{self._snap.tx_done} / {self._snap.tx_total} Tx verarbeitet"
                )
            elif phase == "done":
                self._snap.message = "Sync fertig"
            elif phase == "error" and not self._snap.message:
                self._snap.message = "Sync-Fehler"
            self._snap.updated_at = _now_iso()

    def clear(self) -> None:
        with self._lock:
            self._snap = SyncProgressSnapshot()

    def update(
        self,
        *,
        phase: str | None = None,
        tx_done: int | None = None,
        tx_total: int | None = None,
        addresses_done: int | None = None,
        addresses_total: int | None = None,
        message: str | None = None,
        running: bool | None = None,
        auto_message: bool = True,
    ) -> SyncProgressSnapshot:
        with self._lock:
            if running is not None:
                self._snap.running = running
            if phase is not None:
                self._snap.phase = phase
            if tx_done is not None:
                self._snap.tx_done = int(tx_done)
            if tx_total is not None:
                self._snap.tx_total = int(tx_total)
            if addresses_done is not None:
                self._snap.addresses_done = int(addresses_done)
            if addresses_total is not None:
                self._snap.addresses_total = int(addresses_total)
            if message is not None:
                self._snap.message = message
            elif auto_message:
                self._snap.message = self._default_message_locked()
            self._snap.updated_at = _now_iso()
            return SyncProgressSnapshot(**asdict(self._snap))

    def snapshot(self) -> SyncProgressSnapshot:
        with self._lock:
            return SyncProgressSnapshot(**asdict(self._snap))

    def as_dict(self) -> dict[str, Any]:
        return self.snapshot().as_dict()

    def _default_message_locked(self) -> str:
        s = self._snap
        if s.tx_total > 0 or s.tx_done > 0:
            return f"{s.tx_done} / {s.tx_total} Tx verarbeitet"
        if s.addresses_total > 0:
            return f"Adressen {s.addresses_done}/{s.addresses_total} …"
        if s.phase == "connecting":
            return "Electrum …"
        if s.phase == "deriving":
            return "Adressen ableiten …"
        if s.phase == "enrich":
            return "Anreichern …"
        if s.phase == "done":
            return "Sync fertig"
        if s.phase == "error":
            return "Sync-Fehler"
        if s.running:
            return "Electrum …"
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
