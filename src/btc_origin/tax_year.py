"""Jahressteuerreport — Datenbasis für ein Kalenderjahr (Veranlagungsjahr).

Der Jahressteuerreport ist ein Auszug aus dem Herkunftsnachweis: Er nutzt denselben
Bericht (``HerkunftReport`` mit ``until`` = 31.12. des Jahres, also die vollständige
Historie bis Jahresende) und filtert auf die Vorgänge vom 01.01. bis 31.12. (UTC). Nur die
Anschaffungen der veräußerten Teilbestände dürfen vor dem Jahr liegen.

* Veräußerungen: je Vorgang ein Block mit den veräußerten Teilbeständen (Einzelbetrachtung
  bzw. FiFo auf dem Börsenkonto) — dieselben Werte wie Abschnitt 3 des Herkunftsnachweises.
* Anschaffungen des Jahres, sonstige Bewegungen (Umbuchungen, Satoshi-Tests, Einzahlungen
  auf eigene Börsenkonten), offene Punkte, Wallets mit Aktivität, Mengenabstimmung.
* Einkünfte nach § 22 Nr. 3 EStG: Export-Zeilen und Zuflüsse, die nach ``kategorien.yaml``
  einer Kategorie mit ``behandlung: einkunft_22_3`` zugeordnet sind (z. B. empfehlung) — keine
  Texterkennung. Bewertung zum Tageskurs; der Zufluss ist zugleich eine Anschaffung.
* ``local/steuer.csv``: weitere private Veräußerungsgeschäfte je Jahr (nicht in diesem
  Report). Freigrenzen und Stichtag Altbestand kommen aus dem Regelwerk des Jahres
  (``regeln/<JJJJ>.yaml``, siehe regelwerk.py).

Nur im Arbeitsspeicher. Keine Steuerberatung.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable

from btc_origin.origin_report import (
    HerkunftReport,
    InflowLine,
    _base_name,
    inflow_explanation,
    outflow_explanation,
    tax_free_from,
    tx_refs,
    wallet_reconciliation,
)
from btc_origin.regelwerk import Regelwerk, rules_for
from btc_origin.year_summary import YearSummary, freigrenze_eur

SATS_PER_BTC = 100_000_000
TAX_FILE = "steuer.csv"
_OTHER_SALES = "weitere veräußerungsgeschäfte"
_ALT_BIS = "altbestand bis"  # früher hier, jetzt im Regelwerk
_ACQ_BUY = "Kauf auf "


# ---------------------------------------------------------------------------
# Konfiguration: local/steuer.csv
# ---------------------------------------------------------------------------


@dataclass
class TaxConfig:
    # Jahr → Gewinn/Verlust weiterer privater Veräußerungsgeschäfte (EUR), nicht in diesem Report
    other_sales: dict[int, float] = field(default_factory=dict)


def _eur(cell: str) -> float | None:
    """„-1.234,56 €“, „1234.5“, „0“ → float mit Vorzeichen; None, wenn keine Zahl."""
    s = re.sub(r"[€\s]|EUR", "", cell or "", flags=re.IGNORECASE)
    neg = s.startswith(("-", "−"))
    s = s.lstrip("-−+")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    return -float(s) if neg else float(s)


def _date(cell: str) -> date | None:
    s = (cell or "").strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse_tax_config(text: str) -> tuple[TaxConfig, list[str]]:
    """``local/steuer.csv`` — je Zeile ``Jahr;Weitere Veräußerungsgeschäfte;Betrag``.
    Trennzeichen ; , oder Tab, #-Kommentare. (Der Stichtag Altbestand steht im Regelwerk.)"""
    from btc_origin.local_files import _simple_rows

    cfg = TaxConfig()
    errors: list[str] = []
    for i, row in enumerate(_simple_rows((text or "").lstrip("﻿"), max_fields=3), 1):
        key = row[0].strip().lower() if row else ""
        if key == _ALT_BIS:
            errors.append(f"Zeile {i}: „Altbestand bis“ steht jetzt im Regelwerk "
                          "(regeln/<Jahr>.yaml, reform.stichtag_altbestand) — Zeile ignoriert")
            continue
        if re.fullmatch(r"\d{4}", key) and len(row) >= 3 and row[1].strip().lower() == _OTHER_SALES:
            v = _eur(row[2])
            if v is None:
                errors.append(f"Zeile {i}: Betrag „{row[2]}“ nicht erkannt")
            else:
                cfg.other_sales[int(key)] = v
            continue
        if i == 1 and key in ("jahr", "art", "schlüssel", "schluessel"):
            continue  # Kopfzeile
        errors.append(
            f"Zeile {i}: erwartet „Jahr;Weitere Veräußerungsgeschäfte;Betrag“"
        )
    return cfg, errors


