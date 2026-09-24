"""Graph state and the structured Report the write node produces."""

from typing import Any

from pydantic import BaseModel, field_validator

MAX_QUOTE_WORDS = 25


class Citation(BaseModel):
    company: str
    doc_type: str
    period: str
    source_blob: str
    chunk_no: int
    page: int
    quote: str  # short supporting snippet, <= 25 words

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


class ResearchState(BaseModel):
    query: str
    companies: list[str] = []  # optional filter
    retrieved: list[dict[str, Any]] = []  # raw search hits
    report: Report | None = None
    error: str | None = None
