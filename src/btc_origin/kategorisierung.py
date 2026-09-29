"""Export-Zeilen einordnen: Art laut Export → Kategorie aus ``kategorien.yaml``.

REGELWERK.md Abschnitt 4: Jede Art einer Börse braucht eine Zuordnung. Nicht unterstützt —
kein steuerlicher Wert, keine Summe, Ausweis unter den offenen Punkten — ist eine Zeile,

* deren Art der Parser nicht kennt oder die in ``zuordnung_export`` fehlt,
* deren Kategorie ``behandlung: nicht_unterstuetzt`` hat (z. B. mining),
* deren Zuordnung nicht zur Richtung laut Export passt (``kauf`` für einen Verkauf …),
* die einen anderen Kryptowert als BTC auf einer Seite hat (Tausch).

Die Mengenabstimmung der Wallets bleibt unberührt (Blockchain-Mengen zählen weiter).
Zeilen mit ``behandlung: einkunft_22_3`` werden zur Anschaffung zum Tageskurs (Betrag laut
Export entfällt) und als Einkunft nach § 22 Nr. 3 EStG ausgewiesen — auch wenn der Parser die
Art nicht kennt, denn eine Einkunft ist immer ein Zufluss.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from typing import Any

from btc_origin.exchanges.common import ExchangeTrade
from btc_origin.regelwerk import Kategorien, Quittung

# technische Arten und die Richtung laut Export, zu der sie passen
_TECHNISCH = {"verkauf": ("sell",), "auszahlung": ("withdraw",), "einzahlung": ("deposit",)}
# Nicht unterstützte Kategorien, die nur in einer Richtung vorkommen (Auswahl in der Oberfläche)
_NUR_ZUGANG = frozenset({"mining", "staking", "airdrop", "fork"})  # Lending: Verleihen ist ein Abgang
_NUR_ABGANG: frozenset[str] = frozenset()
_KIND_DE = {"buy": "Kauf", "sell": "Verkauf", "withdraw": "Auszahlung", "deposit": "Einzahlung",
            "unknown": "unbekannt"}
_FIAT = ("", "EUR", "USD", "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "KARTE")
STATUS_UNSUPPORTED = "nicht unterstützt – manuell prüfen"


@dataclass(frozen=True)
class NichtUnterstuetzt:
    """Export-Zeile ohne steuerlichen Wert (offene Punkte, Seite 1, Prüfprotokoll)."""

    exchange: str
    day: date
    art: str
    richtung: str  # Kauf | Verkauf | Auszahlung | Einzahlung | unbekannt
    sats: int
    grund: str
    file: str = ""
    status: str = STATUS_UNSUPPORTED
    ref: str = ""  # Vorgangs-ID laut Export

    def as_dict(self) -> dict[str, Any]:
        return {"exchange": self.exchange, "day": self.day.isoformat(), "art": self.art,
                "richtung": self.richtung, "sats": self.sats, "grund": self.grund, "file": self.file,
                "status": self.status, "ref": self.ref, "tnr": "", "quittiert": False, "erlaeuterung": ""}


def _is_swap(t: ExchangeTrade) -> bool:
    return t.kind in ("buy", "sell") and t.eur is None and t.usd is None \
        and str(t.quote or "").upper() not in _FIAT


def categorize_trades(
    trades: list[ExchangeTrade], kat: Kategorien
) -> tuple[list[ExchangeTrade], list[NichtUnterstuetzt]]:
    """(bewertbare Zeilen mit ``kategorie``, nicht unterstützte Zeilen)."""
    ok: list[ExchangeTrade] = []
    bad: list[NichtUnterstuetzt] = []

    def reject(t: ExchangeTrade, grund: str) -> None:
        bad.append(NichtUnterstuetzt(t.exchange, t.day, t.art, _KIND_DE.get(t.kind, t.kind), t.sats, grund, t.file,
                                     ref=t.ref))

    for t in trades:
        if t.kind == "unknown":
            mapped = kat.kategorien.get(kat.export_kategorie(t.exchange, t.art) or "")
            if mapped is not None and mapped.behandlung == "einkunft_22_3":
                # Einkunft ist immer ein Zufluss — die Richtung ergibt sich aus der Kategorie
                ok.append(dataclasses.replace(t, kind="buy", eur=None, usd=None, fee_eur=None, quote="",
                                              kategorie=mapped.name, einkunft=True))
                continue
            if mapped is not None and mapped.behandlung == "nicht_unterstuetzt":
                reject(t, f"Kategorie „{mapped.anzeigename}“" + (f" ({mapped.beschreibung})" if mapped.beschreibung else ""))
            else:  # Richtung unbekannt — auch eine Zuordnung macht die Zeile nicht bewertbar
                reject(t, f"Art „{t.art or '(leer)'}“ vom Parser nicht erkannt (Richtung unbekannt)")
            continue
        if _is_swap(t):
            reject(t, f"Tausch gegen {t.quote} (anderer Kryptowert)")
            continue
        name = kat.export_kategorie(t.exchange, t.art)
        if name is None:
            reject(t, f"Art „{t.art or '(leer)'}“ für {t.exchange} nicht in kategorien.yaml zugeordnet")
            continue
        if name in _TECHNISCH:
            if t.kind not in _TECHNISCH[name]:
                reject(t, f"Zuordnung „{name}“ passt nicht zur Richtung laut Export ({_KIND_DE[t.kind]})")
                continue
            ok.append(dataclasses.replace(t, kategorie=name))
            continue
        k = kat.kategorien[name]
        if k.behandlung == "nicht_unterstuetzt":
            reject(t, f"Kategorie „{k.anzeigename}“" + (f" ({k.beschreibung})" if k.beschreibung else ""))
            continue
        if k.behandlung == "anschaffung":
            if t.kind != "buy":
                reject(t, f"Zuordnung „{name}“ passt nicht zur Richtung laut Export ({_KIND_DE[t.kind]})")
                continue
            ok.append(dataclasses.replace(t, kategorie=name))
            continue
        # einkunft_22_3: Zufluss auf der Börse → Anschaffung zum Tageskurs (kein Kaufpreis)
        if t.kind not in ("buy", "deposit"):
            reject(t, f"Zuordnung „{name}“ passt nicht zur Richtung laut Export ({_KIND_DE[t.kind]})")
            continue
        ok.append(dataclasses.replace(t, kind="buy", eur=None, usd=None, fee_eur=None, quote="",
                                      kategorie=name, einkunft=True))
    return ok, bad


def passende_kategorien(richtungen: set[str], kat: Kategorien) -> list[str]:
    """Bewertbare Zuordnungen, die zur Richtung laut Export passen (dieselben Regeln wie in
    ``categorize_trades``), in der Reihenfolge Kauf, Verkauf, Einkünfte, Ein-/Auszahlung.
    ``richtungen``: deutsche Bezeichnungen (Kauf, Verkauf, Auszahlung, Einzahlung, unbekannt).
    Danach die nicht unterstützten Kategorien, die zu Zugang bzw. Abgang passen (sie
    kennzeichnen nur; eine neue Kategorie im Regelwerk gilt für beide Richtungen)."""
    kinds = {k for k, de in _KIND_DE.items() if de in richtungen}
    by = {b: [k.name for k in kat.kategorien.values() if k.behandlung == b]
          for b in ("anschaffung", "einkunft_22_3")}
    out: list[str] = []
    if "buy" in kinds:
        out += by["anschaffung"]
    if "sell" in kinds:
        out.append("verkauf")
    if kinds & {"buy", "deposit", "unknown"}:
        out += by["einkunft_22_3"]
    if "deposit" in kinds:
        out.append("einzahlung")
    if "withdraw" in kinds:
        out.append("auszahlung")
    zugang, abgang = bool(kinds & {"buy", "deposit", "unknown"}), bool(kinds & {"sell", "withdraw", "unknown"})
    for k in kat.kategorien.values():
        if k.behandlung == "nicht_unterstuetzt" and (
            (zugang and k.name not in _NUR_ABGANG) or (abgang and k.name not in _NUR_ZUGANG)
        ):
            out.append(k.name)
    return out


def art_overview(trades: list[ExchangeTrade], kat: Kategorien) -> list[dict[str, Any]]:
    """Je Börse und Art: Anzahl, Richtung laut Export, Zuordnung und Status, dazu je Jahr die
    Anzahl und das erste Vorkommen (Oberfläche: Auswahl nach Jahr, Sortierung nach Zeitpunkt).
    Ohne Beträge. Sortiert nach dem ersten Vorkommen."""
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    ok, bad = categorize_trades(trades, kat)

    def entry(exchange: str, art: str, **init: Any) -> dict[str, Any]:
        return acc.setdefault((exchange, art), {"exchange": exchange, "art": art, "count": 0, "richtung": set(),
                                                "jahre": {}, "erste_je_jahr": {}, **init})

    def add(e: dict[str, Any], day: date, richtung: str) -> None:
        e["count"] += 1
        e["richtung"].add(richtung)
        y = str(day.year)
        e["jahre"][y] = e["jahre"].get(y, 0) + 1
        first = e["erste_je_jahr"].get(y)
        e["erste_je_jahr"][y] = min(first, day.isoformat()) if first else day.isoformat()

    for t in ok:
        add(entry(t.exchange, t.art, zuordnung=t.kategorie, status="ok", grund=""), t.day, _KIND_DE[t.kind])
    for b in bad:
        e = entry(b.exchange, b.art, zuordnung=kat.export_kategorie(b.exchange, b.art) or "",
                  status="nicht_unterstuetzt", grund=b.grund)
        add(e, b.day, b.richtung)
        e["status"], e["grund"] = "nicht_unterstuetzt", b.grund
    out = []
    for e in acc.values():
        out.append({**e, "richtung": ", ".join(sorted(e["richtung"])),
                    "passend": passende_kategorien(e["richtung"], kat),
                    "erste": min(e["erste_je_jahr"].values())})
    return sorted(out, key=lambda e: (e["erste"], e["exchange"].lower(), e["art"]))


STATUS_ACKED = "nicht unterstützt – geprüft (quittiert)"


def apply_acks(unsupported: list[dict[str, Any]], kat: Kategorien) -> list[Quittung]:
    """Quittungen (geprueft_nicht_unterstuetzt) auf die nicht unterstützten Vorgänge anwenden:
    gefundene werden „quittiert“ (nur Warnung) und tragen die Erläuterung. Rückgabe: Quittungen
    ohne passenden Vorgang (Hinweis in der Kontrolldatei)."""
    used: set[int] = set()
    for u in unsupported:
        for i, q in enumerate(kat.quittungen):
            if q.matches(u):
                u["quittiert"], u["erlaeuterung"], u["status"] = True, q.erlaeuterung, STATUS_ACKED
                used.add(i)
                break
    return [q for i, q in enumerate(kat.quittungen) if i not in used]


def treatment_changes(trades: list[ExchangeTrade], kat: Kategorien) -> list[dict[str, Any]]:
    """Export-Zeilen, deren Behandlung sich durch die Einordnung nach kategorien.yaml ändert
    (Prüfung vor dem Merge, nur lokal): bisher bewertete der Parser jede erkannte Zeile nach
    ihrer Richtung und verwarf unbekannte Arten. ``wirkt_auf_werte`` = die Zeile wurde bisher
    bewertet und ist jetzt nicht unterstützt oder eine Einkunft (Tageskurs statt Betrag)."""
    out: list[dict[str, Any]] = []
    for t in trades:
        ok, bad = categorize_trades([t], kat)
        before = "verworfen (Art unbekannt)" if t.kind == "unknown" else f"{_KIND_DE[t.kind]} (bewertet)"
        if bad:
            after = f"nicht unterstützt: {bad[0].grund}"
        elif ok[0].einkunft:
            after = f"Einkunft § 22 Nr. 3 ({ok[0].kategorie}), Anschaffung zum Tageskurs"
        else:
            continue  # unverändert: gleiche Richtung, gleiche Bewertung
        out.append({"exchange": t.exchange, "art": t.art, "day": t.day.isoformat(), "sats": t.sats,
                    "file": t.file, "bisher": before, "neu": after,
                    "wirkt_auf_werte": t.kind != "unknown"})
    return sorted(out, key=lambda r: (r["exchange"].lower(), r["day"], r["art"]))
