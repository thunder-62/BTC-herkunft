"""Public BTC reference-price oracle (no API key).

Hard rules
----------
* Prices are **never** persisted to disk. An optional **in-memory session
  cache** (process lifetime) is allowed for Low-Cost / rate-limit friendliness.
* Historical per-inflow EUR/USD = **Anschaffungs-Referenz** (acquisition
  reference) — NOT Kostenbasis / Steuerwert, NOT advice.
* Spot at report generation time = optional **REFERENZWERT** (current
  market reference) — must NEVER be framed or used as Kauf-/Anschaffungspreis.
* Fail-open: if the public API is unreachable, callers get a clear
  „Kurs nicht ermittelbar“ marker and report export still proceeds.

Historical daily prices (one documented rule, BMF 06.03.2025 Rz. 91):
1. Binance BTC/EUR daily close (from 2020-01-03),
2. before that Binance BTC/USDT daily close ÷ ECB reference rate USD per EUR
   of the same day (last published rate for weekends/holidays; from 2017-08-17),
3. only before that mempool.space.
Per-day requests (mempool, then CoinGecko) remain the fallback when the bulk
sources are unreachable. ``DAY_SOURCES`` records which rule priced each day.
"""

from __future__ import annotations

import bisect
import csv
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Protocol

from btc_origin.config import get_settings

# Human-readable fail-open markers (UI / report copy — keep exact phrases).
SPOT_UNAVAILABLE = "Referenzkurs nicht verfügbar"
HISTORICAL_UNAVAILABLE = "Kurs nicht ermittelbar"
# Shown while prices are still being fetched in the background (UI only).
PRICE_LOADING = "Kurs wird geladen …"

SOURCE_MEMPOOL = "mempool.space (public, no API key)"
SOURCE_COINGECKO = "CoinGecko (public, no API key)"
SOURCE_CHAIN = "mempool.space / CoinGecko (public, no API key)"

_HTTP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "BTC-Herkunft/1.0 (local provenance; no API key)",
}


@dataclass(frozen=True)
class SpotReference:
    """Current market REFERENZWERT — never Kauf-/Anschaffungspreis."""

    label: str = "REFERENZWERT"
    usd: float | None = None
    eur: float | None = None
    timestamp_utc: str | None = None
    source: str | None = None
    available: bool = False
    message: str | None = None

    def as_report_fields(self) -> dict[str, str]:
        """Flat, clearly labeled fields for CSV/PDF — no Kaufpreis wording."""
        if not self.available:
            return {
                "referenzwert_label": self.label,
                "referenzwert_usd": SPOT_UNAVAILABLE,
                "referenzwert_eur": SPOT_UNAVAILABLE,
                "referenzwert_timestamp_utc": "",
                "referenzwert_source": self.source or "",
                "referenzwert_note": (
                    "REFERENZWERT (aktueller Markt) — kein Kaufpreis, "
                    "keine Anschaffungs-Referenz, keine Kostenbasis."
                ),
            }
        return {
            "referenzwert_label": self.label,
            "referenzwert_usd": f"{self.usd:.2f}" if self.usd is not None else SPOT_UNAVAILABLE,
            "referenzwert_eur": f"{self.eur:.2f}" if self.eur is not None else SPOT_UNAVAILABLE,
            "referenzwert_timestamp_utc": self.timestamp_utc or "",
            "referenzwert_source": self.source or "",
            "referenzwert_note": (
                "REFERENZWERT (aktueller Markt) — kein Kaufpreis, "
                "keine Anschaffungs-Referenz, keine Kostenbasis."
            ),
        }


