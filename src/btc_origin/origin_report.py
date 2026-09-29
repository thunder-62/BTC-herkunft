"""„Herkunftsanalyse Bitcoin“ — Bericht fürs Finanzamt (PDF, im Browser).

Aufbau — vom Einfachen zum Detail:
  Kopf    Titel, Stand (Datum/Uhrzeit), Stichtag, optional Name / Steuer-ID
  1       Betrachtete Wallets (xpub, Zeitraum, Tx), Bestand zum Stichtag, Mengenabstimmung
  2       Zu- und Abflüsse je Wallet (neue Seite) mit Überleitung zum Bestand am Stichtag;
          2.1 Flussgrafik
  3       Mögliche Gewinne und Verluste (hypothetisch: Abfluss = Verkauf); 3.1 Verkäufe auf
          Börsen ohne Abfluss, 3.1/3.2 Jahresübersicht für die Anlage SO
  4       Belege und Ersatzwerte (EXPLAIN_EVIDENCE)
  5       Nicht durch Belege nachgewiesene Transaktionen
  6       Rechtsgrundlagen und Quellen
  7       Methodik und Annahmen
  8       Unklare Transaktionen: unbelegte Käufe innerhalb der Haltefrist (8.1, 8.2)
  9       Erläuterungen für Leser ohne Bitcoin-Vorkenntnisse (EXPLAIN_BASICS)
  Anhang  A Abgleich mit Börsen-Exporten, B nicht zugeordnete Börsenvorgänge,
          C alle Export-Zeilen, D Transaktionsverzeichnis (Kurzreferenz → Hash)

Zwei Fassungen (``HerkunftReport.fassung``): intern (Full-Detail) und Finanzamt (ohne Bestände,
xpubs, Adressen und Transaktions-IDs ohne Bezug zu einer Veräußerung); check_reports prüft beide.

Name und Steuer-ID kommen nur aus der Anfrage und werden nirgends gespeichert.
Bytes in memory only (Disk-Written: false). Keine Steuerberatung.
"""

from __future__ import annotations

import logging
import math
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from dataclasses import replace as dataclasses_replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from btc_origin.external_book import is_bitcoin_address
from btc_origin.regelwerk import rules_for
from btc_origin.year_summary import YearSummary, freigrenze_eur

# fpdf2 bettet Schriften per fontTools-Subsetting ein. Windows-Fonts (Segoe UI,
# Segoe UI Symbol) enthalten die Tabelle „MERG“ (nur ClearType-Darstellung), die
# fontTools verwirft und dabei „MERG NOT subset …“ loggt — folgenlos fürs PDF.
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

SATS_PER_BTC = 100_000_000
HOLDING_DAYS = 365
TITLE = "Herkunftsanalyse Bitcoin"
MASK = "•••"

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class WalletInfo:
    name: str
    kind: str  # xpub | address
    address_type: str
    first: str | None
    last: str | None
    tx_count: int
    balance_sats: int
    key: str = ""  # public xpub / address as entered (never a secret)


@dataclass
class SourceInfo:
    name: str
    entries: int = 0
    entered_sats: int = 0
    first: str | None = None
    last: str | None = None
    held: dict[str, int] = field(default_factory=dict)  # wallet → sats today
    exited: dict[str, int] = field(default_factory=dict)  # destination → sats

    @property
    def held_sats(self) -> int:
        return sum(self.held.values())

    @property
    def exited_sats(self) -> int:
        return sum(self.exited.values())

    @property
    def fees_sats(self) -> int:
        """Rest = network fees paid from these coins (entered − held − exited)."""
        return max(0, self.entered_sats - self.held_sats - self.exited_sats)


@dataclass
class InflowLine:
    """One Zufluss (Cloud-Eintritt) into a wallet."""

    wallet: str
    date: str
    source: str
    txid: str
    sats: int
    remaining_sats: int
    price_eur: float | None
    acquisition: str = ""  # purchase day (export) — else = date
    acq_source: str = ""  # "Kauf auf Bison lt. Export" / "" = Blockchain


@dataclass
class LotLine:
    acquisition: str
    source: str
    wallet: str
    sats: int
    days_held: int
    qualifies: bool
    price_eur: float | None
    acq_source: str = ""
    origin_txid: str = ""  # Zufluss-Transaktion
    address: str = ""  # eigene Adresse, auf der der Teilbestand heute liegt


@dataclass
class TxRef:
    """Eine Transaktion im Transaktionsverzeichnis (Anhang D): Kurzreferenz T-001 …"""

    ref: str
    day: str  # ISO-Tag der Blockzeit ("" = unbestätigt)
    wallet: str
    art: str  # Zufluss | Abfluss | Umbuchung
    txid: str


def build_tx_refs(
    rows: Iterable[dict[str, Any]],
    wallet_names: dict[Any, str],
    in_txids: set[str],
    out_txids: set[str],
) -> list[TxRef]:
    """Kurzreferenzen T-001, T-002, … chronologisch nach Blockzeit (bei gleicher Zeit nach
    TxID) — bei unveränderten Daten in jedem Lauf gleich; ein späterer Stichtag hängt nur
    Nummern an. ``rows``: Flows der Wallets (txid, block_time, wallet_id)."""
    txs: dict[str, dict[str, Any]] = {}
    for r in rows:
        txid = str(r.get("txid") or "")
        if not txid:
            continue
        e = txs.setdefault(txid, {"time": "", "wallets": set()})
        t = str(r.get("block_time") or "")
        if t and (not e["time"] or t < e["time"]):
            e["time"] = t
        name = wallet_names.get(r.get("wallet_id"))
        if name:
            e["wallets"].add(name)
    order = sorted(txs, key=lambda x: (txs[x]["time"] or "9999", x))
    out = []
    for i, txid in enumerate(order, 1):
        e = txs[txid]
        kinds = [k for k, s in (("Zufluss", in_txids), ("Abfluss", out_txids)) if txid in s]
        out.append(TxRef(
            ref=f"T-{i:03d}", day=e["time"][:10], wallet=", ".join(sorted(e["wallets"])),
            art=" und ".join(kinds) or "Umbuchung", txid=txid,
        ))
    return out


@dataclass
class HerkunftReport:
    generated_at: datetime
    stichtag: date
    wallets: list[WalletInfo]
    sources: list[SourceInfo]
    years: list[YearSummary]
    lots: list[LotLine]
    inflow_sats: int = 0
    outflow_sats: int = 0
    balance_sats: int = 0
    consistent: bool = True
    person_name: str = ""
    tax_id: str = ""
    privacy: bool = False
    # "full" = Full-Detail-Bericht; "finanzamt" = Finanzamt-Fassung (ohne Bestände, xpubs,
    # Adressen und TxIDs ohne Bezug zu einer Veräußerung — siehe released_txids)
    fassung: str = "full"
    # Transaktionsverzeichnis (build_tx_refs); leer → aus den Zeilen des Berichts abgeleitet
    transactions: list[TxRef] = field(default_factory=list)
    # nur Vorgänge bis zu diesem Tag (Tagesende UTC); None = alle
    until: date | None = None
    inflows: list[InflowLine] = field(default_factory=list)
    # Bestandsabstimmung je Wallet bis zum Stichtag (Abschnitt 2).
    recon: dict[str, WalletRecon] = field(default_factory=dict)
    # Network fees from the chain (not derived): of disposals / of Umbuchungen.
    disposal_fees_sats: int | None = None
    transfer_fees_sats: int | None = None
    chain_balance_sats: int | None = None  # unspent own outputs (UTXO)
    # Bestand je Wallet zum Stichtag (Rz. 104): wallet name → sats; None =
    # use today's balance (balance_sats) — e.g. reports built without flows.
    stichtag_balances: dict[str, int] | None = None
    stichtag_price: float | None = None
    stichtag_price_day: date | None = None  # < Stichtag if it lies in the future
    # Exchanges whose export (with sells) valued the deposits (Rz. 20, 55).
    exchange_exports: list[str] = field(default_factory=list)
    # Abgleich per exchange (exchange_sales.ExchangeReport) + file list.
    exchange_reports: list[Any] = field(default_factory=list)
    exchange_files: list[dict[str, Any]] = field(default_factory=list)
    # local/zuordnung.csv: (name, last day ISO) — names marked ³ in the report.
    declared_rules: list[tuple[str, str]] = field(default_factory=list)
    # Entry txid → Käufe − Auszahlungsgebühr − Transaktionskosten = Eingang.
    withdrawal_recon: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Export-Zeilen ohne Wallet-Vorgang (exchange_sales.open_exchange_items).
    open_exchange: list[dict[str, Any]] = field(default_factory=list)
    # local/erlaeuterungen.csv: (Börse/Absender, Datum oder None, Art, Text) — Art
    # „Zufluss“ erläutert Zuflüsse ohne Beleg (Abschnitt 5).
    explanations: list[tuple[str, date | None, str, str]] = field(default_factory=list)
    # Anhang C: all export rows, uniform (exchange_sales.trade_ledger).
    exchange_trades: list[dict[str, Any]] = field(default_factory=list)
    price_source_counts: dict[str, int] = field(default_factory=dict)
    # Export-Zeilen ohne steuerlichen Wert (kategorisierung.NichtUnterstuetzt.as_dict) —
    # Abschnitt 8.3, Zusammenfassung, Kontrolldatei (Fehler)
    unsupported: list[dict[str, Any]] = field(default_factory=list)
    # Quittungen (geprueft_nicht_unterstuetzt) ohne passenden Vorgang — Hinweis Kontrolldatei
    unmatched_acks: list[str] = field(default_factory=list)
    # Geltungsbereich (aus kategorien.yaml) für die Methodik; "" = nicht angegeben
    geltungsbereich: str = ""
    # zuordnung_manuell (local/kategorien.yaml): Zufluss-TxID → {tnr, kategorie, behandlung,
    # erlaeuterung}
    manual_categories: dict[str, dict[str, str]] = field(default_factory=dict)
    price_source: str = (
        "Binance BTC/EUR-Tagesschlusskurs (UTC); vor dem 03.01.2020 Binance "
        "BTC/USDT-Tagesschlusskurs ÷ EZB-Referenzkurs USD/EUR desselben Tages; "
        "vor dem 17.08.2017 mempool.space"
    )


_BUILD_ID: str | None = None


def build_id() -> str:
    """Short commit id of the running code (``BTC_ORIGIN_COMMIT`` env, else
    read from ``.git`` without calling git); "" if unknown (e.g. no checkout)."""
    global _BUILD_ID
    if _BUILD_ID is not None:
        return _BUILD_ID
    import os

    sha = os.environ.get("BTC_ORIGIN_COMMIT", "").strip()
    if not sha:
        for parent in Path(__file__).resolve().parents:
            git = parent / ".git"
            if not git.exists():
                continue
            try:
                if git.is_file():  # worktree: "gitdir: <path>"
                    git = Path(git.read_text("utf-8").split(":", 1)[1].strip())
                head = (git / "HEAD").read_text("utf-8").strip()
                if head.startswith("ref:"):
                    ref = head[4:].strip()
                    common = git
                    if (git / "commondir").is_file():
                        common = (git / (git / "commondir").read_text("utf-8").strip()).resolve()
                    for base in (git, common):
                        if (base / ref).is_file():
                            sha = (base / ref).read_text("utf-8").strip()
                            break
                    else:
                        packed = common / "packed-refs"
                        if packed.is_file():
                            for line in packed.read_text("utf-8").splitlines():
                                if line.endswith(" " + ref):
                                    sha = line.split(" ", 1)[0]
                                    break
                else:
                    sha = head
            except (OSError, IndexError):
                sha = ""
            break
    _BUILD_ID = sha[:7] if len(sha) >= 7 else ""
    return _BUILD_ID


def address_type_label(addresses: Iterable[str]) -> str:
    kinds: list[str] = []
    for a in addresses:
        if a.startswith("bc1p") or a.startswith("tb1p"):
            k = "Taproot (bc1p…)"
        elif a.startswith(("bc1q", "tb1q")):
            k = "Native SegWit (bc1q…)"
        elif a.startswith(("3", "2")):
            k = "SegWit kompatibel (3…)"
        elif a.startswith(("1", "m", "n")):
            k = "Legacy (1…)"
        else:
            continue
        if k not in kinds:
            kinds.append(k)
    return ", ".join(kinds) or "—"


def _day(v: Any) -> str | None:
    return str(v)[:10] if v else None


def source_label(in_row: dict[str, Any]) -> str:
    if in_row.get("source_coinbase"):
        return "Mining (neu gemint)"
    if in_row.get("source_name"):
        return str(in_row["source_name"])
    srcs = [a for a in in_row.get("source_addresses") or [] if a != "coinbase"]
    if srcs:
        a = srcs[0]
        return f"{a[:8]}…{a[-4:]}"
    return "Absender unbekannt"


def build_sources(shaped: list[dict[str, Any]]) -> list[SourceInfo]:
    """Aggregate Cloud-Eintritte per sender; where the coins are today and
    where the spent part went (via the coin's lot id)."""
    by_name: dict[str, SourceInfo] = {}
    lot_source: dict[str, str] = {}
    for r in shaped:
        if r.get("direction") != "in":
            continue
        name = source_label(r)
        s = by_name.setdefault(name, SourceInfo(name=name))
        s.entries += 1
        s.entered_sats += int(r.get("amount_sats") or 0)
        d = _day(r.get("time"))
        if d and (s.first is None or d < s.first):
            s.first = d
        if d and (s.last is None or d > s.last):
            s.last = d
        for loc in r.get("current_locations") or []:
            w = str(loc.get("wallet_name") or f"Wallet {loc.get('wallet_id')}")
            s.held[w] = s.held.get(w, 0) + int(loc.get("remaining_sats") or 0)
        if r.get("lot_id"):
            lot_source[str(r["lot_id"])] = name
    for r in shaped:
        if r.get("direction") != "out" or r.get("kind") == "fee":
            continue
        name = lot_source.get(str(r.get("lot_id") or ""))
        if name is None:
            continue
        dest = str(r.get("external_name") or r.get("address") or "außerhalb der Wallets")
        s = by_name[name]
        s.exited[dest] = s.exited.get(dest, 0) + int(r.get("amount_sats") or 0)
    return sorted(by_name.values(), key=lambda s: (-s.entered_sats, s.name))


def build_inflow_lines(
    shaped: list[dict[str, Any]], price_eur: Callable[[date], float | None]
) -> list[InflowLine]:
    """Every Cloud-Eintritt with its entry wallet, sender and acquisition price."""
    out: list[InflowLine] = []
    for r in shaped:
        if r.get("direction") != "in":
            continue
        d = _day(r.get("time")) or ""
        acq = _day(r.get("lot_date")) or d
        px = r.get("acq_price_eur")
        if px is None:
            try:
                px = price_eur(date.fromisoformat(acq))
            except ValueError:
                px = None
        out.append(
            InflowLine(
                wallet=str(r.get("wallet_name") or f"Wallet {r.get('wallet_id')}"),
                date=d,
                source=source_label(r),
                txid=str(r.get("txid") or ""),
                sats=int(r.get("amount_sats") or 0),
                remaining_sats=int(r.get("remaining_sats") or 0),
                price_eur=float(px) if px is not None else None,
                acquisition=acq,
                acq_source=str(r.get("acq_source") or ""),
            )
        )
    return sorted(out, key=lambda x: (x.date, x.txid, x.acquisition))


@dataclass
class WalletRecon:
    """Bestandsabstimmung einer Wallet (bis Tagesende des Stichtags)."""

    ext_in: int = 0  # Zuflüsse von fremden Adressen
    int_in: int = 0  # Umbuchungen aus anderen betrachteten Wallets
    int_out: int = 0  # Umbuchungen in andere betrachtete Wallets
    ext_out: int = 0  # Abflüsse an fremde Adressen
    fees: int = 0  # Transaktionsgebühren (Anteil dieser Wallet)
    balance: int = 0  # Σ Zugänge − Σ Abgänge laut Blockchain
    # Umbuchungen je Gegen-Wallet (Name → sats): woher bzw. wohin
    int_in_from: dict[str, int] = field(default_factory=dict)
    int_out_to: dict[str, int] = field(default_factory=dict)
    # Abflüsse an fremde Adressen je Transaktion: {date, txid, sats, fee}
    out_lines: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fees_out(self) -> int:
        """Gebühren der Transaktionen mit Abfluss an fremde Adressen."""
        return sum(int(x["fee"]) for x in self.out_lines)

    @property
    def computed(self) -> int:
        return self.ext_in + self.int_in - self.int_out - self.ext_out - self.fees


