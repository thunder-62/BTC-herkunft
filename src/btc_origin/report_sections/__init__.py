"""Abschnitte der „Herkunftsanalyse Bitcoin“ — ein Modul je Teil des Berichts.

``origin_report._render_pdf_once`` baut das Dokument und den Kontext ``c`` (Dokument,
Hilfen wie ``table``/``para``, Bericht ``c.rep``) und ruft die Abschnitte der Reihe nach
auf. Werte, die spätere Abschnitte brauchen, legt ein Abschnitt mit ``_keep`` in ``c`` ab.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def _keep(c: SimpleNamespace, loc: dict[str, Any], names: tuple[str, ...]) -> None:
    """Werte für spätere Abschnitte im Kontext ablegen (nur gebundene Namen)."""
    for n in names:
        if n in loc:
            setattr(c, n, loc[n])
