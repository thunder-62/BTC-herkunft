"""User-maintained local files (git-ignored), read automatically — never written.

``local/`` next to ``data/`` in the repo (or ``BTC_ORIGIN_LOCAL_DIR``):

* ``local/externe-adressen.csv`` — own names for external counterparties,
  same format as the „Externe Adressen speichern“ export (``name,adresse``).
* ``local/boersen/*.csv`` (also ``.txt``) — account exports of exchanges /
  brokers (Relai, Bison, Bitvavo …). The file name is the name
  (``relai.csv`` → „Relai“); every transaction id (64 hex chars) found in the
  file names the matching Cloud-Eintritt sender or Cloud-Austritt receiver.
  Works independent of the export's column layout. Exports with a header
  row additionally yield **trades** (Kauf, Verkauf, Ein-/Auszahlung with
  date, BTC amount, EUR amount and fee) — used to value a Cloud-Austritt to
  the exchange with the real sale date and proceeds (BMF 06.03.2025 Rz. 20, 55).

The app only READS these files; it never writes to disk. The folder is in
``.gitignore`` — personal names must never be committed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from btc_origin.exchanges import parse_rows
from btc_origin.exchanges.binance import _is_binance_orders, _is_binance_statement, _parse_binance_statement
from btc_origin.exchanges.bitgo import _is_bitgo, _parse_bitgo
from btc_origin.exchanges.buy_sell import _is_buy_sell, _parse_buy_sell
from btc_origin.exchanges.coinbase import _is_coinbase, _parse_coinbase
from btc_origin.exchanges.common import (  # noqa: F401 — auch für andere Module
    _TXID_RE,
    ExchangeTrade,
    _BTC_ASSETS,
    _DEPOSIT_WORDS,
    _WITHDRAW_WORDS,
    _amount_to_sats,
    _header_roles,
    _parse_decimal,
    _row_addresses,
    _row_btc_sats,
    _row_dates,
    _skip_preamble,
)
from btc_origin.external_book import parse_csv

LOCAL_DIR_ENV = "BTC_ORIGIN_LOCAL_DIR"
NAMES_FILE = "externe-adressen.csv"
# Date rules: unnamed counterparties whose flows all lie on/before a date get
# a name — the taxpayer's own statement (e.g. „FTX,2022-11-07“).
RULES_FILE = "zuordnung.csv"
RULES_FILE_ALIASES = ("zuordnung.csv", "zuordnungen.csv")
EXCHANGES_DIR = "boersen"
# Optional: Erläuterungen zu nicht zugeordneten Börsenvorgängen
# („Börse;Datum;Art;Erläuterung“ — Datum/Art leer = alle dieser Börse).
EXPLANATIONS_FILE = "erlaeuterungen.csv"
# xpubs nur für den Adressabgleich der Matching-Diagnose (nicht Teil des Berichts)
CHECK_XPUBS_FILE = "pruef-xpubs.txt"
# Jahressteuerreport: weitere Veräußerungsgeschäfte je Jahr, „Altbestand bis“ (tax_year)
TAX_FILE = "steuer.csv"
MAX_FILE_BYTES = 20 * 1024 * 1024


def resolve_local_dir() -> Path:
    """``BTC_ORIGIN_LOCAL_DIR`` or ``<repo>/local`` (repo = folder with data/)."""
    env = os.environ.get(LOCAL_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data" / "label_packs").is_dir():
            return parent / "local"
    return Path.cwd() / "local"


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path.name}: größer als {MAX_FILE_BYTES // (1024 * 1024)} MB")
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def file_counts(files: list[dict[str, Any]], trades: list[ExchangeTrade]) -> list[dict[str, Any]]:
    """Dateitabelle (Anhang A) mit Zahlen je Datei aus den übergebenen Zeilen — beim Bericht
    „bis“ also nur Zeilen bis zu diesem Tag, wie im Abgleich. Nicht verwendete Dateien
    (z. B. Binance-Order-Historie neben der Transaktionshistorie) zählen 0."""
    out = []
    for f in files:
        mine = [t for t in trades if t.file == f.get("file")]
        out.append({
            **f,
            "trades": len(mine),
            "buys": sum(1 for t in mine if t.kind == "buy"),
            "sells": sum(1 for t in mine if t.kind == "sell"),
            "withdrawals": sum(1 for t in mine if t.kind == "withdraw"),
            "deposits": sum(1 for t in mine if t.kind == "deposit"),
        })
    return out


def parse_trades(text: str, name: str) -> list[ExchangeTrade]:
    """Buy/sell/deposit/withdraw rows of a header-based export (BTC only)."""
    lines = _skip_preamble([ln for ln in text.splitlines() if ln.strip()])
    if len(lines) < 2:
        return []
    first = lines[0]
    delim = max(("\t", ";", ","), key=first.count)
    if first.count(delim) == 0:
        return []
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    return parse_rows(rows, name, delim)


def _direction_hint(type_cell: str) -> str | None:
    t = (type_cell or "").strip().lower()
    if any(w in t for w in _WITHDRAW_WORDS):
        return "in"  # exchange → user: a Cloud-Eintritt
    if any(w in t for w in _DEPOSIT_WORDS):
        return "out"  # user → exchange: a Cloud-Austritt
    return None


def _structured_rows(text: str, name: str) -> list[tuple] | None:
    """Rows via header columns, or None when no usable header is found."""
    lines = _skip_preamble([ln for ln in text.splitlines() if ln.strip()])
    if len(lines) < 2:
        return None
    first = lines[0]
    delim = max(("\t", ";", ","), key=first.count)
    if first.count(delim) == 0:
        return None
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    if _is_binance_statement(rows[0]):
        return [
            (name, [t.day], [t.sats], list(t.addresses), "in" if t.kind == "withdraw" else "out")
            for t in _parse_binance_statement(rows, name)
            if t.kind in ("withdraw", "deposit")
        ]
    if _is_binance_orders(rows[0]):
        return []  # Order-Historie: Handel auf der Börse, keine Ein-/Auszahlungen
    if _is_coinbase(rows[0]):
        return [
            (name, [t.day], [t.sats], list(t.addresses), "in" if t.kind == "withdraw" else "out")
            for t in _parse_coinbase(rows, name)
            if t.kind in ("withdraw", "deposit")
        ]
    if _is_buy_sell(rows[0]):
        return [
            (name, [t.day], [t.sats], list(t.addresses), "in" if t.kind == "withdraw" else "out")
            for t in _parse_buy_sell(rows, name)
            if t.kind in ("withdraw", "deposit")
        ]
    if _is_bitgo(rows[0]):
        # Transfers carry TxID/address; settlement rows (Kauf/Verkauf) are no transfers.
        return [
            (name, [t.day], [t.sats], list(t.addresses), "in" if t.kind == "withdraw" else "out")
            for t in _parse_bitgo(rows, name)
            if t.kind in ("withdraw", "deposit") and t.addresses
        ]
    roles = _header_roles(rows[0])
    if "date" not in roles or "amount" not in roles:
        return None
    out: list[tuple] = []
    for row in rows[1:]:
        cell = lambda role: row[roles[role]] if role in roles and roles[role] < len(row) else ""  # noqa: E731
        if "asset" in roles and cell("asset").strip().lower() not in _BTC_ASSETS:
            continue  # other coin (ETH …)
        line = delim.join(row)
        if _TXID_RE.search(line):
            continue  # exact txid match wins for this row
        dates = _row_dates(cell("date"))
        sats = _amount_to_sats(cell("amount"))
        addrs = _row_addresses(line)
        if dates and (sats or addrs):
            out.append((name, dates, [sats] if sats else [], addrs, _direction_hint(cell("type"))))
    return out


# Zusätze im Dateinamen, die nicht zum Börsennamen gehören
# („binance-auszahlungen.csv“, „bitvavo_2024.csv“ → Binance, Bitvavo).
_NAME_SUFFIXES = {
    "auszahlungen", "auszahlung", "einzahlungen", "einzahlung", "withdrawals", "withdrawal",
    "deposits", "deposit", "orders", "order", "trades", "trade", "transaktionen",
    "transactions", "history", "historie", "export", "kauf", "kaeufe", "käufe",
    # zweites/früheres Konto derselben Börse („bitvavo-old.csv“ → Bitvavo)
    "old", "alt", "new", "neu", "konto", "account",
}


def exchange_name_from_file(path: Path) -> str:
    parts = [p for p in re.split(r"[_\-\s]+", path.stem) if p]
    while len(parts) > 1 and (parts[-1].lower() in _NAME_SUFFIXES or parts[-1].isdigit()):
        parts.pop()
    stem = " ".join(parts)
    return stem[:1].upper() + stem[1:] if stem else path.name


@dataclass
class LocalData:
    directory: str = ""
    names: list[tuple[str, str]] = field(default_factory=list)
    names_file: str | None = None
    date_rules: list[tuple[str, date]] = field(default_factory=list)
    txid_names: dict[str, str] = field(default_factory=dict)
    # Rows without txid: (name, dates, sats amounts, bitcoin addresses) for
    # matching by address (e.g. Relai „Destination“) or date + amount.
    amount_rows: list[tuple] = field(default_factory=list)
    trades: list[ExchangeTrade] = field(default_factory=list)
    exchange_files: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # (Börse, Datum oder None, Art oder "", Erläuterung)
    explanations: list[tuple[str, date | None, str, str]] = field(default_factory=list)
    # (Name, xpub) — nur im Speicher, nur für den Adressabgleich; nie ausgeben
    check_xpubs: list[tuple[str, str]] = field(default_factory=list)
    # local/steuer.csv (tax_year.TaxConfig); None = Datei fehlt → Vorgaben
    tax_config: Any = None
    # Export-Zeilen ohne steuerlichen Wert (kategorisierung.NichtUnterstuetzt): Art nicht
    # zugeordnet, Kategorie nicht unterstützt, Tausch … — nicht in ``trades``
    unsupported: list[Any] = field(default_factory=list)
    # alle Export-Zeilen vor der Einordnung (Übersicht der Export-Arten in der Oberfläche)
    all_trades: list[ExchangeTrade] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "names_file": self.names_file,
            "date_rules": [{"name": n, "bis": d.isoformat()} for n, d in self.date_rules],
            "names_count": len(self.names),
            "exchange_files": list(self.exchange_files),
            "check_xpubs_count": len(self.check_xpubs),
            "errors": list(self.errors),
        }


def parse_date_rules(text: str) -> tuple[list[tuple[str, date]], list[str]]:
    """``name,bis`` rows (comma/semicolon, optional header) → (name, last day)."""
    rules: list[tuple[str, date]] = []
    errors: list[str] = []
    for n, line in enumerate((text or "").lstrip("\ufeff").splitlines(), 1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[;,\t]", line, maxsplit=1)]
        days = _row_dates(parts[1]) if len(parts) > 1 else []
        if len(parts) < 2 or not parts[0] or not days:
            if n > 1 or not parts[0].lower().startswith("name"):
                errors.append(f"Zeile {n}: erwartet name,bis (z. B. FTX,2022-11-07)")
            continue
        rules.append((parts[0][:80], days[0]))
    return sorted(rules, key=lambda r: r[1]), errors


def _simple_rows(text: str, max_fields: int | None = None) -> list[list[str]]:
    """Kleine Konfigurationsdatei: Trennzeichen ; oder , bzw. Tab, #-Kommentare,
    Leerzeilen ignoriert. Das Trennzeichen gilt je Zeile (das erste, das vorkommt) — so
    funktionieren auch Dateien mit gemischten Zeilen („Name;Datum;…“ und „Name,,…“); im
    letzten Feld dürfen die anderen Zeichen frei vorkommen. ``max_fields``: alles ab diesem
    Feld bleibt ein Feld (mit dem Trennzeichen der Zeile wieder zusammengesetzt)."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    rows = []
    for ln in lines:
        found = [(ln.find(d), d) for d in (";", "\t", ",") if d in ln]
        delim = min(found)[1] if found else ";"
        for row in csv.reader([ln], delimiter=delim):
            if max_fields and len(row) > max_fields:
                row = row[: max_fields - 1] + [delim.join(row[max_fields - 1 :])]
            rows.append([c.strip() for c in row])
    return rows