def wallet_reconciliation(
    rows: Iterable[dict[str, Any]], wallet_names: dict[Any, str], cut: date | None = None
) -> dict[str, WalletRecon]:
    """Per wallet: external in − external out − fees ± Umbuchungen = balance.

    Raw ledger rows (one per own input/output). Per transaction with own
    inputs, each wallet's net change is split into its external outflow
    share, its fee share (by spent amount, exact integers) and the rest —
    the internal transfer (change to the same wallet nets out).
    """
    by_tx: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if cut is not None and (not r.get("block_time") or str(r["block_time"])[:10] > cut.isoformat()):
            continue
        by_tx.setdefault(str(r.get("txid") or ""), []).append(r)
    out: dict[str, WalletRecon] = {name: WalletRecon() for name in wallet_names.values()}
    name_of = {id(r): name for name, r in out.items()}

    def rec(r: dict[str, Any]) -> WalletRecon | None:
        name = wallet_names.get(r.get("wallet_id"))
        return out.get(name) if name is not None else None

    for group in by_tx.values():
        ins = [r for r in group if r.get("direction") == "in"]
        outs = [r for r in group if r.get("direction") == "out"]
        for r in group:
            w = rec(r)
            if w is not None:
                sats = int(r.get("amount_sats") or 0)
                w.balance += sats if r.get("direction") == "in" else -sats
        if not outs:
            for r in ins:
                w = rec(r)
                if w is not None:
                    w.ext_in += int(r.get("amount_sats") or 0)
            continue
        own_in = sum(int(r.get("amount_sats") or 0) for r in outs)
        fee = max((int(r.get("fee_sats") or 0) for r in outs), default=0)
        per_wallet: dict[int, list[int]] = {}  # id(recon) → [in, out, ext]
        recons: dict[int, WalletRecon] = {}
        for r in ins:
            w = rec(r)
            if w is not None:
                per_wallet.setdefault(id(w), [0, 0, 0])[0] += int(r.get("amount_sats") or 0)
                recons[id(w)] = w
        for r in outs:
            w = rec(r)
            if w is None:
                continue
            amt = int(r.get("amount_sats") or 0)
            ext = 0
            if not r.get("is_internal"):
                ext = int(r["external_amount_sats"]) if r.get("external_amount_sats") is not None else amt
            v = per_wallet.setdefault(id(w), [0, 0, 0])
            v[1] += amt
            v[2] += ext
            recons[id(w)] = w
        cum = 0
        senders: list[list[Any]] = []  # [recon, sats] — gibt an andere Wallets ab
        receivers: list[list[Any]] = []  # [recon, sats] — erhält von anderen Wallets
        for key in sorted(per_wallet, key=lambda k: per_wallet[k][1]):
            w_in, w_out, w_ext = per_wallet[key]
            before = fee * cum // own_in if own_in else 0
            cum += w_out
            w_fee = (fee * cum // own_in if own_in else 0) - before
            w = recons[key]
            w.ext_out += w_ext
            w.fees += w_fee
            if w_ext:
                day = str(next((r.get("block_time") for r in group if r.get("block_time")), "") or "")[:10]
                w.out_lines.append({"date": day, "txid": str(group[0].get("txid") or ""), "sats": w_ext, "fee": w_fee})
            internal = w_in - (w_out - w_ext - w_fee)
            if internal > 0:
                w.int_in += internal
                receivers.append([w, internal])
            elif internal < 0:
                w.int_out += -internal
                senders.append([w, -internal])
        # Wer gibt an wen? Ganzzahlig der Reihe nach zuordnen (Summen gehen auf).
        i = j = 0
        while i < len(senders) and j < len(receivers):
            amt = min(senders[i][1], receivers[j][1])
            if amt > 0:
                s_w, r_w = senders[i][0], receivers[j][0]
                s_w.int_out_to[name_of[id(r_w)]] = s_w.int_out_to.get(name_of[id(r_w)], 0) + amt
                r_w.int_in_from[name_of[id(s_w)]] = r_w.int_in_from.get(name_of[id(s_w)], 0) + amt
            senders[i][1] -= amt
            receivers[j][1] -= amt
            if senders[i][1] == 0:
                i += 1
            if receivers[j][1] == 0:
                j += 1
    for r in out.values():
        r.out_lines.sort(key=lambda x: (x["date"], x["txid"]))
    return out


def tax_free_from(acquisition: date) -> date:
    """First day a sale is tax-free: more than one year after acquisition
    (§ 23 Abs. 1 Satz 1 Nr. 2 EStG) — the day after the anniversary."""
    try:
        anniversary = acquisition.replace(year=acquisition.year + 1)
    except ValueError:  # 29.02. → 28.02. of the following year
        anniversary = acquisition.replace(year=acquisition.year + 1, day=28)
    return anniversary + timedelta(days=1)


def reform_cutoff(rep: "HerkunftReport") -> date:
    """Stichtag Altbestand (Kryptosteuer-Reform, Entwurf) aus dem Regelwerk des Berichtsjahres."""
    return rules_for(rep.stichtag.year).altbestand_stichtag()


def is_altbestand(acquisition: str | date, cutoff: date) -> bool:
    """Anschaffung bis einschließlich ``cutoff`` (Regelwerk: reform.stichtag_altbestand)."""
    d = acquisition if isinstance(acquisition, date) else date.fromisoformat(str(acquisition)[:10])
    return d <= cutoff


def build_lot_lines(
    lots: list[dict[str, Any]],
    lot_source: dict[str, str],
    stichtag: date,
    price_eur: Callable[[date], float | None],
) -> list[LotLine]:
    out: list[LotLine] = []
    for lot in lots:
        if int(lot.get("remaining_sats") or 0) <= 0:
            continue
        acq = _day(lot.get("lot_date")) or ""
        try:
            acq_d = date.fromisoformat(acq)
        except ValueError:
            continue
        days = (stichtag - acq_d).days
        out.append(
            LotLine(
                acquisition=acq,
                source=lot_source.get(str(lot.get("lot_id")), "—"),
                wallet=str(lot.get("current_wallet_name") or f"Wallet {lot.get('current_wallet_id')}"),
                sats=int(lot["remaining_sats"]),
                days_held=days,
                qualifies=days >= HOLDING_DAYS,
                price_eur=(
                    float(lot["acq_price_eur"]) if lot.get("acq_price_eur") is not None else price_eur(acq_d)
                ),
                acq_source=str(lot.get("acq_source") or ""),
                origin_txid=str((lot.get("origin") or {}).get("txid") or ""),
                address=str(lot.get("current_address") or ""),
            )
        )
    return sorted(out, key=lambda x: (x.acquisition, x.wallet))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def fmt_btc(sats: int, privacy: bool = False, *, unit: bool = True) -> str:
    suffix = " BTC" if unit else ""
    if privacy:
        return f"{MASK}{suffix}"
    return f"{sats / SATS_PER_BTC:.8f}".replace(".", ",") + suffix


def fmt_eur(v: float | None, privacy: bool = False, *, signed: bool = False) -> str:
    if v is None:
        return "Kurs fehlt"
    if privacy:
        return f"{MASK} €"
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "−" if v < 0 else ("+" if signed and v > 0 else "")
    return f"{sign}{s} €"


def fmt_date(d: str | None) -> str:
    if not d:
        return "—"
    y, m, dd = d[:10].split("-")
    return f"{dd}.{m}.{y}"


def _mask_name(name: str, privacy: bool) -> str:
    """Mask sensitive identifiers (xpub, address, TxID) in the privacy view."""
    return MASK if privacy else name


def _name(name: str, privacy: bool) -> str:
    """Names (person, wallet, counterparty, exchange) stay readable in the
    privacy view — only a „name“ that is really an address (full, or
    shortened as ``bc1qabcd…wxyz``) is masked."""
    if privacy and ("…" in (name or "") or is_bitcoin_address((name or "").strip())):
        return MASK
    return name


# ---------------------------------------------------------------------------
# Finanzamt-Fassung
# ---------------------------------------------------------------------------

FA_LABEL = "Finanzamt-Fassung"
TX_ANNEX_NO = "Anhang D"
TX_ANNEX_TITLE = "Transaktionsverzeichnis"
FULL_LABEL = "Interne Fassung (Full-Detail)"
ON_REQUEST = "auf Anforderung"
REQ_MARK = "°"  # an der T-Nummer: Transaktions-Hash auf Anforderung (Finanzamt-Fassung)
FA_NOTICE = (
    "Diese Fassung enthält alle Angaben zu Veräußerungsgeschäften. Öffentliche Schlüssel (xpub), "
    "Wallet-Adressen, Bestände und Transaktionskennungen ohne Bezug zu einer Veräußerung sind nicht "
    "abgedruckt; sie werden auf Anforderung vollständig vorgelegt (Rz. 87, 101–104)."
)


def file_origins(rep: HerkunftReport) -> dict[str, str]:
    """Herkunft je Exportdatei aus erlaeuterungen.csv (Art „Datei“, Name = Dateiname, Datum =
    Bereitstellung), z. B. „von der Börse auf Anfrage per E-Mail bereitgestellt am 15.09.2026“ —
    steht in Anhang A unter dem Dateinamen und macht die Belegkette nachvollziehbar (Rz. 101–103).
    Schlüssel ist der Dateiname wie in ``exchange_files``; erste passende Zeile gilt."""
    files = {str(f.get("file") or "").lower(): str(f.get("file") or "") for f in rep.exchange_files}
    out: dict[str, str] = {}
    for r_name, r_day, r_art, text in rep.explanations:
        if not r_art.strip().lower().startswith("datei"):
            continue
        fname = files.get(r_name.strip().lower())
        if fname and fname not in out:
            out[fname] = text + (f" am {r_day:%d.%m.%Y}" if r_day else "")
    return out


def _md_escape(text: str) -> str:
    """Markdown-Zeichen der Tabellen (``**``, ``__``, ``~~``, ``--``) wörtlich darstellen."""
    return re.sub(r"(\\|\*\*|__|~~|--)", lambda m: "\\" + m.group(0), text)


def is_fa(rep: HerkunftReport) -> bool:
    return rep.fassung == "finanzamt"


def aufbau_text(fa: bool) -> str:
    """„So ist der Bericht aufgebaut“ — steht unter dem Inhaltsverzeichnis."""
    return (
        "Abschnitt 1 nennt die Wallets"
        + (" und die Abstimmung mit der Blockchain. " if fa else ", den Bestand und die Abstimmung mit der Blockchain. ")
        + "Abschnitt 2 zeigt je Wallet, woher die Bitcoin kamen und wohin sie abgeflossen sind, "
        + ("mit Abstimmung gegen die Blockchain. " if fa else "und leitet auf den Bestand über. ")
        + "Abschnitt 3 zeigt je Jahr, was abgeflossen ist und welcher Gewinn oder Verlust sich ergäbe, mit der "
        "Übersicht für die Anlage SO. Die Abschnitte 4 und 5 behandeln Belege, 6 und 7 Rechtsgrundlagen und "
        "Methodik; Abschnitt 8 nennt "
        + ("unbelegt gekaufte Bitcoin, deren Haltefrist am Stichtag noch läuft; " if fa
           else "noch gehaltene, unbelegt gekaufte Bitcoin innerhalb der Haltefrist; ")
        + "Abschnitt 9 erklärt die Begriffe für Leser ohne Bitcoin-Vorkenntnisse. Mit Börsen-Exporten folgen "
        "der Abgleich je Börse (Anhang A), nicht zugeordnete Börsenvorgänge (Anhang B) und alle Export-Zeilen "
        "(Anhang C). Das Transaktionsverzeichnis (Anhang D) löst die Kurzreferenzen (T-Nummern) auf"
        + ("; es nennt die Transaktionen der Veräußerungen und ihrer Herkunft, übrige auf Anforderung." if fa
           else ".")
    )


def fa_basics() -> tuple[tuple[str, str], ...]:
    """Abschnitt 9 der Finanzamt-Fassung: Verweise auf nicht abgedruckte Teile angepasst."""
    out = []
    for head, text in EXPLAIN_BASICS:
        text = text.replace(
            "sie stehen in Abschnitt 1, damit die Angaben unabhängig geprüft werden können",
            "sie werden auf Anforderung vorgelegt, damit die Angaben unabhängig geprüft werden können",
        )
        out.append((head, text))
    return tuple(out)


def tx_refs(rep: HerkunftReport) -> list[TxRef]:
    """Transaktionsverzeichnis des Berichts; ohne ``rep.transactions`` (z. B. direkt gebaute
    Berichte) aus Zuflüssen, Abflüssen und Veräußerungen abgeleitet."""
    if rep.transactions:
        return rep.transactions
    rows: list[dict[str, Any]] = []
    ins: set[str] = set()
    outs: set[str] = set()
    for x in rep.inflows:
        rows.append({"txid": x.txid, "block_time": x.date, "wallet_id": x.wallet})
        ins.add(x.txid)
    for name, r in rep.recon.items():
        for o in r.out_lines:
            rows.append({"txid": o["txid"], "block_time": o["date"], "wallet_id": name})
            outs.add(str(o["txid"]))
    for y in rep.years:
        for d in y.details:
            if d.get("origin", "wallet") == "wallet" and d.get("txid"):
                rows.append({"txid": d["txid"], "block_time": d["exit_date"]})
                outs.add(str(d["txid"]))
    names = {r["wallet_id"]: r["wallet_id"] for r in rows if r.get("wallet_id")}
    return build_tx_refs(rows, names, ins, outs)


def released_txids(rep: HerkunftReport) -> set[str]:
    """TxIDs, die die Finanzamt-Fassung vollständig abdruckt: jeder Abfluss, der als
    Veräußerung gilt (angenommen oder belegt), und jeder Zufluss, aus dem ein veräußerter
    Teilbestand stammt. Verkäufe auf einer Börse ohne Abfluss brauchen keine TxID."""
    out: set[str] = set()
    for y in rep.years:
        for d in y.details:
            if d.get("origin", "wallet") != "wallet" or not d.get("disposal", True):
                continue
            out.update(str(d[k]) for k in ("txid", "origin_txid") if d.get(k))
    return out


_B58_CHARS = "1-9A-HJ-NP-Za-km-z"
_RE_FA_XKEY = re.compile(rf"\b[A-Za-z]{{1,2}}p(?:ub|rv)[{_B58_CHARS}]{{90,}}\b")
_RE_FA_BECH32 = re.compile(r"\b(?:bc|tb|bcrt)1[02-9ac-hj-np-z]{6,87}\b", re.IGNORECASE)
_RE_FA_B58 = re.compile(rf"\b[123mn][{_B58_CHARS}]{{25,34}}\b")
_RE_FA_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8,}(?![0-9A-Za-z])")
# gekürzte Kennungen („3Abc12…xy9Z“, „a1b2c3d4…“) — auch Teil-Hashes sind im Explorer suchbar
_RE_FA_SHORT = re.compile(r"(?<![0-9A-Za-z])(?=[0-9A-Za-z]*\d)[0-9A-Za-z]{4,}…[0-9A-Za-z]*|…(?=[0-9A-Za-z]*\d)[0-9A-Za-z]{4,}")


def fa_redact(text: str, released: set[str]) -> str:
    """Letzte Sicherung der Finanzamt-Fassung: Schlüssel, Adressen und nicht freigegebene
    Transaktionskennungen werden vor dem Rendern durch „auf Anforderung“ ersetzt."""
    text = _RE_FA_XKEY.sub(ON_REQUEST, text)
    text = _RE_FA_BECH32.sub(ON_REQUEST, text)
    text = _RE_FA_B58.sub(ON_REQUEST, text)
    text = _RE_FA_SHORT.sub(ON_REQUEST, text)

    def hex_id(m: re.Match[str]) -> str:
        s = m.group()
        if s in released or (len(s) != 64 and not (re.search(r"\d", s) and re.search(r"[a-fA-F]", s))):
            return s
        return ON_REQUEST

    return _RE_FA_HEX.sub(hex_id, text)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    # (regular, bold) — Linux, Windows, macOS
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]

