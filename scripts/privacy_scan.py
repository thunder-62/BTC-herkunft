"""Datenschutz-Prüfung über Commits: dieselben Regeln wie tests/test_privacy_guard.py, aber für
jede Dateiversion und jede Commit-Nachricht in einem Bereich — nicht nur für den Endstand.

    python scripts/privacy_scan.py origin/main..HEAD   # alle Commits eines PR (CI)
    python scripts/privacy_scan.py --all               # gesamte Historie (vor „öffentlich synchronisieren“)

Liegen echte Daten in ``local/``, wird zusätzlich gegen genau diese Werte geprüft. Ausgabe nennt
Fundstellen nur gekürzt; Rückgabewert 1 bei Funden."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_privacy_guard as guard  # noqa: E402


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


HISTORY_OK = ROOT / "tests" / "privacy_history_ok.txt"


def _history_ok() -> set[str]:
    """Geprüfte Altwerte (nur Beträge/Zeitstempel) — siehe tests/privacy_history_ok.txt."""
    if not HISTORY_OK.is_file():
        return set()
    return {v for line in HISTORY_OK.read_text(encoding="utf-8").splitlines()
            if (v := line.split("#", 1)[0].strip())}


def _benign(finding: str, ok: set[str]) -> bool:
    kind_value = finding.rsplit(": ", 1)[-1]
    return kind_value.startswith(("krummer Betrag ", "Zeitstempel mit Sekunden ")) and kind_value.split()[-1] in ok


def scan(rev_args: list[str]) -> list[str]:
    allowed = guard._allowed()
    ok = _history_ok()
    real = guard.real_values()
    real_re = None
    if real:
        import re

        real_re = re.compile("|".join(re.escape(v) for v in sorted(real, key=len, reverse=True)))
    found: list[str] = []
    seen: set[str] = set()
    for line in _git("rev-list", "--objects", *rev_args).splitlines():
        sha, _, path = line.partition(" ")
        if not path or sha in seen:
            continue
        seen.add(sha)
        p = ROOT / path
        if path.startswith(guard.SKIP_PREFIXES) or p.name in guard.SKIP_NAMES:
            continue
        if p.suffix.lower() not in guard.TEXT_SUFFIXES or path.startswith("local/") and p.name != "README.md":
            continue
        blob = subprocess.run(["git", "cat-file", "-p", sha], cwd=ROOT, capture_output=True).stdout
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found += [f"{sha[:8]} {f}" for f in guard._findings(p, text, allowed) if not _benign(f, ok)]
        if real_re is not None:
            for n, ln in enumerate(text.splitlines(), 1):
                for m in {*real_re.findall(ln), *real_re.findall(ln.lower())}:
                    found.append(f"{sha[:8]} {path}:{n}: echter Wert aus local/ ({m[:4]}…)")
    for rec in _git("log", "--format=%H%x00%B%x01", *rev_args).split("\x01"):
        commit, _, body = rec.strip().partition("\x00")
        if not commit:
            continue
        found += [f"Commit {commit[:8]} Nachricht: {f.split(': ', 1)[1]}"
                  for f in guard._findings(ROOT / "COMMIT_MSG.md", body, allowed) if not _benign(f, ok)]
        if real_re is not None and (real_re.search(body) or real_re.search(body.lower())):
            found.append(f"Commit {commit[:8]} Nachricht: echter Wert aus local/")
    return found


def main(argv: list[str]) -> int:
    rev_args = ["--all"] if not argv or argv == ["--all"] else argv
    found = scan(rev_args)
    for f in found:
        print(f)
    print(f"{len(found)} Fund(e) in {' '.join(rev_args)}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
