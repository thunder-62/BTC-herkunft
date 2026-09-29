"""Spurensuche für ungeklärte Gegenstellen (``ext-NNN``) — heuristisch.

Verfolgt die öffentlichen Bewegungen einer fremden Gegenstelle ein paar Schritte
weiter, um zu erkennen, wem sie vermutlich gehört:

* **vorwärts** (Abflüsse an die Gegenstelle): Wohin wurden die Bitcoin von dort
  weitergeschickt?
* **rückwärts** (Zuflüsse von der Gegenstelle): Woher hatte der Absender sie?

Hinweise, von stark nach schwach:

1. eine Adresse unterwegs steht in einem öffentlichen Label-Pack (z. B. Binance);
2. die Bitcoin gehen in eine eigene Wallet (→ evtl. eigene, nicht erfasste Wallet);
3. börsentypische Muster: Konsolidierung vieler Einzahlungen, Sammelauszahlung
   an viele Empfänger, sehr häufig genutzte Adresse;
4. die Bitcoin liegen unbewegt auf einer wenig genutzten Adresse (private Wallet).

Nur Lesezugriffe über denselben Electrum-Server wie beim Sync; nichts wird
gespeichert. Ergebnis ist ein Hinweis, kein Beleg.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

MAX_ADDRESSES = 5  # Adressen je Gegenstelle
MAX_TXS_PER_ADDRESS = 25  # jüngste Transaktionen je Adresse
MAX_FRONTIER = 3  # verfolgte Adressen je Schritt
MAX_HOPS = 3
CONSOLIDATION_INPUTS = 10  # ab so vielen Eingängen: Konsolidierung
BATCH_OUTPUTS = 10  # ab so vielen Ausgängen: Sammelauszahlung
BUSY_ADDRESS_TXS = 100  # ab so vielen Transaktionen: stark genutzte Adresse
QUIET_ADDRESS_TXS = 5  # höchstens so viele: kaum genutzt


class ChainProvider(Protocol):
    def history(self, address: str) -> list[tuple[str, int | None]]: ...
    def tx_hex(self, txid: str) -> str: ...
    def block_time(self, height: int | None) -> str | None: ...


@dataclass
class TxInfo:
    txid: str
    vin: list[tuple[str, int]]
    vout: list[tuple[str | None, int]]


def parse_tx(txid: str, tx_hex: str) -> TxInfo:
    from embit.networks import NETWORKS
    from embit.transaction import Transaction

    tx = Transaction.from_string(tx_hex)
    vin = [(bytes(i.txid).hex(), int(i.vout)) for i in tx.vin]
    vout: list[tuple[str | None, int]] = []
    for o in tx.vout:
        try:
            addr = o.script_pubkey.address(NETWORKS["main"])
        except Exception:  # noqa: BLE001 — OP_RETURN, exotische Skripte
            addr = None
        vout.append((str(addr) if addr else None, int(o.value)))
    return TxInfo(txid=txid, vin=vin, vout=vout)


@dataclass
class Evidence:
    kind: str  # label | own | exchange | private
    hop: int
    text: str
    name: str | None = None


@dataclass
class TrailResult:
    name: str
    direction: str  # out | in | both
    guess: str | None = None
    confidence: str = "keine"  # hoch | mittel | niedrig | keine
    reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    checked_addresses: int = 0
    checked_txs: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "guess": self.guess,
            "confidence": self.confidence,
            "reason": self.reason,
            "steps": self.steps,
            "evidence": [e.__dict__ for e in self.evidence],
            "checked": {"addresses": self.checked_addresses, "transactions": self.checked_txs},
            "errors": self.errors,
        }


class TrailSearch:
    def __init__(
        self,
        provider: ChainProvider,
        *,
        label_of: Callable[[str], str | None],
        own: set[str],
        max_hops: int = MAX_HOPS,
    ) -> None:
        self.p = provider
        self.label_of = label_of
        self.own = own
        self.max_hops = max_hops
        self._txs: dict[str, TxInfo] = {}
        self._hist: dict[str, list[tuple[str, int | None]]] = {}
        self._failed: set[str] = set()  # Historie nicht lieferbar → keine Schlüsse

    # --- Kettendaten (zwischengespeichert nur für diesen Lauf) ---------------
    def _history(self, address: str, res: TrailResult, hop: int = 1) -> list[tuple[str, int | None]]:
        if address not in self._hist:
            try:
                self._hist[address] = self.p.history(address)
            except Exception as exc:  # noqa: BLE001 — Server-Grenze/Netz: weiter mit dem Rest
                self._hist[address] = []
                self._failed.add(address)
                if "too many" in str(exc).lower():
                    res.evidence.append(
                        Evidence(
                            "exchange", hop,
                            f"Schritt {hop}: Adresse mit so vielen Transaktionen, dass der Server die "
                            "Historie nicht liefert (typisch Börse oder Zahlungsdienst)",
                        )
                    )
                    res.steps.append(
                        {"hop": hop, "address": address, "txid": None, "time": None,
                         "text": "extrem viele Transaktionen — Historie vom Server nicht lieferbar"}
                    )
                else:
                    res.errors.append(f"{address[:12]}…: {exc}")
            res.checked_addresses += 1
        return self._hist[address]

    def _tx(self, txid: str, res: TrailResult) -> TxInfo:
        if txid not in self._txs:
            self._txs[txid] = parse_tx(txid, self.p.tx_hex(txid))
            res.checked_txs += 1
        return self._txs[txid]

    def _note_outputs(self, tx: TxInfo, hop: int, res: TrailResult, skip: set[str]) -> None:
        for addr, _v in tx.vout:
            if not addr or addr in skip:
                continue
            if addr in self.own:
                res.evidence.append(
                    Evidence("own", hop, f"Schritt {hop}: Bitcoin gehen in eine eigene Wallet")
                )
            else:
                label = self.label_of(addr)
                if label:
                    res.evidence.append(
                        Evidence("label", hop, f"Schritt {hop}: Empfänger steht im Label „{label}“", label)
                    )

    def _patterns(self, tx: TxInfo, hop: int, res: TrailResult) -> list[str]:
        notes = []
        if len(tx.vin) >= CONSOLIDATION_INPUTS:
            notes.append(f"Konsolidierung von {len(tx.vin)} Eingängen")
            res.evidence.append(
                Evidence("exchange", hop, f"Schritt {hop}: {len(tx.vin)} Eingänge zusammengeführt (typisch Börse)")
            )
        if len(tx.vout) >= BATCH_OUTPUTS:
            notes.append(f"Sammelauszahlung an {len(tx.vout)} Empfänger")
            res.evidence.append(
                Evidence("exchange", hop, f"Schritt {hop}: Auszahlung an {len(tx.vout)} Empfänger (typisch Börse)")
            )
        return notes

    def _busy(self, address: str, n: int, hop: int, res: TrailResult) -> None:
        if n >= BUSY_ADDRESS_TXS:
            res.evidence.append(
                Evidence("exchange", hop, f"Schritt {hop}: Adresse mit {n} Transaktionen (typisch Börse/Dienst)")
            )

    # --- vorwärts: wohin gingen die Bitcoin von der Gegenstelle? -------------
    def forward(self, addresses: list[str], res: TrailResult) -> None:
        frontier = list(dict.fromkeys(addresses))[:MAX_ADDRESSES]
        seen: set[str] = set(frontier)
        for hop in range(1, self.max_hops + 1):
            nxt: list[tuple[int, str]] = []
            for addr in frontier:
                hist = self._history(addr, res, hop)
                self._busy(addr, len(hist), hop, res)
                recent = hist[-MAX_TXS_PER_ADDRESS:]
                txs = [self._tx(t, res) for t, _h in recent]
                heights = dict(recent)
                mine = {(t.txid, i) for t in txs for i, (a, _v) in enumerate(t.vout) if a == addr}
                spends = [t for t in txs if any(p in mine for p in t.vin)]
                if addr in self._failed:
                    continue
                if not spends:
                    res.steps.append(
                        {"hop": hop, "address": addr, "txid": None, "time": None,
                         "text": "Bitcoin liegen noch auf dieser Adresse (nicht weitergeleitet)"}
                    )
                    if hop == 1 and len(hist) <= QUIET_ADDRESS_TXS:
                        res.evidence.append(
                            Evidence("private", hop, "Bitcoin liegen unbewegt auf einer kaum genutzten Adresse")
                        )
                    continue
                for t in spends[-MAX_FRONTIER:]:
                    notes = self._patterns(t, hop, res)
                    self._note_outputs(t, hop, res, skip={addr})
                    outs = sorted(
                        ((a, v) for a, v in t.vout if a and a != addr), key=lambda x: -x[1]
                    )
                    res.steps.append(
                        {
                            "hop": hop,
                            "address": addr,
                            "txid": t.txid,
                            "time": self.p.block_time(heights.get(t.txid)),
                            "text": "weitergeleitet an "
                            + ", ".join(self._describe(a) for a, _v in outs[:3])
                            + (f" (+{len(outs) - 3} weitere)" if len(outs) > 3 else "")
                            + (f" — {'; '.join(notes)}" if notes else ""),
                        }
                    )
                    for a, v in outs[:2]:
                        if a not in seen and a not in self.own:
                            nxt.append((v, a))
            if self._decided(res):
                break
            nxt.sort(key=lambda x: -x[0])
            frontier = []
            for _v, a in nxt:
                if a not in seen:
                    seen.add(a)
                    frontier.append(a)
                if len(frontier) >= MAX_FRONTIER:
                    break
            if not frontier:
                break

    # --- rückwärts: woher hatte der Absender die Bitcoin? --------------------
    def backward(self, addresses: list[str], res: TrailResult) -> None:
        frontier = list(dict.fromkeys(addresses))[:MAX_ADDRESSES]
        seen: set[str] = set(frontier)
        for hop in range(1, self.max_hops + 1):
            nxt: list[tuple[int, str]] = []
            for addr in frontier:
                hist = self._history(addr, res, hop)
                self._busy(addr, len(hist), hop, res)
                recent = hist[-MAX_TXS_PER_ADDRESS:]
                heights = dict(recent)
                funding = [
                    t for t in (self._tx(x, res) for x, _h in recent)
                    if any(a == addr for a, _v in t.vout)
                ]
                for t in funding[-MAX_FRONTIER:]:
                    notes = self._patterns(t, hop, res)
                    senders: list[tuple[str, int]] = []
                    for prev_txid, vout in t.vin[:MAX_FRONTIER]:
                        if prev_txid == "0" * 64:
                            continue
                        prev = self._tx(prev_txid, res)
                        if vout < len(prev.vout) and prev.vout[vout][0]:
                            senders.append((prev.vout[vout][0], prev.vout[vout][1]))
                    for a, _v in senders:
                        if a in self.own:
                            res.evidence.append(
                                Evidence("own", hop, f"Schritt {hop}: Bitcoin kamen aus einer eigenen Wallet")
                            )
                        else:
                            label = self.label_of(a)
                            if label:
                                res.evidence.append(
                                    Evidence("label", hop, f"Schritt {hop}: Absender steht im Label „{label}“", label)
                                )
                    res.steps.append(
                        {
                            "hop": hop,
                            "address": addr,
                            "txid": t.txid,
                            "time": self.p.block_time(heights.get(t.txid)),
                            "text": "erhalten von "
                            + (", ".join(self._describe(a) for a, _v in senders) or "unbekannt")
                            + (f" — {'; '.join(notes)}" if notes else ""),
                        }
                    )
                    for a, v in senders:
                        if a not in seen and a not in self.own:
                            nxt.append((v, a))
            if self._decided(res):
                break
            nxt.sort(key=lambda x: -x[0])
            frontier = []
            for _v, a in nxt:
                if a not in seen:
                    seen.add(a)
                    frontier.append(a)
                if len(frontier) >= MAX_FRONTIER:
                    break
            if not frontier:
                break

    # --- rückwärts über Transaktionen (ohne Adress-Historie) ------------------
    def backward_txs(self, txids: list[str], res: TrailResult) -> None:
        """Von den eigenen Zufluss-Transaktionen Schritt für Schritt zu den
        Vorgänger-Transaktionen — braucht keine Adress-Historie (funktioniert
        auch bei Börsen-Adressen mit riesiger Historie)."""
        frontier = list(dict.fromkeys(txids))[:MAX_FRONTIER]
        seen: set[str] = set(frontier)
        for hop in range(1, self.max_hops + 1):
            nxt: list[tuple[int, str]] = []
            for txid in frontier:
                t = self._tx(txid, res)
                notes = self._patterns(t, hop, res)
                senders: list[tuple[str, int, str]] = []
                for prev_txid, vout in t.vin[:MAX_FRONTIER * 2]:
                    if prev_txid == "0" * 64:
                        continue
                    prev = self._tx(prev_txid, res)
                    if vout < len(prev.vout) and prev.vout[vout][0]:
                        senders.append((prev.vout[vout][0], prev.vout[vout][1], prev_txid))
                for a, _v, _p in senders:
                    if a in self.own and hop > 1:
                        res.evidence.append(
                            Evidence("own", hop, f"Schritt {hop}: Bitcoin kamen aus einer eigenen Wallet")
                        )
                    elif a not in self.own:
                        label = self.label_of(a)
                        if label:
                            res.evidence.append(
                                Evidence("label", hop, f"Schritt {hop}: Absender steht im Label „{label}“", label)
                            )
                uniq = list(dict.fromkeys(a for a, _v, _p in senders))
                res.steps.append(
                    {
                        "hop": hop,
                        "address": uniq[0] if uniq else "",
                        "txid": t.txid,
                        "time": None,
                        "text": f"Transaktion mit {len(t.vin)} Eingängen und {len(t.vout)} Ausgängen; Absender "
                        + (", ".join(self._describe(a) for a in uniq[:3]) or "unbekannt")
                        + (f" (+{len(uniq) - 3} weitere)" if len(uniq) > 3 else "")
                        + (f" — {'; '.join(notes)}" if notes else ""),
                    }
                )
                for a, v, p in senders:
                    if p not in seen and a not in self.own:
                        nxt.append((v, p))
            if self._decided(res):
                break
            nxt.sort(key=lambda x: -x[0])
            frontier = []
            for _v, p in nxt:
                if p not in seen:
                    seen.add(p)
                    frontier.append(p)
                if len(frontier) >= MAX_FRONTIER:
                    break
            if not frontier:
                break

    def _describe(self, address: str) -> str:
        if address in self.own:
            return "eigene Wallet"
        label = self.label_of(address)
        return f"{label} (Label)" if label else address

    @staticmethod
    def _decided(res: TrailResult) -> bool:
        return any(e.kind in ("label", "own") for e in res.evidence)


def conclude(res: TrailResult) -> None:
    """Vermutung aus den Hinweisen: Label > eigene Wallet > Börsen-Muster > privat."""
    labels = [e for e in res.evidence if e.kind == "label"]
    if labels:
        best = min(labels, key=lambda e: e.hop)
        res.guess = best.name
        res.confidence = "hoch" if best.hop == 1 else "mittel"
        res.reason = best.text
        return
    own = [e for e in res.evidence if e.kind == "own"]
    if own:
        res.guess = "eigene, nicht erfasste Wallet?"
        res.confidence = "mittel"
        res.reason = own[0].text + " — die Gegenstelle ist womöglich eine eigene Wallet, die hier fehlt."
        return
    exch = [e for e in res.evidence if e.kind == "exchange"]
    if exch:
        res.guess = "Börse oder Zahlungsdienst (Name unbekannt)"
        res.confidence = "mittel" if len(exch) > 1 else "niedrig"
        res.reason = "; ".join(e.text for e in exch[:3])
        return
    priv = [e for e in res.evidence if e.kind == "private"]
    if priv:
        res.guess = "private Wallet (keine Börse erkennbar)"
        res.confidence = "niedrig"
        res.reason = priv[0].text + " — evtl. eine eigene, nicht erfasste Wallet oder eine Privatperson."
        return
    res.guess = None
    res.confidence = "keine"
    res.reason = "Keine Zuordnung gefunden."


def search(
    provider: ChainProvider,
    *,
    name: str,
    out_addresses: list[str],
    in_addresses: list[str],
    label_of: Callable[[str], str | None],
    own: set[str],
    max_hops: int = MAX_HOPS,
    in_txids: list[str] | None = None,
) -> TrailResult:
    direction = "both" if out_addresses and in_addresses else "out" if out_addresses else "in"
    res = TrailResult(name=name, direction=direction)
    t = TrailSearch(provider, label_of=label_of, own=own, max_hops=max_hops)
    try:
        if out_addresses:
            t.forward(out_addresses, res)
        if in_txids and not TrailSearch._decided(res):
            t.backward_txs(in_txids, res)  # ohne Adress-Historie
        elif in_addresses and not TrailSearch._decided(res):
            t.backward(in_addresses, res)
    except Exception as exc:  # noqa: BLE001 — Netz/Server: Teilergebnis zeigen
        res.errors.append(str(exc))
    conclude(res)
    return res
