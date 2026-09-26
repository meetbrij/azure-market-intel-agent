"""Request/response models for the research API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.graph.state import Critique, Evidence, Report, ResearchPlan, ResearchState
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


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    query: str
    subject: str | None = None
    last_node: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: Job) -> "JobSummary":
        return cls(
            job_id=job.id,
            status=JobStatus(job.status),
            query=job.query,
            subject=job.subject,
            last_node=job.last_node,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    query: str
    companies: list[str]
    subject: str | None = None
    report: Report | None = None
    error: str | None = None
    archive_prefix: str | None = None
    # What each agent stage produced, read from the job's latest checkpoint.
    # Null when there is no checkpoint yet (or it can't be read).
    plan: ResearchPlan | None = None
    filing_evidence: list[Evidence] = []
    news_evidence: list[Evidence] = []
    critique: Critique | None = None
    degraded: list[str] = []
    loop_count: int = 0
    # Progress: the last graph node completed and how many steps have run.
    last_node: str | None = None
    checkpoint_count: int = 0
    # The approval request while status == awaiting_approval.
    interrupt: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: Job, state: ResearchState | None = None) -> "JobView":
        stages: dict[str, Any] = {}
        if state is not None:
            stages = {
                "plan": state.plan,
                "filing_evidence": state.filing_evidence,
                "news_evidence": state.news_evidence,
                "critique": state.critique,
                "degraded": state.degraded,
                "loop_count": state.loop_count,
            }
        return cls(
            job_id=job.id,
            status=JobStatus(job.status),
            query=job.query,
            companies=job.companies,
            subject=job.subject,
            report=Report.model_validate(job.result) if job.result else None,
            error=job.error,
            archive_prefix=job.archive_prefix,
            **stages,
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


class Companies(BaseModel):
    companies: list[str]


class OpsStatus(BaseModel):
    index_name: str
    document_count: int | None = None
    last_ingestion: dict[str, Any] | None = None
    jobs_by_status: dict[str, int]
    running_jobs: list[JobSummary]
    reports_container: str
    # Parts that couldn't be fetched; the rest of the status is still returned.
    errors: list[str] = []
