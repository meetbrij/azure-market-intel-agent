"""Shared fixtures. No network: Azure settings are dummies, the DB is a temp
SQLite file, and the arq queue is a mock."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
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
}
os.environ.update(DUMMY_ENV)


from app.api.main import app
from app.api.routes import get_queue
from app.config import get_settings
from app.graph.state import Citation, Report, ReportSection
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
