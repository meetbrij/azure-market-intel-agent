"""Request/response models for the research API."""

from datetime import datetime

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


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    query: str
    companies: list[str]
    report: Report | None = None
    error: str | None = None
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
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class Health(BaseModel):
    status: str
    postgres: str
    redis: str
