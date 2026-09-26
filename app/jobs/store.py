"""Job persistence: engine/session setup plus create/get/update."""

from functools import lru_cache
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.jobs.models import Base, Job, JobStatus, utcnow


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


# create_all never alters an existing table; bring a Phase 1 table up to date.
# Idempotent. Alembic replaces this later.
_POSTGRES_MIGRATIONS = [
    "ALTER TABLE jobs ALTER COLUMN status TYPE VARCHAR(32)",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_node VARCHAR(64)",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS checkpoint_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS interrupt JSON",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS subject VARCHAR(300)",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS archive_prefix VARCHAR(200)",
]


async def init_db() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            for statement in _POSTGRES_MIGRATIONS:
                await conn.execute(text(statement))


async def ping_db() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()


async def create_job(query: str, companies: list[str]) -> Job:
    job = Job(query=query, companies=companies, status=JobStatus.QUEUED)
    async with get_sessionmaker()() as session:
        session.add(job)
        await session.commit()
    return job


async def get_job(job_id: str) -> Job | None:
    async with get_sessionmaker()() as session:
        return await session.get(Job, job_id)


async def update_job(
    job_id: str,
    *,
    status: JobStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    interrupt: dict[str, Any] | None = None,
) -> None:
    """Set status and its outcome fields (unset ones are cleared). Progress
    fields are left alone."""
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise LookupError(f"Job {job_id} not found")
        job.status = status
        job.result = result
        job.error = error
        job.interrupt = interrupt
        job.updated_at = utcnow()
        await session.commit()


async def record_progress(job_id: str, node: str, subject: str | None = None) -> None:
    values: dict[str, Any] = {
        "last_node": node,
        "checkpoint_count": Job.checkpoint_count + 1,
        "updated_at": utcnow(),
    }
    if subject:
        values["subject"] = subject[:300]
    async with get_sessionmaker()() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(**values))
        await session.commit()


async def set_archive_prefix(job_id: str, prefix: str) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Job).where(Job.id == job_id).values(archive_prefix=prefix)
        )
        await session.commit()


async def transition(
    job_id: str, *, from_status: JobStatus, to_status: JobStatus
) -> bool:
    """Compare-and-set on status: True only for the caller that won the race
    (e.g. two concurrent resume calls)."""
    async with get_sessionmaker()() as session:
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == from_status)
            .values(status=to_status, updated_at=utcnow())
        )
        await session.commit()
        return bool(result.rowcount)  # type: ignore[attr-defined]


async def list_jobs(
    status: JobStatus | None = None, limit: int | None = None
) -> list[Job]:
    """Newest first; optionally filtered by status."""
    query = select(Job).order_by(Job.created_at.desc())
    if status is not None:
        query = query.where(Job.status == status)
    if limit is not None:
        query = query.limit(limit)
    async with get_sessionmaker()() as session:
        return list(await session.scalars(query))


async def count_by_status() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            select(Job.status, func.count()).group_by(Job.status)
        )
        return {status: count for status, count in rows.all()}
