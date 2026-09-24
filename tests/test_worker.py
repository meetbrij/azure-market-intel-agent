"""Worker runs the real graph with Search and the LLM mocked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.jobs import store
from app.jobs.worker import run_research
from tests.conftest import HIT, make_citation, make_report


def fake_aoai(report: object) -> MagicMock:
    message = SimpleNamespace(parsed=report, refusal=None)
    client = MagicMock()
    client.chat.completions.parse = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )
    return client


async def test_run_research_completes_job(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = AsyncMock(return_value=[HIT])
    aoai = fake_aoai(make_report(make_citation()))
    monkeypatch.setattr("app.graph.nodes.search", search)
    monkeypatch.setattr("app.graph.nodes.get_async_aoai", lambda: aoai)
    job = await store.create_job("Amazon operating income", ["Amazon"])

    await run_research({}, job.id)

    done = await store.get_job(job.id)
    assert done is not None
    assert done.status == "completed"
    assert done.result is not None
    assert done.result["sections"][0]["citations"][0]["chunk_no"] == 157
    search.assert_awaited_once_with("Amazon operating income", ["Amazon"], k=8)
    prompt = aoai.chat.completions.parse.await_args.kwargs["messages"][1]["content"]
    assert "chunk_no=157" in prompt


async def test_run_research_records_failure(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.graph.nodes.search", AsyncMock(side_effect=TimeoutError("search down"))
    )
    job = await store.create_job("anything", [])

    await run_research({}, job.id)

    failed = await store.get_job(job.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "TimeoutError: search down"
