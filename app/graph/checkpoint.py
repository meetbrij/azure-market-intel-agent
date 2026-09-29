"""Postgres checkpointer for the research graph.

Same database as the jobs table, separate schema (`CHECKPOINT_SCHEMA`), so
LangGraph's tables never collide with ours. One pool per worker process.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db_auth import entra_enabled, pg_token

log = logging.getLogger(__name__)

POOL_MAX_SIZE = 5


def checkpoint_conninfo() -> str:
    """DATABASE_URL is a SQLAlchemy URL (postgresql+asyncpg://…); psycopg
    wants the plain libpq form. asyncpg's `ssl=` query becomes libpq's
    `sslmode=`."""
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("The checkpointer needs a PostgreSQL DATABASE_URL")
    query = dict(url.query)
    if "ssl" in query:
        query["sslmode"] = query.pop("ssl")
    return url.set(drivername="postgresql", query=query).render_as_string(
        hide_password=False
    )


async def _password_kwargs() -> dict[str, Any]:
    """Keyless (DATABASE_AUTH=entra): a fresh Entra token per connection."""
    return {"password": await pg_token()} if entra_enabled() else {}


@asynccontextmanager
async def postgres_checkpointer(
    *, setup: bool = True
) -> AsyncIterator[AsyncPostgresSaver]:
    """setup=True (the worker) creates the schema and LangGraph's tables.
    Readers (the API) pass False so two processes never race on migrations."""
    conninfo = checkpoint_conninfo()
    schema = get_settings().checkpoint_schema
    if setup:
        async with await AsyncConnection.connect(
            conninfo, autocommit=True, **await _password_kwargs()
        ) as conn:
            await conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
            )
    async def connection_kwargs() -> dict[str, Any]:
        # Called by the pool for every new connection (fresh token when keyless).
        return {
            # Required by AsyncPostgresSaver when you supply your own connections.
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
            "options": f"-c search_path={schema}",
            **await _password_kwargs(),
        }

    pool: AsyncConnectionPool = AsyncConnectionPool(
        conninfo,
        max_size=POOL_MAX_SIZE,
        open=False,
        kwargs=connection_kwargs,  # type: ignore[arg-type]  # async callable is supported
    )
    async with pool:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        if setup:
            await saver.setup()  # creates/migrates LangGraph's tables; idempotent
        log.info("Checkpointer ready (schema %r, setup=%s)", schema, setup)
        yield saver
