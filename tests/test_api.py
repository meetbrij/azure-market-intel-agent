from unittest.mock import AsyncMock

from httpx import AsyncClient

from app.jobs import store
from app.jobs.models import JobStatus


async def test_post_returns_job_id_and_enqueues(
    client: AsyncClient, queue: AsyncMock
) -> None:
    resp = await client.post(
        "/api/v1/research",
        json={"query": "Compare cloud revenue growth", "companies": ["Amazon"]},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    job_id = body["job_id"]
    queue.enqueue_job.assert_awaited_once_with("run_research", job_id, _job_id=job_id)

    job = await client.get(f"/api/v1/research/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "queued"
    assert job.json()["companies"] == ["Amazon"]
    assert job.json()["report"] is None


async def test_get_unknown_job_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/research/does-not-exist")
    assert resp.status_code == 404


async def test_post_rejects_empty_query(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/research", json={"query": ""})
    assert resp.status_code == 422


async def test_enqueue_failure_marks_job_failed(
    client: AsyncClient, queue: AsyncMock
) -> None:
    queue.enqueue_job.side_effect = ConnectionError("redis down")

    resp = await client.post("/api/v1/research", json={"query": "anything at all"})

    assert resp.status_code == 503
    job_id = queue.enqueue_job.await_args.args[1]
    job = await store.get_job(job_id)
    assert job is not None
    assert job.status == "failed"
    assert job.error is not None and "redis down" in job.error


async def test_health_reports_dependencies(
    client: AsyncClient, queue: AsyncMock
) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "postgres": "ok", "redis": "ok"}

    queue.ping.side_effect = ConnectionError("redis down")
    resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["redis"] == "error: ConnectionError"


# ---------- approval: resume endpoint ----------


async def paused_job() -> str:
    job = await store.create_job("q", ["Amazon"])
    await store.update_job(
        job.id,
        status=JobStatus.AWAITING_APPROVAL,
        interrupt={"sub_questions": ["Amazon AWS net sales"], "pass": 1},
    )
    return job.id


async def test_get_exposes_approval_request(client: AsyncClient) -> None:
    job_id = await paused_job()

    body = (await client.get(f"/api/v1/research/{job_id}")).json()

    assert body["status"] == "awaiting_approval"
    assert body["interrupt"]["sub_questions"] == ["Amazon AWS net sales"]


async def test_resume_enqueues_decision(client: AsyncClient, queue: AsyncMock) -> None:
    job_id = await paused_job()

    resp = await client.post(
        f"/api/v1/research/{job_id}/resume",
        json={"approved": False, "notes": "Add Microsoft"},
    )

    assert resp.status_code == 202
    assert resp.json() == {"job_id": job_id, "status": "queued"}
    call = queue.enqueue_job.await_args
    assert call.args == ("run_research", job_id)
    assert call.kwargs["resume"] == {"approved": False, "notes": "Add Microsoft"}
    assert call.kwargs["_job_id"].startswith(f"{job_id}:resume:")


async def test_resume_twice_conflicts(client: AsyncClient) -> None:
    job_id = await paused_job()
    first = await client.post(
        f"/api/v1/research/{job_id}/resume", json={"approved": True}
    )
    second = await client.post(
        f"/api/v1/research/{job_id}/resume", json={"approved": True}
    )
    assert (first.status_code, second.status_code) == (202, 409)


async def test_resume_unknown_job_404(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/research/nope/resume", json={"approved": True})
    assert resp.status_code == 404


async def test_resume_enqueue_failure_keeps_job_paused(
    client: AsyncClient, queue: AsyncMock
) -> None:
    job_id = await paused_job()
    queue.enqueue_job.side_effect = ConnectionError("redis down")

    resp = await client.post(
        f"/api/v1/research/{job_id}/resume", json={"approved": True}
    )

    assert resp.status_code == 503
    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.AWAITING_APPROVAL