@dataclass(frozen=True)
class AcquisitionReference:
    """Per-inflow historical EUR/USD = Anschaffungs-Referenz (not Kostenbasis)."""

    label: str = "Anschaffungs-Referenz"
    as_of: date | None = None
    usd: float | None = None
    eur: float | None = None
    source: str | None = None
    available: bool = False
    message: str | None = None

    def as_report_fields(self) -> dict[str, str]:
        if not self.available:
            return {
                "anschaffungs_referenz_label": self.label,
                "anschaffungs_referenz_date": self.as_of.isoformat() if self.as_of else "",
                "anschaffungs_referenz_usd": HISTORICAL_UNAVAILABLE,
                "anschaffungs_referenz_eur": HISTORICAL_UNAVAILABLE,
                "anschaffungs_referenz_source": self.source or "",
                "anschaffungs_referenz_note": (
                    "Anschaffungs-Referenz (historischer Marktpreis am Zufluss) — "
                    "keine Kostenbasis, keine Steuerberechnung, keine Beratung."
                ),
            }
        return {
            "anschaffungs_referenz_label": self.label,
            "anschaffungs_referenz_date": self.as_of.isoformat() if self.as_of else "",
            "anschaffungs_referenz_usd": (
                f"{self.usd:.2f}" if self.usd is not None else HISTORICAL_UNAVAILABLE
            ),
            "anschaffungs_referenz_eur": (
                f"{self.eur:.2f}" if self.eur is not None else HISTORICAL_UNAVAILABLE
            ),
            "anschaffungs_referenz_source": self.source or "",
            "anschaffungs_referenz_note": (
                "Anschaffungs-Referenz (historischer Marktpreis am Zufluss) — "
                "keine Kostenbasis, keine Steuerberechnung, keine Beratung."
            ),
        }


class SpotFetcher(Protocol):
    def __call__(self) -> tuple[float | None, float | None]:
        """Return (usd, eur); either may be None on partial failure."""


class HistoricalFetcher(Protocol):
    def __call__(self, on: date) -> tuple[float | None, float | None]:
        """Return (usd, eur) for calendar day ``on``."""


_HTTP_CLIENT: Any = None
_HTTP_LOCK = threading.Lock()


@contextmanager
def _http_client() -> Iterator[Any]:
    """Shared keep-alive HTTPS client (thread-safe) — avoids one TLS handshake
    per price request. RAM only; nothing is written to disk."""
    global _HTTP_CLIENT
    import httpx

    with _HTTP_LOCK:
        if _HTTP_CLIENT is None:
            _HTTP_CLIENT = httpx.Client(timeout=8.0, headers=_HTTP_HEADERS)
        client = _HTTP_CLIENT
    yield client


# Parallel per-day requests when prefetching (low: public APIs rate-limit).
PREFETCH_WORKERS = 3
# Use the one-shot full-history download when at least this many days miss.
BULK_MIN_DAYS = 3
# A bulk series point counts for a day if it is at most this far away.
BULK_MAX_DISTANCE_S = 2 * 86400
# Failed days are retried after this many seconds (never cached as final).
RETRY_FAILED_AFTER_S = 120.0

# Last error per price source (for the UI: why are prices missing?).
LAST_PRICE_ERRORS: dict[str, str] = {}


def _note_error(source: str, exc: Exception | str) -> None:
    msg = str(exc)
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        msg = f"HTTP {status}"
    LAST_PRICE_ERRORS[source] = msg[:200]


