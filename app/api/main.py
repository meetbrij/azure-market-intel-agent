"""FastAPI app + lifespan.

uv run uvicorn app.api.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.jobs.store import dispose_engine, init_db
from app.logging_setup import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    await init_db()
    app.state.arq = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        yield
    finally:
        await app.state.arq.aclose()
        await dispose_engine()


app = FastAPI(
    title="Azure Market Intelligence Agent",
    version="0.1.0",
    description="Submit a research question; poll for a cited report grounded "
    "in SEC 10-K filings.",
    lifespan=lifespan,
)
app.include_router(router)
