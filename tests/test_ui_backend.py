"""Day 12 backend: API additions for the UI, report archival, provenance."""

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.api.main import app
from app.api.routes import get_graph
from app.graph.llm import _log_served, recording_calls
from app.graph.nodes import evidence_from_hit
from app.graph.state import (
    Critique,
    Evidence,
    LlmCall,
    ResearchState,
)
from app.jobs import store
from app.jobs.models import JobStatus
from app.jobs.worker import run_research
from app.reports import archive
from app.reports.markdown import render_report_md
from tests.conftest import (
    DEFAULT_PLAN,
    HIT,
    FakeContainer,
    GraphDeps,
    make_citation,
    make_report,
)

NEWS = make_citation(
    source_type="news",
    reference="https://www.example.com/aws-deal",
    company="Amazon",
    period="2026-09-20",
    doc_type=None,
    source_blob=None,
    chunk_no=None,
    page=None,
    quote="AWS signs a deal",
)


def state_with_everything() -> ResearchState:
    return ResearchState(
        query="Amazon operating income?",
        companies=["Amazon"],
        plan=DEFAULT_PLAN,
        filing_evidence=[evidence_from_hit(HIT)],
        news_evidence=[
            Evidence(
                source_type="news",
                title="AWS deal",
                snippet="s",
                reference=NEWS.reference,
            )
        ],
        critique=Critique(is_complete=False, missing=["AWS margin"]),
        degraded=["news"],
        loop_count=2,
        report=make_report(make_citation(), NEWS),
        llm_calls=[
            LlmCall(
                node="plan",
                deployment="chat-mini",
                prompt_tokens=100,
                completion_tokens=50,
                at="t",
            ),
            LlmCall(
                node="write",
                deployment="chat-fallback",
                prompt_tokens=200,
                completion_tokens=70,
                at="t",
            ),
        ],
    )


# ---------- GET /research (list) ----------


async def test_list_jobs_newest_first_with_filter(client: AsyncClient) -> None:
    first = await store.create_job("first", [])
    second = await store.create_job("second", [])
    await store.update_job(first.id, status=JobStatus.AWAITING_APPROVAL)

    everything = (await client.get("/api/v1/research")).json()
    waiting = (await client.get("/api/v1/research?status=awaiting_approval")).json()
    one = (await client.get("/api/v1/research?limit=1")).json()

    assert [j["job_id"] for j in everything] == [second.id, first.id]
    assert [j["job_id"] for j in waiting] == [first.id]
    assert len(one) == 1
    assert set(everything[0]) >= {"job_id", "status", "query", "subject", "created_at"}


