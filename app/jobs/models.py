"""SQLAlchemy tables: `jobs` and the append-only `audit_events`. Tables are
created on API startup (no Alembic yet)."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED)
    query: Mapped[str] = mapped_column(Text)
    companies: Mapped[list[str]] = mapped_column(JSON, default=list)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Progress, updated as the graph streams (one row update per node).
    last_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint_count: Mapped[int] = mapped_column(Integer, default=0)
    # Report subject from the plan, for the jobs list.
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Blob prefix of the archived report bundle (reports/{yyyy}/{mm}/{job_id}/).
    archive_prefix: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # The approval request while status == awaiting_approval.
    interrupt: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Who submitted it (Entra object id and display name). Null for jobs from
    # before Day 15.
    submitted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AuditEvent(Base):
    """Who did what, when. Rows are only ever inserted: the app has no update
    or delete path, and on Postgres a trigger rejects UPDATE, DELETE and
    TRUNCATE (app/jobs/store.py). Not exposed by the API."""

    __tablename__ = "audit_events"

    # BigInteger on Postgres; SQLite (tests) only autoincrements INTEGER.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # A person (Entra object id + name), or "system" for the worker's own events.
    actor: Mapped[str] = mapped_column(String(64))
    actor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
