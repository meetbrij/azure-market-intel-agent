"""Worker runs the real graph with Search and the LLM mocked."""

from app.jobs import store
from app.jobs.worker import run_research
from tests.conftest import GraphDeps


async def test_run_research_completes_job(db: None, graph_deps: GraphDeps) -> None:
    job = await store.create_job("Amazon operating income", ["Amazon"])

    await run_research({}, job.id)

    done = await store.get_job(job.id)
    assert done is not None
    assert done.status == "completed"
    assert done.result is not None
    citation = done.result["sections"][0]["citations"][0]
    assert (citation["chunk_no"], citation["page"]) == (157, 27)
    assert graph_deps.search.await_args is not None
    assert graph_deps.search.await_args.args[1] == ["Amazon"]


async def test_run_research_records_failure(db: None, graph_deps: GraphDeps) -> None:
    graph_deps.search.side_effect = TimeoutError("search down")
    job = await store.create_job("anything", [])

    await run_research({}, job.id)

    failed = await store.get_job(job.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "TimeoutError: search down"
