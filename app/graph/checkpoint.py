"""Postgres checkpointer for the research graph.

Same database as the jobs table, separate schema (`CHECKPOINT_SCHEMA`), so
LangGraph's tables never collide with ours. One pool per worker process.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url

from app.config import get_settings

log = logging.getLogger(__name__)

POOL_MAX_SIZE = 5


def checkpoint_conninfo() -> str:
    """DATABASE_URL is a SQLAlchemy URL (postgresql+asyncpg://…); psycopg
    wants the plain libpq form."""
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("The checkpointer needs a PostgreSQL DATABASE_URL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def postgres_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    conninfo = checkpoint_conninfo()
    schema = get_settings().checkpoint_schema
    async with await AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
        )
    pool: AsyncConnectionPool = AsyncConnectionPool(
        conninfo,
        max_size=POOL_MAX_SIZE,
        open=False,
        # Required by AsyncPostgresSaver when you supply your own connections.
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
            "options": f"-c search_path={schema}",
        },
    )
    async with pool:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()  # creates/migrates LangGraph's tables; idempotent
        log.info("Checkpointer ready (schema %r)", schema)
        yield saver
