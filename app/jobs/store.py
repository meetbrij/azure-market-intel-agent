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
from app.jobs.models import AuditEvent, Base, Job, JobStatus, utcnow

SYSTEM = "system"  # audit actor for the worker's own events


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
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS submitted_by VARCHAR(64)",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS submitted_by_name VARCHAR(200)",
    # audit_events is append-only: refuse changes at the database, not just
    # in the app. (A superuser can still drop the trigger; a separate role
    # without that privilege is the deployment-time fix.)
    """
    CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events is append-only (% refused)', TG_OP;
    END
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()
    """,
    """
    CREATE OR REPLACE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()
    """,
]


# Any constant: serialises schema setup between processes starting together.
_SCHEMA_LOCK_ID = 7_214_019


async def init_db() -> None:
    """Create/upgrade the tables. Idempotent, and safe when the API and the
    worker start at the same moment (Kubernetes gives no start order): on
    Postgres the whole setup runs under a transaction-scoped advisory lock."""
    async with get_engine().begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text(f"SELECT pg_advisory_xact_lock({_SCHEMA_LOCK_ID})"))
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


async def create_job(
    query: str,
    companies: list[str],
    submitted_by: str | None = None,
    submitted_by_name: str | None = None,
) -> Job:
    """Insert the job and, when there is a submitter, its `job_submitted`
    audit event, in one transaction: no job without its audit record."""
    job = Job(
        query=query,
        companies=companies,
        status=JobStatus.QUEUED,
        submitted_by=submitted_by,
        submitted_by_name=submitted_by_name,
    )
    async with get_sessionmaker()() as session:
        session.add(job)
        if submitted_by is not None:
            await session.flush()  # assigns job.id
            session.add(
                AuditEvent(
                    actor=submitted_by,
                    actor_name=submitted_by_name,
                    action="job_submitted",
                    job_id=job.id,
                    detail={"query": query, "companies": companies},
                )
            )
        await session.commit()
    return job


async def record_audit(
    action: str,
    *,
    actor: str = SYSTEM,
    actor_name: str | None = None,
    job_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one audit event. Raises on failure: callers decide whether the
    action may proceed without its record."""
    async with get_sessionmaker()() as session:
        session.add(
            AuditEvent(
                actor=actor,
                actor_name=actor_name,
                action=action,
                job_id=job_id,
                detail=detail or {},
            )
        )
        await session.commit()


async def list_audit(job_id: str | None = None) -> list[AuditEvent]:
    """Oldest first. For tests and operators; the API does not expose it."""
    query = select(AuditEvent).order_by(AuditEvent.id)
    if job_id is not None:
        query = query.where(AuditEvent.job_id == job_id)
    async with get_sessionmaker()() as session:
        return list(await session.scalars(query))


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
    status: JobStatus | None = None,
    limit: int | None = None,
    submitted_by: str | None = None,
) -> list[Job]:
    """Newest first; optionally filtered by status and submitter."""
    query = select(Job).order_by(Job.created_at.desc())
    if status is not None:
        query = query.where(Job.status == status)
    if submitted_by is not None:
        query = query.where(Job.submitted_by == submitted_by)
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
