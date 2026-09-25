"""Graph state, the evidence/plan/critique models, and the Report.

State holds references and short snippets, never whole documents: every
field here is written to each checkpoint (Phase 2, Day 9–10).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_QUOTE_WORDS = 25


def merge_unique(left: list[str], right: list[str]) -> list[str]:
    """Reducer: order-preserving union, safe for concurrent writers."""
    return left + [x for x in right if x not in left]


# ---------- planning / evidence / critique ----------


class ResearchPlan(BaseModel):
    subject: str
    sub_questions: list[str] = Field(description="3-5 specific things to look for")
    companies: list[str]
    needs_live_news: bool


class Evidence(BaseModel):
    source_type: Literal["filing", "news"]
    title: str
    snippet: str
    reference: str  # filings: index chunk id (blob + chunk); news: URL
    period: str | None = None
    # Filing metadata, so citations can be filled in without re-querying.
    company: str | None = None
    doc_type: str | None = None
    source_blob: str | None = None
    chunk_no: int | None = None
    page: int | None = None


class Critique(BaseModel):
    is_complete: bool
    missing: list[str] = []
    citation_problems: list[str] = []


# ---------- report (API contract: Phase 1 fields kept, new ones added) ----------


class Citation(BaseModel):
    company: str | None = None
    doc_type: str | None = None
    period: str | None = None
    source_blob: str | None = None
    chunk_no: int | None = None
    page: int | None = None
    quote: str  # short supporting snippet, <= 25 words
    source_type: Literal["filing", "news"] = "filing"
    reference: str  # the Evidence.reference this citation points to

    @field_validator("quote")
    @classmethod
    def _trim_quote(cls, v: str) -> str:
        words = v.split()
        if len(words) <= MAX_QUOTE_WORDS:
            return " ".join(words)
        return " ".join(words[:MAX_QUOTE_WORDS]) + "…"


class ReportSection(BaseModel):
    heading: str
    body: str
    citations: list[Citation]


class Report(BaseModel):
    subject: str
    summary: str
    sections: list[ReportSection]
    # Sources that were unavailable for this run (set by Python, not the LLM).
    data_gaps: list[str] = []


# ---------- LLM output for `write`: cites by reference id only ----------
# Metadata (company, page, …) is filled from Evidence in Python, so the model
# can't mis-copy it and output stays short.


class DraftCitation(BaseModel):
    reference: str
    quote: str


class DraftSection(BaseModel):
    heading: str
    body: str
    citations: list[DraftCitation]


class DraftReport(BaseModel):
    subject: str
    summary: str
    sections: list[DraftSection]


# ---------- graph state ----------


class ResearchState(BaseModel):
    query: str
    companies: list[str] = []  # optional filter from the request
    plan: ResearchPlan | None = None
    # Each evidence list has exactly one writer node, so plain assignment is
    # safe for the parallel branches.
    filing_evidence: list[Evidence] = []
    news_evidence: list[Evidence] = []
    compacted_context: str = ""
    report: Report | None = None
    critique: Critique | None = None
    loop_count: int = 0
    # Any node may mark a source unavailable, possibly concurrently.
    degraded: Annotated[list[str], merge_unique] = []
    approval: dict[str, Any] | None = None
    error: str | None = None
