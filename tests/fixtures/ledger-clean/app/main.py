import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response

from .api import accounts, transfers
from .db import create_pool
from .logging import configure_logging

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.pool = await create_pool()
    log.info("ledger api started")
    yield
    await app.state.pool.close()


app = FastAPI(title="ledger", lifespan=lifespan)
app.include_router(transfers.router)
app.include_router(accounts.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request.headers.get("x-request-id", ""), path=request.url.path
    )
    return await call_next(request)


@app.get("/livez")
async def livez():
    return {"ok": True}


@app.get("/readyz")
async def readyz(request: Request, response: Response):
    try:
        async with request.app.state.pool.acquire(timeout=1) as conn:
            await conn.fetchval("SELECT 1", timeout=1)
    except (asyncio.TimeoutError, OSError, Exception) as exc:  # any failure means not ready
        log.warning("readiness check failed", error=str(exc))
        response.status_code = 503
        return {"ok": False}
    return {"ok": True}