# ---------------------------------------------------------------------------
# Daten
# ---------------------------------------------------------------------------


@dataclass
class TaxLot:
    """Veräußerter Teilbestand (↳-Zeile eines Veräußerungsvorgangs)."""

    acquisition: str | None  # ISO-Tag; None = unbekannt (gilt als steuerbar)
    sats: int
    days_held: int | None
    taxable: bool  # innerhalb der Haltefrist (§ 23 Abs. 1 Satz 1 Nr. 2 EStG)
    proceeds_eur: float | None
    cost_eur: float | None
    fee_eur: float | None  # anteilige Werbungskosten
    gain_eur: float | None
    origin_txid: str = ""  # Zufluss, aus dem der Teilbestand stammt
    acq_source: str = ""  # „Kauf auf X lt. Export“ … / "" = Zuflusstag, Tageskurs
    origin: str = "wallet"  # wallet | exchange (auf der Börse gekauft) | unknown
    altbestand: bool | None = None  # angeschafft bis reform.stichtag_altbestand; None = unbekannt
    fee_sats: int = 0  # Netzwerkgebühr (Anteil), in fee_eur enthalten

    @property
    def beleg(self) -> str:
        """Belegart der Anschaffung: „Export“ (Kauf lt. Börsen-Export) oder „Tageskurs“."""
        return "Export" if self.acq_source.startswith(_ACQ_BUY) else "Tageskurs"


@dataclass
class TaxDisposal:
    """Ein Veräußerungsvorgang: Abfluss an eine fremde Adresse (angenommen) oder Verkauf
    laut Börsen-Export (Teilbestände aus mehreren Einzahlungen/Käufen möglich)."""

    day: str
    art: str
    counterparty: str
    txid: str  # Abfluss-Transaktion (angenommene Veräußerung); "" bei Verkauf lt. Export
    status: str  # „Verkauf am … lt. Export“ / ""
    lots: list[TaxLot]
    assumed: bool  # Abfluss ohne Verkaufsbeleg → Veräußerung am Abflusstag angenommen
    proceeds_estimated: bool  # Erlös zum Tageskurs (kein Betrag im Export bzw. angenommen)
    note: str = ""  # Erläuterung aus erlaeuterungen.csv (Art „Abfluss“)

    @property
    def sats(self) -> int:
        return sum(x.sats for x in self.lots)

    @property
    def fee_sats(self) -> int:
        return sum(x.fee_sats for x in self.lots)

    def _sum(self, attr: str, taxable: bool | None = None) -> float | None:
        vals = [getattr(x, attr) for x in self.lots if taxable is None or x.taxable == taxable]
        return None if any(v is None for v in vals) else float(sum(vals))

    @property
    def proceeds_eur(self) -> float | None:
        return self._sum("proceeds_eur")

    @property
    def fee_eur(self) -> float | None:
        return self._sum("fee_eur")

    def gain(self, taxable: bool) -> float | None:
        return self._sum("gain_eur", taxable)

    @property
    def has_taxable(self) -> bool:
        return any(x.taxable for x in self.lots)

    @property
    def has_free(self) -> bool:
        return any(not x.taxable for x in self.lots)


@dataclass
class TaxAcquisition:
    """Anschaffung im Jahr (Abschnitt 3). Mehrere belegte Käufe eines Zuflusses = eine Zeile
    mit ``count`` (Einzelheiten in den Export-Zeilen, Anhang B)."""

    day: str  # Anschaffungstag (bei mehreren Käufen der erste)
    wallet: str  # Ziel-Wallet oder „Börse X“ (nicht ausgezahlt)
    source: str
    txid: str  # Zufluss ("" = auf der Börse verblieben)
    sats: int
    cost_eur: float | None
    beleg: str  # „Kauf auf X lt. Export“ / „Tageskurs“
    count: int = 1
    last_day: str = ""  # letzter Kauftag bei count > 1
    category: str = ""  # Kategorie mit behandlung einkunft_22_3 (z. B. empfehlung)
    altbestand: bool = True

    @property
    def holding_end(self) -> str:
        """Haltefrist endet am (Jahrestag des letzten Kaufs; ab dem Folgetag steuerfrei)."""
        d = date.fromisoformat((self.last_day or self.day)[:10])
        return (tax_free_from(d) - timedelta(days=1)).isoformat()


