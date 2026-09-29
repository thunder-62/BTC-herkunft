"""Abgleich mit Portfolio Performance (``local/check-pp.csv``) — nur Browser.

Hilfe zur Bereinigung des PP-Depots: Bitcoin-Buchungen aus einem PP-Export
(Wertpapier-Umsätze, deutsch oder englisch) werden mit den Zu- und Abflüssen
der Wallet-Cloud verglichen — Bestand, je Jahr und je Buchung. Nie im
Finanzamt-Bericht, nie auf Platte.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

from btc_origin.cloud_summary import _truthy_internal
from btc_origin.local_files import _amount_to_sats, _parse_decimal, _row_dates

PP_FILE = "check-pp.csv"
MATCH_DAYS = 7
TOL_SATS = 100_000  # 0.001 BTC (Auszahlungs-/Netzwerkgebühr)
TOL_RATIO = 0.03

_OUT = ("auslieferung", "outbound", "verkauf", "sell", "sale")
_IN = ("einlieferung", "inbound", "kauf", "buy", "purchase")
_SKIP = ("umbuchung", "transfer")
_BTC = ("bitcoin", "btc", "xbt")
# Wertpapiere, die „Bitcoin“ im Namen tragen, aber keine Bitcoin sind (Stück ≠ BTC):
# börsengehandelte Produkte, Fonds, Aktien von Bitcoin-Firmen, Zertifikate.
_NOT_COIN = ("etp", "etf", "etn", "etc", "trust", "tracker", "fund", "fonds", "shares", "physical",
             "index", "zertifikat", "certificate", "mining", "miner", "strategy", "inc", "ag", "plc",
             "ishares", "21shares", "wisdomtree", "coinshares", "vaneck", "fidelity", "invesco", "grayscale")


# Zeilen, die die App selbst per Delta-Import angelegt hat: Korrekturen zu einer
# bestehenden Buchung (keine eigene Bewegung) — werden der Buchung zugerechnet.
_ADJ_RE = re.compile(
    r"^BTC-Herkunft: (Korrektur Stück zu|Auszahlungsgebühr zu|Netzwerkgebühr zu|"
    r"Netzwerkgebühren interner Umbuchungen)"
)


def is_bitcoin_security(name: str) -> bool:
    """„Bitcoin“, „BTC“, „Bitcoin (BTC)“, „BTC-EUR“ — ja; „… Physical Bitcoin ETP“ — nein."""
    n = name.lower()
    if not any(w in n for w in _BTC):
        return False
    return not any(w in re.findall(r"[a-z0-9]+", n) for w in _NOT_COIN)

_COLS = {
    "date": ("datum", "date"),
    "type": ("typ", "type"),
    "shares": ("stück", "stueck", "shares", "quantity", "anzahl"),
    "value": ("wert", "value", "betrag", "amount", "bruttobetrag", "gross amount"),
    "gross": ("bruttobetrag", "gross amount"),
    # Export „Alle Buchungen“: Betrag (ohne Gebühren) und Gesamtpreis (= Wert beim Import)
    "total": ("gesamtpreis", "total"),
    "price": ("kurs", "quote"),
    "fee": ("gebühren", "gebuehren", "fees", "fee"),
    "taxes": ("steuern", "taxes"),
    "time": ("uhrzeit", "time"),
    "security": ("wertpapiername", "wertpapier", "security name", "security", "name", "ticker-symbol", "ticker symbol", "isin"),
    "note": ("notiz", "note"),
}


@dataclass
class PPRow:
    line: int
    day: date
    direction: str  # "in" | "out"
    type: str
    sats: int
    eur: float | None
    fee_eur: float | None
    note: str = ""
    raw: list[str] = field(default_factory=list, repr=False)  # Zellen wie im Export

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "day": self.day.isoformat(),
            "direction": self.direction,
            "type": self.type,
            "sats": self.sats,
            "eur": self.eur,
            "fee_eur": self.fee_eur,
            "note": self.note,
        }


def _roles(header: list[str]) -> dict[str, int]:
    roles: dict[str, int] = {}
    norm = [h.strip().strip('"').lower() for h in header]
    for role, names in _COLS.items():
        for name in names:  # exact names first, in priority order
            if name in norm and role not in roles:
                roles[role] = norm.index(name)
    return roles


def _direction(type_cell: str) -> str | None:
    t = type_cell.strip().lower()
    if any(w in t for w in _SKIP):
        return None
    if any(w in t for w in _OUT):  # before _IN: „Verkauf“ contains „kauf“
        return "out"
    if any(w in t for w in _IN):
        return "in"
    return None


@dataclass
class PPFile:
    header: list[str]
    delim: str
    roles: dict[str, int]
    rows: list[PPRow]
    skipped_transfers: int = 0
    errors: list[str] = field(default_factory=list)
    counted: dict[str, int] = field(default_factory=dict)  # Wertpapier → Zeilen (als Bitcoin)
    ignored: dict[str, int] = field(default_factory=dict)  # „Bitcoin“ im Namen, aber kein Coin


def parse_pp_file(text: str) -> PPFile:
    """PP-CSV → Bitcoin-Zu-/Abgänge (mit Originalzellen für den Re-Import).
    Umbuchungen und andere Wertpapiere entfallen."""
    lines = [ln for ln in (text or "").lstrip("\ufeff").splitlines() if ln.strip()]
    if len(lines) < 2:
        return PPFile([], ";", {}, [], errors=["keine Datenzeilen"])
    delim = max((";", "\t", ","), key=lines[0].count)
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    roles = _roles(rows[0])
    missing = [r for r in ("date", "type", "shares") if r not in roles]
    if missing:
        return PPFile(rows[0], delim, roles, [], errors=[
            "Spalten nicht erkannt ("
            + ", ".join({"date": "Datum", "type": "Typ", "shares": "Stück"}[m] for m in missing)
            + ") — Kopfzeile: "
            + delim.join(rows[0])[:200]
        ])
    out = PPFile(rows[0], delim, roles, [])
    for n, row in enumerate(rows[1:], 2):
        def cell(role: str) -> str:
            i = roles.get(role)
            return row[i] if i is not None and i < len(row) else ""

        sec = cell("security").strip()
        if "security" in roles:
            # mit Wertpapier-Spalte: nur Zeilen des Coins selbst (leer = Kontobuchung)
            if not is_bitcoin_security(sec):
                if sec and any(w in sec.lower() for w in _BTC):
                    out.ignored[sec] = out.ignored.get(sec, 0) + 1
                continue
            out.counted[sec] = out.counted.get(sec, 0) + 1
        direction = _direction(cell("type"))
        if direction is None:
            if any(w in cell("type").lower() for w in _SKIP):
                out.skipped_transfers += 1
            continue
        days = _row_dates(cell("date"))
        sats = _amount_to_sats(cell("shares"))
        if not days or not sats:
            out.errors.append(f"Zeile {n}: Datum oder Stück fehlt")
            continue
        out.rows.append(
            PPRow(
                line=n,
                day=days[0],
                direction=direction,
                type=cell("type").strip(),
                sats=sats,
                eur=_parse_decimal(cell("value")),
                fee_eur=_parse_decimal(cell("fee")),
                note=cell("note").strip()[:120],
                raw=list(row),
            )
        )
    out.rows.sort(key=lambda r: (r.day, r.line))
    return out


def parse_pp_export(text: str) -> tuple[list[PPRow], list[str]]:
    f = parse_pp_file(text)
    return f.rows, f.errors


def internal_fees(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Netzwerkgebühren reiner Umbuchungen (je Transaktion) aus dem Roh-Ledger —
    mindern den Bestand, sind aber weder Zu- noch Abfluss."""
    seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        fee = int(r.get("fee_sats") or 0)
        txid = str(r.get("txid") or "")
        if r.get("direction") != "out" or not _truthy_internal(r.get("is_internal")) or fee <= 0 or not txid:
            continue
        day = str(r.get("block_time") or r.get("lot_date") or "")[:10]
        if day:
            seen.setdefault(
                txid,
                {"txid": txid, "day": day, "direction": "out", "sats": fee, "fee_sats": 0,
                 "wallet": str(r.get("wallet_name") or ""), "counterparty": "interne Umbuchung"},
            )
    return sorted(seen.values(), key=lambda m: (m["day"], m["txid"]))


