"""Regelwerk: steuerrechtliche Parameter und Kategorien aus YAML (``btc-regeln/``).

Aufbau und Pflege: ``btc-regeln/REGELWERK.md`` (verbindlich).

* ``btc-regeln/regeln/<JJJJ>.yaml`` — je Veranlagungsjahr eine Datei. Ein Rechenschritt für
  das Jahr X verwendet ausschließlich ``regeln/X.yaml``, auch innerhalb eines Berichts über
  mehrere Jahre. Fehlt die Datei, bricht das Tool ab (``RegelwerkFehler``) — es übernimmt nie
  die Werte eines anderen Jahres.
* ``btc-regeln/kategorien.yaml`` — jahresunabhängig: Kategorien und Zuordnungen. Persönliche
  Zuordnungen einzelner Zuflüsse (``zuordnung_manuell``) stehen in ``local/kategorien.yaml``
  (nicht im Git).

Validierung beim Laden (REGELWERK.md Abschnitt 5): Datei je benötigtem Jahr vorhanden,
``veranlagungsjahr`` = Dateiname, Pflichtfelder und Typen, Freigrenzen in den Textbausteinen =
Zahlenwerte, Kategorien der Zuordnungen definiert, T-Nummern vorhanden und Datum passend,
keine doppelten ``id``/Arten/Schlüssel. Jeder Fehler bricht ab.

Unterstützt: Haltefrist 12 Monate; ``reform.beschlossen: true`` ist nicht umgesetzt → Abbruch.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

RULES_DIR_ENV = "BTC_ORIGIN_RULES_DIR"
ROOT_DIR = "btc-regeln"
YEARS_DIR = "regeln"
CATEGORIES_FILE = "kategorien.yaml"
UNTERSTUETZTE_HALTEFRIST_MONATE = 12
BEHANDLUNGEN = ("anschaffung", "einkunft_22_3", "nicht_unterstuetzt")
# Technische Arten des Parsers — keine steuerlichen Kategorien (REGELWERK.md 4.2)
TECHNISCHE_ARTEN = ("verkauf", "auszahlung", "einzahlung")
# Kursregeln, die das Programm umsetzt (price_oracle): Kennung → Beschreibung. Eine andere
# Kennung in kursregel.id bricht ab — der Text in der Regeldatei ist nur Anzeige.
KURSREGELN = {
    "binance_tagesschluss_utc": "Binance BTC/EUR-Tagesschlusskurs (UTC); vor dem 03.01.2020 Binance "
    "BTC/USDT ÷ EZB-Referenzkurs USD/EUR; vor dem 17.08.2017 mempool.space",
}
_ID_RE = re.compile(r"[a-z0-9_]+")
_TNR_RE = re.compile(r"T-\d{3,}")
# Transaktions-Hash, optional mit Output-Nummer (txid:vout) — stabil, anders als die T-Nummer
_TXID_RE = re.compile(r"([0-9a-f]{64})(?::(\d+))?")


def split_key(key: str) -> tuple[str, int | None] | None:
    """„txid“ bzw. „txid:vout“ → (txid, vout); None = kein Transaktions-Hash."""
    m = _TXID_RE.fullmatch(key or "")
    return (m.group(1), int(m.group(2)) if m.group(2) is not None else None) if m else None


def short_key(key: str) -> str:
    """Hash gekürzt für Meldungen (nie vollständig in Fehlermeldungen)."""
    k = split_key(key)
    return key if k is None else k[0][:12] + "…" + (f":{k[1]}" if k[1] is not None else "")


def inflow_outputs(rows: Iterable[dict[str, Any]], entry_txids: set[str]) -> dict[str, list[tuple[int, int]]]:
    """Zuflüsse in die betrachteten Wallets je Transaktion: txid → [(vout, sats)] (Flow-Zeilen
    „in“ der Cloud-Eintritte)."""
    out: dict[str, dict[int, int]] = {}
    for r in rows:
        tx = str(r.get("txid") or "")
        if r.get("direction") == "in" and tx in entry_txids and r.get("vout") is not None:
            out.setdefault(tx, {})
            out[tx][int(r["vout"])] = out[tx].get(int(r["vout"]), 0) + int(r.get("amount_sats") or 0)
    return {tx: sorted(v.items()) for tx, v in out.items()}


def check_outputs(key: str, where: str, outputs: dict[str, list[tuple[int, int]]] | None) -> None:
    """txid allein nur bei genau einem Zufluss in die betrachteten Wallets; txid:vout muss ein
    solcher Zufluss sein. ``outputs``: txid → [(vout, sats)] der Zuflüsse (None = nicht prüfen)."""
    k = split_key(key)
    if k is None or outputs is None:
        return
    outs = outputs.get(k[0], [])
    if k[1] is None and len(outs) > 1:
        raise RegelwerkFehler(f"{where}: die Transaktion hat {len(outs)} Zuflüsse in die betrachteten Wallets "
                              f"— Output angeben als txid:vout (Outputs {', '.join(str(v) for v, _ in outs)})")
    if k[1] is not None and k[1] not in {v for v, _ in outs}:
        raise RegelwerkFehler(f"{where}: Output {k[1]} ist kein Zufluss in die betrachteten Wallets")


class RegelwerkFehler(Exception):
    """Regel- oder Kategoriendatei fehlt, ist ungültig oder verlangt etwas, das das Tool
    nicht rechnet. Die Meldung nennt Datei und Feld."""


# ---------------------------------------------------------------------------
# YAML ohne doppelte Schlüssel
# ---------------------------------------------------------------------------


def _load_yaml(text: str, datei: str) -> Any:
    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    def mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        out: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in out:
                raise RegelwerkFehler(
                    f"{datei}: Schlüssel „{key}“ doppelt (Zeile {key_node.start_mark.line + 1})"
                )
            out[key] = loader.construct_object(value_node, deep=deep)
        return out

    _Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        return yaml.load(text, Loader=_Loader)  # noqa: S506 — SafeLoader-Ableitung
    except yaml.YAMLError as exc:
        raise RegelwerkFehler(f"{datei}: kein gültiges YAML ({exc})") from exc


_KIND = {"int": "Ganzzahl", "date": "Datum JJJJ-MM-TT", "bool": "Boolesch", "str": "Text",
         "list": "Liste", "dict": "Zuordnung"}


def _get(d: Any, path: str, typ: type, datei: str, *, required: bool = True) -> Any:
    cur = d
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur or cur[key] is None:
            if required:
                raise RegelwerkFehler(f"{datei}: Pflichtfeld „{path}“ fehlt")
            return None
        cur = cur[key]
    # bool ist in Python ein int, datetime ein date — ausdrücklich ausschließen
    wrong = not isinstance(cur, typ) or (typ is not bool and isinstance(cur, bool))
    if typ is date and type(cur).__name__ == "datetime":
        wrong = True
    if wrong:
        raise RegelwerkFehler(
            f"{datei}: Feld „{path}“ hat den falschen Typ (erwartet {_KIND.get(typ.__name__, typ.__name__)})"
        )
    return cur


# ---------------------------------------------------------------------------
# Regeldatei je Veranlagungsjahr
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rechtstext:
    id: str
    titel: str
    text: str


@dataclass(frozen=True)
class Regelwerk:
    veranlagungsjahr: int
    stand: date
    geprueft_von: str
    bmf_datum: date
    bmf_gz: str
    bmf_titel: str
    bmf_anwendung: str
    haltefrist_monate: int
    freigrenze_23_eur: int
    verlustverrechnung: str
    freigrenze_22_3_eur: int
    kursregel_id: str
    kursregel_primaer: str
    kursregel_ezb: str
    reform_beschlossen: bool
    reform_hinweis: str
    reform_ausblick: str
    stichtag_altbestand: date | None = None
    bmf_vorgaenger: str = ""
    texte: tuple[Rechtstext, ...] = ()
    datei: str = ""
    sha256: str = ""

    @property
    def vermerk(self) -> str:
        """„Regelwerk VZ 2025, Stand 29.09.2026“."""
        return f"Regelwerk VZ {self.veranlagungsjahr}, Stand {self.stand.strftime('%d.%m.%Y')}"

    @property
    def bmf_quelle(self) -> str:
        """BMF-Schreiben aus ``quelle`` als Satz (Abschnitt Rechtsgrundlagen)."""
        out = f"BMF-Schreiben vom {self.bmf_datum.strftime('%d.%m.%Y')}, GZ {self.bmf_gz}: „{self.bmf_titel}“"
        out += f"; anzuwenden auf {self.bmf_anwendung}"
        if self.bmf_vorgaenger:
            out += f"; Vorgänger: {self.bmf_vorgaenger}"
        return out + "."

    def altbestand_stichtag(self) -> date:
        if self.stichtag_altbestand is None:
            raise RegelwerkFehler(
                f"{self.datei}: reform.stichtag_altbestand fehlt — Alt-/Neubestand für "
                f"VZ {self.veranlagungsjahr} nicht bestimmbar"
            )
        return self.stichtag_altbestand


def _amounts(text: str) -> list[int]:
    """„1.000 €“, „1000 €“, „256 EUR“ → [1000, 256]."""
    return [int(m.group(1).replace(".", "")) for m in re.finditer(r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:€|EUR)", text)]


def parse_rules(text: str, year: int, datei: str = "") -> Regelwerk:
    """YAML-Text einer Regeldatei → ``Regelwerk`` (Schema nach REGELWERK.md Abschnitt 3)."""
    datei = datei or f"{YEARS_DIR}/{year}.yaml"
    d = _load_yaml(text, datei)
    if not isinstance(d, dict):
        raise RegelwerkFehler(f"{datei}: erwartet eine Zuordnung (Schlüssel: Wert)")
    vz = _get(d, "veranlagungsjahr", int, datei)
    if vz != year:
        raise RegelwerkFehler(f"{datei}: veranlagungsjahr {vz} passt nicht zum Dateinamen ({year})")
    halte = _get(d, "paragraph_23.haltefrist_monate", int, datei)
    if halte != UNTERSTUETZTE_HALTEFRIST_MONATE:
        raise RegelwerkFehler(
            f"{datei}: haltefrist_monate {halte} wird nicht unterstützt (nur {UNTERSTUETZTE_HALTEFRIST_MONATE})"
        )
    if _get(d, "reform.beschlossen", bool, datei):
        raise RegelwerkFehler(
            f"{datei}: reform.beschlossen: true — eine beschlossene Reform rechnet das Tool noch nicht "
            "(eigener Regelblock nötig, REGELWERK.md 3.2); Berechnung abgebrochen"
        )
    fg23 = _get(d, "paragraph_23.freigrenze_eur", int, datei)
    fg22 = _get(d, "paragraph_22_nr_3.freigrenze_eur", int, datei)
    parsed: list[Rechtstext] = []
    for i, t in enumerate(_get(d, "texte", list, datei)):
        where = f"{datei}: texte[{i}]"
        if not isinstance(t, dict):
            raise RegelwerkFehler(f"{where} erwartet id, titel, text")
        tid, titel, body = (_get(t, k, str, where) for k in ("id", "titel", "text"))
        if not _ID_RE.fullmatch(tid):
            raise RegelwerkFehler(f"{where}: id „{tid}“ — nur a–z, 0–9 und _ erlaubt")
        parsed.append(Rechtstext(tid, titel, body))
    ids = [t.id for t in parsed]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise RegelwerkFehler(f"{datei}: texte enthält die id „{dup[0]}“ mehrfach")
    # Freigrenzen in den Textbausteinen = Zahlenwerte derselben Datei
    for t in parsed:
        for marker, value, field_name in (("§ 23 Abs. 3 Satz 5", fg23, "paragraph_23.freigrenze_eur"),
                                          ("§ 22 Nr. 3", fg22, "paragraph_22_nr_3.freigrenze_eur")):
            if marker not in t.titel:
                continue
            found = _amounts(t.text)
            wrong = [a for a in found if a != value]
            if wrong or not found:
                raise RegelwerkFehler(
                    f"{datei}: Textbaustein „{t.id}“ nennt "
                    + (f"{wrong[0]} €" if wrong else "keinen Betrag")
                    + f", {field_name} ist {value} €"
                )
    q = _get(d, "quelle", dict, datei)
    kurs_id = _get(d, "kursregel.id", str, datei)
    if kurs_id not in KURSREGELN:
        raise RegelwerkFehler(
            f"{datei}: kursregel.id „{kurs_id}“ ist im Programm nicht umgesetzt "
            f"(bekannt: {', '.join(KURSREGELN)})"
        )
    return Regelwerk(
        veranlagungsjahr=vz,
        stand=_get(d, "stand", date, datei),
        geprueft_von=_get(d, "geprueft_von", str, datei),
        bmf_datum=_get(d, "quelle.bmf_schreiben.datum", date, datei),
        bmf_gz=_get(d, "quelle.bmf_schreiben.gz", str, datei),
        bmf_titel=_get(d, "quelle.bmf_schreiben.titel", str, datei),
        bmf_anwendung=_get(d, "quelle.bmf_schreiben.anwendung", str, datei),
        bmf_vorgaenger=_get(q, "vorgaenger", str, datei, required=False) or "",
        haltefrist_monate=halte,
        freigrenze_23_eur=fg23,
        verlustverrechnung=_get(d, "paragraph_23.verlustverrechnung", str, datei),
        freigrenze_22_3_eur=fg22,
        kursregel_id=kurs_id,
        kursregel_primaer=_get(d, "kursregel.primaer", str, datei),
        kursregel_ezb=_get(d, "kursregel.wochenende_feiertag_ezb", str, datei),
        reform_beschlossen=False,
        reform_hinweis=_get(d, "reform.hinweis", str, datei),
        reform_ausblick=_get(d, "reform.ausblick", str, datei),
        stichtag_altbestand=_get(d, "reform.stichtag_altbestand", date, datei, required=False),
        texte=tuple(parsed),
        datei=datei,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def rules_root() -> Path:
    """``BTC_ORIGIN_RULES_DIR`` oder ``<Projekt>/btc-regeln`` (Projekt = Ordner mit data/)."""
    env = os.environ.get(RULES_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data" / "label_packs").is_dir():
            return parent / ROOT_DIR
    return Path.cwd() / ROOT_DIR


_CACHE: dict[str, tuple[float, Any]] = {}


def _cached(path: Path, parse: Any) -> Any:
    mtime = path.stat().st_mtime
    hit = _CACHE.get(str(path))
    if hit is not None and hit[0] == mtime:
        return hit[1]
    value = parse(path.read_text(encoding="utf-8"))
    _CACHE[str(path)] = (mtime, value)
    return value


def rules_for(year: int, root: Path | None = None) -> Regelwerk:
    """Regelwerk des Veranlagungsjahres. Fehlende oder ungültige Datei → ``RegelwerkFehler``."""
    path = (root or rules_root()) / YEARS_DIR / f"{year}.yaml"
    if not path.is_file():
        raise RegelwerkFehler(
            f"Regelwerk für das Veranlagungsjahr {year} fehlt ({YEARS_DIR}/{year}.yaml). Ohne "
            "Regeldatei wird für dieses Jahr nichts berechnet — Datei nach Prüfung der Rechtslage "
            "anlegen (REGELWERK.md Abschnitt 6)."
        )
    return _cached(path, lambda text: parse_rules(text, year, f"{YEARS_DIR}/{year}.yaml"))


def rules_used(years: Iterable[int]) -> list[Regelwerk]:
    """Regelwerke der genannten Jahre (sortiert, ohne Doppelte) — lädt und prüft jedes."""
    return [rules_for(y) for y in sorted({int(y) for y in years})]


def methodik_vermerk(years: Iterable[int]) -> str:
    """Satz für die Methodik: welches Regelwerk für welches Jahr galt."""
    return "Steuerliche Parameter je Veranlagungsjahr: " + "; ".join(r.vermerk for r in rules_used(years)) + "."


# ---------------------------------------------------------------------------
# kategorien.yaml
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Kategorie:
    name: str
    behandlung: str
    anschaffung_zum_tageskurs: bool = False
    beschreibung: str = ""
    anzeigename: str = ""  # im Report statt des technischen Schlüssels


@dataclass(frozen=True)
class ExportZuordnung:
    boerse: str
    spalte_art: str
    arten: dict[str, str]
    anschaffungskosten: str = ""
    hinweis: str = ""


@dataclass(frozen=True)
class ManuelleZuordnung:
    tnr: str  # Schlüssel: Transaktions-Hash (bevorzugt, stabil) oder T-Nummer (ältere Einträge)
    kategorie: str
    datum: date
    erlaeuterung: str = ""

    @property
    def per_hash(self) -> bool:
        return split_key(self.tnr) is not None

    @property
    def txid(self) -> str:
        k = split_key(self.tnr)
        return k[0] if k else ""

    @property
    def vout(self) -> int | None:
        k = split_key(self.tnr)
        return k[1] if k else None

    def resolve(self, refs: Iterable[Any]) -> Any:
        """Eintrag im Transaktionsverzeichnis (TxRef) — per Hash oder T-Nummer; None = fehlt."""
        return next((r for r in refs if (r.txid == self.txid if self.per_hash else r.ref == self.tnr)), None)


@dataclass(frozen=True)
class Quittung:
    """Geprüfter nicht unterstützter Vorgang (local/kategorien.yaml, geprueft_nicht_unterstuetzt):
    bleibt unter den offenen Punkten, erzeugt aber nur eine Warnung statt eines Fehlers."""

    datum: date
    erlaeuterung: str
    tnr: str = ""  # Wallet-Vorgang per txid bzw. txid:vout (ältere Einträge: T-Nummer) …
    boerse: str = ""  # … oder Export-Zeile: Börse + Operation-ID bzw. Art
    ref: str = ""
    art: str = ""

    def matches(self, u: dict[str, Any]) -> bool:
        if str(u.get("day") or "") != self.datum.isoformat():
            return False
        k = split_key(self.tnr)
        if k is not None:
            return u.get("txid") == k[0] and (k[1] is None or u.get("vout") in (None, k[1]))
        if self.tnr:
            return u.get("tnr") == self.tnr
        if str(u.get("exchange") or "").strip().lower() != self.boerse:
            return False
        return u.get("ref") == self.ref if self.ref else u.get("art") == self.art

    @property
    def label(self) -> str:
        what = short_key(self.tnr) if self.tnr else f"{self.boerse} " + (f"ID {self.ref}" if self.ref else f"„{self.art}“")
        return f"{what} am {self.datum.strftime('%d.%m.%Y')}"


@dataclass
class Kategorien:
    kategorien: dict[str, Kategorie]
    export: dict[str, ExportZuordnung] = field(default_factory=dict)
    manuell: dict[str, ManuelleZuordnung] = field(default_factory=dict)
    quittungen: list[Quittung] = field(default_factory=list)
    # (Datei, sha256) — Repo-Datei und ggf. local/kategorien.yaml (Prüfprotokoll)
    hashes: list[tuple[str, str]] = field(default_factory=list)

    def export_kategorie(self, boerse: str, art: str) -> str | None:
        """Kategorie bzw. technische Art einer Export-Zeile; None = nicht zugeordnet
        (→ nicht unterstützt)."""
        z = self.export.get(boerse.strip().lower())
        return z.arten.get(art.strip()) if z is not None else None

    @property
    def unterstuetzt(self) -> list[Kategorie]:
        return [k for k in self.kategorien.values() if k.behandlung != "nicht_unterstuetzt"]

    @property
    def nicht_unterstuetzt(self) -> list[Kategorie]:
        return [k for k in self.kategorien.values() if k.behandlung == "nicht_unterstuetzt"]

    def geltungsbereich(self, offene_punkte: str = "Abschnitt 8.3") -> str:
        """Text „Geltungsbereich“ für die Methodik — aus den Anzeigenamen der Kategorien in
        kategorien.yaml (keine technischen Schlüssel im Report)."""

        def names(items: list[str]) -> str:
            return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " und " + items[-1]

        kauf = [k.anzeigename for k in self.kategorien.values() if k.behandlung == "anschaffung"]
        e22 = [k.anzeigename for k in self.kategorien.values() if k.behandlung == "einkunft_22_3"]
        parts = ["Bitcoin on-chain im Privatvermögen"]
        if kauf:
            parts.append("Anschaffung durch " + names(kauf))
        parts += ["Veräußerung durch Verkauf oder Abfluss an Dritte",
                  "Umbuchungen zwischen eigenen Wallets und auf eigene Börsenkonten"]
        if e22:
            parts.append("Zuflüsse als " + names(e22) + " (Ausweis nach § 22 Nr. 3 EStG)")
        text = "Geltungsbereich — unterstützt: " + "; ".join(parts) + "."
        if self.nicht_unterstuetzt:
            text += " Nicht unterstützt: " + ", ".join(k.anzeigename for k in self.nicht_unterstuetzt)
            text += " sowie jede Zeilenart eines Börsen-Exports, die keiner Kategorie zugeordnet ist."
        return text + (
            " Solche Vorgänge lagen im Berichtszeitraum nicht vor bzw. sind unter „Offene Punkte“ "
            f"({offene_punkte}) als nicht unterstützt ausgewiesen."
        )


def _parse_categories(d: Any, datei: str) -> dict[str, Kategorie]:
    out: dict[str, Kategorie] = {}
    for name, v in _get(d, "kategorien", dict, datei).items():
        where = f"{datei}: kategorien.{name}"
        if not isinstance(name, str) or not _ID_RE.fullmatch(name):
            raise RegelwerkFehler(f"{where}: Name nur a–z, 0–9 und _")
        if not isinstance(v, dict):
            raise RegelwerkFehler(f"{where}: erwartet behandlung")
        beh = _get(v, "behandlung", str, where)
        if beh not in BEHANDLUNGEN:
            raise RegelwerkFehler(f"{where}: behandlung „{beh}“ unbekannt (erlaubt: {', '.join(BEHANDLUNGEN)})")
        tk = _get(v, "anschaffung_zum_tageskurs", bool, where, required=False)
        if tk and beh != "einkunft_22_3":
            raise RegelwerkFehler(f"{where}: anschaffung_zum_tageskurs nur bei behandlung einkunft_22_3")
        out[name] = Kategorie(name, beh, bool(tk), _get(v, "beschreibung", str, where, required=False) or "",
                              _get(v, "anzeigename", str, where))
    return out


def _parse_export(d: Any, datei: str, known: dict[str, Kategorie]) -> dict[str, ExportZuordnung]:
    out: dict[str, ExportZuordnung] = {}
    for boerse, v in (_get(d, "zuordnung_export", dict, datei, required=False) or {}).items():
        where = f"{datei}: zuordnung_export.{boerse}"
        if not isinstance(v, dict):
            raise RegelwerkFehler(f"{where}: erwartet spalte_art und arten")
        arten: dict[str, str] = {}
        for art, kat in _get(v, "arten", dict, where).items():
            if not isinstance(kat, str) or (kat not in known and kat not in TECHNISCHE_ARTEN):
                raise RegelwerkFehler(f"{where}: Art „{art}“ → Kategorie „{kat}“ ist in kategorien nicht definiert")
            key = str(art).strip()
            if key in arten:
                raise RegelwerkFehler(f"{where}: Art „{key}“ doppelt")
            arten[key] = kat
        name = str(boerse).strip().lower()
        if name in out:
            raise RegelwerkFehler(f"{where}: Börse doppelt")
        out[name] = ExportZuordnung(
            boerse=name,
            spalte_art=_get(v, "spalte_art", str, where),
            arten=arten,
            anschaffungskosten=_get(v, "anschaffungskosten", str, where, required=False) or "",
            hinweis=_get(v, "hinweis", str, where, required=False) or "",
        )
    return out


def _parse_manual(d: Any, datei: str, known: dict[str, Kategorie]) -> dict[str, ManuelleZuordnung]:
    out: dict[str, ManuelleZuordnung] = {}
    for tnr, v in (_get(d, "zuordnung_manuell", dict, datei, required=False) or {}).items():
        where = f"{datei}: zuordnung_manuell.{short_key(str(tnr))}"
        if not isinstance(tnr, str) or not (_TXID_RE.fullmatch(tnr) or _TNR_RE.fullmatch(tnr)):
            raise RegelwerkFehler(f"{where}: erwartet einen Transaktions-Hash (64 Hex-Zeichen, ggf. txid:vout) "
                                  "oder eine T-Nummer wie „T-012“ (ältere Einträge)")
        if not isinstance(v, dict):
            raise RegelwerkFehler(f"{where}: erwartet kategorie und datum")
        kat = _get(v, "kategorie", str, where)
        if kat not in known:
            raise RegelwerkFehler(f"{where}: Kategorie „{kat}“ ist in kategorien nicht definiert")
        out[tnr] = ManuelleZuordnung(tnr, kat, _get(v, "datum", date, where),
                                     _get(v, "erlaeuterung", str, where, required=False) or "")
    return out


def _parse_acks(d: Any, datei: str) -> list[Quittung]:
    out: list[Quittung] = []
    for i, v in enumerate(_get(d, "geprueft_nicht_unterstuetzt", list, datei, required=False) or []):
        where = f"{datei}: geprueft_nicht_unterstuetzt[{i}]"
        if not isinstance(v, dict):
            raise RegelwerkFehler(f"{where}: erwartet datum, erlaeuterung und tnr oder boerse")
        tnr = _get(v, "txid", str, where, required=False) or _get(v, "tnr", str, where, required=False) or ""
        if tnr and split_key(tnr) is None and "txid" in v:
            raise RegelwerkFehler(f"{where}: txid — erwartet einen Transaktions-Hash (64 Hex-Zeichen, ggf. txid:vout)")
        boerse = (_get(v, "boerse", str, where, required=False) or "").strip().lower()
        ref = str(v.get("id") or "").strip()
        art = _get(v, "art", str, where, required=False) or ""
        if tnr and split_key(tnr) is None and not _TNR_RE.fullmatch(tnr):
            raise RegelwerkFehler(f"{where}: tnr „{tnr}“ — erwartet eine T-Nummer wie „T-012“")
        if not tnr and not (boerse and (ref or art)):
            raise RegelwerkFehler(f"{where}: txid (Wallet-Vorgang) oder boerse mit id (Operation-ID) bzw. art angeben")
        erl = _get(v, "erlaeuterung", str, where).strip()
        if not erl:
            raise RegelwerkFehler(f"{where}: erlaeuterung darf nicht leer sein")
        out.append(Quittung(_get(v, "datum", date, where), erl, tnr, boerse, ref, art))
    return out


def parse_categories(text: str, datei: str = CATEGORIES_FILE, local_text: str | None = None,
                     local_datei: str = "local/kategorien.yaml") -> Kategorien:
    """kategorien.yaml (+ optional local/kategorien.yaml mit weiteren Zuordnungen) → Kategorien.
    Die lokale Datei darf ``zuordnung_export`` und ``zuordnung_manuell`` enthalten, aber keine
    eigenen ``kategorien`` (Behandlung bleibt zentral)."""
    d = _load_yaml(text, datei)
    if not isinstance(d, dict):
        raise RegelwerkFehler(f"{datei}: erwartet eine Zuordnung (Schlüssel: Wert)")
    known = _parse_categories(d, datei)
    if d.get("geprueft_nicht_unterstuetzt"):
        raise RegelwerkFehler(f"{datei}: geprueft_nicht_unterstuetzt nur in local/kategorien.yaml (persönliche Daten)")
    kat = Kategorien(
        kategorien=known,
        export=_parse_export(d, datei, known),
        manuell=_parse_manual(d, datei, known),
        hashes=[(datei, hashlib.sha256(text.encode("utf-8")).hexdigest())],
    )
    if local_text is not None:
        ld = _load_yaml(local_text, local_datei) or {}
        if not isinstance(ld, dict):
            raise RegelwerkFehler(f"{local_datei}: erwartet eine Zuordnung (Schlüssel: Wert)")
        if "kategorien" in ld:
            raise RegelwerkFehler(f"{local_datei}: „kategorien“ nur in {datei} (Behandlung zentral festgelegt)")
        for boerse, z in _parse_export(ld, local_datei, known).items():
            base = kat.export.get(boerse)
            if base is None:
                kat.export[boerse] = z
                continue
            dup = sorted(set(base.arten) & set(z.arten))
            if dup:
                raise RegelwerkFehler(f"{local_datei}: Art „{dup[0]}“ für {boerse} steht schon in {datei}")
            kat.export[boerse] = ExportZuordnung(boerse, base.spalte_art, {**base.arten, **z.arten},
                                                 base.anschaffungskosten, base.hinweis)
        for tnr, m in _parse_manual(ld, local_datei, known).items():
            if tnr in kat.manuell:
                raise RegelwerkFehler(f"{local_datei}: {tnr} steht schon in {datei}")
            kat.manuell[tnr] = m
        kat.quittungen = _parse_acks(ld, local_datei)
        kat.hashes.append((local_datei, hashlib.sha256(local_text.encode("utf-8")).hexdigest()))
    return kat


# Zuordnungen aus der Oberfläche für die laufende Sitzung — nur im Arbeitsspeicher. Gesetzt,
# gilt dieser Text statt local/kategorien.yaml; Speichern in local/ ist optional.
SESSION_DATEI = "local/kategorien.yaml (Sitzung, nicht gespeichert)"
_session_text: str | None = None


def set_session_categories(text: str | None) -> None:
    """Zuordnungen der Sitzung setzen (validiert) oder mit ``None`` verwerfen."""
    global _session_text
    if text is not None:
        parse_categories((rules_root() / CATEGORIES_FILE).read_text(encoding="utf-8"), local_text=text,
                         local_datei=SESSION_DATEI)
    _session_text = text


def session_categories() -> str | None:
    return _session_text


def load_categories(root: Path | None = None, local_dir: Path | None = None) -> Kategorien:
    """``btc-regeln/kategorien.yaml`` + optional ``local/kategorien.yaml`` — bzw. die
    Zuordnungen der Sitzung, wenn in der Oberfläche geändert und nicht gespeichert."""
    path = (root or rules_root()) / CATEGORIES_FILE
    if not path.is_file():
        raise RegelwerkFehler(f"{ROOT_DIR}/{CATEGORIES_FILE} fehlt")
    if local_dir is None:
        from btc_origin.local_files import resolve_local_dir

        local_dir = resolve_local_dir()
    if _session_text is not None and root is None:
        return parse_categories(path.read_text(encoding="utf-8"), CATEGORIES_FILE, _session_text, SESSION_DATEI)
    local = local_dir / CATEGORIES_FILE
    local_text = local.read_text(encoding="utf-8") if local.is_file() else None
    return parse_categories(path.read_text(encoding="utf-8"), CATEGORIES_FILE, local_text)


def check_manual(kat: Kategorien, refs: Iterable[Any], until: date | None = None,
                 outputs: dict[str, list[tuple[int, int]]] | None = None) -> None:
    """Jeder Eintrag in zuordnung_manuell (txid, txid:vout oder ältere T-Nummer) existiert, ist ein
    Zufluss und das Datum passt; txid allein nur bei genau einem Zufluss (``outputs``: txid →
    [(vout, sats)] der Zuflüsse in die betrachteten Wallets). ``refs``: Transaktionsverzeichnis
    (TxRef mit ref, txid, day, art). Einträge nach ``until`` werden übersprungen."""
    refs = list(refs)
    for m in kat.manuell.values():
        if until is not None and m.datum > until:
            continue
        r = m.resolve(refs)
        where = f"zuordnung_manuell.{short_key(m.tnr)}"
        if r is None:
            raise RegelwerkFehler(f"{where}: {'Transaktion' if m.per_hash else 'T-Nummer'} nicht im "
                                  "Transaktionsverzeichnis")
        check_outputs(m.tnr, where, outputs)
        if r.day != m.datum.isoformat():
            day = f"{r.day[8:10]}.{r.day[5:7]}.{r.day[:4]}" if len(r.day) == 10 else "unbestätigt"
            raise RegelwerkFehler(
                f"{where}: Datum {m.datum.strftime('%d.%m.%Y')} passt nicht zur Transaktion "
                f"(Blockzeit {day})" + ("" if m.per_hash else
                " — T-Nummern haben sich vermutlich verschoben (neue ältere Transaktionen); Eintrag "
                "anpassen oder in der Oberfläche neu übernehmen (dann per Transaktions-Hash)")
            )
        if "Zufluss" not in r.art:
            raise RegelwerkFehler(f"{where}: {r.ref} ist kein Zufluss ({r.art})")


def validate_all(root: Path | None = None, local_dir: Path | None = None) -> list[str]:
    """Alle Regeldateien und kategorien.yaml laden und prüfen (Start der App). Rückgabe:
    „Regelwerk VZ …, Stand …“ je Datei; jeder Fehler → ``RegelwerkFehler``."""
    base = root or rules_root()
    years_dir = base / YEARS_DIR
    if not years_dir.is_dir():
        raise RegelwerkFehler(f"Ordner {ROOT_DIR}/{YEARS_DIR} fehlt ({years_dir})")
    out = []
    for path in sorted(years_dir.glob("*.yaml")):
        if not re.fullmatch(r"\d{4}", path.stem):
            raise RegelwerkFehler(f"{YEARS_DIR}/{path.name}: Dateiname muss das Jahr sein (JJJJ.yaml)")
        out.append(rules_for(int(path.stem), base).vermerk)
    load_categories(base, local_dir)
    return out