@dataclass
class TaxIncome:
    """Einkunft nach § 22 Nr. 3 EStG (Einordnung nach kategorien.yaml)."""

    day: str
    category: str
    source: str
    wallet: str
    sats: int
    value_eur: float | None  # Menge × Tageskurs
    txid: str = ""
    note: str = ""


@dataclass
class TaxMovement:
    """Bewegung ohne Veräußerung (Abschnitt 4, Rz. 54)."""

    kind: str  # Umbuchung | Satoshi-Test | Einzahlung auf eigenes Börsenkonto
    day: str
    txid: str
    sats: int
    fee_sats: int
    detail: str = ""  # z. B. „Wallet A → Wallet B“ oder Börse


@dataclass
class WalletActivity:
    name: str
    first: str
    last: str
    tx_count: int


@dataclass
class QuantityRecon:
    """Anfangsbestand + Zuflüsse − Abflüsse − Gebühren = Endbestand (= UTXO / Ledger)."""

    opening: int
    inflows: int
    outflows: int
    fees: int
    closing: int
    check_sats: int | None  # unverbrauchte Outputs (UTXO) bzw. Bestand laut Ledger am 31.12.
    check_label: str = ""

    @property
    def computed(self) -> int:
        return self.opening + self.inflows - self.outflows - self.fees

    @property
    def ok(self) -> bool:
        return self.computed == self.closing and (self.check_sats is None or self.check_sats == self.closing)


@dataclass
class TaxYear:
    year: int
    disposals: list[TaxDisposal]
    acquisitions: list[TaxAcquisition]
    income: list[TaxIncome]
    movements: list[TaxMovement]
    wallets: list[WalletActivity]
    recon: QuantityRecon | None
    unproven_inflows: list[InflowLine]
    open_exchange: list[dict[str, Any]]
    refs: dict[str, str]  # txid → T-Nummer (wie im Herkunftsnachweis)
    # nicht unterstützte Vorgänge des Jahres (kein Wert, keine Summe; Hinweis auf Seite 1,
    # offene Punkte, Prüfprotokoll mit Fehler)
    unsupported: list[dict[str, Any]] = field(default_factory=list)
    other_sales_eur: float = 0.0
    other_sales_given: bool = False
    altbestand_bis: date = date(2026, 12, 31)  # aus dem Regelwerk (build_tax_year)
    regelwerk: Regelwerk | None = None  # regeln/<Jahr>.yaml
    summary: YearSummary | None = None  # Jahreswerte des Herkunftsnachweises (Abgleich)
    unproven_notes: dict[str, str] = field(default_factory=dict)  # txid → Erläuterung

    # ---- § 23 EStG -------------------------------------------------------
    @property
    def freigrenze_eur(self) -> int:
        return self.regelwerk.freigrenze_23_eur if self.regelwerk else freigrenze_eur(self.year)

    @property
    def show_reform(self) -> bool:
        """Spalte Alt-/Neubestand erst, wenn das Jahr Tage nach dem Stichtag „Altbestand bis“
        enthält (Vorgabe 31.12.2026 → ab Veranlagungsjahr 2027)."""
        return date(self.year, 12, 31) > self.altbestand_bis

    def _lots(self, taxable: bool) -> list[TaxLot]:
        return [x for d in self.disposals for x in d.lots if x.taxable == taxable]

    def total(self, taxable: bool, attr: str) -> float | None:
        vals = [getattr(x, attr) for x in self._lots(taxable)]
        return None if any(v is None for v in vals) else float(sum(vals))

    def count(self, taxable: bool) -> int:
        return sum(1 for d in self.disposals if (d.has_taxable if taxable else d.has_free))

    def sats(self, taxable: bool) -> int:
        return sum(x.sats for x in self._lots(taxable))

    @property
    def gain_taxable(self) -> float | None:
        return self.total(True, "gain_eur")

    @property
    def gain_total(self) -> float | None:
        """Gewinn/Verlust innerhalb der Haltefrist + weitere Veräußerungsgeschäfte (Konfig)."""
        g = self.gain_taxable
        return None if g is None else g + self.other_sales_eur

    @property
    def freigrenze_reached(self) -> bool | None:
        g = self.gain_total
        return None if g is None else g >= self.freigrenze_eur

    @property
    def loss(self) -> bool:
        g = self.gain_total
        return g is not None and g < 0

    # ---- § 22 Nr. 3 EStG -------------------------------------------------
    @property
    def income_total(self) -> float | None:
        vals = [x.value_eur for x in self.income]
        return None if any(v is None for v in vals) else float(sum(vals))

    @property
    def income_freigrenze_reached(self) -> bool | None:
        t = self.income_total
        return None if t is None else t >= self.freigrenze_22_3_eur

    @property
    def freigrenze_22_3_eur(self) -> int:
        """§ 22 Nr. 3 Satz 2 EStG — aus dem Regelwerk des Jahres."""
        return (self.regelwerk or rules_for(self.year)).freigrenze_22_3_eur

    # ---- offene Punkte ---------------------------------------------------
    @property
    def assumed_disposals(self) -> list[TaxDisposal]:
        return [d for d in self.disposals if d.assumed]

    def ref(self, txid: str) -> str:
        return self.refs.get(txid, "")

    def lot_in_year(self, lot: TaxLot) -> bool:
        return bool(lot.acquisition) and str(lot.acquisition)[:4] == str(self.year)


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