async def test_list_rejects_unknown_status(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/research?status=bogus")).status_code == 422


# ---------- GET /research/{id} (stages from the checkpoint) ----------


async def test_detail_exposes_agent_stages(client: AsyncClient) -> None:
    job = await store.create_job("q", ["Amazon"])
    snapshot = SimpleNamespace(values=state_with_everything().model_dump())
    app.dependency_overrides[get_graph] = lambda: SimpleNamespace(
        aget_state=AsyncMock(return_value=snapshot)
    )

    body = (await client.get(f"/api/v1/research/{job.id}")).json()

    assert body["plan"]["sub_questions"] == DEFAULT_PLAN.sub_questions
    assert body["filing_evidence"][0]["reference"] == HIT["id"]
    assert body["news_evidence"][0]["reference"] == NEWS.reference
    assert body["critique"]["missing"] == ["AWS margin"]
    assert (body["degraded"], body["loop_count"]) == (["news"], 2)


async def test_detail_without_checkpoint_still_works(client: AsyncClient) -> None:
    job = await store.create_job("q", [])
    app.dependency_overrides[get_graph] = lambda: SimpleNamespace(
        aget_state=AsyncMock(side_effect=RuntimeError("no tables yet"))
    )

    resp = await client.get(f"/api/v1/research/{job.id}")

    assert resp.status_code == 200
    assert resp.json()["plan"] is None and resp.json()["filing_evidence"] == []


# ---------- GET /research/{id}/report.md ----------


async def test_report_md_served_from_archive(
    client: AsyncClient, blob_storage: dict[str, FakeContainer]
) -> None:
    job = await store.create_job("q", [])
    prefix = archive.archive_prefix(job.id, job.created_at)
    blob_storage["reports"].blobs[prefix + "report.md"] = b"# Archived"
    await store.set_archive_prefix(job.id, prefix)

    resp = await client.get(f"/api/v1/research/{job.id}/report.md")

    assert resp.text == "# Archived"
    assert resp.headers["x-report-source"] == "archive"
    assert resp.headers["content-type"].startswith("text/markdown")


async def test_report_md_rendered_when_not_archived(client: AsyncClient) -> None:
    job = await store.create_job("q", [])
    await store.update_job(
        job.id,
        status=JobStatus.COMPLETED,
        result=make_report(make_citation()).model_dump(),
    )

    resp = await client.get(f"/api/v1/research/{job.id}/report.md")

    assert resp.headers["x-report-source"] == "rendered"
    assert resp.text.startswith("# Amazon operating income")


async def test_report_md_404_before_completion(client: AsyncClient) -> None:
    job = await store.create_job("q", [])
    assert (await client.get(f"/api/v1/research/{job.id}/report.md")).status_code == 404


# ---------- companies + ops ----------


async def test_companies(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.list_companies", AsyncMock(return_value=["Alphabet", "Amazon"])
    )
    assert (await client.get("/api/v1/companies")).json() == {
        "companies": ["Alphabet", "Amazon"]
    }


async def test_ops_status(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    blob_storage: dict[str, FakeContainer],
) -> None:
    monkeypatch.setattr(
        "app.api.routes.get_async_search_client",
        lambda: SimpleNamespace(get_document_count=AsyncMock(return_value=1256)),
    )
    blob_storage["raw-filings"].blobs["_manifest/last-ingestion.json"] = json.dumps(
        {"ingested_at": "2026-09-26T08:00:00+00:00", "documents": 3, "chunks": 1256}
    ).encode()
    stuck = await store.create_job("stuck", [])
    await store.update_job(stuck.id, status=JobStatus.RUNNING)
    await store.create_job("waiting", [])

    body = (await client.get("/api/v1/ops/status")).json()

    assert body["document_count"] == 1256
    assert body["last_ingestion"]["chunks"] == 1256
    assert body["jobs_by_status"] == {"running": 1, "queued": 1}
    assert [j["job_id"] for j in body["running_jobs"]] == [stuck.id]
    assert body["errors"] == []


async def test_ops_status_fails_soft(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.routes.get_async_search_client",
        lambda: SimpleNamespace(
            get_document_count=AsyncMock(side_effect=ValueError("x"))
        ),
    )

    body = (await client.get("/api/v1/ops/status")).json()

    assert body["document_count"] is None and body["last_ingestion"] is None
    assert len(body["errors"]) == 2


# ---------- report markdown ----------


def test_markdown_numbers_citations_and_lists_sources() -> None:
    report = make_report(make_citation(), make_citation(), NEWS)
    report.data_gaps = ["live news unavailable"]

    md = render_report_md(
        report,
        query="q?",
        job_id="job-1",
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
    )

    assert "Up in 2025. [1][1][2]" in md  # same citation keeps its number
    assert "**⚠ Data gaps" in md and "live news unavailable" in md
    assert (
        "1. Amazon 10-K (period 2025-12-31), p.27 — `amzn_annual_report_10k.pdf`, chunk 157"
        in md
    )
    assert (
        "2. News (2026-09-20): Amazon — [example.com](https://www.example.com/aws-deal)"
        in md
    )
    assert "2026-09-26 00:00 UTC" in md


# ---------- archive ----------


async def test_archive_bundle_is_written_once(
    db: None, blob_storage: dict[str, FakeContainer]
) -> None:
    job = await store.create_job("Amazon operating income?", ["Amazon"])
    state = state_with_everything()

    prefix = await archive.archive_report(job, state)
    blobs = blob_storage["reports"].blobs
    original = blobs[prefix + "report.json"]
    state.report.summary = "CHANGED"  # type: ignore[union-attr]
    await archive.archive_report(job, state)  # e.g. a re-run after a crash

    created = job.created_at
    assert prefix == f"{created:%Y}/{created:%m}/{job.id}/"
    assert {n.removeprefix(prefix) for n in blobs} == {
        "report.json",
        "report.md",
        "provenance.json",
    }
    assert blobs[prefix + "report.json"] == original  # never overwritten


async def test_provenance_records_models_tokens_and_references(
    db: None, blob_storage: dict[str, FakeContainer]
) -> None:
    job = await store.create_job("q", ["Amazon"])
    prefix = await archive.archive_report(job, state_with_everything())

    prov = json.loads(blob_storage["reports"].blobs[prefix + "provenance.json"])

    assert [c["deployment"] for c in prov["models"]["calls"]] == [
        "chat-mini",
        "chat-fallback",
    ]
    assert prov["tokens"] == {"prompt": 300, "completion": 120}
    assert prov["evidence"]["filings"][0] == {
        "reference": HIT["id"],
        "title": "Amazon 10-K 2025-12-31 p.27",
        "source_blob": "amzn_annual_report_10k.pdf",
        "chunk_no": 157,
        "page": 27,
        "period": "2025-12-31",
    }
    assert "snippet" not in json.dumps(prov["evidence"])  # references, not documents
    assert prov["degraded_sources"] == ["news"]
    assert prov["retrieval"]["search_index"] == "filings-v1"


async def test_worker_archives_approval_and_report(
    db: None,
    graph_deps: GraphDeps,
    worker_ctx: dict[str, Any],
    blob_storage: dict[str, FakeContainer],
) -> None:
    job = await store.create_job("Amazon operating income", ["Amazon"])
    await run_research(worker_ctx, job.id)
    await run_research(worker_ctx, job.id, resume={"approved": True, "notes": "ok"})

    done = await store.get_job(job.id)
    assert done is not None and done.status == JobStatus.COMPLETED
    assert done.subject == DEFAULT_PLAN.subject
    prefix = done.archive_prefix
    assert prefix is not None
    blobs = blob_storage["reports"].blobs
    approval = json.loads(blobs[prefix + "approval-pass1.json"])
    assert (approval["decision"], approval["notes"]) == ("approved", "ok")
    assert approval["plan_reviewed"]["sub_questions"] == DEFAULT_PLAN.sub_questions
    assert prefix + "report.md" in blobs


async def test_archive_failure_does_not_lose_the_report(
    db: None,
    graph_deps: GraphDeps,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.reports.archive._put", AsyncMock(side_effect=ConnectionError("blob down"))
    )
    job = await store.create_job("q", ["Amazon"])
    await run_research(worker_ctx, job.id)
    await run_research(worker_ctx, job.id, resume={"approved": True})

    done = await store.get_job(job.id)
    assert done is not None and done.status == JobStatus.COMPLETED
    assert done.result is not None and done.archive_prefix is None


# ---------- LLM call recording ----------


def test_llm_calls_are_recorded_per_block() -> None:
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    _log_served("outside", "chat-mini", usage)  # not recording: ignored
    with recording_calls() as calls:
        _log_served("plan", "chat-mini", usage)
    assert [(c.node, c.deployment, c.prompt_tokens) for c in calls] == [
        ("plan", "chat-mini", 10)
    ]


def test_markdown_disarms_injected_content() -> None:
    evil = make_citation(
        source_type="news",
        reference="https://example.com/a(b)",
        quote="# HEADING ![x](http://track.example/p.gif) [click](http://phish)",
    )
    report = make_report(evil)
    report.sections[
        0
    ].body = "Fine **bold** ![img](http://track) [link](http://p) <script>"

    md = render_report_md(report, query="q", job_id="j", generated_at=datetime.now(UTC))

    # No live image or link syntax (an escaped "\\](" is literal text).
    assert not re.search(r"(?<!\\)\]\((http://track|http://phish|http://p\))", md)
    assert "\\# HEADING" in md  # quoted web text stays literal
    assert "**bold**" in md  # the model's own formatting survives
    assert "\\<script>" in md
    assert "(https://example.com/a%28b%29)" in md  # URL can't break the link