# Symbol fonts for glyphs the body font lacks (e.g. Segoe UI has no ↳ ✓ ✗).
_SYMBOL_FONT_CANDIDATES = [
    "C:/Windows/Fonts/seguisym.ttf",  # Segoe UI Symbol (Windows 7+)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]

_ASCII_FALLBACK = {"€": "EUR", "−": "-", "–": "-", "—": "-", "…": "...", "•": "*", "→": "->", "≥": ">=", "✓": "ja"}


def _find_font() -> tuple[str, str] | None:
    for reg, bold in _FONT_CANDIDATES:
        if Path(reg).is_file():
            return reg, bold if Path(bold).is_file() else reg
    return None


# Wording checked against the original BMF-Schreiben vom 06.03.2025
# (GZ IV C 1 - S 2256/00042/064/043, DOK COO.7005.100.4.11527963).
# Abschnitt 9 — für Leserinnen und Leser ohne Bitcoin-Vorkenntnisse.
EXPLAIN_BASICS: tuple[tuple[str, str], ...] = (
    (
        "Bitcoin und die Blockchain",
        "Bitcoin ist eine digitale Währung, die ohne Bank auskommt. Alle Überweisungen werden "
        "in einem öffentlichen Kassenbuch festgehalten, der Blockchain, das auf tausenden "
        "Rechnern weltweit gleichzeitig geführt wird. Jede Buchung trägt Datum, Uhrzeit und "
        "Betrag auf den kleinsten Teilbetrag genau (1 Bitcoin = 100.000.000 Satoshi) und kann "
        "nachträglich nicht mehr verändert werden. Deshalb lässt sich jede Angabe dieses "
        "Berichts von jedermann nachprüfen, etwa mit einem sogenannten Block-Explorer, einer "
        "Suchmaschine für die Blockchain (Rz. 20, 101).",
    ),
    (
        "Wallet, Adresse und öffentlicher Schlüssel (xpub)",
        "Eine Wallet ist mit einem Depot vergleichbar. Sie verwaltet viele Adressen — ähnlich "
        "Unterkonten; für jeden Zahlungseingang wird üblicherweise eine neue Adresse verwendet, "
        "daher die große Zahl von Adressen. Der öffentliche Schlüssel (xpub) ist eine Art "
        "Leseschlüssel: Aus ihm lassen sich alle Adressen einer Wallet und deren Buchungen "
        "ableiten, Bitcoin bewegen lässt sich damit aber nicht. Für diesen Bericht wurden "
        "ausschließlich diese Leseschlüssel verwendet; sie stehen in Abschnitt 1, damit die "
        "Angaben unabhängig geprüft werden können (Rz. 87).",
    ),
    (
        "Bitcoin-Beträge funktionieren wie Geldscheine",
        "Bitcoin wird nicht als Kontostand geführt, sondern in Einzelbeträgen (technisch: UTXO): "
        "Jeder Zahlungseingang ist ein Einzelbetrag in genau dieser Höhe. Wer bezahlt, gibt "
        "ganze Einzelbeträge aus und erhält den Rest als Wechselgeld zurück — wie mit einem "
        "Geldschein an der Kasse. Weil jeder Einzelbetrag erkennbar bleibt, lässt sich für jede "
        "Veräußerung feststellen, wann genau die ausgegebenen Bitcoin angeschafft wurden "
        "(Einzelbetrachtung, Rz. 61). Das Wechselgeld behält das ursprüngliche "
        "Anschaffungsdatum (Rz. 56). Der Bericht führt den Bestand deshalb in Teilbeständen: "
        "Ein Teilbestand ist der Teil eines Zuflusses mit eigenem Anschaffungsdatum und "
        "eigener Herkunft.",
    ),
    (
        "Zufluss, Umbuchung, Abfluss",
        "Der Bericht betrachtet alle angegebenen Wallets gemeinsam. Ein Zufluss bedeutet: "
        "Bitcoin kommen von außen, etwa von einer Börse nach einem Kauf. Eine Umbuchung ist "
        "eine Bewegung zwischen den eigenen Wallets — wie eine Überweisung zwischen zwei "
        "eigenen Konten. Sie ist keine Veräußerung, das Anschaffungsdatum bleibt erhalten "
        "(Rz. 54). Ein Abfluss bedeutet: Bitcoin gehen an eine fremde Adresse, zum Beispiel "
        "an eine Börse zum Verkauf.",
    ),
    (
        "Gebühren",
        "Jede Überweisung kostet eine kleine Netzwerkgebühr, die in Bitcoin bezahlt wird. "
        "Gebühren im Zusammenhang mit einem Verkauf mindern den Gewinn als Werbungskosten "
        "(Rz. 59); Gebühren bei Umbuchungen verringern lediglich den Bestand.",
    ),
    (
        "Börse und Wallet",
        "Gekauft und verkauft wird meist über eine Börse (Handelsplattform). Von dort werden "
        "gekaufte Bitcoin in die eigene Wallet übertragen, und zum Verkauf gehen sie wieder "
        "an die Börse. Steuerlich maßgeblich ist der Zeitpunkt des Handels auf der Börse "
        "(Rz. 20, 55). Liegen Kontoauszüge der Börse vor, verwendet der Bericht deren Daten; "
        "sonst den Tag, an dem die Bitcoin in die Wallet kamen bzw. sie verließen. "
        "Abschnitt 5 führt die Käufe ohne Beleg einzeln auf.",
    ),
    (
        "Lightning",
        "Lightning ist ein Zahlungsnetz, das auf Bitcoin aufsetzt. Bitcoin werden dafür in einen "
        "Zahlungskanal eingebracht — das ist eine Buchung auf der Blockchain. Zahlungen innerhalb "
        "des Kanals laufen danach schnell und günstig außerhalb der Blockchain und stehen dort "
        "nicht einzeln. Erst wenn Bitcoin das Lightning-Netz wieder verlassen (Kanal schließen oder "
        "Tausch zurück, „Swap“), entsteht wieder eine Buchung. Dieser Bericht sieht deshalb nur "
        "die Ein- und Ausgänge der betrachteten Wallets: Wechseln Bitcoin in eine Lightning-Wallet "
        "oder zu einem Dienst, der Lightning nutzt, erscheint das als Abfluss bzw. Zufluss. Eine "
        "Übertragung zwischen eigenen Wallets oder Konten über Lightning ist keine Veräußerung "
        "(Rz. 54).",
    ),
    (
        "Haltefrist",
        "Werden Bitcoin länger als ein Jahr gehalten, ist der Gewinn aus einem Verkauf nicht "
        "steuerpflichtig (§ 23 Abs. 1 Satz 1 Nr. 2 EStG). Innerhalb eines Jahres ist er "
        "steuerpflichtig, sofern der Gesamtgewinn aller privaten Veräußerungsgeschäfte des "
        "Jahres die Freigrenze erreicht (bis 2023: 600 €, ab 2024: 1.000 €).",
    ),
)

# Abschnitt 4 — warum nicht jeder Kauf/Verkauf belegt werden kann.
EXPLAIN_EVIDENCE: tuple[tuple[str, str], ...] = (
    (
        "Haltefrist",
        "Nach mehr als einem Jahr Haltedauer ist ein Veräußerungsgewinn nicht steuerbar "
        "(§ 23 Abs. 1 Satz 1 Nr. 2 EStG). Maßgeblich ist dann nur der Anschaffungszeitpunkt; "
        "er ist in der Blockchain dokumentiert.",
    ),
    (
        "Rechtslage",
        "Kryptowerte sind andere Wirtschaftsgüter im Sinne des § 23 EStG (BFH, Urteil vom "
        "14.02.2023, IX R 3/22). Vorgaben zu Aufzeichnungen: BMF-Schreiben vom 10.05.2022, neu "
        "gefasst am 06.03.2025 (Rz. 87 ff.); abweichende Aufzeichnungen werden bis einschließlich "
        "Veranlagungszeitraum 2024 nicht beanstandet (Rz. 106).",
    ),
    (
        "Aufbewahrung",
        "Eine Aufbewahrungspflicht für Privatpersonen besteht erst bei Überschusseinkünften über "
        "500.000 € im Jahr (§ 147a AO; Rz. 105).",
    ),
    (
        "Ersatzwerte ohne Beleg",
        "Ohne Kaufbeleg gilt der Tag des Zuflusses in die Wallet als Anschaffung, bewertet zum "
        "Tageskurs nach der dokumentierten Kursregel (Rz. 91); nicht belegte Abflüsse gelten als "
        "Veräußerung am Abflusstag. Fehlende Nachweise sind im Wege der Schätzung zu würdigen, "
        "die keinen Sanktionscharakter hat (Rz. 92; BFH, Urteil vom 29.05.2008, VI R 11/07).",
    ),
)

# Insolvent platforms: named in the report (Abschnitt 4) when they occur as a
# counterparty — explains why no account statements exist.
DEFUNCT_PLATFORMS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("ftx",),
        "FTX",
        "Auszahlungen am 08.11.2022 gestoppt, Insolvenzantrag (Chapter 11, USA) am 11.11.2022. "
        "Kontoauszüge der Plattform nicht mehr abrufbar; ggf. Unterlagen aus dem "
        "Insolvenzverfahren (Claims-Portal).",
    ),
    (
        ("mt. gox", "mt.gox", "mtgox", "mt gox"),
        "Mt. Gox",
        "Auszahlungen am 07.02.2014 gestoppt, Insolvenzantrag in Tokio am 28.02.2014; seitdem "
        "Insolvenz- bzw. Sanierungsverfahren. Kontoauszüge sind über die Plattform nicht mehr "
        "abrufbar.",
    ),
    (
        ("celsius",),
        "Celsius Network",
        "Auszahlungen am 12.06.2022 gestoppt, Insolvenzantrag (Chapter 11, USA) am 13.07.2022. "
        "Kontoauszüge sind über die Plattform nicht mehr abrufbar.",
    ),
    (
        ("voyager",),
        "Voyager Digital",
        "Handel und Auszahlungen am 01.07.2022 ausgesetzt, Insolvenzantrag (Chapter 11, USA) am "
        "05.07.2022. Kontoauszüge sind über die Plattform nicht mehr abrufbar.",
    ),
    (
        ("blockfi",),
        "BlockFi",
        "Auszahlungen am 10.11.2022 gestoppt, Insolvenzantrag (Chapter 11, USA) am 28.11.2022. "
        "Kontoauszüge sind über die Plattform nicht mehr abrufbar.",
    ),
)


def _and_list(items: list[str]) -> str:
    """„A“, „A und B“, „A, B und C“."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " und " + items[-1]


def defunct_platforms_in(names: Iterable[str]) -> list[tuple[str, str]]:
    """Insolvent platforms among the report's counterparty names."""
    lowered = [str(n).lower() for n in names if n]
    return [
        (label, text)
        for keys, label, text in DEFUNCT_PLATFORMS
        if any(k in n for n in lowered for k in keys)
    ]


INK = (20, 24, 32)
MUTED = (95, 102, 115)
ACCENT = (32, 76, 128)
RULE = (200, 205, 212)
UNPROVEN = (30, 90, 180)  # blau: Zufluss ohne Kaufbeleg
HEAD_FILL = (232, 237, 244)
ZEBRA = (247, 248, 250)


class _Blue(str):
    """Tabellenzelle, die blau gedruckt wird (Wert ohne Kaufbeleg)."""


# Page legend: every marker used on a page is explained in its footer.
_YEAR_STAR = re.compile(r"\b\d{4} \*")


def _mark_legend(rep: HerkunftReport) -> list[tuple[str, str]]:
    return [
        (
            "¹",
            "Anschaffung laut Börsen-Export: Kaufdatum belegt; Kaufpreis inkl. Gebühren laut Export, "
            "wo dort kein Betrag steht zum Tageskurs (dann „(Tageskurs)“)",
        ),
        ("²", "Zufluss nach dem Stichtag — nicht in Zwischensumme und Bestand enthalten"),
        (
            "³",
            "Zuordnung der Gegenstelle nach Angabe des Steuerpflichtigen (eigene Benennung oder "
            "Datumsregel), nicht durch Label oder Börsen-Export belegt — Einzelheiten in Abschnitt 7",
        ),
        (
            "⁴",
            f"Alt/Neu = angeschafft bis/nach {fmt_date(reform_cutoff(rep).isoformat())} "
            "(Referentenentwurf Kryptosteuer-Reform, "
            "nicht beschlossen)",
        ),
        (
            "⁵",
            "Tausch gegen einen anderen Kryptowert laut Export — Hingabe des anderen Kryptowerts ist eine "
            "eigene Veräußerung (Rz. 54); hier nur gekennzeichnet, nicht bewertet",
        ),
        (
            "⁶",
            "Satoshi-Test: kleiner Betrag aus der eigenen Wallet an die Börse als Nachweis der "
            "Wallet-Inhaberschaft (EU-Geldtransferverordnung (EU) 2023/1113, ab 30.12.2024); Umbuchung "
            "auf das eigene Börsenkonto, keine Veräußerung (Rz. 54); mit der Auszahlung zurück",
        ),
        *(
            [(REQ_MARK, "Transaktions-Hash auf Anforderung (nicht im Transaktionsverzeichnis, Anhang D)")]
            if is_fa(rep)
            else []
        ),
        ("↳", "einzelner belegter Kauf des darüber stehenden Zuflusses"),
        ("✓", "stimmt mit der Blockchain überein"),
        ("✗", "Abweichung — Zahlen prüfen"),
        ("*", "Jahr mit *: für einzelne Tage fehlt ein Kurs, Werte unvollständig"),
    ]


# Fußnoten fortlaufend: Zuordnung alter → neuer Marker für den zweiten Durchlauf von render_pdf
_FOOTNOTE_ORDER = ("¹", "²", "³", "⁴", "⁵", "⁶")
_FOOTNOTE_REMAP: ContextVar[dict[str, str] | None] = ContextVar("_FOOTNOTE_REMAP", default=None)
_LAST_MARKS: ContextVar[dict[int, set[str]] | None] = ContextVar("_LAST_MARKS", default=None)
# Alles, was der letzte Durchlauf gezeichnet hat: (Seite, Kopf-/Fußzeile?, Text) — fürs Prüfprotokoll
_TEXT_LOG: ContextVar[list[tuple[int, bool, str]] | None] = ContextVar("_TEXT_LOG", default=None)
# Platz unten für Fußnoten und Fußzeile (mm) und im letzten Durchlauf fehlender Platz: ragt der
# Text in die Fußnoten einer Seite, rendert render_pdf mit größerem Rand neu
_PAGE_MARGIN: ContextVar[float] = ContextVar("_PAGE_MARGIN", default=40.0)
_OVERLAP: ContextVar[list[float] | None] = ContextVar("_OVERLAP", default=None)
# Tabellenköpfe des letzten Durchlaufs — sie wiederholen sich nach jedem Seitenumbruch
_TABLE_HEADS: ContextVar[set[str] | None] = ContextVar("_TABLE_HEADS", default=None)