def _in_year(day: Any, year: int) -> bool:
    return bool(day) and str(day)[:4] == str(year)


def _is_alt(day: str | None, cutoff: date) -> bool | None:
    if not day:
        return None
    try:
        return date.fromisoformat(str(day)[:10]) <= cutoff
    except ValueError:
        return None


def _disposals(rep: HerkunftReport, year: int, cut: date) -> list[TaxDisposal]:
    ys = next((y for y in rep.years if y.year == year), None)
    if ys is None:
        return []
    groups: dict[tuple[str, ...], TaxDisposal] = {}
    for d in ys.details:
        if not d.get("disposal", True) or not _in_year(d.get("exit_date"), year):
            continue
        status = str(d.get("status") or "")
        origin = str(d.get("origin") or "wallet")
        day = str(d["exit_date"])[:10]
        cp = str(d.get("counterparty") or "")
        sale = bool(status)  # Verkauf laut Export
        key = ("sale", day, cp, status) if sale else ("out", str(d.get("txid") or ""), day, cp)
        g = groups.get(key)
        if g is None:
            g = groups[key] = TaxDisposal(
                day=day,
                art="Verkauf lt. Export" if sale else "Abfluss an fremde Adresse (Veräußerung angenommen)",
                counterparty=cp,
                txid="" if sale else str(d.get("txid") or ""),
                status=status,
                lots=[],
                assumed=not sale,
                proceeds_estimated=not sale or bool(d.get("proceeds_estimated")),
            )
        acq = d.get("acquisition_date")
        g.lots.append(TaxLot(
            acquisition=str(acq)[:10] if acq else None,
            sats=int(d.get("btc_sats") or 0),
            days_held=d.get("days_held"),
            taxable=bool(d.get("short_term")),
            proceeds_eur=d.get("proceeds_eur"),
            cost_eur=d.get("cost_eur"),
            fee_eur=d.get("fee_eur"),
            gain_eur=d.get("gain_eur"),
            origin_txid=str(d.get("origin_txid") or ""),
            acq_source=str(d.get("acq_source") or ""),
            origin=origin,
            altbestand=_is_alt(acq, cut),
            fee_sats=int(d.get("fee_sats") or 0),
        ))
        if d.get("proceeds_estimated"):
            g.proceeds_estimated = True
    out = sorted(groups.values(), key=lambda g: (g.day, g.art, g.counterparty, g.txid))
    for g in out:
        g.lots.sort(key=lambda x: (x.acquisition or "9999", x.origin_txid))
        if g.assumed:
            g.note = outflow_explanation(rep.explanations, g.counterparty, g.day)
    return out


