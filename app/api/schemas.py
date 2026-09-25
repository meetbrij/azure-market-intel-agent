"""Request/response models for the research API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.graph.state import Report
from app.jobs.models import Job, JobStatus


class ResearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    companies: list[str] = Field(
        default=[],
        description="Optional filter: Amazon, Alphabet, Microsoft",
        examples=[["Amazon", "Alphabet"]],
    )


class JobCreated(BaseModel):
    job_id: str
    status: JobStatus


class ResumeRequest(BaseModel):
    approved: bool
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Guidance for the planner; with approved=false the plan is revised",
    )


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    query: str
    companies: list[str]
    report: Report | None = None
    error: str | None = None
    # Progress: the last graph node completed and how many steps have run.
    last_node: str | None = None
    checkpoint_count: int = 0
    # The approval request while status == awaiting_approval.
    interrupt: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: Job) -> "JobView":
        return cls(
            job_id=job.id,
            status=JobStatus(job.status),
            query=job.query,
            companies=job.companies,
            report=Report.model_validate(job.result) if job.result else None,
            error=job.error,
            last_node=job.last_node,
            checkpoint_count=job.checkpoint_count or 0,
            interrupt=job.interrupt,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class Health(BaseModel):
    status: str
    postgres: str
    redis: str
