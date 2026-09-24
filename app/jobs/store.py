"""Job persistence: engine/session setup plus create/get/update."""

from functools import lru_cache
from typing import Any

from sqlalchemy import text
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


async def init_db() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
) -> None:
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise LookupError(f"Job {job_id} not found")
        job.status = status
        job.result = result
        job.error = error
        job.updated_at = utcnow()
        await session.commit()
