"""FastAPI app — ephemeral in-memory session. Localhost bind by default.

Hard Boundaries
---------------
* No durable persistence: wallets / txs / labels live in process RAM
  (SQLite ``:memory:`` + WalletRegistry). Process exit = all gone.
* Input = cut-and-paste only (no USB / device auto-detect).
* Reports: bytes generated in memory **only on explicit request**;
  endpoints return downloadable content and never write report files to disk.
"""

from __future__ import annotations


from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from btc_origin import __version__
from btc_origin.config import get_settings
from btc_origin.regelwerk import RegelwerkFehler, validate_all
from btc_origin.api.core import AppState, _session  # noqa: F401 — für Tests/Erweiterungen
from btc_origin.api.routes import session, cloud, external, local, trace, report, regelwerk
from btc_origin.api.routes.report import _session_lots_for_fa  # noqa: F401 — bisheriger Importpfad


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Regelwerk beim Start prüfen (REGELWERK.md Abschnitt 5) — Fehler: App startet nicht
    validate_all()
    state = AppState()
    app.state.session = state
    try:
        yield
    finally:
        state.registry.clear()
        state.oracle.clear_cache()
        state.chain_cache.clear()
        state.db.close()


app = FastAPI(
    title="BTC-Herkunft",
    description=(
        "Ephemeral local Bitcoin provenance & holding-period analysis. "
        "Hardware-wallet-agnostic: paste any BIP32/BIP84 xpub "
        "(e.g. Trezor, BitBox, Ledger, Coldcard, Foundation Devices, …). "
        "Unlimited xpubs. Never accepts seeds or private keys. "
        "NO durable persistence — session memory only; closing the process "
        "clears everything. Reports only on explicit Save/Print."
    ),
    version=__version__,
    lifespan=lifespan,
)

# Localhost Vite UI (5173) + same-origin via proxy. No credentials needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-BTC-Herkunft-Disk-Written",
        "X-BTC-Herkunft-Referenzwert",
        "X-BTC-Herkunft-Anschaffungs-Referenz",
        "Content-Disposition",
        "X-BTC-Herkunft-Report-Kind",
        "X-PP-Import-Stats",
        "X-BTC-Herkunft-Pruefergebnis",
    ],
)



@app.exception_handler(RegelwerkFehler)
async def _regelwerk_fehler(_request: Request, exc: RegelwerkFehler) -> JSONResponse:
    """Regeldatei fehlt oder ist ungültig → kein Bericht, klare Meldung (nie ein anderes Jahr)."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


app.include_router(session.router)
app.include_router(cloud.router)
app.include_router(external.router)
app.include_router(local.router)
app.include_router(trace.router)
app.include_router(report.router)
app.include_router(regelwerk.router)


def run() -> None:
    import uvicorn

    try:
        checked = validate_all()
    except RegelwerkFehler as exc:
        raise SystemExit(f"Regelwerk fehlerhaft — BTC-Herkunft startet nicht:\n  {exc}") from None
    print("Regelwerk geprüft: " + "; ".join(checked))
    settings = get_settings()
    uvicorn.run(
        "btc_origin.api.app:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
