"""Tracing: a real Langfuse client whose spans go to an in-memory exporter,
so the trace a job produces (ids, nesting, models, tokens, tags) is checked
offline."""

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import BaseModel

from app import observability
from app.graph.state import Critique, DraftReport, InjectionScreen, ResearchPlan
from app.jobs import store
from app.jobs.worker import run_research
from tests.conftest import DEFAULT_PLAN, HIT, GraphDeps, make_draft, resume_job


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-test",
        secret_key="sk-test",
        host="http://langfuse.invalid",
        tracer_provider=TracerProvider(),
        span_exporter=exporter,
    )
    observability.set_langfuse(client)
    yield exporter
    client.shutdown()
    observability.set_langfuse(None)


def exported(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    client = observability.get_langfuse()
    assert client is not None
    client.flush()
    return list(exporter.get_finished_spans())


def attr(span: ReadableSpan, key: str) -> Any:
    return (span.attributes or {}).get(key)



# ---------- fake Azure clients under the real llm / retrieval code ----------


class _Results:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._it = iter(rows)

    def __aiter__(self) -> "_Results":
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeSearch:
    async def search(self, **kwargs: Any) -> _Results:
        return _Results([{**HIT, "@search.score": 0.03, "@search.reranker_score": 2.9}])


def _completion(parsed: Any = None, content: str | None = None) -> SimpleNamespace:
    message = SimpleNamespace(parsed=parsed, content=content, refusal=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class _FakeAoai:
    """chat.completions.parse/create and embeddings.create, answering by the
    requested schema, as the real client would per node."""

    def __init__(self) -> None:
        answers: dict[type, BaseModel] = {
            ResearchPlan: DEFAULT_PLAN,
            DraftReport: make_draft(str(HIT["id"])),
            Critique: Critique(is_complete=True),
            InjectionScreen: InjectionScreen(),
        }

        async def parse(*, response_format: type, **kwargs: Any) -> SimpleNamespace:
            return _completion(parsed=answers[response_format].model_copy(deep=True))

        async def create(**kwargs: Any) -> SimpleNamespace:
            return _completion(content=f"Operating income rose [{HIT['id']}]")

        async def embed(*, input: list[str], **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                data=[SimpleNamespace(index=i, embedding=[0.1]) for i in range(len(input))],
                usage=SimpleNamespace(prompt_tokens=7),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse, create=create))
        self.embeddings = SimpleNamespace(create=embed)


def install_fake_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.graph import llm, nodes, retrieval

    monkeypatch.setattr(nodes, "parse_structured", llm.parse_structured)
    monkeypatch.setattr(nodes, "complete_text", llm.complete_text)
    monkeypatch.setattr(nodes, "search_many", retrieval.search_many)
    aoai = _FakeAoai()
    monkeypatch.setattr(llm, "get_async_aoai", lambda: aoai)
    monkeypatch.setattr(retrieval, "get_async_aoai", lambda: aoai)
    search = _FakeSearch()
    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: search)


def test_trace_id_is_the_job_id() -> None:
    job_id = "3ed1b61a-3441-4645-a73c-4f42fe045a4a"
    assert observability.trace_id_for(job_id) == "3ed1b61a34414645a73c4f42fe045a4a"
    assert len(observability.trace_id_for("not-a-uuid")) == 32


def test_tracing_is_off_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_vault() -> None:
        raise PermissionError("no access")

    monkeypatch.setattr("app.azure_clients.get_secret_client", no_vault)
    assert observability.init_tracing() is None
    with observability.job_trace("j", segment="start") as callbacks:
        assert callbacks == []
    with observability.observe("x") as span:
        assert span is None


async def test_a_job_is_one_trace_with_nodes_generations_and_retrieval(
    db: None,
    graph_deps: GraphDeps,
    worker_ctx: dict[str, Any],
    spans: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await store.create_job("Amazon operating income", ["Amazon"], "oid-alice")
    install_fake_clients(monkeypatch)

    await run_research(worker_ctx, job.id)
    await resume_job(worker_ctx, job.id)

    all_spans = exported(spans)
    trace_ids = {format(s.context.trace_id, "032x") for s in all_spans}
    assert trace_ids == {observability.trace_id_for(job.id)}  # one trace per job

    by_name: dict[str, list[ReadableSpan]] = {}
    for s in all_spans:
        by_name.setdefault(s.name, []).append(s)
    assert {"job:start", "job:resume:pass1", "job:outcome"} <= set(by_name)

    # Generations carry the model name (not the deployment) and token usage.
    generations = [s for s in all_spans if attr(s, "langfuse.observation.type") == "generation"]
    assert [g.name for g in generations] == ["plan", "compact", "write", "critique"]
    for g in generations:
        assert attr(g, "langfuse.observation.model.name") == "gpt-5-mini"
        usage = json.loads(attr(g, "langfuse.observation.usage_details"))
        assert usage == {"input": 100, "output": 20}

    # Each generation sits inside its graph node's span, not at the root.
    ids = {s.context.span_id: s for s in all_spans}
    plan_gen = next(g for g in generations if g.name == "plan")
    assert plan_gen.parent is not None
    assert ids[plan_gen.parent.span_id].name == "plan"

    # Retrieval: the embedding and the search, with the mode used.
    [search] = by_name["search"]
    assert attr(search, "langfuse.observation.metadata.mode_used") == "hybrid_semantic"
    [embed] = by_name["embed"]
    assert attr(embed, "langfuse.observation.model.name") == "text-embedding-3-small"

    # Trace attributes: submitter, session, variant/model/outcome tags.
    root = by_name["job:start"][0]
    assert attr(root, "user.id") == "oid-alice"
    assert attr(root, "session.id") == job.id
    tags = set(attr(root, "langfuse.trace.tags") or ())
    assert {"variant:hybrid_semantic", "model:gpt-5-mini"} <= tags
    outcome_tags = set(attr(by_name["job:outcome"][0], "langfuse.trace.tags") or ())
    assert "outcome:completed" in outcome_tags