def _income(
    rep: HerkunftReport, year: int, price_eur: Callable[[date], float | None] | None
) -> list[TaxIncome]:
    """Einkünfte nach § 22 Nr. 3 EStG im Jahr — nur aus der Einordnung nach kategorien.yaml
    (behandlung einkunft_22_3), keine Texterkennung: Export-Zeilen, deren Art so zugeordnet
    ist, und Zuflüsse aus ``zuordnung_manuell``. Wert = Menge × Tageskurs am Zuflusstag."""
    out: list[TaxIncome] = []

    def value(sats: int, day: str, fallback: float | None) -> float | None:
        px = price_eur(date.fromisoformat(day[:10])) if price_eur is not None else fallback
        return None if px is None else sats / SATS_PER_BTC * px

    for t in rep.exchange_trades:
        if not t.get("einkunft") or not _in_year(t.get("day"), year):
            continue
        day = str(t["day"])[:10]
        out.append(TaxIncome(day, str(t.get("kategorie") or ""), str(t["exchange"]), f"Börse {t['exchange']}",
                             int(t["sats"]), value(int(t["sats"]), day, t.get("price_eur")), "",
                             f"Art laut Export „{t.get('art') or ''}“"))
    seen: set[str] = set()
    for x in rep.inflows:
        m = rep.manual_categories.get(x.txid)
        if m is None or m.get("behandlung") != "einkunft_22_3" or not _in_year(x.date, year) or x.txid in seen:
            continue
        seen.add(x.txid)
        sats = int(m["sats"]) if m.get("sats") is not None else sum(y.sats for y in rep.inflows if y.txid == x.txid)
        out.append(TaxIncome(x.date, m["kategorie"], x.source, x.wallet, sats, value(sats, x.date, x.price_eur),
                             x.txid, m.get("erlaeuterung") or f"{m['tnr']} (Angabe des Steuerpflichtigen)"))
    out.sort(key=lambda i: (i.day, i.source, i.txid))
    return out


def _acquisitions(
    rep: HerkunftReport, year: int, cut: date, income: list[TaxIncome],
    price_eur: Callable[[date], float | None] | None,
) -> list[TaxAcquisition]:
    """Anschaffungen im Jahr: Zuflüsse ohne Kaufbeleg (Zuflusstag), belegte Käufe mit Kaufdatum
    im Jahr (je Zufluss eine Zeile), Käufe auf einer Börse, die nicht ausgezahlt wurden, und
    Leistungen (zum Tageskurs)."""
    out: list[TaxAcquisition] = []
    income_tx = {i.txid: i for i in income if i.txid}
    by_tx: dict[str, list[InflowLine]] = {}
    for x in rep.inflows:
        by_tx.setdefault(x.txid, []).append(x)
    for txid, lines in by_tx.items():
        lines = [x for x in lines if _in_year(x.acquisition or x.date, year)
                 and (not x.acq_source or x.acq_source.startswith(_ACQ_BUY))]
        if not lines:
            continue  # Anschaffung vor dem Jahr (Umbuchung vom eigenen Börsenkonto)
        lines.sort(key=lambda x: x.acquisition or x.date)
        first = lines[0]
        inc = income_tx.get(txid)
        costs = [x.price_eur * x.sats / SATS_PER_BTC if x.price_eur is not None else None for x in lines]
        cost = None if any(c is None for c in costs) else float(sum(c for c in costs if c is not None))
        if inc is not None:
            cost = inc.value_eur  # Leistung: Anschaffung zum Tageskurs
        day = first.acquisition or first.date
        out.append(TaxAcquisition(
            day=day, wallet=first.wallet, source=first.source, txid=txid, sats=sum(x.sats for x in lines),
            cost_eur=cost, beleg=first.acq_source or "Tageskurs", count=len(lines),
            last_day=(lines[-1].acquisition or lines[-1].date) if len(lines) > 1 else "",
            category=inc.category if inc is not None else "",
            altbestand=bool(_is_alt(day, cut)),
        ))
    # Käufe auf einer Börse, soweit nicht mit einem zugeordneten Zufluss ausgezahlt
    paid_out: dict[tuple[str, str], int] = {}
    for rc in rep.withdrawal_recon.values():
        for p in rc.get("pieces") or []:
            if str(p.get("source") or "").startswith(_ACQ_BUY):
                k = (str(rc.get("exchange") or ""), str(p.get("day") or "")[:10])
                paid_out[k] = paid_out.get(k, 0) + int(p.get("sats") or 0)
    buys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for t in rep.exchange_trades:
        if t.get("kind") == "Kauf" and _in_year(t.get("day"), year):
            buys.setdefault((str(t["exchange"]), str(t["day"])[:10]), []).append(t)
    for (ex, day), rows in sorted(buys.items()):
        total = sum(int(t["sats"]) for t in rows)
        rest = total - paid_out.get((ex, day), 0)
        if rest <= 0:
            continue
        eur = [t.get("eur") for t in rows]
        if all(e is not None for e in eur):
            cost: float | None = float(sum(eur)) * rest / total  # type: ignore[arg-type]
            beleg = f"Kauf auf {ex} lt. Export"
        else:
            px = price_eur(date.fromisoformat(day)) if price_eur is not None else None
            cost = None if px is None else rest / SATS_PER_BTC * px
            beleg = f"Kauf auf {ex} lt. Export (Tageskurs)"
        out.append(TaxAcquisition(
            day=day, wallet=f"Börse {ex}", source=ex, txid="", sats=rest, cost_eur=cost, beleg=beleg,
            count=len(rows), altbestand=bool(_is_alt(day, cut)),
        ))
    # Leistungen auf der Börse (keine Zufluss-Transaktion) — Anschaffung zum Tageskurs
    for inc in income:
        if inc.txid:
            continue
        hit = next((a for a in out if not a.txid and a.day == inc.day and _base_name(a.source) == _base_name(inc.source)),
                   None)
        if hit is not None:
            hit.category, hit.cost_eur, hit.beleg = inc.category, inc.value_eur, "Tageskurs"
        else:
            out.append(TaxAcquisition(
                day=inc.day, wallet=inc.wallet, source=inc.source, txid="", sats=inc.sats, cost_eur=inc.value_eur,
                beleg="Tageskurs", category=inc.category, altbestand=bool(_is_alt(inc.day, cut)),
            ))
    return sorted(out, key=lambda a: (a.day, a.wallet, a.txid))


