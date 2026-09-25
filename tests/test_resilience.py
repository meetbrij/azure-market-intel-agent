"""Retries, model fallback, degradation to data_gaps, and node timeouts."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
import openai
import pytest
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeTimeoutError
from tavily.errors import BadRequestError as TavilyBadRequest
from tavily.errors import TimeoutError as TavilyTimeout

from app.config import get_settings
from app.graph import llm
from app.graph.build import build_graph, thread_config
from app.graph.state import ResearchState
from app.resilience import is_transient, with_retries
from mcp_news import server
from tests.conftest import GraphDeps

# The OpenAI SDK (3.x) is built on httpx2.
REQUEST = httpx2.Request("POST", "https://example.invalid")


def api_error(status: int) -> openai.APIStatusError:
    cls = {400: openai.BadRequestError, 429: openai.RateLimitError}.get(
        status, openai.InternalServerError
    )
    return cls("err", response=httpx2.Response(status, request=REQUEST), body=None)


def azure_error(status: int) -> HttpResponseError:
    error = HttpResponseError(message="err")
    error.status_code = status
    return error


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    def set_env(**env: str) -> None:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()

    return set_env


# ---------- what counts as transient ----------


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (api_error(429), True),
        (api_error(500), True),
        (api_error(400), False),
        (openai.APITimeoutError(request=REQUEST), True),
        (azure_error(429), True),
        (azure_error(503), True),
        (azure_error(403), False),
        (ServiceRequestError("connection reset"), True),
        (TimeoutError(), True),
        (ValueError("bug"), False),
    ],
)
def test_is_transient(exc: BaseException, transient: bool) -> None:
    assert is_transient(exc) is transient


# ---------- retries ----------


async def test_retries_transient_errors_then_succeeds() -> None:
    call = AsyncMock(side_effect=[api_error(429), azure_error(503), "ok"])
    assert await with_retries("t", call) == "ok"
    assert call.await_count == 3


async def test_never_retries_client_errors() -> None:
    call = AsyncMock(side_effect=api_error(400))
    with pytest.raises(openai.BadRequestError):
        await with_retries("t", call)
    assert call.await_count == 1


async def test_gives_up_after_configured_attempts(settings: Any) -> None:
    settings(RETRY_ATTEMPTS="3")
    call = AsyncMock(side_effect=api_error(429))
    with pytest.raises(openai.RateLimitError):
        await with_retries("t", call)
    assert call.await_count == 3


# ---------- model fallback ----------


def fake_aoai(fail_on: dict[str, BaseException]) -> tuple[MagicMock, list[str]]:
    """Chat client whose deployments in `fail_on` always raise."""
    served: list[str] = []

    async def create(*, model: str, **_: Any) -> Any:
        served.append(model)
        if model in fail_on:
            raise fail_on[model]
        message = SimpleNamespace(content=f"answer from {model}")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    client = MagicMock()
    client.chat.completions.create = create
    return client, served


async def test_falls_back_after_repeated_429(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings(AZURE_OPENAI_CHAT_FALLBACK_DEPLOYMENT="test-fallback", RETRY_ATTEMPTS="2")
    client, served = fake_aoai({"test-chat": api_error(429)})
    monkeypatch.setattr(llm, "get_async_aoai", lambda: client)

    text = await llm.complete_text("compact", "sys", "user")

    assert text == "answer from test-fallback"
    assert served == ["test-chat", "test-chat", "test-fallback"]


async def test_no_fallback_for_client_errors(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings(AZURE_OPENAI_CHAT_FALLBACK_DEPLOYMENT="test-fallback")
    client, served = fake_aoai({"test-chat": api_error(400)})
    monkeypatch.setattr(llm, "get_async_aoai", lambda: client)

    with pytest.raises(openai.BadRequestError):
        await llm.complete_text("compact", "sys", "user")
    assert served == ["test-chat"]  # a bad request would fail on any model


async def test_no_fallback_configured_raises(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings(RETRY_ATTEMPTS="2")
    client, served = fake_aoai({"test-chat": api_error(429)})
    monkeypatch.setattr(llm, "get_async_aoai", lambda: client)

    with pytest.raises(openai.RateLimitError):
        await llm.complete_text("compact", "sys", "user")
    assert served == ["test-chat", "test-chat"]


# ---------- graceful degradation -> data_gaps ----------


async def run_approved(query: str = "q") -> ResearchState:
    from langgraph.types import Command

    graph = build_graph(InMemorySaver())
    config = thread_config("job-1")
    await graph.ainvoke(ResearchState(query=query, companies=["Amazon"]), config)
    await graph.ainvoke(Command(resume={"approved": True}), config)
    return ResearchState.model_validate((await graph.aget_state(config)).values)


async def test_every_failed_source_becomes_a_data_gap(graph_deps: GraphDeps) -> None:
    graph_deps.search.side_effect = TimeoutError("search down")
    plan = graph_deps.llm.responses["plan"]
    graph_deps.llm.responses["plan"] = plan.model_copy(update={"needs_live_news": True})

    final = await run_approved()  # no news tool configured, Search failing

    assert sorted(final.degraded) == ["filings", "news"]
    assert final.report is not None
    assert sorted(final.report.data_gaps) == [
        "filing search unavailable",
        "live news unavailable",
    ]


async def test_healthy_run_has_no_data_gaps(graph_deps: GraphDeps) -> None:
    final = await run_approved()
    assert final.report is not None and final.report.data_gaps == []


# ---------- per-node timeout ----------


async def test_hung_llm_node_times_out(
    graph_deps: GraphDeps, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings(NODE_TIMEOUT_S="0.05")

    async def hang(*_: Any) -> Any:
        await asyncio.sleep(10)

    monkeypatch.setattr("app.graph.nodes.parse_structured", hang)
    graph = build_graph(InMemorySaver())  # reads NODE_TIMEOUT_S when compiled

    with pytest.raises(NodeTimeoutError):
        await graph.ainvoke(ResearchState(query="q"), thread_config("job-1"))


async def test_hung_search_degrades(graph_deps: GraphDeps, settings: Any) -> None:
    settings(NODE_TIMEOUT_S="0.05")

    async def hang(*_: Any, **__: Any) -> Any:
        await asyncio.sleep(10)

    graph_deps.search.side_effect = hang
    from app.graph import nodes

    update = await nodes.retrieve_filings(
        ResearchState.model_validate(
            {"query": "q", "plan": graph_deps.llm.responses["plan"]}
        )
    )
    assert update == {"degraded": ["filings"]}


# ---------- Tavily retries in the MCP server ----------


class FlakyTavily:
    def __init__(self, *errors: BaseException) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return {"results": [{"title": "t", "url": "https://a.example", "content": "c"}]}


async def test_tavily_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    flaky = FlakyTavily(TavilyTimeout(8))
    monkeypatch.setattr(server, "get_tavily", lambda: flaky)
    monkeypatch.setattr(server, "wait_random_exponential", lambda **_: lambda _s: 0)

    results = await server.search_company_news("Amazon")

    assert flaky.calls == 2
    assert results[0]["url"] == "https://a.example"


async def test_tavily_bad_request_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flaky = FlakyTavily(TavilyBadRequest("bad query"))
    monkeypatch.setattr(server, "get_tavily", lambda: flaky)

    with pytest.raises(TavilyBadRequest):
        await server.search_company_news("Amazon")
    assert flaky.calls == 1
