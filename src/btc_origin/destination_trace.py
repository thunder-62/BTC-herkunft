"""Verbleib einer Zieladresse aus einem Börsen-Export (Matching-Diagnose, Abschnitt 3).

Zwei Prüfungen für eine Zieladresse, die zu keiner betrachteten Wallet gehört:

* **Tiefe Ableitung:** Liegt die Adresse in einer der xpubs weiter hinten, als der
  Sync abgeleitet hat (Gap-Limit)?
* **Blockchain:** Kam dort etwas an, und wohin ging es danach — in eine eigene
  Wallet, an eine Adresse aus einem öffentlichen Label-Pack, oder liegt es noch dort?

Nur Lesezugriffe; die Ergebnistexte enthalten keine Adressen und keine Beträge
(Abweichungen nur in Tagen und Prozent).
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Iterable

from btc_origin.hd_deriver import HdDeriver
from btc_origin.trail import ChainProvider, TxInfo, parse_tx

DEEP_MAX_INDEX = 1000  # Adressen je Kette (Empfang/Wechselgeld) bei der tiefen Suche
MAX_HOPS = 3
MAX_TXS = 25  # jüngste Transaktionen je Adresse
BUSY_TXS = 100  # ab so vielen Transaktionen: stark genutzte Adresse (Börse/Dienst)


def script_type_of(address: str) -> str | None:
    a = address.lower()
    if a.startswith(("bc1q", "tb1q")):
        return "p2wpkh"
    if a.startswith(("bc1p", "tb1p")):
        return "p2tr"
    if address.startswith(("3", "2")):
        return "p2sh-p2wpkh"
    if address.startswith(("1", "m", "n")):
        return "p2pkh"
    return None


def deep_owner(
    address: str,
    wallets: Iterable[tuple[str, str]],
    deriver: HdDeriver | None = None,
    max_index: int = DEEP_MAX_INDEX,
) -> tuple[str, str] | None:
    """(Wallet-Name, Pfad) der xpub, zu der die Adresse gehört — oder None."""
    stype = script_type_of(address)
    if stype is None:
        return None
    d = deriver or HdDeriver()
    for name, xpub in wallets:
        try:
            if stype not in d.candidate_script_types(xpub):
                continue
            for derive in (d.derive_receive, d.derive_change):
                for a in derive(xpub, count=max_index + 1, script_type=stype):
                    if a.address == address:
                        return name, a.derivation_path
        except Exception:  # noqa: BLE001 — ungültige/fremde Schlüssel überspringen
            continue
    return None


def _day(provider: ChainProvider, height: int | None) -> date | None:
    t = provider.block_time(height)
    try:
        return date.fromisoformat(str(t)[:10]) if t else None
    except ValueError:
        return None


def _txs(provider: ChainProvider, address: str) -> tuple[list[tuple[TxInfo, int | None]], int]:
    hist = provider.history(address)
    return [(parse_tx(t, provider.tx_hex(t)), h) for t, h in hist[-MAX_TXS:]], len(hist)


def chain_fate(
    provider: ChainProvider,
    address: str,
    day: date,
    sats: int,
    own_name_of: dict[str, str],
    label_of: Callable[[str], str | None],
    max_hops: int = MAX_HOPS,
    ref: str = "zur Menge laut Export",
) -> str:
    """Was geschah mit den Bitcoin an ``address`` (Export: ``sats`` am ``day``)?"""
    txs, n_hist = _txs(provider, address)
    if not txs:
        return "Blockchain: Adresse nie benutzt — dort ist nichts angekommen"
    receipts = [
        (tx, i, v, h) for tx, h in txs for i, (a, v) in enumerate(tx.vout) if a == address
    ]
    if not receipts:
        return "Blockchain: kein Eingang auf dieser Adresse gefunden"

    def gap(r: tuple[TxInfo, int, int, int | None]) -> int:
        d = _day(provider, r[3])
        return abs((d - day).days) if d else 10**6

    tx, idx, value, height = min(receipts, key=lambda r: (gap(r), abs(r[2] - sats)))
    d0 = _day(provider, height)
    parts = [
        "Blockchain: Eingang "
        + (f"am {d0:%d.%m.%Y} ({(d0 - day).days:+d} Tage, " if d0 else "(unbestätigt, ")
        + f"{100 * (value - sats) / sats:+.2f} % {ref})"
    ]
    if len(receipts) > 1:
        # nur die Tage (keine Beträge): passen sie zu anderen eigenen Vorgängen?
        days = sorted({d for r in receipts if (d := _day(provider, r[3])) is not None})
        parts.append(
            f"{len(receipts)} Eingänge insgesamt auf dieser Adresse"
            + (f" (Tage: {', '.join(f'{d:%d.%m.%Y}' for d in days)})" if days else "")
            + (" — mehrfach genutzte Adresse, typisch für eine Einzahlungsadresse bei Börse/Dienst"
               if len(receipts) >= 3 else "")
        )

    cur_addr, cur = address, (tx.txid, idx)
    cur_txs, cur_n = txs, n_hist
    for hop in range(1, max_hops + 1):
        if cur_n >= BUSY_TXS:
            parts.append(f"Schritt {hop}: stark genutzte Adresse (vermutlich Börse oder Zahlungsdienst)")
            break
        spend = next(((t, h) for t, h in cur_txs if cur in t.vin), None)
        if spend is None:
            parts.append(
                "liegt noch dort (nicht weitergeleitet)" if hop == 1
                else f"liegt nach {hop - 1} Schritt{'en' if hop > 2 else ''} unbewegt auf einer fremden Adresse"
            )
            break
        st, sh = spend
        sd = _day(provider, sh)
        when = f"am {sd:%d.%m.%Y}" if sd else "(unbestätigt)"
        more = len(st.vin) - 1
        extra = f", zusammen mit {more} weiteren Eingängen" if more else ""
        outs = [(a, v, i) for i, (a, v) in enumerate(st.vout) if a and a != cur_addr]
        own = [(a, v, i) for a, v, i in outs if a in own_name_of]
        if own:
            a, _v, _i = max(own, key=lambda o: o[1])
            parts.append(f"Schritt {hop}: {when} weitergeleitet an eigene Wallet „{own_name_of[a]}“{extra}")
            break
        labelled = [(a, v, i, label_of(a)) for a, v, i in outs]
        labelled = [x for x in labelled if x[3]]
        if labelled:
            parts.append(f"Schritt {hop}: {when} weitergeleitet an {labelled[0][3]} (Label-Pack){extra}")
            break
        if not outs:
            parts.append(f"Schritt {hop}: {when} ausgegeben (kein auswertbarer Ausgang)")
            break
        a, _v, i = max(outs, key=lambda o: o[1])
        parts.append(
            f"Schritt {hop}: {when} weitergeleitet an eine fremde Adresse"
            + (f" ({len(outs)} Ausgänge)" if len(outs) > 1 else "") + extra
        )
        cur_addr, cur = a, (st.txid, i)
        cur_txs, cur_n = _txs(provider, a)
    else:
        parts.append(f"Spur nach {max_hops} Schritten beendet")
    return "; ".join(parts)
