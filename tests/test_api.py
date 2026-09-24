from unittest.mock import AsyncMock

from httpx import AsyncClient

from app.jobs import store


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