def _make_doc(
    rep: HerkunftReport, *, running_title: str, doc_title: str, released: set[str] | None = None,
    legend_text: dict[str, str] | None = None,
) -> Any:
    """Shared PDF setup (fonts, header/footer, helpers) for all Finanzamt PDFs. ``released``:
    Freigabeliste der Finanzamt-Fassung (Standard: released_txids des Herkunftsnachweises)."""
    from types import SimpleNamespace

    from fpdf import FPDF
    from fpdf.enums import TableCellFillMode
    from fpdf.fonts import FontFace

    font_files = _find_font()
    unicode_ok = font_files is not None
    P = rep.privacy
    if is_fa(rep):
        released = released if released is not None else released_txids(rep)
    else:
        released = None

    def t(s: str) -> str:
        if released is not None:
            s = fa_redact(s, released)
        if unicode_ok:
            return s
        for k, v in _ASCII_FALLBACK.items():
            s = s.replace(k, v)
        return s.encode("latin-1", "replace").decode("latin-1")

    stand = rep.generated_at.strftime("%Y-%m-%d %H:%M:%S")

    remap = _FOOTNOTE_REMAP.get()
    table_ = str.maketrans(remap) if remap else None
    # zweiter Durchlauf: nur die verwendeten Fußnoten, neu nummeriert — unbenutzte fallen weg,
    # sonst trüge z. B. die unbenutzte ² dieselbe Nummer wie die neu nummerierte ³ → ²
    # (Marker nach Umnummerierung, Marker wie im Quelltext, Text) — die Fußzeile schreibt den
    # Quelltext-Marker; cell/multi_cell nummerieren ihn wie jeden anderen Text genau einmal um
    legend = [
        (remap.get(m, m) if remap else m, m, (legend_text or {}).get(m, txt))
        for m, txt in _mark_legend(rep)
        if not remap or m not in _FOOTNOTE_ORDER or m in remap
    ]
    marks: dict[int, set[str]] = {}
    _LAST_MARKS.set(marks)
    log: list[tuple[int, bool, str]] = []
    _TEXT_LOG.set(log)
    heads: set[str] = set()
    _TABLE_HEADS.set(heads)
    overlap = [0.0]
    _OVERLAP.set(overlap)

    def renum(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
        if table_ is None:
            return args, kwargs
        if isinstance(kwargs.get("text"), str):
            kwargs["text"] = kwargs["text"].translate(table_)
        elif len(args) > 2 and isinstance(args[2], str):
            args = (*args[:2], args[2].translate(table_), *args[3:])
        return args, kwargs

    def note_marks(page: int, text: Any) -> None:
        txt = str(text or "")
        found = {m for m, _o, _t in legend if m != "*" and m in txt}
        if _YEAR_STAR.search(txt):
            found.add("*")
        if found:
            marks.setdefault(page, set()).update(found)

    class Doc(FPDF):
        in_footer = False

        def cell(self, *args: Any, **kwargs: Any) -> Any:
            args, kwargs = renum(args, kwargs)
            text = kwargs.get("text", args[2] if len(args) > 2 else "")
            if not self.in_footer:
                note_marks(self.page, text)
            log.append((self.page, self.in_footer, str(text or "")))
            return super().cell(*args, **kwargs)

        def multi_cell(self, *args: Any, **kwargs: Any) -> Any:
            args, kwargs = renum(args, kwargs)
            if not (kwargs.get("dry_run") or kwargs.get("split_only")):
                text = kwargs.get("text", args[2] if len(args) > 2 else "")
                if not self.in_footer:
                    note_marks(self.page, text)
                log.append((self.page, self.in_footer, str(text or "")))
            return super().multi_cell(*args, **kwargs)

        def header(self) -> None:
            if self.page_no() == 1:
                return
            self.in_footer = True  # Kopfzeile: wie die Fußzeile kein Seiteninhalt
            self.set_font(FONT, "", 8)
            self.set_text_color(*MUTED)
            parts = [running_title, f"Stand {stand}"]
            if rep.person_name:
                parts.append(rep.person_name)
            if rep.tax_id:
                parts.append(f"Steuer-ID {MASK if P else rep.tax_id}")
            self.cell(0, 5, t(" · ".join(parts)), align="L")
            self.ln(7)
            self.in_footer = False

        def footer(self) -> None:
            body_bottom = self.get_y()  # Ende des Seiteninhalts
            self.in_footer = True
            used = [(orig, txt) for m, orig, txt in legend if m in marks.get(self.page, set())]
            if used:
                self.set_font(FONT, "", 6.8)
                self.set_text_color(*MUTED)
                ind = 3.2  # hängender Einzug: Zeichen links, Folgezeilen unter dem Text
                n = sum(
                    len(self.multi_cell(self.epw - ind, 3.2, t(txt), dry_run=True, output="LINES"))
                    for _m, txt in used
                )
                self.set_y(-14 - 3.2 * n - 1)
                # Fußnoten beginnen oberhalb des Inhaltsendes → Überschneidung merken
                overlap[0] = max(overlap[0], body_bottom + 1 - self.get_y())
                for m, txt in used:
                    y = self.get_y()
                    self.set_x(self.l_margin)
                    self.cell(ind, 3.2, t(m))
                    self.set_xy(self.l_margin + ind, y)
                    self.multi_cell(self.epw - ind, 3.2, t(txt), align="L", new_x="LMARGIN", new_y="NEXT")
            self.set_y(-14)
            self.set_line_width(0.2)  # Grafiken können eine dickere Linie eingestellt haben
            self.set_draw_color(*RULE)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.set_font(FONT, "", 7.5)
            self.set_text_color(*MUTED)
            self.cell(
                0,
                6,
                t(
                    f"Erstellt mit BTC-Herkunft {build_id()}".rstrip()
                    + " · Referenzkurse, keine Steuerberatung"
                ),
                align="L",
            )
            self.set_x(self.l_margin)
            self.cell(0, 6, t(f"Seite {self.page_no()} von {{nb}}"), align="R")
            self.in_footer = False

    pdf = Doc(orientation="P", unit="mm", format="A4")
    if font_files:
        pdf.add_font("Body", "", font_files[0])
        pdf.add_font("Body", "B", font_files[1])
        FONT = "Body"
        fallbacks = []
        for i, sym in enumerate(c for c in _SYMBOL_FONT_CANDIDATES if Path(c).is_file()):
            if Path(sym) != Path(font_files[0]):
                pdf.add_font(f"Sym{i}", "", sym)
                fallbacks.append(f"Sym{i}")
        if fallbacks:
            pdf.set_fallback_fonts(fallbacks, exact_match=False)
    else:
        FONT = "Helvetica"
    pdf.set_margins(18, 16, 18)
    # room for the page legend (up to seven short lines) above the footer
    pdf.set_auto_page_break(auto=True, margin=_PAGE_MARGIN.get())
    pdf.alias_nb_pages()
    pdf.set_title(t(doc_title))
    pdf.set_creator("BTC-Herkunft")
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    def para(text: str, size: float = 9, *, bold: bool = False, color=INK, h: float = 4.6) -> None:
        pdf.set_font(FONT, "B" if bold else "", size)
        pdf.set_text_color(*color)
        for part in text.split("\n") if "• " in text else [text]:
            pdf.set_x(pdf.l_margin)
            if part.startswith("• "):
                # Aufzählung mit hängendem Einzug: Folgezeilen beginnen unter dem Text, nicht unter dem Punkt
                indent = pdf.get_string_width(t("• ")) + 0.6
                if pdf.get_y() + h > pdf.page_break_trigger:
                    pdf.add_page()
                pdf.cell(indent, h, t("•"))
                pdf.multi_cell(pdf.epw - indent, h, t(part[2:]), align="L", new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.multi_cell(pdf.epw, h, t(part), align="L", new_x="LMARGIN", new_y="NEXT")

    def explain(items: tuple[tuple[str, str], ...]) -> None:
        for head, text in items:
            # Überschrift und Text zusammen halten: passt der Block nicht mehr ganz auf die Seite
            # (bis 12 Zeilen), beginnt er auf der nächsten — sonst rutscht ein Rest allein hinüber
            pdf.set_font(FONT, "", 8.8)
            n = len(pdf.multi_cell(pdf.epw, 4.5, t(text), dry_run=True, output="LINES"))
            fits = pdf.get_y() + 5 + 4.5 * n <= pdf.page_break_trigger
            if pdf.get_y() > pdf.page_break_trigger or (n <= 12 and not fits):
                pdf.add_page()
            pdf.set_x(pdf.l_margin)
            pdf.set_font(FONT, "B", 9.5)
            pdf.set_text_color(*ACCENT)
            pdf.multi_cell(pdf.epw, 5, t(head), align="L")
            para(text, 8.8, h=4.5)
            pdf.ln(1.8)

    def section(no: str, title: str, lead: str = "") -> None:
        if pdf.get_y() > pdf.page_break_trigger - 35:
            pdf.add_page()
        # Lesezeichen im PDF + Eintrag im Inhaltsverzeichnis (render_pdf)
        pdf.start_section(t(f"{no}  {title}" if no else title), level=0)
        pdf.ln(3)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 12.5)
        pdf.set_text_color(*ACCENT)
        pdf.cell(pdf.epw, 7, t(f"{no}  {title}" if no else title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*ACCENT)
        pdf.set_line_width(0.4)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + pdf.epw, pdf.get_y())
        pdf.set_line_width(0.2)
        pdf.ln(2)
        if lead:
            para(lead, 8.5, color=MUTED, h=4.2)
            pdf.ln(1.5)

    body_cmap = getattr(pdf.fonts.get("body"), "cmap", None) if font_files else None

    def lead(text: str) -> str:
        """Zelle, die mit einem Zeichen der Ersatzschrift beginnt (z. B. „↳“ bei Segoe UI):
        geschütztes Leerzeichen der Grundschrift davor. Sonst setzt fpdf2 nach einem
        Seitenumbruch die Ersatzschrift als Seitenschrift, und die umbrochenen Zeilen
        erscheinen als Zeichensalat (falsche Schrift zu den Zeichencodes)."""
        if body_cmap and text and ord(text[0]) not in body_cmap:
            return "\u00a0" + text
        return text

    def table(
        header: list[str],
        rows: list[list[Any]],
        widths: list[float],
        aligns: list[str],
        *,
        bold_last: bool = False,
        size: float = 8,
        head_size: float = 7.5,
        markdown: bool = False,
    ) -> None:
        """``markdown``: Zellen dürfen **fett** enthalten (Text vorher mit ``_md_escape``)."""
        pdf.set_font(FONT, "", size)
        pdf.set_text_color(*INK)
        pdf.set_draw_color(*RULE)
        with pdf.table(
            col_widths=widths,
            width=pdf.epw,
            text_align=tuple(aligns),
            line_height=4.6,
            padding=(1.2, 1.6),
            headings_style=FontFace(emphasis="B", size_pt=head_size, fill_color=HEAD_FILL, color=INK),
            cell_fill_color=ZEBRA,
            cell_fill_mode=TableCellFillMode.ROWS,
            borders_layout="HORIZONTAL_LINES",
            repeat_headings=1,
            markdown=markdown,
        ) as tbl:
            r = tbl.row()
            for h in header:
                heads.add(t(h).translate(table_) if table_ else t(h))
                r.cell(_md_escape(t(h)) if markdown else t(h))
            for i, row in enumerate(rows):
                style = FontFace(emphasis="B") if bold_last and i == len(rows) - 1 else None
                rr = tbl.row(style=style)
                for c in row:
                    if isinstance(c, tuple):  # (text, colspan)
                        rr.cell(lead(t(c[0])), colspan=c[1])
                    elif isinstance(c, _Blue):
                        rr.cell(lead(t(c)), style=FontFace(color=UNPROVEN, emphasis=style.emphasis if style else None))
                    else:
                        rr.cell(lead(t(c)))
        pdf.ln(2)

    return SimpleNamespace(
        pdf=pdf, t=t, para=para, explain=explain, section=section, table=table,
        FONT=FONT, W=W, P=P, stand=stand,
    )


def _wallets_by_age(rep: HerkunftReport, by_wallet: dict[str, list[InflowLine]]) -> list[str]:
    """Wallet-Namen nach erstem Zufluss sortiert (älteste zuerst); ohne Daten ans Ende."""
    first_seen = {w.name: w.first for w in rep.wallets}
    names = [w.name for w in rep.wallets] + [n for n in by_wallet if n not in first_seen]

    def first(name: str) -> str:
        dates = [x.date for x in by_wallet.get(name, [])]
        if first_seen.get(name):
            dates.append(str(first_seen[name])[:10])
        return min(dates) if dates else "9999-12-31"

    return sorted(names, key=first)  # stabil: gleiche Tage behalten die Eingabereihenfolge


def _is_transit(rep: HerkunftReport, name: str) -> bool:
    """Durchgangs-Wallet: bis zum Stichtag nur Umbuchungen zwischen den betrachteten
    Wallets (kein Zu- oder Abfluss von/an fremde Adressen), am Stichtag leer."""
    r = rep.recon.get(name)
    if r is None or r.ext_in or r.ext_out or not (r.int_in or r.int_out):
        return False
    return (rep.stichtag_balances or {}).get(name, r.computed) == 0


def _transfer_rows(r: WalletRecon, P: bool) -> list[list[str]]:
    """Umbuchungen innerhalb der betrachteten Wallets, je Gegen-Wallet eine Zeile."""
    rows = [
        [f"+ Umbuchung von „{_name(src, P)}“", fmt_btc(v, P, unit=False)]
        for src, v in sorted(r.int_in_from.items(), key=lambda kv: -kv[1])
    ]
    rest_in = r.int_in - sum(r.int_in_from.values())
    if rest_in:
        rows.append(["+ Umbuchungen aus anderen betrachteten Wallets" + (" (übrige)" if rows else ""),
                     fmt_btc(rest_in, P, unit=False)])
    out_rows = [
        [f"− Umbuchung an „{_name(dst, P)}“", fmt_btc(v, P, unit=False)]
        for dst, v in sorted(r.int_out_to.items(), key=lambda kv: -kv[1])
    ]
    rest_out = r.int_out - sum(r.int_out_to.values())
    if rest_out:
        out_rows.append(["− Umbuchungen in andere betrachtete Wallets" + (" (übrige)" if out_rows else ""),
                         fmt_btc(rest_out, P, unit=False)])
    return rows + out_rows


FLOW_PROVEN = (40, 150, 90)  # grün: Zufluss mit Kaufbeleg
FLOW_TRANSFER = (95, 102, 115)
FLOW_EXIT = (200, 70, 70)
_SRC, _SINK = "\x00in", "\x00out"


def wallet_flow_edges(rep: HerkunftReport, order: list[str]) -> list[tuple[str, str, str, int]]:
    """Kanten des Wallet-Flusses bis zum Stichtag: (von, nach, Art, sats).

    Art: "proven" / "unproven" (Zufluss mit/ohne Kaufbeleg), "transfer"
    (Umbuchung zwischen betrachteten Wallets), "exit" (Abfluss an fremde
    Adressen). Gleiche Zahlen wie die Überleitungen in Abschnitt 2.
    """
    cut = rep.stichtag.isoformat()
    edges: list[tuple[str, str, str, int]] = []
    for name in order:
        r = rep.recon.get(name)
        if r is None:
            continue
        proven = sum(x.sats for x in rep.inflows if x.wallet == name and x.date <= cut and x.acq_source)
        proven = min(proven, r.ext_in)
        if proven:
            edges.append((_SRC, name, "proven", proven))
        if r.ext_in - proven:
            edges.append((_SRC, name, "unproven", r.ext_in - proven))
        for dst, v in sorted(r.int_out_to.items(), key=lambda kv: -kv[1]):
            if v:
                edges.append((name, dst, "transfer", v))
        if r.ext_out:
            edges.append((name, _SINK, "exit", r.ext_out))
    return edges


def _draw_wallet_flow(
    pdf: Any, rep: HerkunftReport, order: list[str], t: Callable[[str], str], font: str,
    *, structure_only: bool = False,
) -> None:
    """Statische Fassung der Grafik „Lot-Fluss“ der Web-Oberfläche: Wallets als
    Kreise (Größe = Bestand am Stichtag, nach Alter im Uhrzeigersinn ab links
    oben), Pfeile mit Menge; links alle Zuflüsse, rechts alle Abflüsse.

    ``structure_only`` (Finanzamt-Fassung): ohne Zahlen und ohne Bestand — Linienstärke
    weiter nach Menge (die Mengen stehen ohnehin in Abschnitt 2), Kreisgröße nach dem
    Durchfluss der Wallet (alles, was hineinkam) statt nach dem Bestand."""
    P = rep.privacy
    at = rep.stichtag_balances or {}
    names = [n for n in order if n in rep.recon]
    edges = wallet_flow_edges(rep, names)
    x0, y0, w = pdf.l_margin, pdf.get_y() + 2, pdf.epw
    # Platz für die Farblegende darunter lassen (sonst rutscht sie unter den Seitenrand)
    h = min(95.0 + 12.0 * len(names), pdf.page_break_trigger - pdf.get_y() - 18)
    cx, cy = x0 + w / 2, y0 + h / 2
    pos: dict[str, tuple[float, float]] = {_SRC: (x0 + 10, cy), _SINK: (x0 + w - 10, cy)}
    n = len(names)
    for i, name in enumerate(names):
        if n == 1:
            pos[name] = (cx, cy)
            continue
        a = math.radians(-160 + 320 * (i + 0.5) / n)
        pos[name] = (cx + (w / 2 - 42) * math.cos(a), cy + (h / 2 - 20) * math.sin(a))
    bal = {nm: at.get(nm, rep.recon[nm].computed) for nm in names}
    if structure_only:  # Durchfluss statt Bestand
        size = {nm: sum(v for _a, b, _k, v in edges if b == nm) for nm in names}
    else:
        size = {nm: max(bal[nm], 0) for nm in names}
    max_size = max([1, *size.values()])
    rad = {nm: 3.0 + 8.0 * math.sqrt(size[nm] / max_size) for nm in names}
    rad[_SRC] = rad[_SINK] = 5.0
    # Beschriftung auf die Seite, von der weniger Linien kommen
    above = {}
    for key, (x, y) in pos.items():
        others = [pos[b if a == key else a][1] for a, b, _k, _v in edges if key in (a, b)]
        above[key] = bool(others) and sum(others) / len(others) > y + 1
    # Leerraum oben/unten abschneiden
    top = min(y - rad[k] - (8 if above[k] else 0) for k, (_x, y) in pos.items())
    shift = y0 + 2 - top
    pos = {k: (x, y + shift) for k, (x, y) in pos.items()}
    bottom = max(y + rad[k] + (2 if above[k] else 9) for k, (_x, y) in pos.items())
    max_sats = max([1, *(e[3] for e in edges)])
    color = {"proven": FLOW_PROVEN, "unproven": UNPROVEN, "transfer": FLOW_TRANSFER, "exit": FLOW_EXIT}
    labels: list[tuple[float, float, str, tuple[int, int, int]]] = []
    pairs: dict[tuple[str, str], int] = {}
    for src, dst, kind, sats in edges:
        (ax, ay), (bx, by) = pos[src], pos[dst]
        k = pairs[(src, dst)] = pairs.get((src, dst), 0) + 1  # zweite Kante gleicher Richtung stärker gebogen
        dx, dy = bx - ax, by - ay
        d = max(1.0, math.hypot(dx, dy))
        bend = min(22.0, d * 0.18) * (1 if k == 1 else 2.1)
        c = ((ax + bx) / 2 - dy / d * bend, (ay + by) / 2 + dx / d * bend)

        def trim(px: float, py: float, r: float) -> tuple[float, float]:
            vx, vy = c[0] - px, c[1] - py
            ln = max(0.01, math.hypot(vx, vy))
            return px + vx / ln * r, py + vy / ln * r

        s0 = trim(ax, ay, rad[src] + 0.6)
        s1 = trim(bx, by, rad[dst] + 2.2)
        lw = 0.35 + 2.6 * math.sqrt(sats / max_sats)
        pdf.set_draw_color(*color[kind])
        pdf.set_line_width(lw)
        if kind in ("proven", "unproven"):
            pdf.set_dash_pattern(dash=2.2, gap=1.0)
        pdf.bezier([s0, c, s1])
        pdf.set_dash_pattern()
        # Pfeilspitze am Ziel, in Richtung der Tangente
        tx, ty = s1[0] - c[0], s1[1] - c[1]
        tl = max(0.01, math.hypot(tx, ty))
        ux, uy = tx / tl, ty / tl
        hl, hw = 2.2 + lw * 0.6, 1.2 + lw * 0.5
        tip = (s1[0] + ux * 2.0, s1[1] + uy * 2.0)
        pdf.set_fill_color(*color[kind])
        pdf.set_line_width(0.1)
        pdf.polygon(
            [tip, (tip[0] - ux * hl - uy * hw, tip[1] - uy * hl + ux * hw),
             (tip[0] - ux * hl + uy * hw, tip[1] - uy * hl - ux * hw)],
            style="DF",
        )
        mx = 0.25 * s0[0] + 0.5 * c[0] + 0.25 * s1[0]
        my = 0.25 * s0[1] + 0.5 * c[1] + 0.25 * s1[1]
        if not structure_only:
            labels.append((mx, my, fmt_btc(sats, P, unit=False), color[kind]))
    # Kreise und Beschriftung über den Linien
    pdf.set_line_width(0.4)
    for key, (x, y) in pos.items():
        r = rad[key]
        if key == _SRC:
            pdf.set_draw_color(*FLOW_PROVEN)
            pdf.set_fill_color(234, 246, 239)
        elif key == _SINK:
            pdf.set_draw_color(*FLOW_EXIT)
            pdf.set_fill_color(250, 236, 236)
        else:
            pdf.set_draw_color(*ACCENT)
            pdf.set_fill_color(*HEAD_FILL)
        pdf.circle(x, y, r, style="DF")
    for mx, my, text, col in labels:
        pdf.set_font(font, "", 6.2)
        tw = pdf.get_string_width(t(text)) + 1.6
        pdf.set_fill_color(255, 255, 255)
        pdf.set_text_color(*col)
        pdf.set_xy(mx - tw / 2, my - 1.6)
        pdf.cell(tw, 3.2, t(text), align="C", fill=True)
    def tag(x: float, y: float, text: str, size: float, bold: bool, col: tuple[int, int, int]) -> None:
        pdf.set_font(font, "B" if bold else "", size)
        tw = pdf.get_string_width(t(text)) + 1.4
        pdf.set_fill_color(255, 255, 255)
        pdf.set_text_color(*col)
        pdf.set_xy(x - tw / 2, y)
        pdf.cell(tw, size * 0.45, t(text), align="C", fill=True)

    for key, (x, y) in pos.items():
        r = rad[key]
        if key in (_SRC, _SINK):
            label, sub = ("Zuflüsse" if key == _SRC else "Abflüsse"), "fremde Adressen"
        else:
            label = _name(key, P)
            label = label if len(label) <= 24 else label[:22].rstrip() + " …"
            sub = "" if structure_only else f"Bestand {fmt_btc(bal[key], P)}"
        ly = y - r - 7.4 if above[key] else y + r + 0.8
        tag(x, ly, label, 7, True, INK)
        if sub:
            tag(x, ly + 3.2, sub, 6.2, False, MUTED)
    pdf.set_line_width(0.2)
    pdf.set_draw_color(*RULE)
    pdf.set_text_color(*INK)
    pdf.set_y(bottom + 3)


def _thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def stichtag_in_future(rep: HerkunftReport) -> bool:
    return rep.stichtag > rep.generated_at.date()


def bestand_label(rep: HerkunftReport, *, note: bool = True) -> str:
    """„Bestand zum Stichtag 31.12.2025“ — liegt der Stichtag nach dem Erstellungstag:
    „Bestand am <Erstellungstag> (Stichtag … liegt in der Zukunft; …)“."""
    stichtag = fmt_date(rep.stichtag.isoformat())
    if not stichtag_in_future(rep):
        return f"Bestand zum Stichtag {stichtag}"
    label = f"Bestand am {rep.generated_at:%d.%m.%Y}"
    if note:
        label += (
            f" (Stichtag {stichtag} liegt in der Zukunft; Bestand bis dahin fortgeschrieben "
            "ohne weitere Bewegungen)"
        )
    return label


def deviations(rep: HerkunftReport) -> list[str]:
    """Alle Einzelprüfungen mit ✗ (Abschnitt 2): Zufluss aus Börsen-Auszahlung,
    der nicht aufgeht, und Wallet-Überleitungen, die nicht aufgehen —
    je „TT.MM.JJJJ, Wallet“."""
    out: list[str] = []
    first = {}
    for x in rep.inflows:
        first.setdefault(x.txid, x)
    for txid, rc in rep.withdrawal_recon.items():
        if rc.get("ok", True):
            continue
        x = first.get(txid)
        day = x.date if x is not None else str(rc.get("day") or "")
        wallet = _name(x.wallet, rep.privacy) if x is not None else str(rc.get("exchange") or "")
        out.append(f"{fmt_date(day)}, {wallet}")
    cut = rep.stichtag.isoformat()
    for name, r in rep.recon.items():
        target = (rep.stichtag_balances or {}).get(name, r.balance)
        if not r.computed == r.balance == target:
            out.append(f"Überleitung {fmt_date(cut)}, {_name(name, rep.privacy)}")
    return sorted(out, key=lambda s: (s[6:10], s[3:5], s[:2]) if s[:1].isdigit() else ("9", s))


def anlage_so_rows(rep: HerkunftReport) -> list[dict[str, Any]]:
    """Eine Zeile je Kalenderjahr mit Bewegungen (erstes bis letztes Jahr, lückenlos):
    Anzahl Veräußerungen < 1 Jahr, Gewinn/Verlust < 1 Jahr, Hinweis."""
    years: set[int] = set()
    for w in rep.wallets:
        for v in (w.first, w.last):
            if v:
                years.add(int(str(v)[:4]))
    years.update(int(x.date[:4]) for x in rep.inflows if x.date)
    by_year = {y.year: y for y in rep.years}
    years.update(y for y, s in by_year.items() if s.details)
    cut = rep.stichtag.year
    years = {y for y in years if y <= max(cut, rep.generated_at.year)}
    if not years:
        return []
    out = []
    for year in range(min(years), max(years) + 1):
        y = by_year.get(year)
        ds = [d for d in (y.details if y else []) if d.get("disposal", True)]
        # ein Veräußerungsgeschäft = eine Transaktion bzw. ein Verkauf laut Export; es kann
        # mehrere Teilbestände (Zeilen) umfassen
        short = list({
            (d.get("txid") or "", d.get("exit_date"), d.get("status") or "", d.get("counterparty") or ""): d
            for d in ds
            if d.get("short_term")
        }.values())
        gain = y.short.gain_eur if y else 0.0
        incomplete = bool(y and y.short.missing_price)
        if not ds:
            note = "keine Veräußerung"
        elif not short:
            note = "nur Veräußerungen nach Ablauf der Haltefrist (nicht steuerbar)"
        else:
            fg = freigrenze_eur(year)  # Regelwerk des Jahres (nur Jahre mit Veräußerung)
            note = (
                f"Freigrenze {fg:,} € ".replace(",", ".")
                + ("erreicht" if gain >= fg else "nicht erreicht")
                + (" (unvollständig)" if incomplete else "")
            )
        out.append({"year": year, "count": len(short), "gain": gain, "note": note, "incomplete": incomplete})
    return out


def _base_name(name: str) -> str:
    """„bitvavo-alt³“, „Bitvavo old“, „Bitvavo“ → „bitvavo“ (wie Börsennamen aus Dateinamen)."""
    from pathlib import Path

    from btc_origin.local_files import exchange_name_from_file

    plain = name.replace("³", "").strip()
    if re.fullmatch(r"ext-\d+", plain, re.IGNORECASE):
        return plain.lower()  # automatische Namen: jede Nummer ist eine eigene Gegenstelle
    return exchange_name_from_file(Path(plain + ".csv")).lower()


def inflow_explanation(rules: Iterable[tuple[str, date | None, str, str]], x: InflowLine) -> str:
    """Erläuterung aus erlaeuterungen.csv für einen Zufluss ohne Beleg: Art „Zufluss“,
    Börse = Absender (ohne ³), Datum leer oder gleich dem Zuflusstag."""
    source = _base_name(x.source)
    for r_ex, r_day, r_art, text in rules:
        if not r_art.strip().lower().startswith("zufluss") or _base_name(r_ex) != source:
            continue
        if r_day is not None and r_day.isoformat() != x.date[:10]:
            continue
        return text
    return ""


def outflow_explanation(rules: Iterable[tuple[str, date | None, str, str]], counterparty: str, day: str) -> str:
    """Erläuterung aus erlaeuterungen.csv für einen Abfluss ohne Verkaufsbeleg: Art „Abfluss“,
    Börse = Empfänger (ohne ³), Datum leer oder gleich dem Abflusstag."""
    target = _base_name(counterparty)
    for r_ex, r_day, r_art, text in rules:
        if not r_art.strip().lower().startswith("abfluss") or _base_name(r_ex) != target:
            continue
        if r_day is not None and r_day.isoformat() != day[:10]:
            continue
        return text
    return ""


def assumed_disposals(rep: HerkunftReport) -> list[dict[str, Any]]:
    """Abflüsse an fremde Adressen ohne Verkaufsbeleg — als Veräußerung am Abflusstag
    angenommen. Eine Zeile je Transaktion und Empfänger (Teilbestände zusammengefasst)."""
    wallet_of = {o["txid"]: name for name, r in rep.recon.items() for o in r.out_lines}
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for y in sorted(rep.years, key=lambda y: y.year):
        for d in y.details:
            if (d.get("origin", "wallet") != "wallet" or not d.get("disposal", True) or d.get("status")):
                continue
            key = (str(d.get("txid") or ""), str(d["exit_date"]), str(d.get("counterparty") or ""))
            g = groups.setdefault(key, {
                "day": key[1], "txid": key[0], "counterparty": key[2], "wallet": wallet_of.get(key[0], ""),
                "address": str(d.get("address") or ""),
                "sats": 0, "proceeds": 0.0, "gain": 0.0, "priced": False,
            })
            g["sats"] += int(d["btc_sats"])
            if d.get("proceeds_eur") is not None:
                g["priced"] = True
                g["proceeds"] += float(d["proceeds_eur"])
                g["gain"] += float(d.get("gain_eur") or 0.0)
    out = sorted(groups.values(), key=lambda g: (g["day"], g["txid"]))
    for g in out:
        g["note"] = outflow_explanation(rep.explanations, g["counterparty"], g["day"])
    return out


def summary_rows(rep: HerkunftReport) -> list[list[str]]:
    """Zeilen der Zusammenfassung (Ergebnis, Wert, Abschnitt) — gleiche Zahlen wie
    in den Abschnitten, nur verdichtet."""
    P = rep.privacy
    at = rep.stichtag_balances
    bal = {w.name: (at.get(w.name, 0) if at is not None else w.balance_sats) for w in rep.wallets}
    total_at = sum(bal.values())
    holding = sum(1 for v in bal.values() if v > 0)
    px_day = rep.stichtag_price_day or rep.stichtag
    value = (
        f" (≈ {fmt_eur(rep.stichtag_price * total_at / SATS_PER_BTC, P)}, "
        f"Kurs vom {fmt_date(px_day.isoformat())})"
        if rep.stichtag_price is not None and total_at
        else ""
    )
    details = [d for y in rep.years for d in y.details if d.get("origin", "wallet") == "wallet"]
    in_sum = sum(x.sats for x in rep.inflows) if rep.inflows else rep.inflow_sats
    out_sum = sum(int(d["btc_sats"]) for d in details) if details else rep.outflow_sats
    lot_sum = sum(x.sats for x in rep.lots)
    chain = rep.chain_balance_sats if rep.chain_balance_sats is not None else rep.balance_sats
    if rep.disposal_fees_sats is None or rep.transfer_fees_sats is None:
        fees = max(0, in_sum - out_sum - lot_sum)
    else:
        fees = rep.disposal_fees_sats + rep.transfer_fees_sats
    exact = in_sum - out_sum - fees == lot_sum == chain and rep.consistent
    devs = deviations(rep)
    open_lots = [x for x in rep.lots if not x.acq_source and not x.qualifies]
    last_free = max(
        (tax_free_from(date.fromisoformat(x.acquisition)) for x in open_lots), default=None
    )
    no_buy = [x for x in rep.inflows if not x.acq_source]
    assumed = assumed_disposals(rep)
    in_work = sum(
        1 for r in rep.open_exchange if r["note"] == "offen" or re.search(r"klär|prüf", str(r["note"]), re.I)
    )
    fa = is_fa(rep)
    n_acked = sum(1 for u in rep.unsupported if u.get("quittiert"))
    n_open = len(rep.unsupported) - n_acked
    rows = [
        ["Betrachtete Wallets", str(len(rep.wallets)) if fa else f"{len(rep.wallets)} (davon {holding} mit Bestand zum Stichtag)", "1"],
        *([] if fa else [[bestand_label(rep), fmt_btc(total_at, P) + value, "1"]]),
        (
            [
                "Abstimmung mit der Blockchain",
                f"{len(devs)} Abweichung{'en' if len(devs) > 1 else ''} ({'; '.join(devs)}) ✗",
                "2",
            ]
            if exact and devs
            else [
                "Abstimmung mit der Blockchain",
                "stimmt mit der Blockchain überein ✓" if exact else "Abweichung — bitte prüfen ✗",
                "1",
            ]
        ),
        [
            "Zuflüsse ohne Kaufbeleg (Anschaffung = Zuflusstag, Tageskurs)",
            f"{len(no_buy)}" if no_buy else "keine",
            "5",
        ],
        *(
            [[
                "Abflüsse ohne Verkaufsbeleg (Veräußerung angenommen)",
                f"{len(assumed)}"
                + (", alle mit Erläuterung" if all(g["note"] for g in assumed)
                   else f", davon {sum(1 for g in assumed if not g['note'])} ohne Erläuterung"),
                "5",
            ]]
            if assumed
            else []
        ),
        *(
            [[
                "Nicht zugeordnete Börsenvorgänge",
                f"{len(rep.open_exchange)}"
                + (f", davon {in_work} offen bzw. in Klärung" if in_work else ", alle mit Erläuterung"),
                "Anh. B",
            ]]
            if rep.open_exchange
            else []
        ),
        *(
            [[
                "NICHT UNTERSTÜTZTE VORGÄNGE — nicht bewertet, manuell prüfen",
                f"{n_open} ✗" + (f" (weitere {n_acked} geprüft)" if n_acked else ""),
                "8.3",
            ]]
            if n_open
            else [["Nicht unterstützte Vorgänge (geprüft, nicht bewertet)", f"{n_acked}", "8.3"]]
            if n_acked
            else []
        ),
        [
            "Unbelegt gekauft, Haltefrist noch offen",
            (
                f"{len(open_lots)} {'Teilbestand' if len(open_lots) == 1 else 'Teilbestände'}"
                + ("" if fa else f", {fmt_btc(sum(x.sats for x in open_lots), P)}")
                + f"; alle steuerfrei spätestens ab {fmt_date(last_free.isoformat())}"
                if open_lots and last_free
                else "keine"
            ),
            "8",
        ],
    ]
    return rows


def render_pdf(rep: HerkunftReport) -> bytes:
    """Herkunftsanalyse als PDF. Fußnoten werden fortlaufend nummeriert: erst rendern, die
    tatsächlich verwendeten Marker sammeln, bei Lücken (¹ ³ ⁶) mit ¹ ² ³ neu rendern. Ragt der
    Text einer Seite in ihre Fußnoten (lange Legende, breitere Schrift), wird mit entsprechend
    größerem unteren Rand neu gerendert."""
    data = _render_pdf_once(rep)
    used = set().union(*(_LAST_MARKS.get() or {}).values())
    present = [m for m in _FOOTNOTE_ORDER if m in used]
    remap = {m: _FOOTNOTE_ORDER[i] for i, m in enumerate(present)}  # alle verwendeten, auch unverändert
    renumber = not all(m == _FOOTNOTE_ORDER[i] for i, m in enumerate(present))
    margin = _PAGE_MARGIN.get()
    for _ in range(4):
        overlap = (_OVERLAP.get() or [0.0])[0]
        if not renumber and overlap <= 0:
            return data
        renumber = False  # Nummerierung hängt nicht vom Rand ab — einmal genügt
        margin += max(overlap, 0.0) + (2.0 if overlap > 0 else 0.0)
        tokens = (_FOOTNOTE_REMAP.set(remap), _PAGE_MARGIN.set(margin))
        try:
            data = _render_pdf_once(rep)
        finally:
            _PAGE_MARGIN.reset(tokens[1])
            _FOOTNOTE_REMAP.reset(tokens[0])
    return data


_SUPERSCRIPTS = "¹²³⁴⁵⁶"
_RE_HEX64 = re.compile(r"(?<![0-9A-Fa-f])[0-9a-fA-F]{64}(?![0-9A-Fa-f])")
# Beschriftungen, die Bestände zeigen — dürfen in der Finanzamt-Fassung nicht vorkommen
FA_FORBIDDEN_LABELS = (
    "Heute vorhanden", "Bestand (BTC)", "Wert (Tageskurs Stichtag)", "Bestand zum Stichtag",
    "Altbestand", "Kryptosteuer-Reform", "Überleitung zum Bestand",
)


@dataclass
class FaCheck:
    name: str
    ok: bool
    detail: str


def _section_text(
    log: list[tuple[int, bool, str]], start: str, end: str, skip: set[str] = frozenset()
) -> list[str]:
    """Gezeichnete Texte zwischen zwei Abschnittsüberschriften (ohne Kopf-/Fußzeilen, ohne
    Tabellenköpfe ``skip`` — sie wiederholen sich je nach Seitenumbruch —, ohne
    Fußnotenzeichen — deren Nummerierung hängt von der Fassung ab)."""
    body = [txt for _p, footer, txt in log if not footer]
    try:
        i = body.index(start)
        j = body.index(end, i + 1)
    except ValueError:
        return []
    return [
        txt.translate({ord(c): None for c in _SUPERSCRIPTS}).strip()
        for txt in body[i:j]
        if txt not in skip or txt in (start, end)
    ]


def _pdf_text(data: bytes) -> tuple[str | None, dict[str, str]]:
    """Text und Metadaten des fertigen PDFs (pymupdf, falls installiert)."""
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return None, {}
    doc = fitz.open(stream=data, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    text += "\n" + "\n".join(str(e[1]) for e in doc.get_toc())  # Lesezeichen
    return text, {k: str(v or "") for k, v in (doc.metadata or {}).items()}


_RE_TREF = re.compile(r"T-\d{3,}")


def _check_common(
    rep: HerkunftReport, data: bytes, log: list[tuple[int, bool, str]], extracted: str | None,
) -> list[FaCheck]:
    """Prüfungen 1, 2 und 5 für eine Fassung (Kurzreferenzen, Verzeichnis, Fußnoten/TOC)."""
    checks: list[FaCheck] = []
    fa = is_fa(rep)
    released = released_txids(rep) if fa else None
    listed = [x for x in tx_refs(rep) if released is None or x.txid in released]
    listed_refs = {x.ref for x in listed}
    body = [txt for _p, footer, txt in log if not footer]
    head = f"{TX_ANNEX_NO}  {TX_ANNEX_TITLE}"
    end = body.index(head) if head in body else len(body)

    # 1 jede T-Nummer im Verzeichnis aufgelöst oder als „auf Anforderung“ (°) gekennzeichnet
    open_refs = sorted({
        m.group() for txt in body[:end] for m in _RE_TREF.finditer(txt)
        if m.group() not in listed_refs and not txt[m.end():].startswith(REQ_MARK)
    })
    checks.append(FaCheck(
        "1 T-Nummern aufgelöst oder als „auf Anforderung“ gekennzeichnet", not open_refs,
        f"{len(listed_refs)} im Verzeichnis" + (f"; nicht aufgelöst: {', '.join(open_refs[:10])}" if open_refs else ""),
    ))

    # 2 jede Transaktions-ID im Verzeichnis als zusammenhängende Zeichenkette
    if rep.privacy:
        checks.append(FaCheck("2 Transaktions-IDs im Verzeichnis zusammenhängend", True,
                              "maskierte Fassung — IDs als •••, nicht geprüft"))
    else:
        text = extracted if extracted is not None else "\n".join(body[end:])
        tokens = set(re.findall(r"[0-9A-Za-z]+", text))
        broken = [x.ref for x in listed if x.txid not in tokens]
        checks.append(FaCheck(
            "2 Transaktions-IDs im Verzeichnis zusammenhängend", not broken,
            f"{len(listed) - len(broken)} von {len(listed)} als eine Zeichenkette lesbar"
            + (f"; getrennt: {', '.join(broken[:10])}" if broken else ""),
        ))

    # 5 Fußnoten je Seite erklärt, Inhaltsverzeichnis stimmt
    pages: dict[int, tuple[set[str], set[str]]] = {}
    marks = _SUPERSCRIPTS + REQ_MARK
    for page, footer, txt in log:
        body_m, foot_m = pages.setdefault(page, (set(), set()))
        if footer:
            if txt[:1] in marks:
                foot_m.add(txt[:1])
        else:
            body_m.update(c for c in txt if c in marks)
    unexplained = sorted(p for p, (b_, f_) in pages.items() if not b_ <= f_)
    first_page: dict[str, int] = {}
    toc: list[tuple[str, int]] = []
    for i, (page, footer, txt) in enumerate(log):
        if footer:
            continue
        nxt = log[i + 1][2] if i + 1 < len(log) else ""
        if nxt.isdigit() and re.match(r"^(\d+|Anhang [A-Z])  ", txt) and txt in first_page:
            toc.append((txt, int(nxt)))
        else:
            first_page.setdefault(txt, page)
    wrong = [name for name, page in toc if first_page.get(name) != page]
    checks.append(FaCheck(
        "5 Fußnoten je Seite erklärt, Inhaltsverzeichnis stimmt", not unexplained and not wrong and bool(toc),
        f"{len(pages)} Seiten, {len(toc)} Einträge im Inhaltsverzeichnis geprüft"
        + (f"; Fußnote nicht erklärt auf Seite {', '.join(map(str, unexplained))}" if unexplained else "")
        + (f"; Seitenzahl falsch: {', '.join(wrong)}" if wrong else ""),
    ))
    return checks


def _check_fa_only(
    rep: HerkunftReport, data: bytes, log: list[tuple[int, bool, str]], extracted: str | None,
    meta: dict[str, str], released: set[str] | None = None,
) -> list[FaCheck]:
    """Prüfungen 3a–3e der Finanzamt-Fassung (``released``: Freigabeliste, Standard wie im
    Herkunftsnachweis)."""
    released = released if released is not None else released_txids(rep)
    text = extracted if extracted is not None else "\n".join(txt for _p, _f, txt in log)
    checks: list[FaCheck] = []
    n_keys = len(_RE_FA_XKEY.findall(text))
    n_addr = len(_RE_FA_BECH32.findall(text)) + len(_RE_FA_B58.findall(text))
    checks.append(FaCheck(
        "3a Keine xpubs oder Adressen", not n_keys and not n_addr,
        "keine gefunden" if not n_keys and not n_addr else f"{n_keys} Schlüssel, {n_addr} Adressen",
    ))
    foreign = [h for h in _RE_HEX64.findall(text) if h not in released]
    checks.append(FaCheck(
        "3b Keine 64-stelligen Hashes außerhalb der Freigabeliste", not foreign,
        "keine gefunden" if not foreign else f"{len(foreign)} gefunden",
    ))
    tokens = set(re.findall(r"[0-9A-Za-z]+", text))
    printed = set(_RE_HEX64.findall(text)) | {x for x in released if x in tokens}
    missing, extra = released - printed, printed - released
    if rep.privacy:  # maskierte Fassung: freigegebene TxIDs stehen als •••
        missing = set()
    checks.append(FaCheck(
        "3c Abgedruckte Transaktions-IDs = Freigabeliste", not missing and not extra,
        f"{len(released)} freigegeben, {len(printed)} abgedruckt"
        + (" (maskiert: freigegebene als •••)" if rep.privacy else "")
        + (f", {len(missing)} fehlen" if missing else "") + (f", {len(extra)} zu viel" if extra else ""),
    ))
    body = [txt for _p, footer, txt in log if not footer]
    labels = sorted({lab for lab in FA_FORBIDDEN_LABELS for txt in body if lab in txt})
    btc = re.compile(r"^[−-]?\d+,\d{8}( BTC)?$")
    amounts = [
        i for i, txt in enumerate(body)
        if (txt.startswith("= Bestand") and any(btc.match(x) for x in body[i + 1:i + 3]))
        or re.match(r"^Bestand [\d•]", txt)  # Beschriftung der Flussgrafik
    ]
    checks.append(FaCheck(
        "3d Keine Bestandsangaben", not labels and not amounts,
        "keine" if not labels and not amounts
        else "; ".join(labels + ([f"{len(amounts)} Bestandszeile(n) mit Menge"] if amounts else [])),
    ))
    meta_text = " ".join(meta.values()) if meta else fa_redact(TITLE, released)
    meta_bad = bool(_RE_FA_XKEY.search(meta_text) or _RE_FA_BECH32.search(meta_text)
                    or _RE_FA_B58.search(meta_text) or _RE_HEX64.search(meta_text))
    raw_bad = [k for k in (b"/URI", b"/EmbeddedFile", b"/FileAttachment", b"/OCProperties") if k in data]
    checks.append(FaCheck(
        "3e Metadaten ohne Wallet-Daten; keine externen Links, Anhänge oder Ebenen", not meta_bad and not raw_bad,
        ("Metadaten sauber" if not meta_bad else "Metadaten enthalten Wallet-Daten")
        + ("" if not raw_bad else "; gefunden: " + ", ".join(k.decode() for k in raw_bad)),
    ))
    return checks


def rule_years(rep: HerkunftReport) -> set[int]:
    """Jahre, deren Regelwerk der Bericht verwendet: Veräußerungen, Einkünfte nach § 22 Nr. 3
    (Export-Zeilen mit einkunft, Einordnung per T-Nummer) und das Berichtsjahr."""
    years = {y.year for y in rep.years if y.disposals} | {rep.stichtag.year}
    last = (rep.until or rep.stichtag).isoformat()
    years |= {int(str(t["day"])[:4]) for t in rep.exchange_trades if t.get("einkunft") and str(t["day"]) <= last}
    inflow_day = {x.txid: x.date for x in rep.inflows}
    years |= {int(inflow_day[tx][:4]) for tx, m in rep.manual_categories.items()
              if m.get("behandlung") == "einkunft_22_3" and inflow_day.get(tx) and inflow_day[tx] <= last}
    return years


def _unsupported_label(u: dict[str, Any]) -> str:
    d = str(u.get("day") or "")
    what = u.get("tnr") or f"{u['exchange']} „{u['art']}“"
    return f"{d[8:10]}.{d[5:7]}.{d[:4]} {what}"


def _rules_block(rep: HerkunftReport) -> list[str]:
    """Verwendete Regeldateien mit Stand und SHA-256, dazu kategorien.yaml (Wiederholbarkeit)."""
    from btc_origin.regelwerk import load_categories, rules_used

    used = rules_used(rule_years(rep))
    return (
        ["Regelwerk (btc-regeln/):"]
        + [f"  {r.vermerk} · geprüft von {r.geprueft_von} · {r.datei} · SHA-256 {r.sha256}" for r in used]
        + [f"  {name} · SHA-256 {digest}" for name, digest in load_categories().hashes]
    )


def check_reports(rep: HerkunftReport) -> tuple[bytes, bytes, str]:
    """Beide Fassungen aus demselben Lauf erzeugen und gegen die Abnahmekriterien prüfen.

    Gibt (PDF intern, PDF Finanzamt-Fassung, Kontrolldatei) zurück; die Kontrolldatei enthält
    die Prüfprotokolle beider Fassungen und die Freigabeliste der Transaktions-IDs."""
    full_rep = dataclasses_replace(rep, fassung="full")
    fa_rep = dataclasses_replace(rep, fassung="finanzamt")
    full_pdf = render_pdf(full_rep)
    full_log = list(_TEXT_LOG.get() or [])
    heads = set(_TABLE_HEADS.get() or ())
    fa_pdf = render_pdf(fa_rep)
    fa_log = list(_TEXT_LOG.get() or [])
    heads |= set(_TABLE_HEADS.get() or ())
    full_text, _meta = _pdf_text(full_pdf)
    fa_text, fa_meta = _pdf_text(fa_pdf)
    source = "Text aus dem PDF (pymupdf)" if fa_text is not None else "gezeichnete Texte (pymupdf fehlt)"

    full_checks = _check_common(full_rep, full_pdf, full_log, full_text)
    fa_checks = _check_common(fa_rep, fa_pdf, fa_log, fa_text)
    fa_checks[2:2] = _check_fa_only(fa_rep, fa_pdf, fa_log, fa_text, fa_meta)

    # 4 Abschnitt 3 / 3.1 / 3.2 in beiden Fassungen identisch
    start, end = "3  Mögliche Gewinne und Verluste", "4  Belege und Ersatzwerte"
    a, b = _section_text(full_log, start, end, heads), _section_text(fa_log, start, end, heads)
    diff = ""
    if a and a != b:
        # erste Abweichung benennen — Ziffern als #, damit das Protokoll keine Beträge zeigt
        k = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))

        def shown(v: list[str]) -> str:
            return re.sub(r"\d", "#", v[k])[:70] if k < len(v) else "(Ende)"

        diff = f"Abweichung ab Textfeld {k + 1}: intern „{shown(a)}“, Finanzamt „{shown(b)}“"
    open_unsupported = [u for u in rep.unsupported if not u.get("quittiert")]
    both = [FaCheck(
        "4 Abschnitt 3, 3.1, 3.2 in beiden Fassungen identisch", bool(a) and a == b,
        f"{len(b)} Textfelder verglichen, identisch" if a and a == b
        else "Abschnitt 3 nicht gefunden" if not a else diff,
    ), FaCheck(
        "5 Keine nicht unterstützten Vorgänge (Geltungsbereich)", not open_unsupported,
        ("keine" if not rep.unsupported else f"keine offenen; {len(rep.unsupported) - len(open_unsupported)} geprüft")
        if not open_unsupported
        else f"{len(open_unsupported)} Vorgang/Vorgänge nicht unterstützt und nicht quittiert — nicht bewertet, "
        "manuell prüfen (Abschnitt 8.3): " + "; ".join(_unsupported_label(u) for u in open_unsupported[:10])
        + (" …" if len(open_unsupported) > 10 else ""),
    )]

    released = released_txids(fa_rep)
    ok = all(c.ok for c in full_checks + fa_checks + both)
    warnings = [
        f"Quittiert (geprüft, nicht bewertet): {_unsupported_label(u)} — {u['erlaeuterung']}"
        for u in rep.unsupported if u.get("quittiert")
    ] + [f"Quittung ohne passenden Vorgang: {a}" for a in rep.unmatched_acks]

    def block(title: str, checks: list[FaCheck]) -> list[str]:
        good = all(c.ok for c in checks)
        return [f"{title}: {'bestanden' if good else 'NICHT BESTANDEN'}"] + [
            f"  [{'OK' if c.ok else 'FEHLER'}] {c.name}: {c.detail}" for c in checks
        ]

    until = f" · Vorgänge bis {fmt_date(rep.until.isoformat())}" if rep.until is not None else ""
    lines = [
        f"{TITLE}: Kontrolldatei",
        f"Stand {rep.generated_at:%Y-%m-%d %H:%M:%S} · Build {build_id() or 'unbekannt'} · Stichtag "
        f"{fmt_date(rep.stichtag.isoformat())}{until}",
        "Nur für die eigenen Unterlagen — enthält die vollständigen freigegebenen Transaktions-IDs.",
        f"Grundlage der Prüfungen: {source}. Ergebnis: {'alle Prüfungen bestanden' if ok else 'NICHT BESTANDEN'}",
        "",
        *_rules_block(rep),
        "",
        *block(f"Prüfprotokoll {FULL_LABEL}", full_checks),
        "",
        *block(f"Prüfprotokoll {FA_LABEL}", fa_checks),
        "",
        *block("Beide Fassungen", both),
        *[f"  [WARNUNG] {w}" for w in warnings],
        "",
        f"Freigabeliste ({len(released)} Transaktions-IDs, in der Finanzamt-Fassung vollständig abgedruckt):",
    ]
    ref_of = {x.txid: x.ref for x in tx_refs(fa_rep)}
    lines += [f"  {ref_of.get(x, '—'):<7} {x}" for x in sorted(released, key=lambda x: (ref_of.get(x, ""), x))]
    lines += [
        "",
        ("ERGEBNIS: BESTANDEN" + (f" mit {len(warnings)} Warnung(en)" if warnings else "")) if ok
        else "ERGEBNIS: FEHLER — " + ("; ".join(c.name for c in full_checks + fa_checks + both if not c.ok)),
        "",
        "Keine Steuerberatung.",
    ]
    return full_pdf, fa_pdf, "\n".join(lines) + "\n"