def _get_json_with_retry(url: str, *, source: str, attempts: int = 3) -> Any:
    """GET JSON; on HTTP 429 / 5xx wait and retry. Raises the last error."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with _http_client() as client:
                r = client.get(url)
                if r.status_code == 429 or r.status_code >= 500:
                    r.raise_for_status()
                r.raise_for_status()
                LAST_PRICE_ERRORS.pop(source, None)
                return r.json()
        except Exception as exc:  # noqa: BLE001 — recorded + retried
            last = exc
            _note_error(source, exc)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Only rate limits / server errors are worth waiting for.
            if status is None or (status != 429 and status < 500):
                break
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert last is not None
    raise last


def _series_from_prices(prices: Any) -> list[tuple[float, float | None, float | None]]:
    out: list[tuple[float, float | None, float | None]] = []
    for p in prices or []:
        if not isinstance(p, dict):
            continue
        try:
            t = float(p.get("time") or 0)
        except (TypeError, ValueError):
            continue
        usd, eur = _as_float(p.get("USD")), _as_float(p.get("EUR"))
        if t > 0 and (usd is not None or eur is not None):
            out.append((t, usd, eur))
    out.sort()
    return out


# Binance public market data (no key). data-api.binance.vision is Binance's
# market-data mirror without geo restrictions; api.binance.com as fallback.
BINANCE_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
BINANCE_SYMBOL_EUR = "BTCEUR"
BINANCE_SYMBOL_USDT = "BTCUSDT"
BINANCE_KLINES_LIMIT = 1000
BINANCE_EUR_SINCE = date(2020, 1, 3)
BINANCE_USDT_SINCE = date(2017, 8, 17)
FX_MAX_GAP_DAYS = 7  # weekends/holidays: last published ECB rate

PRICE_SOURCE_BINANCE_EUR = "Binance BTC/EUR-Tagesschlusskurs"
PRICE_SOURCE_BINANCE_USDT_ECB = (
    "Binance BTC/USDT-Tagesschlusskurs ÷ EZB-Referenzkurs USD/EUR desselben Tages"
)
PRICE_SOURCE_MEMPOOL = "mempool.space"
# day → rule that priced it (process RAM only; for the report's Kursquelle).
DAY_SOURCES: dict[date, str] = {}


def _binance_get(path: str) -> Any:
    last: Exception | None = None
    for host in BINANCE_HOSTS:
        try:
            return _get_json_with_retry(f"{host}{path}", source="Binance")
        except Exception as exc:  # noqa: BLE001 — try next host
            last = exc
    assert last is not None
    raise last


def _binance_daily(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
    """Daily closes (UTC day, close) from Binance klines, ≤1000 days per request."""
    out: list[tuple[date, float]] = []
    cursor = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    stop = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    while cursor < stop:
        start_ms = int(cursor.timestamp() * 1000)
        data = _binance_get(
            f"/api/v3/klines?symbol={symbol}&interval=1d"
            f"&startTime={start_ms}&limit={BINANCE_KLINES_LIMIT}"
        )
        if not isinstance(data, list) or not data:
            break
        for k in data:
            try:
                open_ms, close_px = int(k[0]), float(k[4])
            except (TypeError, ValueError, IndexError):
                continue
            out.append((datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).date(), close_px))
        last_open = int(data[-1][0]) / 1000
        nxt = datetime.fromtimestamp(last_open, tz=timezone.utc) + timedelta(days=1)
        if nxt <= cursor or len(data) < BINANCE_KLINES_LIMIT:
            break
        cursor = nxt
    return sorted(out)


def _noon(d: date) -> float:
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp()


def _binance_daily_eur(start: date, end: date) -> list[tuple[float, float | None, float | None]]:
    """BTC/EUR daily closes from Binance klines, ≤1000 days per request.

    Series point = (noon UTC of the day, None, close EUR). BTCEUR trades on
    Binance since 2020-01-03; earlier days are simply absent.
    """
    return [(_noon(d), None, px) for d, px in _binance_daily(BINANCE_SYMBOL_EUR, start, end)]


def _get_text(url: str, *, source: str, accept: str) -> str:
    try:
        with _http_client() as client:
            r = client.get(url, headers={"Accept": accept})
            r.raise_for_status()
            LAST_PRICE_ERRORS.pop(source, None)
            return r.text
    except Exception as exc:  # noqa: BLE001 — recorded, caller falls back
        _note_error(source, exc)
        raise


def _ecb_usd_per_eur(start: date, end: date) -> dict[date, float]:
    """ECB euro reference rates (USD per 1 EUR) — official ECB API, then the
    Frankfurter mirror of the same ECB data. Empty dict if both fail."""
    rates: dict[date, float] = {}
    try:
        text = _get_text(
            "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
            f"?startPeriod={start.isoformat()}&endPeriod={end.isoformat()}&format=csvdata",
            source="EZB",
            accept="text/csv",
        )
        rows = list(csv.reader(io.StringIO(text)))
        head = [c.strip().upper() for c in rows[0]] if rows else []
        ti, vi = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
        for row in rows[1:]:
            try:
                rates[date.fromisoformat(row[ti][:10])] = float(row[vi])
            except (ValueError, IndexError):
                continue
    except Exception:  # noqa: BLE001 — try the mirror
        rates = {}
    if rates:
        return rates
    try:
        data = _get_json_with_retry(
            f"https://api.frankfurter.dev/v1/{start.isoformat()}..{end.isoformat()}"
            "?base=EUR&symbols=USD",
            source="EZB (Frankfurter)",
            attempts=2,
        )
        for day, v in ((data or {}).get("rates") or {}).items():
            usd = _as_float((v or {}).get("USD"))
            if usd:
                rates[date.fromisoformat(day)] = usd
    except Exception:  # noqa: BLE001 — fail-open
        pass
    return rates


def _fx_on(rates: dict[date, float], sorted_days: list[date], on: date) -> float | None:
    """ECB rate of ``on`` or the last published one before (weekend/holiday)."""
    i = bisect.bisect_right(sorted_days, on) - 1
    if i < 0 or (on - sorted_days[i]).days > FX_MAX_GAP_DAYS:
        return None
    return rates[sorted_days[i]]


def usd_per_eur_on(days: list[date]) -> dict[date, float | None]:
    """EZB-Referenzkurs (USD je 1 EUR) je Tag — Wochenende/Feiertag: letzter
    veröffentlichter Kurs. Fail-open: None, wenn nicht abrufbar."""
    if not days:
        return {}
    lo, hi = min(days), max(days)
    rates = _ecb_usd_per_eur(lo - timedelta(days=FX_MAX_GAP_DAYS + 3), hi)
    ordered = sorted(rates)
    return {d: _fx_on(rates, ordered, d) for d in days}


def _binance_usdt_in_eur(days: list[date]) -> list[tuple[float, float | None, float | None]]:
    """Days before BTC/EUR on Binance: BTC/USDT close ÷ ECB USD per EUR."""
    want = [d for d in days if d >= BINANCE_USDT_SINCE]
    if not want:
        return []
    lo, hi = min(want), max(want)
    closes = _binance_daily(BINANCE_SYMBOL_USDT, lo, hi)
    if not closes:
        return []
    rates = _ecb_usd_per_eur(lo - timedelta(days=FX_MAX_GAP_DAYS + 3), hi)
    fx_days = sorted(rates)
    out: list[tuple[float, float | None, float | None]] = []
    for d, usdt in closes:
        fx = _fx_on(rates, fx_days, d)
        if fx:
            out.append((_noon(d), usdt, usdt / fx))
    return out


def _binance_spot_http() -> tuple[float | None, float | None]:
    """Binance BTC/EUR last price — fail-open (EUR only)."""
    try:
        data = _binance_get(f"/api/v3/ticker/price?symbol={BINANCE_SYMBOL_EUR}")
        return None, _as_float((data or {}).get("price"))
    except Exception:  # noqa: BLE001
        return None, None


def _default_bulk_history(days: list[date]) -> list[tuple[float, float | None, float | None]]:
    """One rule for all days (Rz. 91): Binance BTC/EUR close; before 2020-01-03
    Binance BTC/USDT close ÷ ECB USD/EUR; only before 2017-08-17 mempool."""
    if not days:
        return []
    series: list[tuple[float, float | None, float | None]] = []

    def add(points: list[tuple[float, float | None, float | None]], source: str) -> None:
        covered = {int(t // 86400) for t, _, _ in series}
        for p in points:
            if int(p[0] // 86400) in covered:
                continue
            series.append(p)
            DAY_SOURCES[datetime.fromtimestamp(p[0], tz=timezone.utc).date()] = source
        series.sort()

    eur_days = [d for d in days if d >= BINANCE_EUR_SINCE]
    if eur_days:
        try:
            add(_binance_daily_eur(min(eur_days), max(eur_days)), PRICE_SOURCE_BINANCE_EUR)
        except Exception:  # noqa: BLE001 — fall back below
            pass
    def uncovered() -> list[date]:  # exact day, not the ±2-day tolerance
        have = {int(t // 86400) for t, _, _ in series}
        return [d for d in days if int(_noon(d) // 86400) not in have]

    if uncovered():
        try:
            add(_binance_usdt_in_eur(uncovered()), PRICE_SOURCE_BINANCE_USDT_ECB)
        except Exception:  # noqa: BLE001 — fall back below
            pass
    if uncovered():
        add(_mempool_bulk_history_http(), PRICE_SOURCE_MEMPOOL)
    return series


def price_sources_for(days: Iterable[date]) -> dict[str, int]:
    """How many of ``days`` were priced by which rule (bulk sources only)."""
    out: dict[str, int] = {}
    for d in set(days):
        src = DAY_SOURCES.get(d)
        if src:
            out[src] = out.get(src, 0) + 1
    return out


def _mempool_bulk_history_http() -> list[tuple[float, float | None, float | None]]:
    """Full BTC price history in ONE request (mempool.space, no parameters).

    Fallback for days Binance does not cover (before 2020-01-03).

    Returns a sorted series of (unix_time, usd, eur); empty on any failure —
    callers then fall back to per-day requests.
    """
    try:
        data = _get_json_with_retry(
            "https://mempool.space/api/v1/historical-price", source="mempool.space"
        )
        return _series_from_prices((data or {}).get("prices"))
    except Exception:  # noqa: BLE001 — fail-open
        return []


def _nearest(
    series: list[tuple[float, float | None, float | None]], on: date
) -> tuple[float | None, float | None] | None:
    if not series:
        return None
    target = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc).timestamp()
    i = bisect.bisect_left(series, (target,))
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(series):
            if best is None or abs(series[j][0] - target) < abs(best[0] - target):
                best = series[j]
    if best is None or abs(best[0] - target) > BULK_MAX_DISTANCE_S:
        return None
    return best[1], best[2]


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mempool_spot_http() -> tuple[float | None, float | None]:
    """mempool.space /api/v1/prices — fail-open."""
    try:

        url = "https://mempool.space/api/v1/prices"
        with _http_client() as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
            return _as_float(data.get("USD")), _as_float(data.get("EUR"))
    except Exception:
        return None, None


def _coingecko_spot_http() -> tuple[float | None, float | None]:
    """CoinGecko simple/price — fail-open (often 429 on free tier)."""
    try:

        url = get_settings().price_oracle_url
        with _http_client() as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json().get("bitcoin", {})
            return _as_float(data.get("usd")), _as_float(data.get("eur"))
    except Exception:
        return None, None


def _default_spot_http() -> tuple[float | None, float | None]:
    """Binance (exchange) first, then mempool, then CoinGecko."""
    for fetcher in (_binance_spot_http, _mempool_spot_http, _coingecko_spot_http):
        usd, eur = fetcher()
        if usd is not None or eur is not None:
            return usd, eur
    return None, None


def _mempool_historical_http(on: date) -> tuple[float | None, float | None]:
    """mempool.space historical-price for one day — fail-open (noon UTC)."""
    try:
        ts = int(
            datetime(on.year, on.month, on.day, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        )
        data = _get_json_with_retry(
            f"https://mempool.space/api/v1/historical-price?timestamp={ts}",
            source="mempool.space",
        )
        # API may return neighbouring entries — take the nearest.
        hit = _nearest(_series_from_prices((data or {}).get("prices")), on)
        return hit if hit is not None else (None, None)
    except Exception as exc:  # noqa: BLE001 — fail-open, error noted
        _note_error("mempool.space", exc)
        return None, None


def _coingecko_historical_http(on: date) -> tuple[float | None, float | None]:
    """CoinGecko history — fail-open. Date format dd-mm-yyyy."""
    try:
        ds = on.strftime("%d-%m-%Y")
        url = (
            "https://api.coingecko.com/api/v3/coins/bitcoin/history"
            f"?date={ds}&localization=false"
        )
        data = _get_json_with_retry(url, source="CoinGecko", attempts=1)
        market = (data or {}).get("market_data", {}).get("current_price", {})
        return _as_float(market.get("usd")), _as_float(market.get("eur"))
    except Exception as exc:  # noqa: BLE001 — fail-open, error noted
        _note_error("CoinGecko", exc)
        return None, None


def _default_historical_http(on: date) -> tuple[float | None, float | None]:
    """Try mempool first (reliable), then CoinGecko (often rate-limited)."""
    for fetcher in (_mempool_historical_http, _coingecko_historical_http):
        usd, eur = fetcher(on)
        if usd is not None or eur is not None:
            return usd, eur
    return None, None


def days_from(values: Iterable[Any]) -> list[date]:
    """Parse ISO dates/datetimes (or dates) → list of days; skips blanks."""
    out: list[date] = []
    for v in values:
        if isinstance(v, datetime):
            out.append(v.date())
        elif isinstance(v, date):
            out.append(v)
        elif v:
            try:
                out.append(date.fromisoformat(str(v)[:10]))
            except ValueError:
                continue
    return out


class PriceOracle:
    """Session-scoped oracle with optional in-memory cache (never disk)."""

    def __init__(
        self,
        *,
        spot_fetcher: Callable[[], tuple[float | None, float | None]] | None = None,
        historical_fetcher: Callable[[date], tuple[float | None, float | None]]
        | None = None,
        bulk_fetcher: Callable[[list[date]], list[tuple[float, float | None, float | None]]]
        | None = None,
        source: str = SOURCE_CHAIN,
    ) -> None:
        self._spot_fetcher = spot_fetcher or _default_spot_http
        self._historical_fetcher = historical_fetcher or _default_historical_http
        # One-shot full history (mempool). Only with the default per-day
        # fetcher — an injected fetcher (tests) never triggers network bulk.
        if bulk_fetcher is not None:
            self._bulk_fetcher = bulk_fetcher
        else:
            self._bulk_fetcher = _default_bulk_history if historical_fetcher is None else None
        self._bulk_series: list[tuple[float, float | None, float | None]] = []
        self._bulk_tried_at: float | None = None
        # day → monotonic time of last failure (retried after RETRY_FAILED_AFTER_S)
        self._hist_failed: dict[date, float] = {}
        self._spot_failed_at: float | None = None
        self._source = source
        # In-memory only — cleared when process ends.
        self._hist_cache: dict[date, tuple[float | None, float | None]] = {}
        self._spot_cache: SpotReference | None = None
        # Serializes prefetches so parallel UI requests do not fetch the same
        # days twice.
        self._prefetch_lock = threading.Lock()
        # UI endpoints read prices cache-only (never block on HTTP); missing
        # days are fetched by a background thread and filled in on re-poll.
        self._local = threading.local()
        self._bg_lock = threading.Lock()
        self._bg_threads: list[threading.Thread] = []

    def clear_cache(self) -> None:
        self._hist_cache.clear()
        self._spot_cache = None
        self._hist_failed.clear()
        self._spot_failed_at = None
        self._bulk_series = []
        self._bulk_tried_at = None

    def _recently_failed(self, on: date) -> bool:
        t = self._hist_failed.get(on)
        return t is not None and time.monotonic() - t < RETRY_FAILED_AFTER_S

    def price_status(self) -> dict[str, Any]:
        """Loaded / failed day counts + last source errors (for the UI)."""
        return {
            "days_loaded": sum(
                1 for u, e in self._hist_cache.values() if u is not None or e is not None
            ),
            "days_failed": len(self._hist_failed),
            "spot_available": bool(self._spot_cache and self._spot_cache.available),
            "bulk_points": len(self._bulk_series),
            "errors": dict(LAST_PRICE_ERRORS),
        }

    def prefetch_historical(self, days: Iterable[date | None]) -> int:
        """Fetch all missing days in parallel into the session cache.

        Callers that loop over many rows call this first, so the per-row
        ``get_acquisition_reference`` lookups are cache hits instead of one
        sequential HTTP request per day. Fail-open. Returns days fetched.
        No-op inside ``cached_only`` (UI requests never wait on HTTP).
        """
        if self._is_cached_only():
            return 0
        with self._prefetch_lock:
            todo = self.missing_days(days)
            if not todo:
                return 0
            # 1) One request for the whole history (fewest calls, no 429s).
            uncovered = [d for d in todo if _nearest(self._bulk_series, d) is None]
            if (
                self._bulk_fetcher is not None
                and len(uncovered) >= BULK_MIN_DAYS
                and (
                    self._bulk_tried_at is None
                    or time.monotonic() - self._bulk_tried_at >= RETRY_FAILED_AFTER_S
                )
            ):
                self._bulk_tried_at = time.monotonic()
                try:
                    fresh = list(self._bulk_fetcher(uncovered) or [])
                except Exception:  # noqa: BLE001 — fall back to per-day
                    fresh = []
                if fresh:
                    self._bulk_series = sorted(self._bulk_series + fresh)
            rest: list[date] = []
            for day in todo:
                hit = _nearest(self._bulk_series, day)
                if hit is not None:
                    self._hist_cache[day] = hit
                    self._hist_failed.pop(day, None)
                else:
                    rest.append(day)
            # 2) Remaining days one by one (few in parallel, retry on 429).
            if rest:
                workers = max(1, min(PREFETCH_WORKERS, len(rest)))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(self._safe_historical, rest))
                for day, (usd, eur) in zip(rest, results):
                    if usd is None and eur is None:
                        self._hist_failed[day] = time.monotonic()  # retry later
                    else:
                        self._hist_cache[day] = (usd, eur)
                        self._hist_failed.pop(day, None)
            return len(todo)

    @contextmanager
    def cached_only(self) -> Iterator[None]:
        """Within this block, lookups never hit the network (per thread)."""
        prev = getattr(self._local, "cached_only", False)
        self._local.cached_only = True
        try:
            yield
        finally:
            self._local.cached_only = prev

    def _is_cached_only(self) -> bool:
        return bool(getattr(self._local, "cached_only", False))

    @property
    def prefetch_running(self) -> bool:
        with self._bg_lock:
            self._bg_threads = [t for t in self._bg_threads if t.is_alive()]
            return bool(self._bg_threads)

    def wait_for_prefetch(self, timeout: float) -> bool:
        """Wait up to ``timeout`` s for background loads. True when done."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._bg_lock:
            threads = list(self._bg_threads)
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        return not self.prefetch_running

    def missing_days(self, days: Iterable[date | None]) -> list[date]:
        """Days without a price that are due for a (re)try."""
        return sorted(
            {
                d
                for d in days
                if d is not None
                and d not in self._hist_cache
                and not self._recently_failed(d)
            }
        )

    def _spot_due(self) -> bool:
        if self._spot_cache is not None:
            return False
        t = self._spot_failed_at
        return t is None or time.monotonic() - t >= RETRY_FAILED_AFTER_S

    def start_background_prefetch(
        self, days: Iterable[date | None], *, include_spot: bool = True
    ) -> bool:
        """Fetch missing days (+ spot) in a daemon thread. Returns True if
        anything is (still) loading."""
        todo = self.missing_days(days)
        need_spot = include_spot and self._spot_due()
        if not todo and not need_spot:
            return self.prefetch_running

        def work() -> None:
            self.prefetch_historical(todo)
            if need_spot and self._spot_due():
                try:
                    self.get_spot()
                except Exception:  # noqa: BLE001 — fail-open
                    pass

        t = threading.Thread(target=work, name="price-prefetch", daemon=True)
        with self._bg_lock:
            self._bg_threads.append(t)
        t.start()
        return True

    def _safe_historical(self, on: date) -> tuple[float | None, float | None]:
        try:
            return self._historical_fetcher(on)
        except Exception:  # noqa: BLE001 — fail-open
            return None, None

    def get_spot(self, *, use_cache: bool = True) -> SpotReference:
        """Current REFERENZWERT. Fail-open with SPOT_UNAVAILABLE markers."""
        if use_cache and self._spot_cache is not None:
            return self._spot_cache
        if self._is_cached_only():
            return SpotReference(
                available=False,
                message=PRICE_LOADING if self._spot_due() else SPOT_UNAVAILABLE,
                timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                source=self._source,
            )
        usd, eur = self._spot_fetcher()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if usd is None and eur is None:
            ref = SpotReference(
                available=False,
                message=SPOT_UNAVAILABLE,
                timestamp_utc=ts,
                source=self._source,
            )
        else:
            ref = SpotReference(
                usd=usd,
                eur=eur,
                timestamp_utc=ts,
                source=self._source,
                available=True,
            )
        if use_cache:
            if ref.available:
                self._spot_cache = ref
                self._spot_failed_at = None
            else:
                self._spot_failed_at = time.monotonic()  # retry later
        return ref

    def get_acquisition_reference(
        self, on: date, *, use_cache: bool = True
    ) -> AcquisitionReference:
        """Historical Anschaffungs-Referenz for one inflow day. Fail-open."""
        if use_cache and on in self._hist_cache:
            usd, eur = self._hist_cache[on]
        elif self._is_cached_only():
            return AcquisitionReference(
                as_of=on,
                available=False,
                message=(
                    HISTORICAL_UNAVAILABLE if self._recently_failed(on) else PRICE_LOADING
                ),
                source=self._source,
            )
        else:
            hit = _nearest(self._bulk_series, on)
            usd, eur = hit if hit is not None else self._safe_historical(on)
            if use_cache:
                if usd is None and eur is None:
                    self._hist_failed[on] = time.monotonic()
                else:
                    self._hist_cache[on] = (usd, eur)
                    self._hist_failed.pop(on, None)
        if usd is None and eur is None:
            return AcquisitionReference(
                as_of=on,
                available=False,
                message=HISTORICAL_UNAVAILABLE,
                source=self._source,
            )
        return AcquisitionReference(
            as_of=on,
            usd=usd,
            eur=eur,
            source=self._source,
            available=True,
        )