def _movements(
    rep: HerkunftReport, year: int, rows: list[dict[str, Any]], names: dict[Any, str]
) -> list[TaxMovement]:
    from btc_origin.exchange_sales import SATOSHI_TEST_STATUS

    out: list[TaxMovement] = []
    # Einzahlungen auf eigene Börsenkonten (auch später verkaufte) und Satoshi-Tests
    deposits: dict[str, TaxMovement] = {}
    for y in rep.years:
        for d in y.details:
            if str(d.get("origin") or "wallet") != "wallet" or not _in_year(d.get("deposit_date"), year):
                continue
            txid = str(d.get("txid") or "")
            sat_test = str(d.get("status") or "").startswith(SATOSHI_TEST_STATUS)
            m = deposits.setdefault(txid, TaxMovement(
                kind="Satoshi-Test" if sat_test else "Einzahlung auf eigenes Börsenkonto",
                day=str(d["deposit_date"])[:10], txid=txid, sats=0, fee_sats=0,
                detail=str(d.get("counterparty") or "").replace("³", ""),
            ))
            m.sats += int(d.get("btc_sats") or 0)
            m.fee_sats += int(d.get("fee_sats") or 0)
    out += deposits.values()
    # Umbuchungen zwischen eigenen Wallets (Transaktionsverzeichnis: Art „Umbuchung“)
    by_tx: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_tx.setdefault(str(r.get("txid") or ""), []).append(r)
    for ref in tx_refs(rep):
        if ref.art != "Umbuchung" or not _in_year(ref.day, year):
            continue
        group = by_tx.get(ref.txid, [])
        ins = [r for r in group if r.get("direction") == "in"]
        outs = [r for r in group if r.get("direction") == "out"]
        src = sorted({names.get(r.get("wallet_id"), "") for r in outs} - {""})
        dst = sorted({names.get(r.get("wallet_id"), "") for r in ins} - {""})
        out.append(TaxMovement(
            kind="Umbuchung", day=ref.day, txid=ref.txid,
            sats=sum(int(r.get("amount_sats") or 0) for r in ins),
            fee_sats=max((int(r.get("fee_sats") or 0) for r in outs), default=0),
            detail=f"{', '.join(src) or '—'} → {', '.join(dst) or '—'}",
        ))
    return sorted(out, key=lambda m: (m.day, m.kind, m.txid))