def _render_pdf_once(rep: HerkunftReport) -> bytes:
    from btc_origin.report_sections import titel, wallets, zufluesse, gewinne, belege, grundlagen, unklar, anhang

    heading = f"{TITLE} – {rep.person_name}" if rep.person_name else TITLE
    # Datumsregeln (zuordnung.csv) — nur in der Methodik; die Fußnote selbst ist allgemein
    declared_rules_text = "; ".join(
        f"unbenannte Gegenstellen mit Zu- und Abflüssen bis {fmt_date(until)} = {name}"
        for name, until in rep.declared_rules
    )
    FA = is_fa(rep)
    released = released_txids(rep) if FA else set()
    d = _make_doc(
        rep,
        running_title=f"{TITLE} · {FA_LABEL}" if FA else TITLE,
        doc_title=f"{heading} ({FA_LABEL})" if FA else heading,
    )
    pdf, t, para, explain, section, table = d.pdf, d.t, d.para, d.explain, d.section, d.table
    FONT, W, P, stand = d.FONT, d.W, d.P, d.stand

    refs = tx_refs(rep)
    ref_of = {x.txid: x.ref for x in refs}

    def tx_cell(txid: str) -> str:
        """Tx-Spalte: Kurzreferenz T-001 (Anhang D); in der Finanzamt-Fassung für nicht
        freigegebene Transaktionen „T-001°“ (Hash auf Anforderung) — nie ein (gekürzter) Hash."""
        ref = ref_of.get(txid)
        if not ref:
            return "—"
        if FA and txid not in released:
            return f"{ref}{REQ_MARK}"
        return ref

    c = SimpleNamespace(rep=rep, FA=FA, FONT=FONT, P=P, W=W, d=d, declared_rules_text=declared_rules_text, explain=explain, heading=heading, para=para, pdf=pdf, refs=refs, released=released, section=section, stand=stand, t=t, table=table, tx_cell=tx_cell)
    titel.render_titel(c)
    titel.render_zusammenfassung(c)
    wallets.render_wallets(c)
    zufluesse.render_zufluesse(c)
    gewinne.render_gewinne(c)
    gewinne.render_boersenverkaeufe(c)
    gewinne.render_anlage_so(c)
    belege.render_belege_hintergrund(c)
    belege.render_ohne_beleg(c)
    grundlagen.render_rechtsgrundlagen(c)
    grundlagen.render_methodik(c)
    unklar.render_unklare(c)
    unklar.render_erlaeuterungen(c)
    anhang.render_anhang_a(c)
    anhang.render_anhang_b(c)
    anhang.render_anhang_c(c)
    anhang.render_anhang_d(c)

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Nacherklärung (Entwurf) — § 153 AO / § 371 AO
# ---------------------------------------------------------------------------