def parse_explanations(text: str) -> tuple[list[tuple[str, date | None, str, str]], list[str]]:
    out: list[tuple[str, date | None, str, str]] = []
    errors: list[str] = []
    for i, row in enumerate(_simple_rows(text, max_fields=4), 1):
        if i == 1 and row and row[0].lower() in ("börse", "boerse", "exchange"):
            continue  # Kopfzeile
        if len(row) < 4 or not row[0] or not row[3]:
            errors.append(f"Zeile {i}: erwartet „Börse;Datum;Art;Erläuterung“")
            continue
        dates = _row_dates(row[1]) if row[1] else []
        if row[1] and not dates:
            errors.append(f"Zeile {i}: Datum „{row[1]}“ nicht erkannt")
            continue
        text = ";".join(row[3:]).strip()
        if re.search(r"<[^<>]*>", text):
            # Vorlage nicht ausgefüllt (z. B. „<Herkunft eintragen>“) — nie in den Bericht
            errors.append(f"Zeile {i}: Platzhalter „<…>“ nicht ausgefüllt — Zeile ignoriert")
            continue
        out.append((row[0], dates[0] if dates else None, row[2], text))
    return out, errors


def parse_check_xpubs(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Je Zeile ``xpub`` oder ``Name;xpub``. Nur öffentliche Schlüssel; private
    Schlüssel oder Wortlisten werden abgelehnt (und nicht wiederholt)."""
    out: list[tuple[str, str]] = []
    errors: list[str] = []
    for i, line in enumerate((text or "").lstrip("\ufeff").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in re.split(r"[;,\t]", line) if x.strip()]
        key = parts[-1]
        name = parts[0] if len(parts) > 1 else f"Prüf-xpub {len(out) + 1}"
        if len(line.split()) >= 12 or key[1:4].lower() == "prv":
            errors.append(f"Zeile {i}: kein öffentlicher Schlüssel — ignoriert (nie Seeds/private Schlüssel eintragen)")
            continue
        if key[1:4].lower() != "pub":
            errors.append(f"Zeile {i}: erwartet xpub/ypub/zpub")
            continue
        out.append((name[:80], key))
    return out, errors


def load_local_data(directory: Path | None = None) -> LocalData:
    """Read names CSV + exchange exports. Missing folder/files are fine."""
    base = directory or resolve_local_dir()
    out = LocalData(directory=str(base))
    rules_path = next(
        (base / n for n in RULES_FILE_ALIASES if (base / n).is_file()), base / RULES_FILE
    )
    if rules_path.is_file():
        try:
            out.date_rules, errs = parse_date_rules(_read_text(rules_path))
            out.errors += [f"{RULES_FILE}: {e}" for e in errs]
        except (OSError, ValueError) as exc:
            out.errors.append(f"{RULES_FILE}: {exc}")
    names_path = base / NAMES_FILE
    if names_path.is_file():
        try:
            pairs, errors = parse_csv(_read_text(names_path))
            out.names = pairs
            out.names_file = str(names_path)
            out.errors += [f"{NAMES_FILE}: {e}" for e in errors]
        except (OSError, ValueError) as exc:
            out.errors.append(f"{NAMES_FILE}: {exc}")
    expl_path = base / EXPLANATIONS_FILE
    if expl_path.is_file():
        try:
            out.explanations, errs = parse_explanations(_read_text(expl_path))
            out.errors += [f"{EXPLANATIONS_FILE}: {e}" for e in errs]
        except (OSError, ValueError) as exc:
            out.errors.append(f"{EXPLANATIONS_FILE}: {exc}")
    check_path = base / CHECK_XPUBS_FILE
    if check_path.is_file():
        try:
            out.check_xpubs, errs = parse_check_xpubs(_read_text(check_path))
            out.errors += [f"{CHECK_XPUBS_FILE}: {e}" for e in errs]
        except (OSError, ValueError) as exc:
            out.errors.append(f"{CHECK_XPUBS_FILE}: {exc}")
    tax_path = base / TAX_FILE
    if tax_path.is_file():
        from btc_origin.tax_year import parse_tax_config

        try:
            out.tax_config, errs = parse_tax_config(_read_text(tax_path))
            out.errors += [f"{TAX_FILE}: {e}" for e in errs]
        except (OSError, ValueError) as exc:
            out.errors.append(f"{TAX_FILE}: {exc}")
    ex_dir = base / EXCHANGES_DIR
    order_files: dict[str, list[tuple[str, list[ExchangeTrade]]]] = {}
    statement_names: set[str] = set()
    if ex_dir.is_dir():
        for path in sorted(ex_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in (".csv", ".txt"):
                continue
            name = exchange_name_from_file(path)
            try:
                text = _read_text(path)
            except (OSError, ValueError) as exc:
                out.errors.append(f"{path.name}: {exc}")
                continue
            txids = {m.lower() for m in _TXID_RE.findall(text)}
            for t in txids:
                out.txid_names.setdefault(t, name)
            trades = parse_trades(text, name)
            for t in trades:
                t.file = path.name
            if _header_matches(text, _is_binance_orders):
                order_files.setdefault(name, []).append((path.name, trades))
            else:
                if _header_matches(text, _is_binance_statement):
                    statement_names.add(name)
                out.trades.extend(trades)
            structured = _structured_rows(text, name)
            if structured is not None:
                out.amount_rows.extend(structured)
                n_rows = len(structured)
            else:  # unknown layout: scan every line as text
                n_rows = 0
                for line in text.splitlines():
                    if _TXID_RE.search(line):
                        continue  # exact txid match wins for this row
                    dates, sats = _row_dates(line), _row_btc_sats(line)
                    addrs = _row_addresses(line)
                    if dates and (sats or addrs):
                        out.amount_rows.append((name, dates, sats, addrs))
                        n_rows += 1
            out.exchange_files.append(
                {
                    "file": path.name,
                    "name": name,
                    "txids": len(txids),
                    "amount_rows": n_rows,
                    "trades": len(trades),
                    # je Datei (Anhang A) — mehrere Dateien derselben Börse nicht summieren
                    "buys": sum(1 for t in trades if t.kind == "buy"),
                    "sells": sum(1 for t in trades if t.kind == "sell"),
                    "withdrawals": sum(1 for t in trades if t.kind == "withdraw"),
                    "deposits": sum(1 for t in trades if t.kind == "deposit"),
                    # Prüfsumme der Datei (Prüfprotokoll: Wiederholbarkeit, Belegkette)
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
    # Binance: Order-Historie und Transaktionshistorie enthalten dieselben Trades —
    # liegt die (vollständigere) Transaktionshistorie vor, zählt nur sie.
    for ex_name, files in order_files.items():
        for fname, trades in files:
            if ex_name in statement_names:
                out.errors.append(
                    f"{fname}: Order-Historie nicht verwendet — die Transaktionshistorie von "
                    f"{ex_name} enthält dieselben Käufe/Verkäufe (sonst doppelt)"
                )
                for f in out.exchange_files:
                    if f["file"] == fname:
                        f["trades"] = f["buys"] = f["sells"] = f["withdrawals"] = f["deposits"] = 0
            else:
                out.trades.extend(trades)
    # Einordnung nach kategorien.yaml (Repo + local/kategorien.yaml): nicht unterstützte
    # Zeilen fließen in keine Rechnung ein (REGELWERK.md 4.1)
    from btc_origin.kategorisierung import categorize_trades
    from btc_origin.regelwerk import load_categories

    out.all_trades = list(out.trades)
    out.trades, out.unsupported = categorize_trades(out.trades, load_categories(local_dir=base))
    return out


def _header_matches(text: str, check: Callable[[list[str]], bool]) -> bool:
    lines = _skip_preamble([ln for ln in text.splitlines() if ln.strip()])
    if not lines:
        return False
    first = lines[0]
    delim = max(("\t", ";", ","), key=first.count)
    return check(next(csv.reader([first], delimiter=delim)))


# Date/amount matching tolerances (exchange exports without txids).
MATCH_MAX_DAYS = 2
WITHDRAW_FEE_MAX_SATS = 100_000  # 0.001 BTC exchange withdrawal fee
WITHDRAW_FEE_MAX_RATIO = 0.03
DEPOSIT_TOLERANCE_SATS = 1_000


def match_amount_rows(
    amount_rows: list[tuple],
    cloud_rows: list[dict[str, Any]],
) -> dict[str, str]:
    """txid → exchange name from export rows without txid; unique matches only.

    * Row contains a Bitcoin address (e.g. Relai „Destination“) → match the
      Cloud-Eintritt at that own address / the Cloud-Austritt to that address,
      date ±2 days; several candidates are narrowed by amount.
    * Otherwise date (±2 days) + amount:

    * IN (payout to the user): export amount = received amount + exchange
      withdrawal fee (0 … max(0.001 BTC, 3 %)).
    * OUT (deposit to the exchange): export amount ≈ sent amount.
    A cloud tx matched by several export rows of different names, or an
    export row matching several txs, is skipped — no guessing.
    """
    txs: dict[str, dict[str, Any]] = {}
    for r in cloud_rows:
        txid = str(r.get("txid") or "").lower()
        day = str(r.get("time") or "")[:10]
        if not txid or not day:
            continue
        t = txs.setdefault(
            txid, {"dir": r.get("direction"), "day": day, "sats": 0, "addrs": set()}
        )
        t["sats"] += int(r.get("amount_sats") or 0)
        if r.get("address"):
            t["addrs"].add(str(r["address"]))

    def fits(direction: str, cloud_sats: int, export_sats: int) -> bool:
        if direction == "in":
            diff = export_sats - cloud_sats
            return 0 <= diff <= max(WITHDRAW_FEE_MAX_SATS, int(cloud_sats * WITHDRAW_FEE_MAX_RATIO))
        return abs(export_sats - cloud_sats) <= DEPOSIT_TOLERANCE_SATS

    def near(t: dict[str, Any], dates: list[date]) -> bool:
        try:
            cday = date.fromisoformat(t["day"])
        except ValueError:
            return False
        return any(abs((cday - d).days) <= MATCH_MAX_DAYS for d in dates)

    hits: dict[str, set[str]] = {}
    for row in amount_rows:
        name, dates, amounts = row[0], row[1], row[2]
        addrs = set(row[3]) if len(row) > 3 else set()
        want = row[4] if len(row) > 4 else None  # "in" payout / "out" deposit
        cands: list[str] = []
        pool = {
            txid: t for txid, t in txs.items() if want is None or t["dir"] == want
        }
        if addrs:
            cands = [
                txid for txid, t in pool.items() if t["addrs"] & addrs and near(t, dates)
            ]
            if len(cands) > 1 and amounts:
                cands = [
                    txid
                    for txid in cands
                    if any(fits(str(txs[txid]["dir"]), txs[txid]["sats"], a) for a in amounts)
                ]
        else:
            cands = [
                txid
                for txid, t in pool.items()
                if near(t, dates)
                and any(fits(str(t["dir"]), t["sats"], a) for a in amounts)
            ]
        if len(cands) == 1:
            hits.setdefault(cands[0], set()).add(name)
    return {txid: next(iter(names)) for txid, names in hits.items() if len(names) == 1}
