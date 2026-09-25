"""Shared fixtures. No network: Azure settings are dummies, the DB is a temp
SQLite file, and the arq queue is a mock."""

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

# Set before importing app modules: some read settings at import time.
DUMMY_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://example.invalid/",
    "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
    "AZURE_OPENAI_CHAT_DEPLOYMENT": "test-chat",
    "AZURE_OPENAI_EMBED_DEPLOYMENT": "test-embed",
    "AZURE_SEARCH_ENDPOINT": "https://example.invalid",
    "AZURE_KEYVAULT_URL": "https://example.invalid/",
    "AZURE_STORAGE_ACCOUNT_URL": "https://example.invalid",
    # Read by LangGraph at import time: tests must run in strict mode too.
    "LANGGRAPH_STRICT_MSGPACK": "true",
}
os.environ.update(DUMMY_ENV)


from langgraph.checkpoint.memory import InMemorySaver

from app.api.main import app
from app.api.routes import get_queue
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import (
    Citation,
    Critique,
    DraftCitation,
    DraftReport,
    DraftSection,
    Report,
    ReportSection,
    ResearchPlan,
)
from app.graph.tools import set_news_tool
from app.jobs import store


def _clear_caches() -> None:
    get_settings.cache_clear()
    store.get_engine.cache_clear()
    store.get_sessionmaker.cache_clear()


@pytest.fixture(autouse=True)
def settings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
async def db() -> AsyncIterator[None]:
    await store.init_db()
    yield
    await store.dispose_engine()


@pytest.fixture
def queue() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
async def client(db: None, queue: AsyncMock) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_queue] = lambda: queue
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


HIT = {
    "id": "amzn-annual-report-10k-157",
    "content": "Operating income was $68.6 billion and $80.0 billion for 2024 and 2025.",
    "company": "Amazon",
    "ticker": "AMZN",
    "doc_type": "10-K",
    "period": "2025-12-31",
    "source_blob": "amzn_annual_report_10k.pdf",
    "chunk_no": 157,
    "page": 27,
    "score": 0.72,
}


def make_citation(**overrides: object) -> Citation:
    data: dict[str, object] = {
        "company": "Amazon",
        "doc_type": "10-K",
        "period": "2025-12-31",
        "source_blob": "amzn_annual_report_10k.pdf",
        "chunk_no": 157,
        "page": 27,
        "quote": "Operating income was $68.6 billion and $80.0 billion for 2024 and 2025.",
        "source_type": "filing",
        "reference": HIT["id"],
    }
    data.update(overrides)
    return Citation.model_validate(data)


def make_report(*citations: Citation) -> Report:
    return Report(
        subject="Amazon operating income",
        summary="Operating income rose from $68.6B to $80.0B.",
        sections=[
            ReportSection(
                heading="Change", body="Up in 2025.", citations=list(citations)
            )
        ],
    )


# ---------- graph fakes: LLM, Search and the company list, no network ----------

DEFAULT_PLAN = ResearchPlan(
    subject="Amazon operating income",
    sub_questions=["Amazon operating income 2024 and 2025"],
    companies=["Amazon"],
    needs_live_news=False,
)


def make_draft(*references: str) -> DraftReport:
    return DraftReport(
        subject="Amazon operating income",
        summary="Operating income rose from $68.6B to $80.0B.",
        sections=[
            DraftSection(
                heading="Change",
                body="Up in 2025.",
                citations=[
                    DraftCitation(
                        reference=r, quote="Operating income was $80.0 billion"
                    )
                    for r in references
                ],
            )
        ],
    )


@dataclass
class FakeLLM:
    """Stands in for app.graph.llm. Each label maps to one response (reused)
    or a list (consumed in order). Records (label, user message) per call."""

    responses: dict[str, Any] = field(
        default_factory=lambda: {
            "plan": DEFAULT_PLAN,
            "compact": f"Operating income rose [{HIT['id']}]",
            "write": make_draft(str(HIT["id"])),
            "critique": Critique(is_complete=True),
        }
    )
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _next(self, label: str) -> Any:
        r = self.responses[label]
        value = r.pop(0) if isinstance(r, list) else r
        return value.model_copy(deep=True) if hasattr(value, "model_copy") else value

    async def parse_structured(
        self, label: str, system: str, user: str, schema: type[Any]
    ) -> Any:
        self.calls.append((label, user))
        return self._next(label)

    async def complete_text(self, label: str, system: str, user: str) -> str:
        self.calls.append((label, user))
        return str(self._next(label))

    def labels(self) -> list[str]:
        return [label for label, _ in self.calls]


@dataclass
class GraphDeps:
    llm: FakeLLM
    search: AsyncMock


@pytest.fixture
def graph_deps(monkeypatch: pytest.MonkeyPatch) -> Iterator[GraphDeps]:
    llm = FakeLLM()
    search = AsyncMock(return_value=[HIT])
    monkeypatch.setattr("app.graph.nodes.parse_structured", llm.parse_structured)
    monkeypatch.setattr("app.graph.nodes.complete_text", llm.complete_text)
    monkeypatch.setattr("app.graph.nodes.search_many", search)
    monkeypatch.setattr(
        "app.graph.nodes.list_companies",
        AsyncMock(return_value=["Alphabet", "Amazon", "Microsoft"]),
    )
    set_news_tool(None)
    yield GraphDeps(llm=llm, search=search)
    set_news_tool(None)


@pytest.fixture
def worker_ctx() -> dict[str, Any]:
    """What arq passes run_research: the graph compiled with a checkpointer
    (in-memory here; Postgres in the real worker)."""
    return {"graph": build_graph(InMemorySaver())}