NACH_TITLE = "Entwurf: Nacherklärung Kryptowerte"

NACH_CHECKLIST: tuple[tuple[str, str], ...] = (
    (
        "1. Den richtigen Weg wählen — § 153 AO oder § 371 AO",
        "Wer nachträglich erkennt, dass eine Steuererklärung unrichtig oder unvollständig war, "
        "muss dies unverzüglich anzeigen und richtigstellen (§ 153 Abs. 1 AO) — ohne Vorsatz "
        "ist das keine Straftat. Lag eine Steuerhinterziehung vor, führt nur eine wirksame "
        "Selbstanzeige nach § 371 AO zur Straffreiheit. Die Wahl hat straf- und steuerrechtliche "
        "Folgen: bitte vor dem Einreichen mit einer Steuerberaterin/einem Steuerberater oder "
        "einer Fachanwältin/einem Fachanwalt für Steuerrecht klären.",
    ),
    (
        "2. Vollständigkeit (nur § 371 AO)",
        "Die Selbstanzeige muss zu allen Steuerstraftaten einer Steuerart in vollem Umfang "
        "berichtigen, mindestens für alle Steuerstraftaten dieser Steuerart der letzten zehn "
        "Kalenderjahre (§ 371 Abs. 1 AO) — also auch Einkünfte, die diese Aufstellung nicht "
        "enthält (andere Kryptowerte, Staking/Lending, Kapitalerträge, weitere private "
        "Veräußerungsgeschäfte). Eine unvollständige Selbstanzeige wirkt nicht strafbefreiend.",
    ),
    (
        "3. Sperrgründe (§ 371 Abs. 2 AO)",
        "Keine Straffreiheit u. a. nach Bekanntgabe einer Prüfungsanordnung, nach Einleitung "
        "eines Straf- oder Bußgeldverfahrens, nach Erscheinen eines Amtsträgers zur Prüfung "
        "oder wenn die Tat bereits entdeckt ist. Übersteigt die Steuerverkürzung 25.000 € je "
        "Tat, kommt nur ein Absehen von Verfolgung gegen Zahlung eines Geldbetrags in "
        "Betracht (§ 398a AO).",
    ),
    (
        "4. Nachzahlung",
        "Straffreiheit tritt nur ein, wenn die hinterzogenen Steuern und die "
        "Hinterziehungszinsen (§ 235 AO) innerhalb der gesetzten Frist gezahlt werden "
        "(§ 371 Abs. 3 AO).",
    ),
    (
        "5. Welche Jahre betroffen sind",
        "Die Festsetzungsfrist beträgt regulär vier Jahre, bei leichtfertiger Steuerverkürzung "
        "fünf und bei Steuerhinterziehung zehn Jahre (§ 169 Abs. 2 AO).",
    ),
    (
        "6. Annahmen der Aufstellung prüfen",
        "Die Werte beruhen auf der Herkunftsanalyse: Abflüsse an fremde Adressen gelten als "
        "Verkauf, sofern kein Börsen-Export vorliegt; Kurse sind Tageskurse. Abflüsse, die "
        "keine Veräußerung waren, sind herauszunehmen; vorhandene Börsen-Exporte ergänzen die tatsächlichen Verkaufsdaten. Die Freigrenze "
        "(§ 23 Abs. 3 Satz 5 EStG) gilt für alle privaten Veräußerungsgeschäfte des Jahres "
        "zusammen.",
    ),
    (
        "7. Verluste",
        "Verluste aus privaten Veräußerungsgeschäften sind nur mit Gewinnen aus privaten "
        "Veräußerungsgeschäften verrechenbar; Rück- und Vortrag nach § 23 Abs. 3 Satz 7 und 8 "
        "EStG i. V. m. § 10d EStG.",
    ),
)