def _wallets(rows: list[dict[str, Any]], names: dict[Any, str], year: int) -> list[WalletActivity]:
    acc: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = names.get(r.get("wallet_id"))
        day = str(r.get("block_time") or "")[:10]
        if name is None or not _in_year(day, year):
            continue
        a = acc.setdefault(name, {"first": day, "last": day, "txs": set()})
        a["first"], a["last"] = min(a["first"], day), max(a["last"], day)
        a["txs"].add(str(r.get("txid") or ""))
    return [WalletActivity(n, a["first"], a["last"], len(a["txs"])) for n, a in sorted(acc.items())]


def _recon(
    rep: HerkunftReport, year: int, rows: list[dict[str, Any]], names: dict[Any, str], today: date
) -> QuantityRecon:
    start = wallet_reconciliation(rows, names, date(year - 1, 12, 31))
    end = wallet_reconciliation(rows, names, date(year, 12, 31))

    def total(rc: dict[str, Any], attr: str) -> int:
        return sum(int(getattr(w, attr)) for w in rc.values())

    closing = total(end, "balance")
    if date(year, 12, 31) >= today and rep.chain_balance_sats is not None:
        check, label = rep.chain_balance_sats, "unverbrauchte Outputs (UTXO) heute"
    elif rep.stichtag_balances is not None and rep.stichtag == date(year, 12, 31):
        check, label = sum(rep.stichtag_balances.values()), "Bestand laut Blockchain am 31.12."
    else:
        check, label = None, ""
    return QuantityRecon(
        opening=total(start, "balance"),
        inflows=total(end, "ext_in") - total(start, "ext_in"),
        outflows=total(end, "ext_out") - total(start, "ext_out"),
        fees=total(end, "fees") - total(start, "fees"),
        closing=closing,
        check_sats=check,
        check_label=label,
    )


def build_tax_year(
    rep: HerkunftReport,
    year: int,
    *,
    config: TaxConfig | None = None,
    rows: Iterable[dict[str, Any]] = (),
    wallet_names: dict[Any, str] | None = None,
    price_eur: Callable[[date], float | None] | None = None,
    today: date | None = None,
) -> TaxYear:
    """Jahressteuerreport-Daten aus dem Herkunftsnachweis (``rep`` mit ``until`` = 31.12.
    des Jahres). ``rows`` / ``wallet_names``: Rohdaten der Wallets (Mengenabstimmung,
    Umbuchungen, Wallet-Aktivität); ``price_eur``: Tageskurs für Leistungen."""
    cfg = config or TaxConfig()
    rw = rules_for(year)  # fehlt regeln/<Jahr>.yaml → RegelwerkFehler, kein Report
    cut = rw.altbestand_stichtag()
    rows = [r for r in rows if r.get("block_time") and str(r["block_time"])[:10] <= f"{year}-12-31"]
    names = wallet_names or {}
    income = _income(rep, year, price_eur)
    # Zuflüsse mit Einkunft nach § 22 Nr. 3 (Einordnung per zuordnung_manuell) sind belegt —
    # kein offener Punkt (Seite 1, Abschnitt 3, Anhang B)
    income_tx = {i.txid for i in income if i.txid}
    unproven = [x for x in rep.inflows if _in_year(x.date, year) and not x.acq_source and x.txid not in income_tx]
    linked = {str(r.get("entry_txid")): str(r["note"]) for r in rep.open_exchange
              if r.get("entry_txid") and r.get("note") and r["note"] != "offen"}
    return TaxYear(
        year=year,
        disposals=_disposals(rep, year, cut),
        acquisitions=_acquisitions(rep, year, cut, income, price_eur),
        income=income,
        movements=_movements(rep, year, rows, names),
        wallets=_wallets(rows, names, year),
        recon=_recon(rep, year, rows, names, today or date.today()) if names else None,
        unproven_inflows=unproven,
        unproven_notes={x.txid: inflow_explanation(rep.explanations, x) or linked.get(x.txid, "")
                        for x in unproven},
        open_exchange=[r for r in rep.open_exchange if _in_year(r.get("day"), year)],
        refs={r.txid: r.ref for r in tx_refs(rep)},
        unsupported=[u for u in rep.unsupported if _in_year(u.get("day"), year)],
        other_sales_eur=cfg.other_sales.get(year, 0.0),
        other_sales_given=year in cfg.other_sales,
        altbestand_bis=cut,
        regelwerk=rw,
        summary=next((y for y in rep.years if y.year == year), None),
    )
