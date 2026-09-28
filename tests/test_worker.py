"""Worker: checkpointed runs, approval pause/resume, crash recovery.
The real graph runs with Search and the LLM mocked and an in-memory
checkpointer standing in for Postgres."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from arq.constants import in_progress_key_prefix, result_key_prefix
from langgraph.errors import NodeCancelledError

from app.graph.state import Critique
from app.jobs import store
from app.jobs.models import JobStatus
from app.jobs.worker import recover_stuck_jobs, run_research
from tests.conftest import GraphDeps, resume_job


async def test_job_pauses_for_approval_then_completes_on_resume(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    job = await store.create_job("Amazon operating income", ["Amazon"])

    await run_research(worker_ctx, job.id)

    paused = await store.get_job(job.id)
    assert paused is not None
    assert paused.status == JobStatus.AWAITING_APPROVAL
    assert paused.interrupt is not None
    assert paused.interrupt["sub_questions"] == [
        "Amazon operating income 2024 and 2025"
    ]
    assert paused.last_node == "compact"
    assert "write" not in graph_deps.llm.labels()

    await resume_job(worker_ctx, job.id)

    done = await store.get_job(job.id)
    assert done is not None
    assert done.status == JobStatus.COMPLETED
    assert done.interrupt is None
    assert done.last_node == "critique"
    assert (
        done.checkpoint_count >= 7
    )  # plan, 2 branches, compact, gate, write, critique
    assert done.result is not None
    citation = done.result["sections"][0]["citations"][0]
    assert (citation["chunk_no"], citation["page"]) == (157, 27)
    assert graph_deps.llm.labels().count("plan") == 1  # resumed, not restarted


async def test_rerun_while_paused_stays_paused(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    job = await store.create_job("q", ["Amazon"])
    await run_research(worker_ctx, job.id)

    await run_research(worker_ctx, job.id)  # e.g. picked up by crash recovery

    again = await store.get_job(job.id)
    assert again is not None and again.status == JobStatus.AWAITING_APPROVAL
    assert graph_deps.llm.labels().count("plan") == 1


async def test_crash_mid_run_resumes_from_checkpoint(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    # The worker "dies" during retrieval: CancelledError is what a shutdown
    # delivers; a SIGKILL simply never returns. Either way the row stays
    # "running" and the checkpoint keeps everything before retrieval.
    graph_deps.search.side_effect = asyncio.CancelledError()
    job = await store.create_job("q", ["Amazon"])
    with pytest.raises((asyncio.CancelledError, NodeCancelledError)):
        await run_research(worker_ctx, job.id)
    crashed = await store.get_job(job.id)
    assert crashed is not None and crashed.status == JobStatus.RUNNING

    graph_deps.search.side_effect = None  # the restarted worker's Search works
    await run_research(worker_ctx, job.id)  # what recovery triggers
    await resume_job(worker_ctx, job.id)

    done = await store.get_job(job.id)
    assert done is not None and done.status == JobStatus.COMPLETED
    assert graph_deps.llm.labels().count("plan") == 1  # plan was not redone



async def test_stale_resume_for_an_earlier_pass_is_dropped(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    # Pass 1 approved; the critic loops, so the job pauses again at pass 2.
    graph_deps.llm.responses["critique"] = [
        Critique(is_complete=False, missing=["x"]),
        Critique(is_complete=True),
    ]
    job = await store.create_job("q", ["Amazon"])
    await run_research(worker_ctx, job.id)
    await resume_job(worker_ctx, job.id)
    paused = await store.get_job(job.id)
    assert paused is not None and paused.interrupt is not None
    assert paused.interrupt["pass"] == 2
    await store.update_job(job.id, status=JobStatus.QUEUED)  # a real resume is queued

    # arq retries the old pass-1 approval task after a crash.
    await run_research(worker_ctx, job.id, resume={"approved": True, "pass": 1})

    still = await store.get_job(job.id)
    assert still is not None and still.status == JobStatus.QUEUED
    assert graph_deps.llm.labels().count("write") == 1  # pass 2 not written


async def test_resume_is_dropped_unless_the_job_was_claimed(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    job = await store.create_job("q", ["Amazon"])
    await run_research(worker_ctx, job.id)  # awaiting approval of pass 1

    await run_research(worker_ctx, job.id, resume={"approved": True, "pass": 1})

    still = await store.get_job(job.id)
    assert still is not None and still.status == JobStatus.AWAITING_APPROVAL
    assert "write" not in graph_deps.llm.labels()


async def test_worker_audits_models_served_and_outcome(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    job = await store.create_job("q", ["Amazon"])
    await run_research(worker_ctx, job.id)
    await resume_job(worker_ctx, job.id)

    events = await store.list_audit(job.id)
    served = [e.detail for e in events if e.action == "llm_call"]
    assert [d["node"] for d in served] == ["plan", "compact", "write", "critique"]
    assert all(
        (d["deployment"], d["prompt_tokens"], d["completion_tokens"])
        == ("test-chat", 100, 20)
        for d in served
    )
    assert events[-1].action == "job_completed"
    assert all(e.actor == store.SYSTEM for e in events)

async def test_run_research_records_failure(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    graph_deps.llm.responses["plan"] = RuntimeError("model refused")
    job = await store.create_job("anything", [])

    await run_research(worker_ctx, job.id)

    failed = await store.get_job(job.id)
    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.error == "RuntimeError: model refused"


async def test_search_outage_degrades_instead_of_failing(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    graph_deps.search.side_effect = TimeoutError("search down")
    job = await store.create_job("anything", ["Amazon"])

    await run_research(worker_ctx, job.id)
    paused = await store.get_job(job.id)
    assert paused is not None and paused.interrupt is not None
    assert paused.interrupt["degraded"] == ["filings"]  # reviewer sees it
    # No evidence means no citations, so the critic forces the second (last)
    # pass, which pauses for approval again.
    await resume_job(worker_ctx, job.id)
    await resume_job(worker_ctx, job.id)

    done = await store.get_job(job.id)
    assert done is not None and done.status == JobStatus.COMPLETED
    assert done.result is not None
    assert done.result["data_gaps"] == ["filing search unavailable"]


async def test_finished_jobs_are_not_rerun(
    db: None, graph_deps: GraphDeps, worker_ctx: dict[str, Any]
) -> None:
    job = await store.create_job("q", [])
    await store.update_job(job.id, status=JobStatus.COMPLETED)

    await run_research(worker_ctx, job.id)

    assert graph_deps.llm.calls == []


# ---------- startup recovery ----------


def fake_redis(existing_results: set[str] = frozenset()) -> AsyncMock:  # type: ignore[assignment]
    redis = AsyncMock()
    redis.exists.side_effect = lambda key: int(
        key.removeprefix(result_key_prefix) in existing_results
    )
    return redis


async def test_recovery_releases_lock_and_reenqueues_running_jobs(db: None) -> None:
    stuck = await store.create_job("q", [])
    await store.update_job(stuck.id, status=JobStatus.RUNNING)
    paused = await store.create_job("q2", [])
    await store.update_job(paused.id, status=JobStatus.AWAITING_APPROVAL)
    redis = fake_redis()

    assert await recover_stuck_jobs(redis) == 1

    redis.delete.assert_awaited_once_with(in_progress_key_prefix + stuck.id)
    redis.enqueue_job.assert_awaited_once_with(
        "run_research", stuck.id, _job_id=stuck.id
    )


async def test_recovery_uses_fresh_arq_id_when_result_exists(db: None) -> None:
    stuck = await store.create_job("q", [])
    await store.update_job(stuck.id, status=JobStatus.RUNNING)
    redis = fake_redis(existing_results={stuck.id})

    await recover_stuck_jobs(redis)

    arq_id = redis.enqueue_job.await_args.kwargs["_job_id"]
    assert arq_id.startswith(f"{stuck.id}:recover:")