def chain_movements(shaped: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cloud-Flows (je Lot-Anteil) → eine Bewegung je Transaktion und Richtung."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in shaped:
        direction = r.get("direction")
        txid = str(r.get("txid") or "")
        day = str(r.get("time") or "")[:10]
        if direction not in ("in", "out") or not txid or not day:
            continue
        m = by_key.setdefault(
            (direction, txid),
            {
                "txid": txid,
                "day": day,
                "direction": direction,
                "sats": 0,
                "fee_sats": 0,
                "wallet": r.get("wallet_name") or "",
                "counterparty": "",
            },
        )
        amount = int(r.get("amount_sats") or 0)
        if r.get("kind") == "fee":
            m["fee_sats"] += amount
            continue
        m["sats"] += amount
        name = r.get("external_name") if direction == "out" else r.get("source_name")
        if name and not m["counterparty"]:
            m["counterparty"] = str(name)
    return sorted(by_key.values(), key=lambda m: (m["day"], m["txid"]))


def _tolerance(sats: int) -> int:
    return max(TOL_SATS, int(sats * TOL_RATIO))


FEE_TOL_SATS = 100  # separat gebuchte Gebühr: ± 100 sat bzw. 5 %
FEE_TOL_RATIO = 0.05
AGG_DAYS = 45
LOOSE_DAYS = 30  # PP-Käufe auf der Börse bis zu 45 Tage vor der Auszahlung


def _signed(direction: str, sats: int) -> int:
    return sats if direction == "in" else -sats


def compare(
    pp_rows: list[PPRow],
    chain: list[dict[str, Any]],
    *,
    bestand_sats: int | None = None,
    fees: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """PP ↔ Blockchain: Zuordnung (1:1, Sammelbuchungen n:1 und 1:n) und eine
    kompakte Korrekturliste mit Vorschlag und Wirkung auf den PP-Bestand.

    ``fees`` = Netzwerkgebühren interner Umbuchungen (``internal_fees``); in PP
    separat gebuchte Gebühren (kleine Auslieferungen) werden ihnen bzw. der
    Gebühr eines Abgangs zugeordnet."""
    chain = [c for c in chain if c["sats"]]
    cdays = [date.fromisoformat(c["day"]) for c in chain]
    # eigene Korrekturzeilen (Delta-Import) nicht als Bewegung zuordnen, sondern der
    # Buchung zurechnen, zu der sie gehören (s. u.); übrig gebliebene gelten als „nur in PP“
    adj_free = {i for i, p in enumerate(pp_rows) if _ADJ_RE.match(p.note)}
    used_pp: set[int] = set(adj_free)
    used_chain: set[int] = set()
    matches: list[tuple[list[int], list[int]]] = []

    def expected(js: list[int]) -> int:
        """Wirkung der Blockchain-Bewegungen auf den Bestand (Abgang inkl. Gebühr)."""
        return sum(chain[j]["sats"] + (chain[j]["fee_sats"] if chain[j]["direction"] == "out" else 0) for j in js)

    def fits(pp_sum: int, js: list[int]) -> int | None:
        net = sum(chain[j]["sats"] for j in js)
        if not net or not 0.5 <= pp_sum / net <= 2:  # 0,001 BTC nie bei Kleinstbeträgen
            return None
        diff = min(abs(pp_sum - net), abs(pp_sum - expected(js)))
        return diff if diff <= _tolerance(max(pp_sum, net)) else None

    # 1:1 — kleinste Mengenabweichung zuerst, dann kleinster Tagesabstand
    cands = []
    for i, p in enumerate(pp_rows):
        for j, c in enumerate(chain):
            if c["direction"] != p.direction:
                continue
            days = abs((cdays[j] - p.day).days)
            if days > MATCH_DAYS:
                continue
            diff = fits(p.sats, [j])
            if diff is not None:
                cands.append((diff, days, i, j))
    for _diff, _days, i, j in sorted(cands):
        if i not in used_pp and j not in used_chain:
            used_pp.add(i)
            used_chain.add(j)
            matches.append(([i], [j]))

    # n:1 — mehrere PP-Buchungen (z. B. Käufe auf der Börse) = eine Bewegung
    for j, c in enumerate(chain):
        if j in used_chain:
            continue
        pool = [
            i for i, p in enumerate(pp_rows)
            if i not in used_pp and p.direction == c["direction"]
            and -AGG_DAYS <= (p.day - cdays[j]).days <= MATCH_DAYS
        ]
        best = None
        for a in range(len(pool)):
            total = 0
            for b in range(a, len(pool)):
                total += pp_rows[pool[b]].sats
                if b > a:
                    diff = fits(total, [j])
                    if diff is not None and (best is None or diff < best[0]):
                        best = (diff, pool[a : b + 1])
        if best:
            used_chain.add(j)
            used_pp.update(best[1])
            matches.append((best[1], [j]))

    # 1:n — eine PP-Buchung = mehrere Bewegungen
    for i, p in enumerate(pp_rows):
        if i in used_pp:
            continue
        pool = [
            j for j, c in enumerate(chain)
            if j not in used_chain and c["direction"] == p.direction
            and abs((cdays[j] - p.day).days) <= MATCH_DAYS
        ]
        best = None
        for a in range(len(pool)):
            for b in range(a + 1, len(pool)):
                diff = fits(p.sats, pool[a : b + 1])
                if diff is not None and (best is None or diff < best[0]):
                    best = (diff, pool[a : b + 1])
        if best:
            used_pp.add(i)
            used_chain.update(best[1])
            matches.append(([i], best[1]))

    # lose — übrige PP-Zeile und übrige Bewegung gleicher Richtung, ±30 Tage,
    # Menge höchstens Faktor 4 verschieden: dieselbe Buchung mit falscher Menge
    # bzw. falschem Datum. Die PP-Zeile bleibt (Typ, Wert, Notiz), Menge laut Blockchain.
    loose: set[int] = set()
    cands = []
    for i, p in enumerate(pp_rows):
        if i in used_pp:
            continue
        for j, c in enumerate(chain):
            if j in used_chain or c["direction"] != p.direction:
                continue
            days = abs((cdays[j] - p.day).days)
            if days <= LOOSE_DAYS and 0.25 <= p.sats / c["sats"] <= 4:
                cands.append((days, abs(p.sats - c["sats"]), i, j))
    for _days, _diff, i, j in sorted(cands):
        if i not in used_pp and j not in used_chain:
            used_pp.add(i)
            used_chain.add(j)
            loose.add(len(matches))
            matches.append(([i], [j]))

    def fee_row(sats: int, day: str) -> int | None:
        """Freie PP-Auslieferung (±7 Tage), die genau eine Gebühr bucht."""
        d0 = date.fromisoformat(day)
        best = None
        for i, p in enumerate(pp_rows):
            if i in used_pp or p.direction != "out" or abs((p.day - d0).days) > MATCH_DAYS:
                continue
            diff = abs(p.sats - sats)
            if diff <= max(FEE_TOL_SATS, sats * FEE_TOL_RATIO) and (best is None or diff < best[0]):
                best = (diff, i)
        return best[1] if best else None

    def adjustment(effect: int, days: list[str]) -> list[int] | None:
        """Eigene Korrekturzeilen (±2 Tage um die Bewegung), die zusammen genau die
        fehlende Wirkung auf den Bestand buchen — einzeln, paarweise oder alle."""
        ds = [date.fromisoformat(d) for d in days]
        cand = [i for i in sorted(adj_free) if any(abs((pp_rows[i].day - d).days) <= 2 for d in ds)]
        signed = {i: _signed(pp_rows[i].direction, pp_rows[i].sats) for i in cand}
        for i in cand:
            if signed[i] == effect:
                return [i]
        for a in range(len(cand)):
            for b in range(a + 1, len(cand)):
                if signed[cand[a]] + signed[cand[b]] == effect:
                    return [cand[a], cand[b]]
        return cand if cand and sum(signed.values()) == effect else None

    actions: list[dict[str, Any]] = []
    ok = {"exact": 0, "date": 0, "grouped": 0, "fees": 0, "corrected": 0}
    for m, (is_, js) in enumerate(matches):
        direction = chain[js[0]]["direction"]
        pp_sum = sum(pp_rows[i].sats for i in is_)
        want = expected(js)  # was PP buchen müsste (Abgang inkl. Netzwerkgebühr)
        d = pp_sum - want
        if d == 0:
            if len(is_) > 1 or len(js) > 1:
                ok["grouped"] += 1
            elif pp_rows[is_[0]].day.isoformat() == chain[js[0]]["day"]:
                ok["exact"] += 1
            else:
                ok["date"] += 1
            continue
        adj = adjustment(-d if direction == "in" else d, [chain[j]["day"] for j in js])
        if adj:
            adj_free.difference_update(adj)  # per Delta-Import korrigiert
            ok["corrected"] += 1
            continue
        fee = sum(chain[j]["fee_sats"] for j in js)
        if direction == "out" and -d == fee:
            i = fee_row(fee, chain[js[-1]]["day"])  # Gebühr separat in PP gebucht
            if i is not None:
                used_pp.add(i)
                ok["fees"] += 1
                continue
        lines = ", ".join(str(pp_rows[i].line) for i in is_)
        effect = -d if direction == "in" else d
        if m in loose:
            suggest = {"code": "loose", "sats": abs(d), "target_sats": want, "day": chain[js[0]]["day"]}
        elif direction == "in":
            suggest = {"code": "in_fee" if d > 0 else "in_more", "sats": abs(d), "target_sats": want}
        else:
            suggest = {"code": "out_fee" if -d == fee else "out_set", "sats": abs(d), "target_sats": want}
        actions.append(
            {
                "kind": "amount",
                "day": chain[js[0]]["day"],
                "title": f"PP-Zeile {lines}: "
                + ("Menge und Datum weichen ab" if m in loose else "Menge weicht ab"),
                "suggest": suggest,
                "effect_sats": effect,
                "pp": [pp_rows[i].as_dict() for i in is_],
                "chain": [chain[j] for j in js],
            }
        )

    # Netzwerkgebühren interner Umbuchungen: separat gebucht → ok, sonst je Jahr
    open_fees: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in fees or []:
        i = fee_row(f["sats"], f["day"])
        if i is not None:
            used_pp.add(i)
            ok["fees"] += 1
        else:
            open_fees[f["day"][:4]].append(f)
    for year, fs in list(open_fees.items()):
        booked = next((i for i in sorted(adj_free) if pp_rows[i].direction == "out"
                       and f"interner Umbuchungen {year}" in pp_rows[i].note
                       and pp_rows[i].sats == sum(f["sats"] for f in fs)), None)
        if booked is not None:  # per Delta-Import als Jahressumme gebucht
            adj_free.discard(booked)
            ok["fees"] += len(fs)
            del open_fees[year]
    for year, fs in open_fees.items():
        actions.append(
            {
                "kind": "fees",
                "day": fs[0]["day"],
                "title": f"Netzwerkgebühren interner Umbuchungen {year}",
                "suggest": {"code": "book_fees", "text": f"{len(fs)} Transaktion{'en' if len(fs) > 1 else ''}"},
                "effect_sats": -sum(f["sats"] for f in fs),
                "pp": [],
                "chain": fs,
            }
        )

    # Blockchain-Bewegungen ohne PP-Buchung — je Jahr und Gegenstelle gebündelt
    missing: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for j, c in enumerate(chain):
        if j not in used_chain:
            missing[(c["day"][:4], c["counterparty"] or "unbekannt")].append(c)
    for (year, cp), cs in missing.items():
        n_in = sum(1 for c in cs if c["direction"] == "in")
        effect = sum(_signed(c["direction"], c["sats"] + (c["fee_sats"] if c["direction"] == "out" else 0)) for c in cs)
        parts = []
        if n_in:
            parts.append(f"{n_in} Einlieferung{'en' if n_in > 1 else ''}")
        if len(cs) - n_in:
            parts.append(f"{len(cs) - n_in} Auslieferung{'en' if len(cs) - n_in > 1 else ''}")
        actions.append(
            {
                "kind": "missing",
                "day": cs[0]["day"],
                "title": f"{cp} {year}: fehlt in PP",
                "suggest": {"code": "book", "text": " und ".join(parts)},
                "effect_sats": effect,
                "pp": [],
                "chain": cs,
            }
        )

    # PP-Buchungen ohne Blockchain-Gegenstück — je Jahr (auch nicht zuordenbare Korrekturzeilen)
    used_pp -= adj_free
    extra: dict[str, list[PPRow]] = defaultdict(list)
    for i, p in enumerate(pp_rows):
        if i not in used_pp:
            extra[str(p.day.year)].append(p)
    for year, ps in extra.items():
        actions.append(
            {
                "kind": "only_pp",
                "day": ps[0].day.isoformat(),
                "title": f"PP {year}: {len(ps)} Buchung{'en' if len(ps) > 1 else ''} ohne Blockchain-Gegenstück",
                "suggest": {"code": "check"},
                "effect_sats": -sum(_signed(p.direction, p.sats) for p in ps),
                "pp": [p.as_dict() for p in ps],
                "chain": [],
            }
        )
    actions.sort(key=lambda a: (a["day"], a["kind"]))
    for n, a in enumerate(actions):
        a["id"] = f"{a['kind']}:{a['day']}:" + ",".join(
            [str(p["line"]) for p in a["pp"]] + [c["txid"][:12] for c in a["chain"]]
        )
        a["year"] = int(a["day"][:4])
        a["no"] = n + 1

    pp_bestand = sum(_signed(p.direction, p.sats) for p in pp_rows)
    if bestand_sats is None:
        bestand_sats = sum(
            _signed(c["direction"], c["sats"] + (c["fee_sats"] if c["direction"] == "out" else 0)) for c in chain
        ) - sum(f["sats"] for f in fees or [])
    after = pp_bestand + sum(a["effect_sats"] for a in actions)
    years = []
    for y in sorted({a["year"] for a in actions} | {p.day.year for p in pp_rows} | {int(c["day"][:4]) for c in chain}):
        ya = [a for a in actions if a["year"] == y]
        years.append({"year": y, "open": len(ya), "effect_sats": sum(a["effect_sats"] for a in ya)})
    return {
        "bestand": {
            "pp_sats": pp_bestand,
            "chain_sats": bestand_sats,
            "delta_sats": pp_bestand - bestand_sats,
            "after_sats": after,
            # interne Transaktionsgebühren (Umbuchungen) sind keine Zu-/Abflüsse
            "rest_sats": after - bestand_sats,
        },
        "ok": {**ok, "total": sum(ok.values())},
        "actions": actions,
        "years": years,
        "counts": {"pp": len(pp_rows), "chain": len(chain), "matched": len(matches)},
        "tolerance": {"days": MATCH_DAYS, "agg_days": AGG_DAYS, "sats": TOL_SATS, "ratio": TOL_RATIO},
    }



# ---------------------------------------------------------------------------
# Korrigierte CSV für den Re-Import in Portfolio Performance
# ---------------------------------------------------------------------------

_ISO = re.compile(r"^\s*\d{4}-\d{2}-\d{2}")
_EN_TYPES = {"in": "Delivery (Inbound)", "out": "Delivery (Outbound)"}
_DE_TYPES = {"in": "Einlieferung", "out": "Auslieferung"}


# Spalten, die neue Zeilen aus einer bestehenden Bitcoin-Zeile übernehmen (Wertpapier);
# alles andere bleibt leer — v. a. Konto/Verrechnungskonto und Währungen, sonst meldet
# PP „Buchungswährung passt nicht zu Kontowährung“.
_SECURITY_COLS = ("wertpapiername", "wertpapier", "security name", "security", "isin", "wkn",
                  "ticker-symbol", "ticker symbol", "symbol")


class _Fmt:
    """Zahlen-, Datums- und Typformat wie im PP-Export."""

    def __init__(self, f: PPFile) -> None:
        self.f = f
        tpl = next((r for r in f.rows if r.direction == "in"), f.rows[0])
        raw = list(tpl.raw) + [""] * (len(f.header) - len(tpl.raw))
        self.template = [
            raw[i] if h.strip().strip('"').lower() in _SECURITY_COLS else ""
            for i, h in enumerate(f.header)
        ]
        cell = lambda role: tpl.raw[f.roles[role]] if role in f.roles and f.roles[role] < len(tpl.raw) else ""  # noqa: E731
        date_cell = cell("date")
        self.iso = bool(_ISO.match(date_cell))
        self.date_suffix = date_cell.strip()[10:] if self.iso else ""
        shares = cell("shares")
        self.comma = "," in shares and shares.rfind(",") > shares.rfind(".")
        self.english = not any(w in cell("type").lower() for w in ("kauf", "lieferung", "verkauf"))
        # Vorzeichen des Werts wie bei Ein-/Auslieferungen im Export (Käufe zählen nicht)
        self.negative: dict[str, bool] = {}
        for r in f.rows:
            is_delivery = any(w in r.type.lower() for w in ("lieferung", "delivery"))
            if is_delivery and r.direction not in self.negative and "value" in f.roles:
                v = r.raw[f.roles["value"]] if f.roles["value"] < len(r.raw) else ""
                if v.strip():
                    self.negative[r.direction] = v.strip().startswith("-")

    def num(self, v: float, decimals: int) -> str:
        t = f"{v:.{decimals}f}"
        return t.replace(".", ",") if self.comma else t

    def day(self, d: date) -> str:
        return d.isoformat() + self.date_suffix if self.iso else d.strftime("%d.%m.%Y")

    def row(self, d: date, direction: str, sats: int, eur: float | None, note: str) -> list[str]:
        r = list(self.template)
        roles = self.f.roles

        def put(role: str, value: str) -> None:
            if role in roles:
                r[roles[role]] = value

        put("date", self.day(d))
        put("type", (_EN_TYPES if self.english else _DE_TYPES)[direction])
        put("shares", self.num(sats / 1e8, 8))
        value = "" if eur is None else self.num(-eur if self.negative.get(direction) else eur, 2)
        put("value", value)
        put("gross", value)
        put("total", value)
        put("price", "" if eur is None or not sats else self.num(eur / (sats / 1e8), 2))
        put("fee", self.num(0, 2))
        put("taxes", self.num(0, 2))
        put("note", note)
        return r


def import_header(f: PPFile) -> list[str]:
    """Kopfzeile mit den Feldnamen des PP-Imports, damit PP die Spalten selbst zuordnet.
    Der Export „Alle Buchungen“ nennt den Wert „Gesamtpreis“ (bzw. „Betrag“ ohne Gebühren)
    und das Wertpapier „Wertpapier“ — beides kennt der Import nicht („Pflichtfeld Wert“)."""
    header = list(f.header)
    english = all(h.strip().lower() not in ("datum", "typ", "stück") for h in header)
    wert = f.roles.get("total", f.roles.get("value"))
    if wert is not None and header[wert].strip().lower() not in ("wert", "value"):
        header[wert] = "Value" if english else "Wert"
    sec = f.roles.get("security")
    if sec is not None and header[sec].strip().lower() in ("wertpapier", "security"):
        header[sec] = "Security Name" if english else "Wertpapiername"
    return header


def build_import_csv(
    f: PPFile,
    result: dict[str, Any],
    price_eur: Callable[[date], float | None],
    keep_lines: Iterable[int] = (),
) -> tuple[str, dict[str, int]]:
    """Delta für den PP-Import: nur die Zeilen, die PP noch fehlen.

    Bestehende PP-Buchungen bleiben unangetastet (Verrechnungskonten, Währungen,
    Notizen). Die Datei enthält fehlende Bewegungen, Auszahlungs- und Netzwerkgebühren
    und Mengenkorrekturen — jeweils als Ein-/Auslieferung zum Tageskurs (Korrektur =
    Differenz zur gebuchten Menge). PP-Zeilen ohne Blockchain-Gegenstück lassen sich per
    Import nicht löschen: Sie zählen als ``to_delete`` (außer sie stehen in
    ``keep_lines``, z. B. Coins noch auf einer Börse) und werden in PP von Hand gelöscht.
    Rückgabe: CSV-Text, Kennzahlen (``delta_sats`` = Wirkung der Datei auf den Bestand).
    """
    if not f.rows:
        raise ValueError("keine Bitcoin-Buchungen im PP-Export")
    fmt = _Fmt(f)
    keep = {int(x) for x in keep_lines}
    new: list[tuple[date, list[str]]] = []
    stats = {"added": 0, "corrections": 0, "missing_price": 0, "to_delete": 0, "delete_sats": 0}

    def add(day: str, direction: str, sats: int, note: str) -> None:
        d = date.fromisoformat(day)
        p = price_eur(d)
        if p is None:
            stats["missing_price"] += 1
        new.append((d, fmt.row(d, direction, sats, None if p is None else round(sats / 1e8 * p, 2), note)))
        stats["added"] += 1

    for a in result["actions"]:
        code = a["suggest"]["code"]
        if a["kind"] == "amount":
            lines = ", ".join(str(p["line"]) for p in a["pp"])
            effect = int(a["effect_sats"])  # Wirkung auf den PP-Bestand
            if not effect:
                continue
            direction = "in" if effect > 0 else "out"
            if code in ("in_fee", "out_fee"):
                what = "Auszahlungsgebühr" if code == "in_fee" else "Netzwerkgebühr"
                add(a["chain"][-1]["day"], direction, abs(effect), f"BTC-Herkunft: {what} zu Zeile {lines}")
            else:  # Menge der PP-Buchung weicht ab → Differenz buchen
                day = a["suggest"].get("day") or a["chain"][-1]["day"]
                add(day, direction, abs(effect),
                    f"BTC-Herkunft: Korrektur Stück zu Zeile {lines} (Blockchain {a['chain'][-1]['day']})")
                stats["corrections"] += 1
        elif a["kind"] == "missing":
            for c in a["chain"]:
                cp = c.get("counterparty") or "unbekannt"
                if c["direction"] == "in":
                    add(c["day"], "in", c["sats"], f"BTC-Herkunft: {cp}, Tx {c['txid'][:16]}")
                else:
                    add(c["day"], "out", c["sats"] + c["fee_sats"],
                        f"BTC-Herkunft: {cp}, Tx {c['txid'][:16]}, inkl. Netzwerkgebühr")
        elif a["kind"] == "fees":
            fs = a["chain"]
            add(fs[-1]["day"], "out", sum(x["sats"] for x in fs),
                f"BTC-Herkunft: Netzwerkgebühren interner Umbuchungen {fs[-1]['day'][:4]} ({len(fs)} Tx)")
        elif a["kind"] == "only_pp":
            for p in a["pp"]:
                if int(p["line"]) not in keep:
                    stats["to_delete"] += 1
                    stats["delete_sats"] += _signed(p["direction"], int(p["sats"]))

    new.sort(key=lambda x: x[0])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=f.delim, lineterminator="\r\n")
    w.writerow(import_header(f))
    for _d, cells in new:
        w.writerow(cells)
    stats["skipped_transfers"] = f.skipped_transfers
    # Probe: Wirkung der erzeugten Datei auf den Bestand (so wie PP sie einliest)
    stats["delta_sats"] = sum(_signed(r.direction, r.sats) for r in parse_pp_file(buf.getvalue()).rows)
    return buf.getvalue(), stats


# ---------------------------------------------------------------------------
# Diagnose ohne Bestände (zum Weitergeben bei der Fehlersuche)
# ---------------------------------------------------------------------------

_KIND_DE = {"missing": "fehlt in PP", "amount": "Menge weicht ab", "fees": "Gebühren fehlen",
            "only_pp": "nur in PP (löschen/behalten)"}


def _near(a: int, b: int) -> bool:
    return b != 0 and abs(a - b) <= max(abs(b) // 20, 1_000)  # ±5 %, mind. 1000 sat


def pp_diagnose(f: PPFile, result: dict[str, Any], last_delta: dict[str, int] | None = None) -> str:
    """Kurztext zur Fehlersuche — nur Zähler, Jahre, Vorzeichen und Anteile in % des
    Blockchain-Bestands; keine BTC-Mengen, Beträge, Daten, Adressen oder TxIDs."""
    b = result["bestand"]
    chain = int(b["chain_sats"])

    def pct(sats: int) -> str:
        if not sats:
            return "0"
        return f"{100 * sats / chain:+.1f} % des Blockchain-Bestands" if chain else ("+" if sats > 0 else "−")

    years = sorted({r.day.year for r in f.rows})
    out = ["BTC-Herkunft · PP-Diagnose (ohne Bestände, Beträge, Daten, Adressen)"]
    out.append(
        f"PP-Datei: {len(f.rows)} Bitcoin-Zeilen"
        + (f", Jahre {years[0]}–{years[-1]}" if years else "")
        + f"; als Bitcoin gezählt: {len(f.counted) or ('ohne Wertpapier-Spalte' if 'security' not in f.roles else 0)} Wertpapier(e)"
        + f", übergangen mit „Bitcoin“ im Namen: {len(f.ignored)}"
        + f"; Umbuchungen übersprungen: {f.skipped_transfers}; Zeilen mit Fehler: {len(f.errors)}"
    )
    ok = result.get("ok") or {}
    out.append(
        f"Stimmt: {ok.get('total', 0)} (exakt {ok.get('exact', 0)}, Datum verschoben {ok.get('date', 0)}, "
        f"Sammelbuchung {ok.get('grouped', 0)}, Gebühr separat {ok.get('fees', 0)}, "
        f"per Delta korrigiert {ok.get('corrected', 0)})"
    )
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in result["actions"]:
        by_kind[a["kind"]].append(a)
    for kind, acts in by_kind.items():
        per_year = defaultdict(int)
        for a in acts:
            per_year[a["year"]] += 1
        eff = sum(int(a["effect_sats"]) for a in acts)
        out.append(
            f"Offen · {_KIND_DE.get(kind, kind)}: {len(acts)} "
            f"({', '.join(f'{y}: {n}' for y, n in sorted(per_year.items()))}); Wirkung {pct(eff)}"
        )
    now = int(b["pp_sats"]) - chain
    out.append(f"Δ PP − Blockchain jetzt: {'stimmt' if now == 0 else ('PP zu hoch' if now > 0 else 'PP zu niedrig')}"
               + (f" ({pct(now)})" if now else ""))
    rest = int(b["after_sats"]) - chain
    out.append("Rechenprobe PP + Korrekturen = Blockchain: "
               + ("ja" if rest == 0 else f"NEIN, Rest {pct(rest)} — bitte melden (Fehler in der App)"))
    if last_delta:
        eff = int(last_delta.get("delta_sats", 0))
        dele = int(last_delta.get("delete_sats", 0))
        out.append(
            f"Letzte Delta-Datei dieser Sitzung: {last_delta.get('added', 0)} Zeilen, Wirkung {pct(eff)}; "
            f"zu löschen {last_delta.get('to_delete', 0)} Zeilen ({pct(-dele)})"
        )
        if now and _near(now, eff):
            out.append("Deutung: Abweichung ≈ Wirkung der letzten Delta-Datei → Datei doppelt importiert, "
                       "oder check-pp.csv war beim Download nicht der Stand, in den importiert wurde.")
        elif now and _near(now, dele):
            out.append("Deutung: Abweichung ≈ zu löschende PP-Zeilen → diese Zeilen stehen noch in PP.")
        elif now and _near(now, eff + dele):
            out.append("Deutung: Abweichung ≈ Delta-Datei + zu löschende Zeilen → Import und Löschen "
                       "fehlen bzw. doppelt; check-pp.csv neu exportieren und vergleichen.")
    return "\n".join(out) + "\n"
