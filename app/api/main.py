"""FastAPI app + lifespan.

uv run uvicorn app.api.main:app --reload
"""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.api.routes import router
from app.azure_clients import close_async_clients
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.checkpoint import postgres_checkpointer
from app.jobs.store import dispose_engine, init_db
from app.logging_setup import configure_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    await init_db()
    app.state.arq = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    async with AsyncExitStack() as stack:
        # Read-only access to checkpoints, so GET /research/{id} can show what
        # each agent stage produced. The worker owns setup and writes.
        app.state.graph = None
        try:
            saver = await stack.enter_async_context(postgres_checkpointer(setup=False))
            app.state.graph = build_graph(saver)
        except Exception:
            log.exception("Checkpoint reader unavailable; job stages will be empty")
        try:
            yield
        finally:
            await app.state.arq.aclose()
            await close_async_clients()
            await dispose_engine()


app = FastAPI(
    title="Azure Market Intelligence Agent",
    version="0.1.0",
    description="Submit a research question, approve the plan, and get a cited "
    "report grounded in SEC 10-K filings and live news.",
    lifespan=lifespan,
)
app.include_router(router)