def nacherklaerung_years(years: list[YearSummary]) -> list[int]:
    """Default selection: years whose short-term gain reaches the Freigrenze."""
    return [y.year for y in years if y.as_dict().get("over_freigrenze") is True]


def render_nacherklaerung_pdf(rep: HerkunftReport, selected: list[int]) -> bytes:
    """Entwurf einer Nacherklärung (Zuarbeit Anlage SO) für die gewählten Jahre.

    Never a finished Selbstanzeige: the letter has placeholders, the choice
    § 153 / § 371 AO is left open, and a checklist names the conditions.
    """
    heading = f"{NACH_TITLE} – {rep.person_name}" if rep.person_name else NACH_TITLE
    d = _make_doc(rep, running_title=NACH_TITLE, doc_title=heading)
    pdf, t, para, explain, section, table = d.pdf, d.t, d.para, d.explain, d.section, d.table
    FONT, W, P = d.FONT, d.W, d.P
    relevant = set(nacherklaerung_years(rep.years))
    years = [y for y in rep.years if y.year in set(selected) and y.year in relevant]
    ylist = ", ".join(str(y.year) for y in years) or "—"
    analyse_date = rep.generated_at.strftime("%d.%m.%Y")

    pdf.set_font(FONT, "B", 18)
    pdf.set_text_color(*INK)
    pdf.multi_cell(W, 9, t(heading), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_fill_color(253, 236, 234)
    pdf.set_draw_color(170, 40, 40)
    pdf.set_text_color(130, 20, 20)
    pdf.set_font(FONT, "B", 9)
    pdf.multi_cell(
        W,
        4.8,
        t(
            "ENTWURF — nicht ungeprüft einreichen. Keine Steuer- oder Rechtsberatung. Ob eine "
            "Berichtigung nach § 153 AO oder eine Selbstanzeige nach § 371 AO in Betracht kommt "
            "und ob die Aufstellung vollständig ist, muss vor dem Einreichen geprüft werden "
            "(Checkliste in Abschnitt 4)."
        ),
        border=1,
        fill=True,
        align="L",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_fill_color(255, 255, 255)  # tables use the current fill for plain rows
    pdf.ln(3)

    # --- 1 Anschreiben -----------------------------------------------------------
    section("1", "Anschreiben (Entwurf mit Platzhaltern)")
    name = rep.person_name or "[Name]"
    tax = (MASK if P else rep.tax_id) if rep.tax_id else "[Steuer-ID]"
    for line in (
        f"{name}\n[Anschrift]\nSteuer-ID: {tax}",
        "An das Finanzamt [Name, Anschrift]\nSteuernummer: [Steuernummer]",
        "[Ort], den [Datum]",
    ):
        para(line, 9, h=4.8)
        pdf.ln(2)
    para(
        f"Einkommensteuer {ylist}: Nacherklärung von Einkünften aus privaten "
        "Veräußerungsgeschäften mit Kryptowerten (§ 22 Nr. 2, § 23 Abs. 1 Satz 1 Nr. 2 EStG)",
        9.5,
        bold=True,
        h=5,
    )
    pdf.ln(2)
    for text in (
        "Sehr geehrte Damen und Herren,",
        "[Variante § 153 AO:] bei der Durchsicht meiner Unterlagen habe ich festgestellt, dass "
        f"meine Einkommensteuererklärungen für {ylist} Einkünfte aus privaten "
        "Veräußerungsgeschäften mit Bitcoin nicht enthielten. Ich zeige dies nach § 153 Abs. 1 "
        "AO an und berichtige die Erklärungen wie folgt.",
        "[Variante § 371 AO — nur nach Prüfung der Voraussetzungen, siehe Abschnitt 4:] "
        f"hiermit berichtige ich nach § 371 AO meine Angaben zur Einkommensteuer für {ylist} "
        "wie folgt. [Vollständigkeit für alle Jahre der Steuerart ergänzen.]",
        "Die Einkünfte ergeben sich aus der Aufstellung in Abschnitt 2 und der Einzelaufstellung "
        "in Abschnitt 3. Die Ermittlung beruht auf der beigefügten „Herkunftsanalyse Bitcoin“ "
        f"vom {analyse_date}; sie legt Datenquellen, Methodik und Rechtsgrundlagen dar "
        "(Einzelbetrachtung nach Rz. 61, Werbungskosten nach Rz. 59 und Tageskurse nach Rz. 91 "
        "des BMF-Schreibens vom 06.03.2025). Soweit Belege von Handelsplattformen fehlen, "
        "wurden die Werte anhand der Blockchain ermittelt (Abschnitte 5 und 6 der "
        "Herkunftsanalyse). Weitere Unterlagen reiche ich auf Anforderung gern nach.",
        "Mit freundlichen Grüßen\n\n[Unterschrift]",
        "Anlage: Herkunftsanalyse Bitcoin vom " + analyse_date,
    ):
        para(text, 9, h=4.8)
        pdf.ln(2)

    # --- 2 Jahresübersicht ---------------------------------------------------------
    pdf.add_page()
    section(
        "2",
        "Einkünfte je Jahr (Zuarbeit Anlage SO)",
        "Nur Veräußerungen innerhalb der Haltefrist (< 1 Jahr, § 23 Abs. 1 Satz 1 Nr. 2 EStG). "
        "Gewinn = Veräußerungspreis − Anschaffungskosten − Werbungskosten (§ 23 Abs. 3 Satz 1 "
        "EStG). Andere private Veräußerungsgeschäfte des Jahres sind nicht enthalten.",
    )
    rows = []
    for y in years:
        dct = y.as_dict()
        s_ = dct["short"]
        gain = s_["gain_eur"]
        fg = dct["freigrenze_eur"]
        if not s_["btc_sats"]:
            result = "keine Veräußerung < 1 Jahr"
        elif gain < 0:
            result = "Verlust (§ 23 Abs. 3 Satz 7/8 EStG)"
        elif gain >= fg:
            result = "steuerpflichtig (Freigrenze erreicht)"
        else:
            result = "unter Freigrenze (nur diese Aufstellung)"
        has = bool(s_["btc_sats"])
        rows.append(
            [
                str(y.year) + ("" if dct["complete"] else " *"),
                fmt_eur(s_["proceeds_eur"], P) if has else "—",
                fmt_eur(s_["cost_eur"], P) if has else "—",
                fmt_eur(s_["fees_eur"], P) if has else "—",
                fmt_eur(gain, P, signed=True) if has else "—",
                f"{fg:,} €".replace(",", "."),
                result,
            ]
        )
    if rows:
        table(
            ["Jahr", "Veräußerungs- preis", "Anschaffungs- kosten", "Werbungs- kosten", "Gewinn / Verlust", "Freigrenze", "Ergebnis"],
            rows,
            [13, 25, 25, 22, 25, 20, 44],
            ["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
            size=7.8,
            head_size=7,
        )
        if any(not y.as_dict()["complete"] for y in years):
            para("* für einzelne Tage fehlt ein Kurs — Werte unvollständig.", 7.8, color=MUTED, h=4)
    else:
        para("Keine Jahre ausgewählt.", 9, color=MUTED)

    # --- 3 Einzelaufstellung ---------------------------------------------------------
    pdf.add_page(orientation="L")
    section(
        "3",
        "Einzelaufstellung der Veräußerungen innerhalb der Haltefrist",
        "Jede Veräußerung einzeln (Rz. 102): Tag, Anschaffung, Haltedauer, Menge, "
        "Veräußerungspreis, Anschaffungskosten, Werbungskosten, Gewinn/Verlust und Grundlage.",
    )
    for y in years:
        det = [x for x in y.details if x.get("disposal", True) and x.get("short_term")]
        if not det:
            continue
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(pdf.epw, 6, t(str(y.year)), new_x="LMARGIN", new_y="NEXT")
        rows = [
            [
                fmt_date(x["exit_date"]),
                fmt_date(x["acquisition_date"]) + (" ¹" if x.get("acq_source") else ""),
                str(x["days_held"]) if x["days_held"] is not None else "—",
                fmt_btc(x["btc_sats"], P, unit=False),
                fmt_eur(x["proceeds_eur"], P),
                fmt_eur(x["cost_eur"], P),
                fmt_eur(x["fee_eur"], P),
                fmt_eur(x["gain_eur"], P, signed=True),
                _name(str(x["counterparty"]), P)
                + (f" · {x['status']}" if x.get("status") else " · Verkauf angenommen"),
            ]
            for x in det
        ]

        def tot(key: str, det: list[dict[str, Any]] = det) -> float:
            return sum(float(x[key]) for x in det if x.get(key) is not None)

        rows.append(
            [
                "Summe",
                "",
                f"{len(det)}×",
                fmt_btc(sum(int(x["btc_sats"]) for x in det), P, unit=False),
                fmt_eur(tot("proceeds_eur"), P),
                fmt_eur(tot("cost_eur"), P),
                fmt_eur(tot("fee_eur"), P),
                fmt_eur(tot("gain_eur"), P, signed=True),
                "",
            ]
        )
        table(
            ["Verkauf", "Anschaffung", "Tage", "Menge (BTC)", "Veräußerungs- preis", "Anschaffungs- kosten", "Werbungs- kosten", "Gewinn / Verlust", "Empfänger · Grundlage"],
            rows,
            [19, 21, 10, 22, 24, 24, 20, 24, 50],
            ["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
            bold_last=True,
            size=7.5,
            head_size=7,
        )
    para(
        "¹ Anschaffung laut Börsen-Export. „Verkauf angenommen“: kein Beleg einer "
        "Handelsplattform — Abfluss aus der Wallet als Verkauf zum Tageskurs gewertet.",
        7.8,
        color=MUTED,
        h=4,
    )

    # --- 4 Checkliste -----------------------------------------------------------------
    pdf.add_page(orientation="P")
    section(
        "4",
        "Vor dem Einreichen prüfen",
        "Diese Punkte entscheiden, ob und wie die Nacherklärung wirkt. Keine Rechtsberatung.",
    )
    explain(NACH_CHECKLIST)
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Nachweis Altbestand (Kryptosteuer-Reform, Referentenentwurf 09/2026)
# ---------------------------------------------------------------------------

ALT_TITLE = "Nachweis Altbestand Bitcoin"

def alt_explain(cutoff: date, ausblick: str) -> tuple[tuple[str, str], ...]:
    return (
        (
            "Worum es geht",
            ausblick,
        ),
        (
            "Was die Blockchain belegt",
            "Jeder Teilbestand ist einzeln bis zu seinem Zufluss "
            "in die eigenen Wallets zurückverfolgt (Einzelbetrachtung, BMF-Schreiben vom 06.03.2025, "
            "Rz. 61). Der Zeitstempel des Blocks belegt, dass der Teilbestand spätestens an diesem Tag in "
            "einer Wallet lag, die durch den angegebenen erweiterten öffentlichen Schlüssel (xpub) "
            "bestimmt ist. Umbuchungen zwischen eigenen Wallets ändern das Anschaffungsdatum nicht. "
            "Der öffentliche Schlüssel allein ist kein Eigentumsnachweis, dient aber der Prüfung der "
            "Angaben (Rz. 87).",
        ),
        (
            "Was ein Beleg zusätzlich belegt",
            "Liegt ein Konto-Export der Handelsplattform vor (Kennzeichen „Export“), sind Kaufdatum "
            "und Anschaffungskosten inklusive Gebühren belegt. Ohne Beleg (Kennzeichen „Blockchain“) "
            "gilt der Tag des Zuflusses als Anschaffung — der späteste mögliche Zeitpunkt — und die "
            "Anschaffungskosten sind mit dem Tageskurs geschätzt. Für diese Teilbestände sollten Kaufbelege "
            "beschafft und aufbewahrt werden, solange die Plattformen sie noch liefern.",
        ),
        (
            "Aufbewahren",
            "Diese Aufstellung zusammen mit der Herkunftsanalyse, den Börsen-Exporten und den xpubs "
            f"aufbewahren. Nach dem {cutoff.strftime('%d.%m.%Y')} erneut erzeugen: Teilbestände, die bis dahin verkauft oder "
            "ausgegeben wurden, entfallen; die verbleibenden Altbestands-Teilbestände bleiben mit ihrem "
            "Anschaffungsdatum identifizierbar.",
        ),
    )


def render_altbestand_pdf(rep: HerkunftReport) -> bytes:
    """Teilbestände im heutigen Bestand, angeschafft bis 31.12.2026 (Bestandsschutz laut
    Referentenentwurf) — je Teilbestand mit Anschaffung, Nachweis, Herkunft, Tx, Adresse."""
    heading = f"{ALT_TITLE} – {rep.person_name}" if rep.person_name else ALT_TITLE
    d = _make_doc(rep, running_title=ALT_TITLE, doc_title=heading)
    pdf, t, para, explain, section, table = d.pdf, d.t, d.para, d.explain, d.section, d.table
    FONT, W, P = d.FONT, d.W, d.P
    cutoff_day = reform_cutoff(rep)
    cutoff = fmt_date(cutoff_day.isoformat())
    today = rep.generated_at.date()
    alt = [x for x in rep.lots if is_altbestand(x.acquisition, cutoff_day)]
    neu = [x for x in rep.lots if not is_altbestand(x.acquisition, cutoff_day)]
    alt_doc = [x for x in alt if x.acq_source]
    alt_chain = [x for x in alt if not x.acq_source]

    def value(lots: list[LotLine]) -> float:
        return sum(x.price_eur * x.sats / SATS_PER_BTC for x in lots if x.price_eur is not None)

    pdf.set_font(FONT, "B", 18)
    pdf.set_text_color(*INK)
    pdf.multi_cell(W, 9, t(heading), align="L", new_x="LMARGIN", new_y="NEXT")
    para(
        f"Bitcoin im Bestand am {fmt_date(today.isoformat())}, angeschafft bis einschließlich "
        f"{cutoff}",
        10,
        color=MUTED,
    )
    pdf.ln(2)
    pdf.set_fill_color(255, 246, 225)
    pdf.set_draw_color(190, 140, 40)
    pdf.set_text_color(110, 70, 0)
    pdf.set_font(FONT, "B", 8.8)
    pdf.multi_cell(
        W,
        4.7,
        t(
            "Grundlage ist ein Referentenentwurf (September 2026), kein geltendes Recht. Die "
            "Aufstellung dokumentiert Anschaffungsdatum und Herkunft je Teilbestand, damit ein "
            "Bestandsschutz für den Altbestand nachgewiesen werden kann. Keine Steuerberatung."
            + (
                f" Stand vor dem {cutoff}: bis dahin angeschaffte Bitcoin kommen noch hinzu — "
                "nach dem Stichtag erneut erzeugen."
                if today <= cutoff_day
                else ""
            )
        ),
        border=1,
        fill=True,
        align="L",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_fill_color(255, 255, 255)
    pdf.ln(3)

    # --- 1 Übersicht --------------------------------------------------------------
    section("1", "Übersicht")
    rows = [
        ["Altbestand gesamt", str(len(alt)), fmt_btc(sum(x.sats for x in alt), P, unit=False), fmt_eur(value(alt), P)],
        ["  davon Kauf belegt (Börsen-Export)", str(len(alt_doc)), fmt_btc(sum(x.sats for x in alt_doc), P, unit=False), fmt_eur(value(alt_doc), P)],
        ["  davon nur Blockchain (Kaufbeleg fehlt)", str(len(alt_chain)), fmt_btc(sum(x.sats for x in alt_chain), P, unit=False), fmt_eur(value(alt_chain), P)],
        [f"Neubestand (angeschafft nach {cutoff})", str(len(neu)), fmt_btc(sum(x.sats for x in neu), P, unit=False), fmt_eur(value(neu), P)],
        ["Bestand heute", str(len(rep.lots)), fmt_btc(sum(x.sats for x in rep.lots), P, unit=False), fmt_eur(value(rep.lots), P)],
    ]
    table(
        ["", "Teilbestände", "Menge (BTC)", "Anschaffungskosten"],
        rows,
        [80, 18, 36, 40],
        ["LEFT", "RIGHT", "RIGHT", "RIGHT"],
        bold_last=True,
        size=8.3,
    )
    if rep.balance_sats != sum(x.sats for x in rep.lots):
        para(
            "Hinweis: Die Summe der Teilbestände weicht vom Bestand laut Blockchain ab "
            f"({fmt_btc(rep.balance_sats, P)}) — Daten prüfen.",
            8.3,
            color=(170, 40, 40),
        )

    # --- 2 Wallets ------------------------------------------------------------------
    section(
        "2",
        "Wallets mit Altbestand",
        "Wallets sind durch ihren erweiterten öffentlichen Schlüssel (xpub) bestimmt; daraus "
        "lassen sich alle Adressen und Transaktionen der Wallet nachprüfen.",
    )
    keys = {w.name: w.key for w in rep.wallets}
    by_wallet: dict[str, list[LotLine]] = {}
    for x in alt:
        by_wallet.setdefault(x.wallet, []).append(x)
    rows = [
        [
            _name(w, P),
            (MASK if P else keys.get(w, "")) or "—",
            str(len(lots)),
            fmt_btc(sum(x.sats for x in lots), P, unit=False),
        ]
        for w, lots in sorted(by_wallet.items())
    ]
    if rows:
        table(["Wallet", "xpub", "Teilbestände", "Menge (BTC)"], rows, [34, 96, 20, 24], ["LEFT", "LEFT", "RIGHT", "RIGHT"], size=7.2)
    else:
        para("Kein Altbestand.", 9, color=MUTED)

    # --- 3 Einzelaufstellung: mit und ohne Kaufbeleg ------------------------------
    pdf.add_page(orientation="L")
    section(
        "3",
        "Einzelaufstellung je Teilbestand",
        "Jede Zeile ist ein Teilbestand: der Teil eines Zuflusses mit eigenem Anschaffungsdatum "
        "(Einzelbetrachtung). Zufluss-Tx = Transaktion, mit der er in die eigenen Wallets kam; Adresse = eigene Adresse, auf der er heute "
        "liegt.",
    )

    def coin_list(
        no: str,
        title: str,
        lead: str,
        lots: list[LotLine],
        evidence: Callable[[LotLine], str],
        ev_head: str,
    ) -> None:
        if pdf.get_y() > pdf.page_break_trigger - 30:
            pdf.add_page(orientation="L")
        pdf.set_x(pdf.l_margin)
        pdf.set_font(FONT, "B", 10)
        pdf.set_text_color(*INK)
        pdf.cell(pdf.epw, 6, t(f"{no}  {title} ({len(lots)})"), new_x="LMARGIN", new_y="NEXT")
        para(lead, 8, color=MUTED, h=4.1)
        pdf.ln(1)
        if not lots:
            para("Keine.", 8.5, color=MUTED)
            pdf.ln(2)
            return
        rows = [
            [
                fmt_date(x.acquisition),
                evidence(x),
                _name(x.wallet, P),
                fmt_btc(x.sats, P, unit=False),
                fmt_eur(x.price_eur * x.sats / SATS_PER_BTC, P) if x.price_eur is not None else "Kurs fehlt",
                fmt_date(tax_free_from(date.fromisoformat(x.acquisition)).isoformat()),
                MASK if P else (x.origin_txid or "—"),
                MASK if P else (x.address or "—"),
            ]
            for x in lots
        ]
        rows.append(
            ["Summe", f"{len(lots)}×", "", fmt_btc(sum(x.sats for x in lots), P, unit=False), fmt_eur(value(lots), P), "", "", ""]
        )
        table(
            ["Anschaffung", ev_head, "Wallet", "Menge (BTC)", "Anschaffungs- kosten", "steuerfrei ab", "Zufluss-Tx", "Adresse heute"],
            rows,
            [21, 36, 22, 20, 23, 20, 61, 58],
            ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT", "LEFT", "LEFT"],
            bold_last=True,
            size=6.8,
            head_size=6.8,
        )
        pdf.ln(2)

    coin_list(
        "3.1",
        "Teilbestände mit Kaufbeleg",
        "Kaufdatum und Anschaffungskosten (inkl. Gebühren) laut Konto-Export der Handelsplattform; "
        "die Blockchain belegt zusätzlich den Weg in die eigenen Wallets.",
        alt_doc,
        lambda x: x.acq_source.replace(" lt. Export", "") + " (Export)",
        "Beleg",
    )
    coin_list(
        "3.2",
        "Teilbestände ohne Kaufbeleg",
        "Kein Beleg einer Handelsplattform. Die Blockchain belegt den Besitz spätestens ab dem "
        "Zufluss (= Anschaffung, spätester möglicher Zeitpunkt); die Anschaffungskosten sind mit "
        "dem Tageskurs geschätzt. Für diese Teilbestände Kaufbelege beschaffen und aufbewahren.",
        alt_chain,
        lambda x: _name(x.source, P),
        "Herkunft",
    )

    # --- 4 Erläuterungen ------------------------------------------------------------
    pdf.add_page(orientation="P")
    section("4", "Erläuterungen")
    explain(alt_explain(cutoff_day, rules_for(rep.stichtag.year).reform_ausblick))
    return bytes(pdf.output())


from btc_origin.destination_trace import DEEP_MAX_INDEX  # noqa: E402


def render_matching_diagnosis(
    rep: HerkunftReport,
    diag: list[dict[str, Any]],
    reports: dict[str, Any],
    *,
    privacy: bool = False,
    destinations: list[dict[str, Any]] | None = None,
    disposals: list[dict[str, Any]] | None = None,
    own_names: dict[str, Any] | None = None,
) -> str:
    """Markdown für den Steuerpflichtigen (nicht Teil des PDFs): Prüfstatus des
    Berichts und Beinahe-Treffer der Börsen-Zuordnung. Enthält echte Werte —
    bleibt lokal."""

    def btc(v: float | None) -> str:
        if v is None:
            return ""
        return MASK if privacy else f"{v:.8f}".replace(".", ",")

    def day(v: str) -> str:
        return fmt_date(v) if v else ""

    lines = [
        "# Matching-Diagnose — nur für den Steuerpflichtigen",
        "",
        f"Stand {rep.generated_at:%d.%m.%Y %H:%M} · Stichtag {fmt_date(rep.stichtag.isoformat())} · "
        f"Build {build_id()}. "
        + (
            "Maskierte Fassung zum Teilen: Beträge als •••, Abweichungen nur in % und Tagen."
            if privacy
            else "Enthält echte Mengen und Daten; nicht Teil des PDFs."
        ),
        "",
        "## 1 Prüfstatus des Berichts",
        "",
        "| Ergebnis | Wert | Abschnitt |",
        "|---|---|---|",
    ]
    for label, value, sec in summary_rows(dataclasses_replace(rep, privacy=privacy)):
        lines.append(f"| {label} | {value} | {sec} |")
    devs = deviations(rep)
    lines += ["", f"**Einzelprüfungen mit ✗:** {len(devs)}" + (" — " + "; ".join(devs) if devs else ""), ""]
    bad = [n for n, r in rep.recon.items()
           if not r.computed == r.balance == (rep.stichtag_balances or {}).get(n, r.balance)]
    lines.append(f"**Überleitungen je Wallet:** {len(rep.recon) - len(bad)} ✓, {len(bad)} ✗"
                 + (f" ({', '.join(bad)})" if bad else ""))
    missing = sum(1 for x in rep.inflows if x.price_eur is None)
    swaps = [t for t in rep.exchange_trades if "Tausch gegen" in str(t.get("note"))]
    dayprice = [t for t in rep.exchange_trades if "kein Betrag im Export" in str(t.get("note"))]
    lines += [
        "",
        f"**Kurse:** {missing} Zufluss-Teil(e) ohne Kurs im Bericht; Börsenzeilen mit Tageskurs "
        f"(kein Betrag im Export): {len(dayprice)}; Tausch gegen anderen Kryptowert: {len(swaps)}.",
        "",
        "### Börsen",
        "",
        "| Börse | Einzahlungen lt. Export | davon aus Wallets | unbekannte Quelle | Abflüsse an die Börse "
        "| Verkäufe lt. Export | verkauft, nicht aus Wallets (BTC) | Auszahlungen ohne Zufluss |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(reports):
        r = reports[name]
        lines.append(
            f"| {name} | {r.deposits_export} | {r.deposits_matched} | "
            f"{max(0, r.deposits_export - r.deposits_matched - len(r.returns))} | {r.deposits_wallet} | {r.sells} | "
            f"{btc(r.unknown_sold_sats / SATS_PER_BTC) if r.unknown_sold_sats else '0'} | {len(r.unmatched)} |"
        )
    lines += [
        "",
        "## 2 Beinahe-Treffer der Zuordnung",
        "",
        "Regel unverändert: gleiche Börse (oder unbenannter Absender), ±7 Tage, Menge laut Export = "
        "Zufluss + höchstens Auszahlungsgebühr (max. 0,001 BTC bzw. 3 %). Je Zeile der beste Kandidat "
        "der Gegenseite innerhalb ±30 Tagen und warum die Regel nicht greift.",
        "",
        "| Art | Börse | Auszahlung | BTC lt. Export | Zufluss | BTC Zufluss | Wallet | Absender | Tage "
        "| Diff. BTC | Diff. % | Grund |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for d in diag:
        pct = "" if d["diff_prozent"] is None else f"{d['diff_prozent']:.2f}".replace(".", ",")
        grund = re.sub(r"\d+[.,]\d{8}", MASK, d["grund"]) if privacy else d["grund"]
        lines.append(
            f"| {d['art']} | {d['boerse']} | {day(d['auszahlung_tag'])} | {btc(d['auszahlung_btc'])} | "
            f"{day(d['zufluss_tag'])} | {btc(d['zufluss_btc'])} | {d['zufluss_wallet']} | {d['zufluss_absender']} | "
            f"{'' if d['abstand_tage'] is None else d['abstand_tage']} | {btc(d['diff_btc'])} | {pct} | {grund} |"
        )
    if not diag:
        lines.append("| — | | | | | | | | | | | keine offenen Fälle |")
    if destinations:
        lines += [
            "",
            "## 3 Zieladressen nicht zugeordneter Auszahlungen",
            "",
            "Nennt die Export-Zeile eine Zieladresse: Gehört sie zu einer betrachteten Wallet, und welche "
            "Zuflüsse gab es dort (Abstand in Tagen, Mengenabweichung in %)? Gehört sie zu keiner: Liegt sie "
            f"weiter hinten in einer xpub (bis Index {DEEP_MAX_INDEX}), und was geschah laut Blockchain mit den "
            "Bitcoin (bis zu 3 Schritte: eigene Wallet, Label-Pack, noch dort)? Die Adresse selbst steht hier nicht.",
            "",
            "| Börse | Datum | Art | BTC lt. Export | Ergebnis |",
            "|---|---|---|---|---|",
        ]
        for d in destinations:
            lines.append(
                f"| {d['exchange']} | {d['day']:%d.%m.%Y} | {d['kind']} | {btc(d['sats'] / SATS_PER_BTC)} | {d['result']} |"
            )
    if disposals:
        lines += [
            "",
            "## 4 Spur der Abflüsse ohne Verkaufsbeleg (Veräußerung angenommen)",
            "",
            "Was geschah laut Blockchain mit den Bitcoin nach dem Abfluss (bis zu 3 Schritte: eigene Wallet, "
            "Label-Pack, noch dort)? Geht die Spur in eine eigene Wallet zurück, war es womöglich keine "
            "Veräußerung. Die Adressen selbst stehen hier nicht.",
            "",
            "| Datum | Wallet | Empfänger | BTC | Ergebnis |",
            "|---|---|---|---|---|",
        ]
        for g in disposals:
            lines.append(
                f"| {fmt_date(g['day'])} | {g['wallet'] or '—'} | {g['counterparty']} | "
                f"{btc(g['sats'] / SATS_PER_BTC)} | {g.get('trace') or '—'} |"
            )
    if own_names and (own_names.get("rows") or own_names.get("errors")):
        lines += [
            "",
            "## 5 Eigene Namen (local/externe-adressen.csv)",
            "",
            "Greift jeder Name? Die Adressen selbst stehen hier nicht.",
            "",
        ]
        if own_names.get("rows"):
            lines += ["| Name | Ergebnis |", "|---|---|"]
            lines += [f"| {r['name']} | {r['result']} |" for r in own_names["rows"]]
        for e in own_names.get("errors") or []:
            lines.append(f"- Übersprungen: {e}")
    lines += [
        "",
        "Manuelle Zuordnungen (Override-Datei) sind nicht aktiv — Entscheidung nach Sichtung dieser Liste.",
        "",
    ]
    return "\n".join(lines)
