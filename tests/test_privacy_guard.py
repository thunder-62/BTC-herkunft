"""Datenschutz-Wächter: keine echten Wallet-Daten im Repository.

Durchsucht alle Dateien, die Git kennt oder committen würde (nicht ignorierte),
und schlägt fehl bei
- gültigen Bitcoin-Adressen, die weder in `tests/privacy_allowlist.txt` noch in
  den öffentlichen Label-Packs (`data/label_packs/`) stehen,
- gültigen xpub/ypub/zpub/tpub … außer denen in der Erlaubt-Liste,
- 64-stelligen Hex-Werten (Transaktions-IDs) außer der Erlaubt-Liste,
- „krummen“ Beträgen (mehr als 4 signifikante Stellen) — erfundene Werte sind rund
  oder offensichtliche Muster (0.12345678),
- Uhrzeiten mit Sekunden ungleich :00 (echte Export-Zeitstempel),
- persönlichen Namen (nur als Hash hinterlegt).

Eine Zeile mit dem Vermerk `privacy: ok` wird übersprungen — nur für Werte, die
nachweislich öffentlich oder erfunden sind (z. B. Gesetzes- oder Kursgrenzen).

Zusätzlich:
- Diese Datei prüft sich selbst; Negativbeispiele entstehen erst zur Laufzeit.
- Jeder Eintrag der Erlaubt-Liste muss nachrechenbar sein (BIP-Testvektoren aus der
  öffentlichen Mnemonic „abandon … about“, synthetische Adressen aus
  sha256("btc-origin synthetic {i}"), Muster-Platzhalter).
- Liegen echte Daten in `local/` (nur auf dem Rechner des Nutzers, nicht im Git), wird
  das Repository gegen genau diese Werte geprüft — auch „runde“ Werte, ohne Ausnahmen.
- `scripts/privacy_scan.py` wendet dieselben Regeln auf jeden Commit an (CI, vor jeder
  öffentlichen Synchronisierung: `--all`).
Regeln für Testdaten: siehe CLAUDE.md.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "tests" / "privacy_allowlist.txt"
LABEL_PACKS = ROOT / "data" / "label_packs"
MARK = "privacy: ok"
# Regelwerk und Doku: Transaktions-Hashes nie — auch nicht per Erlaubt-Liste oder „privacy: ok“
# (Hashes für zuordnung_manuell/Quittungen nur in local/kategorien.yaml, REGELWERK.md 4.3/4.4).
STRICT_HEX_PREFIXES = ("btc-regeln/", "docs/")

# Öffentliche Label-Packs und generierte Dateien werden nicht durchsucht.
SKIP_PREFIXES = ("data/label_packs/",)
SKIP_NAMES = {"package-lock.json"}  # diese Datei prüft sich selbst mit
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".csv", ".json", ".toml", ".cfg", ".ini", ".yml", ".yaml",
    ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".bat", ".ps1", ".sh", ".example", "",
}

# sha256("btc-origin:" + wort)[:16] — Klarnamen stehen bewusst nicht im Repo.
NAME_HASHES = {"bede9fd256a9a753", "c82e00545ce99fba", "201ccf8704559e5e"}

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
RE_BECH32 = re.compile(r"\b(?:bc|tb)1[02-9ac-hj-np-z]{11,71}\b", re.IGNORECASE)
RE_BASE58_ADDR = re.compile(rf"\b[13mn2][{_B58}]{{25,34}}\b")
RE_XKEY = re.compile(rf"\b[xyztuvYZUV]p(?:ub|rv)[{_B58}]{{100,112}}\b")
RE_HEX64 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
RE_DECIMAL = re.compile(r"(?<![\w.,])\d{1,7}[.,]\d{5,}(?![\d])")
RE_GROUPED = re.compile(r"(?<![\w.])\d{1,3}(?:_\d{3})+(?![\w])")
RE_TIME = re.compile(r"(?<![\d:])([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?![\d:])")
RE_WORD = re.compile(r"[a-zäöüß0-9]+")


def _files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git nicht verfügbar")
    files = []
    for rel in out:
        p = ROOT / rel
        if rel.startswith(SKIP_PREFIXES) or p.name in SKIP_NAMES or not p.is_file():
            continue
        if p.suffix.lower() in TEXT_SUFFIXES:
            files.append(p)
    return files


def _allowed() -> set[str]:
    allowed = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            allowed.add(line)
    for pack in LABEL_PACKS.glob("*.json"):
        allowed.update(re.findall(r"[A-Za-z0-9]{25,90}", pack.read_text(encoding="utf-8")))
    return allowed


def _b58check(s: str) -> bytes | None:
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\0" * (len(s) - len(s.lstrip("1"))) + raw
    if len(raw) < 5:
        return None
    body, check = raw[:-4], raw[-4:]
    ok = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] == check
    return body if ok else None


def _bech32_ok(s: str) -> bool:
    s = s.lower()
    hrp, _, data = s.rpartition("1")
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    values = [charset.index(c) for c in data]
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk in (1, 0x2BC830A3)  # bech32 / bech32m


def _significant(digits: str) -> str:
    return digits.strip("0")


def _is_pattern(sig: str) -> bool:
    """Offensichtlich erfunden: ein Ziffernwert wiederholt oder fortlaufend (123456, 987654)."""
    if len(set(sig)) == 1:
        return True
    steps = {(int(b) - int(a)) % 10 for a, b in zip(sig, sig[1:])}
    return steps in ({1}, {9})


def _findings(path: Path, text: str, allowed: set[str]) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    strict = rel.startswith(STRICT_HEX_PREFIXES) or rel.endswith(".md")
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        where = f"{rel}:{n}"
        if strict:
            for m in RE_HEX64.finditer(line):
                if len(set(m.group().lower())) > 2:
                    found.append(f"{where}: 64-stelliger Hex-Wert (Transaktions-ID?) in Regelwerk/Doku {m.group()[:8]}…")
        if MARK in line:
            continue
        for m in RE_BECH32.finditer(line):
            if m.group() not in allowed and _bech32_ok(m.group()):
                found.append(f"{where}: Bitcoin-Adresse {m.group()[:8]}…")
        for m in RE_BASE58_ADDR.finditer(line):
            s = m.group()
            if s not in allowed and (body := _b58check(s)) is not None and len(body) == 21:
                found.append(f"{where}: Bitcoin-Adresse {s[:8]}…")
        for m in RE_XKEY.finditer(line):
            if m.group() not in allowed and _b58check(m.group()) is not None:
                found.append(f"{where}: Erweiterter Schlüssel {m.group()[:8]}…")
        for m in RE_HEX64.finditer(line):
            if not strict and m.group() not in allowed and len(set(m.group().lower())) > 2:
                found.append(f"{where}: 64-stelliger Hex-Wert (Transaktions-ID?) {m.group()[:8]}…")
        for m in RE_DECIMAL.finditer(line):
            whole, frac = re.split(r"[.,]", m.group())
            sig = _significant(whole.lstrip("0") + frac)
            if len(sig) > 4 and not _is_pattern(sig):
                found.append(f"{where}: krummer Betrag {m.group()}")
        for m in RE_GROUPED.finditer(line):
            sig = _significant(m.group().replace("_", ""))
            if len(sig) > 4 and not _is_pattern(sig):
                found.append(f"{where}: krummer Betrag {m.group()}")
        for m in RE_TIME.finditer(line):
            if m.group(3) != "00":
                found.append(f"{where}: Zeitstempel mit Sekunden {m.group()}")
        for w in RE_WORD.findall(line.lower()):
            if hashlib.sha256(f"btc-origin:{w}".encode()).hexdigest()[:16] in NAME_HASHES:
                found.append(f"{where}: persönlicher Name")
    return found


def test_no_personal_wallet_data_in_repo() -> None:
    allowed = _allowed()
    found: list[str] = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += _findings(path, text, allowed)
    assert not found, (
        "Mögliche echte Daten im Repository (Regeln: CLAUDE.md; erfundene/öffentliche Werte in "
        "tests/privacy_allowlist.txt eintragen oder Zeile mit „privacy: ok“ markieren):\n  "
        + "\n  ".join(found)
    )


def test_guard_rejects_hashes_in_rules_and_docs_even_if_marked() -> None:
    tx = hashlib.sha256(b"btc-origin synthetic tx").hexdigest()
    line = f"  {tx}:1:   # privacy: ok\n"
    for rel in ("btc-regeln/kategorien.yaml", "docs/REGELN.md", "local/README.md"):
        assert _findings(ROOT / rel, line, allowed={tx}), rel
    assert not _findings(ROOT / "tests" / "_sample.py", line, allowed={tx})  # Tests: Markierung gilt
    assert not _findings(ROOT / "docs" / "x.md", "  " + "c" * 64 + "\n", allowed=set())  # Platzhalter


def _sample_bad_values() -> tuple[str, str, str]:
    """Negativbeispiele, erst zur Laufzeit gebildet (stehen so nicht in dieser Datei):
    krummer Betrag, krumme Satoshi-Zahl, Zeitstempel mit Sekunden — alle erfunden."""
    amount = "0." + "".join(str((3 * i + 7) % 10) for i in range(8))
    sats = "_".join(["5", "283", "917"])
    stamp = "2024-02-29 " + ":".join(["13", "37", "42"])
    return amount, sats, stamp


def test_guard_catches_real_looking_values(tmp_path: Path) -> None:
    amount, sats, stamp = _sample_bad_values()
    sample = (
        'addr = "3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"\n'
        f'wd = ("{amount}", {sats}, "{stamp}")\n'
        'ok = ("0.01000000", 250_000, 0.12345678, "10:05:00", "bc1qownaddress")\n'
    )
    p = ROOT / "tests" / "_sample.py"
    found = _findings(p, sample, allowed=set())
    kinds = sorted(f.split(": ", 1)[1].split(" ")[0] for f in found)
    assert kinds == ["Bitcoin-Adresse", "Zeitstempel", "krummer", "krummer"]
    assert all(":3:" not in f for f in found)
    assert _findings(p, sample, allowed={"3MJN645a8x3Fbq7MxPFoHe51djuCvijqtN"})[0].endswith(amount)


def test_guard_checks_itself() -> None:
    """Keine Selbstausnahme: diese Datei wird wie jede andere geprüft."""
    assert Path(__file__).resolve() in {p.resolve() for p in _files()}


# ---------------------------------------------------------------- Erlaubt-Liste nachrechnen

SYNTHETIC_COUNT = 64  # sha256("btc-origin synthetic {i}") für i < 64
_MNEMONIC = " ".join(["abandon"] * 11 + ["about"])  # öffentliche BIP-39-Test-Mnemonic


def _verifiable_values() -> set[str]:
    """Alles, was in der Erlaubt-Liste stehen darf: BIP-44/49/84-Testvektoren (Adressen der ersten
    Empfangs- und Wechseladressen, Konto-xpub/ypub/zpub) und synthetische Adressen."""
    from embit import base58, bech32, bip32, bip39
    from embit.networks import NETWORKS
    from embit.script import p2pkh, p2sh, p2wpkh

    net = NETWORKS["main"]
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(_MNEMONIC))
    out: set[str] = set()
    for purpose, script, version in ((44, p2pkh, "xpub"), (49, lambda k: p2sh(p2wpkh(k)), "ypub"),
                                     (84, p2wpkh, "zpub")):
        account = root.derive(f"m/{purpose}h/0h/0h")
        out.add(account.to_public().to_string(version=net[version]))
        for change in (0, 1):
            for i in range(5):
                out.add(script(account.derive(f"m/{change}/{i}").key).address(net))
    for i in range(SYNTHETIC_COUNT):
        h = hashlib.sha256(f"btc-origin synthetic {i}".encode()).digest()[:20]
        out.add(base58.encode_check(b"\x05" + h))
        out.add(bech32.encode("bc", 0, h))
    return out


def _is_pattern_placeholder(value: str) -> bool:
    """Offensichtlicher Platzhalter: höchstens zwei Zeichen oder ein wiederholter Block."""
    v = value.lower()
    return len(set(v)) <= 2 or any(len(v) % n == 0 and v == v[:n] * (len(v) // n) for n in (1, 2, 4, 8, 16, 32))


def test_allowlist_entries_are_verifiable() -> None:
    """Die Erlaubt-Liste kann keine echten Werte „freischalten“: jeder Eintrag wird nachgerechnet."""
    pytest.importorskip("embit")
    ok = _verifiable_values()
    bad = []
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if value and value not in ok and not _is_pattern_placeholder(value):
            bad.append(value[:8] + "…")
    assert not bad, ("Nicht nachrechenbare Einträge in tests/privacy_allowlist.txt (nur BIP-Testvektoren, "
                     "synthetische Adressen oder Muster-Platzhalter): " + ", ".join(bad))


# ---------------------------------------------------------------- Abgleich mit echten Daten (lokal)

RE_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
RE_AMOUNT = re.compile(r"(?<![\w.])\d{1,7}[.,]\d{2,8}(?![\d])")  # auch nach CSV-Komma


def _local_dir() -> Path:
    env = os.environ.get("BTC_ORIGIN_LOCAL_DIR", "").strip()
    return Path(env).expanduser() if env else ROOT / "local"


def real_values(local: Path | None = None) -> set[str]:
    """Echte Werte aus ``local/`` (nur im Arbeitsspeicher): Adressen, Transaktions-Hashes,
    Zeitstempel mit Sekunden und Beträge mit mehr als 4 signifikanten Stellen — jeweils auch
    in den Schreibweisen, in denen sie im Code stehen könnten (Komma, Satoshi mit „_“)."""
    base = local or _local_dir()
    out: set[str] = set()
    if not base.is_dir():
        return out
    for p in base.rglob("*"):
        if not p.is_file() or p.name == "README.md" or p.stat().st_size > 20_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.update(m.group() for m in RE_BECH32.finditer(text) if _bech32_ok(m.group()))
        out.update(m.group() for m in RE_BASE58_ADDR.finditer(text) if _b58check(m.group()) is not None)
        out.update(m.group().lower() for m in RE_HEX64.finditer(text) if not _is_pattern_placeholder(m.group()))
        for m in RE_DATETIME.finditer(text):
            if not m.group().endswith(":00"):
                out.update({m.group(), m.group().replace("T", " ")})
        for m in RE_AMOUNT.finditer(text):
            whole, frac = re.split(r"[.,]", m.group())
            if len(_significant(whole.lstrip("0") + frac)) <= 4:
                continue
            dot = f"{whole}.{frac}"
            out.update({dot, dot.replace(".", ",")})
            if len(frac) == 8:  # BTC → Satoshi
                sats = int(whole) * 100_000_000 + int(frac)
                out.update({str(sats), f"{sats:_}"})
    return out


def _real_value_hits(files: list[Path], values: set[str]) -> list[str]:
    if not values:
        return []
    pattern = re.compile("|".join(re.escape(v) for v in sorted(values, key=len, reverse=True)))
    hits = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            # Hashes auch in Großschreibung erkennen
            found = {m.group() for m in pattern.finditer(line)} | {m.group() for m in pattern.finditer(line.lower())}
            hits += [f"{path.relative_to(ROOT).as_posix()}:{n}: echter Wert aus local/ ({v[:4]}…)" for v in sorted(found)]
    return hits


def test_no_real_values_from_local_in_repo() -> None:
    """Nur auf dem Rechner des Nutzers wirksam (``local/`` mit echten Daten): keines der echten
    Merkmale darf irgendwo im Repository stehen — ohne Erlaubt-Liste und ohne „privacy: ok“."""
    values = real_values()
    if not values:
        pytest.skip("keine echten Daten in local/ (z. B. in der CI)")
    hits = _real_value_hits(_files(), values)
    assert not hits, "Echte Werte aus local/ im Repository:\n  " + "\n  ".join(hits)


def test_real_values_are_detected(tmp_path: Path) -> None:
    amount, _sats, stamp = _sample_bad_values()
    local = tmp_path / "local"
    (local / "boersen").mkdir(parents=True)
    tx = hashlib.sha256(b"btc-origin synthetic local").hexdigest()
    (local / "boersen" / "x.csv").write_text(f"{stamp},Buy,{amount}\n{tx}\n", encoding="utf-8")
    values = real_values(local)
    sats = int(amount.replace(".", ""))
    assert {amount, amount.replace(".", ","), stamp, tx, f"{sats:_}"} <= values
    probe = tmp_path / "probe.py"
    probe.write_text(f"X = {sats:_}  # privacy: ok\n", encoding="utf-8")
    global ROOT
    old, ROOT = ROOT, tmp_path
    try:
        assert _real_value_hits([probe], values)  # auch mit „privacy: ok“
    finally:
        ROOT = old
