"""Gemeinsame Bausteine der Börsen-Parser: Datums-/Zahlen-Erkennung, Spaltenrollen,
Zeilenarten und das Ergebnis ``ExchangeTrade`` (eine BTC-Zeile eines Exports)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from btc_origin.external_book import is_bitcoin_address

_TXID_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")

_DATE_RES = (
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), (1, 2, 3)),  # 2024-03-01
    (re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)"), (3, 2, 1)),  # 01.03.2024
    (re.compile(r"(?<!\d)(\d{2})/(\d{2})/(\d{4})(?!\d)"), (3, 2, 1)),  # 01/03/2024 (EU)
)


# Decimal numbers: 0,00123456 · 0.00123456 · 1.234,56789 — sign ignored.
_NUM_RE = re.compile(
    r"(?<!\d)(?<!\d[.,])(?:\d{1,3}(?:[.,\u00a0' ]\d{3})*[.,]\d+|\d+[.,]\d+)(?![\d])"
)


# Bitcoin address candidates (validated with embit before use).
_ADDR_RE = re.compile(
    r"(?<![0-9A-Za-z])(bc1[02-9ac-hj-np-z]{11,71}|[13][1-9A-HJ-NP-Za-km-z]{25,34})(?![0-9A-Za-z])"
)


# BTC amounts carry 3–8 decimals; fiat/prices 2 → ignored.
BTC_MIN_DECIMALS = 3


BTC_MAX_DECIMALS = 8


SATS_PER_BTC = 100_000_000


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
_MONTHS.update({"mär": 3, "mai": 5, "okt": 10, "dez": 12})


_MONTH_DATE_RES = (
    re.compile(r"\b([A-Za-zäÄ]{3})[a-zä]*\.? (\d{1,2}),? (\d{4})\b"),  # Jan 15 2024
    re.compile(r"\b(\d{1,2})\.? ([A-Za-zäÄ]{3})[a-zä]*\.? (\d{4})\b"),  # 15 Jan 2024
)


def _row_dates(line: str) -> list[date]:
    out: list[date] = []
    for n, rx in enumerate(_MONTH_DATE_RES):
        for m in rx.finditer(line):
            mon, day = (m.group(1), m.group(2)) if n == 0 else (m.group(2), m.group(1))
            month = _MONTHS.get(mon.lower())
            if month:
                try:
                    out.append(date(int(m.group(3)), month, int(day)))
                except ValueError:
                    continue
    for rx, (yi, mi, di) in _DATE_RES:
        for m in rx.finditer(line):
            try:
                out.append(date(int(m.group(yi)), int(m.group(mi)), int(m.group(di))))
            except ValueError:
                continue
    return out


def _row_btc_sats(line: str) -> list[int]:
    """BTC-looking amounts of one export row, in sats."""
    out: list[int] = []
    for m in _NUM_RE.finditer(line):
        tok = m.group(0).replace("\u00a0", "").replace("'", "").replace(" ", "")
        sep = max(tok.rfind("."), tok.rfind(","))
        decimals = len(tok) - sep - 1
        if not BTC_MIN_DECIMALS <= decimals <= BTC_MAX_DECIMALS:
            continue
        whole = re.sub(r"[.,]", "", tok[:sep])
        try:
            value = float(f"{whole}.{tok[sep + 1:]}")
        except ValueError:
            continue
        if 0 < value < 21_000_000:
            out.append(round(value * SATS_PER_BTC))
    return out


def _row_addresses(line: str) -> list[str]:
    return [a for a in dict.fromkeys(_ADDR_RE.findall(line)) if is_bitcoin_address(a)]


# Header-aware parsing (Relai, Bison, Bitvavo …): column roles by header name.
_BTC_ASSETS = {"btc", "xbt", "bitcoin", ""}


# Rückbuchung eines fehlgeschlagenen Vorgangs (Strike „Reversal“ u. ä.)
_REVERSAL_RE = re.compile(r"\b(reversal|reversed|refund(ed)?|rückbuchung|storno|storniert|fehlgeschlagen)\b",
                          re.IGNORECASE)


# Zeilenart eines abgebrochenen Vorgangs (Bitvavo „withdrawal_cancelled“): die Zeile steht für den
# abgebrochenen Versuch selbst — überspringen, sie hebt keine andere Zeile auf
_CANCELLED_TYPE_RE = re.compile(r"(?<![a-z])cancell?ed(?![a-z])", re.IGNORECASE)


# Status einer Export-Zeile, die keinen ausgeführten Vorgang beschreibt
_NOT_DONE_RE = re.compile(r"cancel|fail|expired|reject|declin|pending|awaiting|abgebrochen|storniert|offen",
                          re.IGNORECASE)


_WITHDRAW_WORDS = ("withdraw", "auszahl", "payout", "send", "versand", "outgoing", "transfer out")


_DEPOSIT_WORDS = ("deposit", "einzahl", "receive", "incoming", "transfer in", "empfang")


def _header_roles(header: list[str]) -> dict[str, int]:
    """Map column roles → index from a header row (case-insensitive)."""
    roles: dict[str, int] = {}
    h = [c.strip().lower() for c in header]
    for i, c in enumerate(h):
        if "date" not in roles and ("date" in c or "datum" in c or c in ("time", "zeit", "timestamp")):
            roles["date"] = i
        if "type" not in roles and ("type" in c or c in ("typ", "art", "vorgang")):
            roles["type"] = i
        if "asset" not in roles and c in ("asset", "coin", "cryptocurrency", "kryptowährung", "währung (krypto)"):
            roles["asset"] = i
    if "asset" not in roles:
        # Bitvavo & co.: the coin is in „Currency“ (only when no Asset column
        # exists — Bison has both, there „Currency“ is the fiat side).
        for i, c in enumerate(h):
            if c in ("currency", "währung", "base currency"):
                roles["asset"] = i
                break
    # BTC amount column: prefer explicit BTC / asset amount, never fiat/price.
    def is_fiat(c: str) -> bool:
        return any(w in c for w in ("eur", "fiat", "usd", "chf", "price", "kurs", "preis", "fee", "gebühr"))
    for pref in ("btc amount", "amount (btc)", "asset (amount)", "menge", "amount"):
        for i, c in enumerate(h):
            if pref in c and not is_fiat(c.replace("asset (amount)", "")):
                roles.setdefault("amount", i)
        if "amount" in roles:
            break
    return roles


def _amount_to_sats(cell: str) -> int | None:
    """Any decimal/integer BTC amount (the column is known to be BTC)."""
    tok = re.sub(r"[^\d.,]", "", cell or "")
    if not tok or not re.search(r"\d", tok):
        return None
    sep = max(tok.rfind("."), tok.rfind(","))
    if sep == -1:
        whole, frac = tok, ""
    else:
        # last separator = decimal separator (1.234,5 / 1,234.5 / 0,05)
        whole, frac = re.sub(r"[.,]", "", tok[:sep]), tok[sep + 1 :]
    try:
        value = float(f"{whole or 0}.{frac or 0}")
    except ValueError:
        return None
    return round(value * SATS_PER_BTC) if 0 < value < 21_000_000 else None


def _parse_decimal(cell: str) -> float | None:
    """1.234,56 · 1,234.56 · 0,05 · -12.5 → float (sign dropped)."""
    tok = re.sub(r"[^\d.,]", "", cell or "")
    if not tok or not re.search(r"\d", tok):
        return None
    sep = max(tok.rfind("."), tok.rfind(","))
    if sep == -1:
        whole, frac = tok, ""
    else:
        whole, frac = re.sub(r"[.,]", "", tok[:sep]), tok[sep + 1 :]
    try:
        return float(f"{whole or 0}.{frac or 0}")
    except ValueError:
        return None


_SELL_WORDS = ("sell", "verkauf", "sold", "sale")


_BUY_WORDS = ("buy", "kauf", "purchase", "bought", "sparplan", "savings plan")


def trade_kind(type_cell: str) -> str | None:
    """Export row type → buy | sell | deposit | withdraw (exchange view)."""
    t = (type_cell or "").strip().lower()
    if any(w in t for w in _SELL_WORDS):
        return "sell"
    if any(w in t for w in _BUY_WORDS):
        return "buy"
    if any(w in t for w in _WITHDRAW_WORDS):
        return "withdraw"
    if any(w in t for w in _DEPOSIT_WORDS):
        return "deposit"
    return None


@dataclass
class ExchangeTrade:
    """One BTC row of an exchange export (exchange's point of view).

    ``kind`` = ``unknown``: BTC-Zeile, deren Art der Parser nicht kennt — sie wird nicht
    bewertet, sondern als nicht unterstützt gemeldet (siehe categorize_trades)."""

    exchange: str
    day: date
    kind: str  # buy | sell | deposit | withdraw
    sats: int
    eur: float | None = None  # fiat amount of a buy/sell
    fee_eur: float | None = None
    fee_sats: int = 0
    destination: bool = False  # buy sent straight to an address (e.g. Relai)
    addresses: tuple[str, ...] = ()  # Bitcoin addresses named in the row
    usd: float | None = None  # fiat amount in USD (BitGo) → EUR via EZB-Kurs
    # Gegenwert laut Export ohne EUR/USD-Betrag: „Karte“ (Kartenkauf, Betrag
    # fehlt im Export) oder ein anderer Kryptowert (Tausch, z. B. „ETH“).
    quote: str = ""
    file: str = field(default="", compare=False)  # Exportdatei (Anhang A: Zahlen je Datei)
    # Art laut Export (Wert der Art-Spalte, z. B. „Buy“); wo er die Richtung nicht festlegt,
    # mit der Lesart des Parsers („trade – Kauf“). Grundlage der Kategorie (kategorien.yaml).
    art: str = field(default="", compare=False)
    # Kategorie bzw. technische Art aus kategorien.yaml (kategorisierung.categorize_trades)
    kategorie: str = field(default="", compare=False)
    einkunft: bool = field(default=False, compare=False)  # behandlung einkunft_22_3
    # Vorgangs-ID laut Export (z. B. „Operation ID“, „Transaction ID“), sonst "" — Quittierung
    ref: str = field(default="", compare=False)


def _trade_roles(header: list[str]) -> dict[str, int]:
    roles: dict[str, int] = {}
    h = [c.strip().lower() for c in header]
    for i, c in enumerate(h):
        if "fiat amount" in c or "fiatamount" in c or "paid amount" in c:
            # „Fiat Amount (excl. fees)“ (Relai), „FiatAmount“ (Strike),
            # „Received / Paid Amount“ (Bitvavo)
            roles.setdefault("fiat", i)
            continue
        if "eur" in c and ("received" in c or "paid" in c):
            roles.setdefault("fiat", i)  # „EUR received / paid“ (älterer Bitvavo-Export), Betrag in EUR
            continue
        if c in ("status", "state", "zustand"):
            roles.setdefault("status", i)
            continue
        if "paid currency" in c:  # Bitvavo „Received / Paid Currency“
            roles.setdefault("fiat_ccy", i)
            continue
        if ("fee" in c or "gebühr" in c) and ("bitcoin" in c or "btc" in c):
            roles.setdefault("fee_btc", i)  # „BitcoinFee“ (Strike): fee in BTC
            continue
        if "fee" in c or "gebühr" in c:
            if "currency" in c or "währung" in c:
                roles.setdefault("fee_ccy", i)
            elif "excl" not in c and "incl" not in c:
                roles.setdefault("fee", i)
            continue
        if any(w in c for w in ("price", "kurs", "preis", "pair", "rate")):
            continue
        if c in ("eur", "eur (amount)", "amount (eur)", "betrag (eur)", "betrag", "total (eur)"):
            roles.setdefault("fiat", i)
        elif "fiat currency" in c:
            roles.setdefault("fiat_ccy", i)
        elif c in ("destination", "address", "adresse", "empfänger", "wallet address"):
            roles.setdefault("dest", i)
    return roles


_FIAT = ("EUR", "USD")


def _skip_preamble(lines: list[str]) -> list[str]:
    """Coinbase & Co.: Vorspann-Zeilen („Transactions“, „User,…“) vor der
    eigentlichen Kopfzeile überspringen."""
    for i, ln in enumerate(lines[:10]):
        low = ln.lower()
        if "transaction type" in low and "quantity transacted" in low:
            return lines[i:]
    return lines


def _plain_header(c: str) -> str:
    """Spaltenname ohne Fußnotenzeichen (Binance: „Type¹“, „Executed²“)."""
    return re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰*]", "", c).strip().lower()


def _money(cell: str) -> float | None:
    """„-€1234.50000“, „€90,000.00“, „$12.50“ → Betrag ohne Vorzeichen."""
    return _parse_decimal(re.sub(r"[^\d.,-]", "", cell or ""))


def _parse_signed(cell: str) -> float | None:
    """Plain decimal with sign („-0.01000000“, „1500.25“); NULL/leer → None."""
    try:
        return float((cell or "").strip().replace(",", ""))
    except ValueError:
        return None
